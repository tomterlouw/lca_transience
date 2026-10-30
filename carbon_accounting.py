"""Optional physical carbon accounts, separate from premise/Pathways LCIA.

Amounts in this module are physical kg C unless a column explicitly says CO2
or CH4. Nothing here edits an inventory, installs a characterization factor or
turns product carbon into a negative elementary flow. Importing has no I/O.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
from uuid import uuid4

import numpy as np
import pandas as pd

ORIGINS = ("fossil", "biogenic", "dac", "unknown")
CO2_PER_C = 44.0 / 12.0
CH4_PER_C = 16.0 / 12.0
CONTEXT = ["scenario", "year"]
GATE_KEYS = CONTEXT + ["product", "route"]


def _require(frame, columns, label):
    missing = set(columns) - set(frame.columns)
    if missing:
        raise ValueError(f"{label} is missing columns: {sorted(missing)}")


def _number(frame, column, label, *, nonnegative=True, fraction=False):
    values = pd.to_numeric(frame[column], errors="raise").astype(float)
    if not np.isfinite(values.to_numpy()).all():
        raise ValueError(f"{label}/{column} must contain finite numbers.")
    if nonnegative and (values < 0).any():
        raise ValueError(f"{label}/{column} cannot be negative.")
    if fraction and (values > 1).any():
        raise ValueError(f"{label}/{column} must be between zero and one.")
    frame[column] = values


def _years(frame, label):
    _number(frame, "year", label)
    if (frame.year % 1 != 0).any():
        raise ValueError(f"{label} years must be integers.")
    frame["year"] = frame.year.astype(int)


def _origins(frame, label):
    if frame.origin.isna().any() or not set(frame.origin).issubset(ORIGINS):
        raise ValueError(f"{label} origins must be one of {ORIGINS}.")


def _wild(value):
    return pd.isna(value) or str(value).strip() in ("", "*")


def _same(left, right, key):
    if key == "year":
        try:
            return float(left) == float(right)
        except (TypeError, ValueError):
            return False
    return str(left).strip() == str(right).strip()


def _rules_for(table, record, keys, label, *, required=True):
    """Select an exact rule before wildcard rules; never average ambiguities."""
    if table is None or table.empty:
        if required:
            raise ValueError(f"No {label} rules for {record}.")
        return pd.DataFrame()
    selected = table.copy()
    score = np.zeros(len(selected), dtype=int)
    mask = np.ones(len(selected), dtype=bool)
    for key in keys:
        if key not in selected or key not in record:
            continue
        values = selected[key].tolist()
        wild = np.array([_wild(value) for value in values])
        match = np.array([_same(value, record[key], key) for value in values])
        mask &= wild | match
        score += (~wild).astype(int)
    selected = selected.loc[mask].copy()
    score = score[mask]
    if selected.empty:
        if required:
            raise ValueError(f"No {label} rules for {record}.")
        return selected
    return selected.loc[score == score.max()].copy()


def audit_carbon_balance(flows, *, absolute_tolerance=1e-7, relative_tolerance=1e-6):
    """Audit unallocated physical flows; residual is incoming minus outgoing C.

    Input: scenario/year/process_id/origin/direction/stream_type/amount_kgC.
    Captured carbon is one outgoing stream at this boundary. Do not include its
    later storage again as another outgoing stream of the same process.
    """
    frame = flows.copy()
    keys = CONTEXT + ["process_id"]
    _require(frame, keys + ["origin", "direction", "stream_type", "amount_kgC"], "Carbon flows")
    if frame[keys + ["direction", "stream_type"]].isna().any().any():
        raise ValueError("Carbon-flow identities cannot be missing.")
    _years(frame, "Carbon flows")
    _origins(frame, "Carbon flows")
    _number(frame, "amount_kgC", "Carbon flows")
    if not set(frame.direction).issubset({"in", "out"}):
        raise ValueError("Carbon-flow direction must be in or out.")
    rows = []
    for identity, part in frame.groupby(keys, sort=True, dropna=False):
        incoming = float(part.loc[part.direction.eq("in"), "amount_kgC"].sum())
        outgoing = float(part.loc[part.direction.eq("out"), "amount_kgC"].sum())
        residual = incoming - outgoing
        tolerance = absolute_tolerance + relative_tolerance * max(incoming, outgoing)
        row = dict(zip(keys, identity), incoming_kgC=incoming, outgoing_kgC=outgoing,
                   residual_kgC=residual, tolerance_kgC=tolerance,
                   balance_status="balanced" if abs(residual) <= tolerance else "unbalanced")
        for origin in ORIGINS:
            subset = part.loc[part.origin.eq(origin)]
            row[f"{origin}_residual_kgC"] = float(
                subset.loc[subset.direction.eq("in"), "amount_kgC"].sum()
                - subset.loc[subset.direction.eq("out"), "amount_kgC"].sum())
        origin_balanced = all(abs(row[f"{origin}_residual_kgC"]) <= tolerance for origin in ORIGINS)
        row["origin_balance_status"] = "balanced" if origin_balanced else "origin_mismatch"
        if abs(residual) <= tolerance and not origin_balanced:
            row["balance_status"] = "origin_mismatch"
        if "allocation_stage" in part and not part.allocation_stage.fillna("pre").eq("pre").all():
            row["balance_status"] = "allocated_recipe_not_physical_balance"
        rows.append(row)
    return pd.DataFrame(rows, columns=keys + ["incoming_kgC", "outgoing_kgC", "residual_kgC",
        "tolerance_kgC", "balance_status", "origin_balance_status"] + [f"{o}_residual_kgC" for o in ORIGINS])


def propagate_carbon_origins(external, transfers, *, tolerance=1e-6):
    """Mix carbon origins through physical process/pool transfers.

    external: scenario/year/process_id/origin/amount_kgC.
    transfers: scenario/year/supplier_id/consumer_id/amount_kgC.
    Process IDs identify homogeneous carbon pools. Keep fuel combustion and
    feedstock pools separate where their carbon partitions differ. A closed
    loop without an external origin anchor stays unknown. Recycling changes
    neither origin nor atmospheric uptake. Transfers exceeding carbon input
    are rejected rather than silently manufacturing carbon.
    """
    ext, links = external.copy(), transfers.copy()
    _require(ext, CONTEXT + ["process_id", "origin", "amount_kgC"], "External carbon")
    _require(links, CONTEXT + ["supplier_id", "consumer_id", "amount_kgC"], "Carbon transfers")
    for table, label in ((ext, "External carbon"), (links, "Carbon transfers")):
        _years(table, label)
        _number(table, "amount_kgC", label)
        if table.drop(columns="amount_kgC").isna().any().any():
            raise ValueError(f"{label} has missing identities.")
    _origins(ext, "External carbon")
    contexts = pd.concat([ext[CONTEXT], links[CONTEXT]], ignore_index=True).drop_duplicates()
    output, diagnostics = [], []
    for context in contexts.to_dict("records"):
        e = ext.loc[ext.scenario.eq(context["scenario"]) & ext.year.eq(context["year"])]
        t = links.loc[links.scenario.eq(context["scenario"]) & links.year.eq(context["year"])]
        nodes = sorted(set(e.process_id) | set(t.supplier_id) | set(t.consumer_id))
        ids = {node: i for i, node in enumerate(nodes)}
        n = len(nodes)
        external_C = np.zeros((n, len(ORIGINS)))
        transfer_C = np.zeros((n, n))  # consumer, supplier
        for row in e.itertuples(index=False):
            external_C[ids[row.process_id], ORIGINS.index(row.origin)] += row.amount_kgC
        for row in t.itertuples(index=False):
            transfer_C[ids[row.consumer_id], ids[row.supplier_id]] += row.amount_kgC
        incoming = external_C.sum(axis=1) + transfer_C.sum(axis=1)
        outgoing = transfer_C.sum(axis=0)
        impossible = outgoing > incoming + tolerance * np.maximum(1., incoming)
        if impossible.any():
            bad = [nodes[i] for i in np.flatnonzero(impossible)]
            raise ValueError(f"Outgoing carbon exceeds physical input for {context}: {bad[:8]}")
        anchored = set(np.flatnonzero(external_C.sum(axis=1) > 0).tolist())
        frontier = list(anchored)
        while frontier:
            supplier = frontier.pop()
            for consumer in np.flatnonzero(transfer_C[:, supplier] > 0):
                if int(consumer) not in anchored:
                    anchored.add(int(consumer)); frontier.append(int(consumer))
        fractions = np.zeros((n, len(ORIGINS)))
        fractions[:, ORIGINS.index("unknown")] = 1.
        active = sorted(anchored)
        if active:
            unresolved = sorted(set(range(n)) - anchored)
            denominator = incoming[active]
            weights = transfer_C[np.ix_(active, active)] / denominator[:, None]
            rhs = external_C[active] / denominator[:, None]
            if unresolved:
                rhs[:, ORIGINS.index("unknown")] += transfer_C[np.ix_(active, unresolved)].sum(axis=1) / denominator
            try:
                solved = np.linalg.solve(np.eye(len(active)) - weights, rhs)
            except np.linalg.LinAlgError as error:
                raise ValueError(f"Carbon-origin network is singular for {context}; review loops and external anchors.") from error
            if (solved < -tolerance).any() or not np.isfinite(solved).all():
                raise ValueError(f"Invalid origin solution for {context}; review physical transfers.")
            solved = np.clip(solved, 0., 1.)
            if not np.allclose(solved.sum(axis=1), 1., atol=tolerance, rtol=tolerance):
                raise ValueError(f"Carbon-origin solution does not sum to one for {context}.")
            fractions[active] = solved / solved.sum(axis=1)[:, None]
        for i, node in enumerate(nodes):
            for j, origin in enumerate(ORIGINS):
                output.append(dict(context, process_id=node, origin=origin, share=float(fractions[i, j])))
            status = "resolved_mixing" if i in anchored else (
                "zero_carbon_input" if incoming[i] == 0 else "unanchored_closed_loop_unknown")
            diagnostics.append(dict(context, process_id=node, incoming_kgC=float(incoming[i]),
                external_kgC=float(external_C[i].sum()), transferred_out_kgC=float(outgoing[i]),
                remaining_for_products_losses_kgC=float(incoming[i]-outgoing[i]), status=status))
    return {"origins": pd.DataFrame(output, columns=CONTEXT + ["process_id", "origin", "share"]),
            "diagnostics": pd.DataFrame(diagnostics)}


def build_factory_gate_carbon(production, content, origins=None):
    """Pair annual production with physical product carbon and origin shares.

    Production needs scenario/year/product/route/production_kg. Content needs
    product/carbon_fraction_kgC_per_kg, with optional source/status. Origins may
    use scenario/year/product/route or process_id keys with wildcard defaults.
    Missing origins become unknown; missing content remains NaN and flagged.
    Carbon retained and CO2-equivalent content are indicators, never credits.
    """
    prod, contents = production.copy(), content.copy()
    _require(prod, GATE_KEYS + ["production_kg"], "Production")
    _require(contents, ["product", "carbon_fraction_kgC_per_kg"], "Carbon content")
    if prod[GATE_KEYS].isna().any().any():
        raise ValueError("Production identities cannot be missing.")
    _years(prod, "Production"); _number(prod, "production_kg", "Production")
    if prod.duplicated(GATE_KEYS).any() or contents.duplicated("product").any():
        raise ValueError("Duplicate production coordinates or product carbon-content rules.")
    values = pd.to_numeric(contents.carbon_fraction_kgC_per_kg, errors="raise")
    if (values.dropna() < 0).any() or (values.dropna() > 1).any() or np.isinf(values).any():
        raise ValueError("Known product carbon fractions must be finite fractions between zero and one.")
    contents["carbon_fraction_kgC_per_kg"] = values
    rules = None if origins is None else origins.copy()
    if rules is not None and not rules.empty:
        _require(rules, ["origin", "share"], "Origin rules")
        _origins(rules, "Origin rules"); _number(rules, "share", "Origin rules", fraction=True)
    joined = prod.merge(contents, on="product", how="left", validate="many_to_one", suffixes=("", "_content"))
    rows = []
    for record in joined.to_dict("records"):
        keys = GATE_KEYS + (["process_id"] if "process_id" in prod and rules is not None and "process_id" in rules else [])
        selected = _rules_for(rules, record, keys, "carbon origin", required=False)
        shares = {origin: 0. for origin in ORIGINS}
        if selected.empty:
            shares["unknown"] = 1.; origin_status = "unresolved_origin"
        else:
            if selected.duplicated("origin").any():
                raise ValueError(f"Ambiguous origin rules for {record['product']}/{record['route']}/{record['year']}.")
            total = float(selected.share.sum())
            if not np.isclose(total, 1., atol=1e-8, rtol=0):
                raise ValueError(f"Origin shares must sum to one, got {total} for {record['product']}/{record['route']}.")
            shares.update(dict(zip(selected.origin, selected.share)))
            origin_status = "unknown_included" if shares["unknown"] > 0 else "specified_origin"
        fraction = record["carbon_fraction_kgC_per_kg"]
        known = pd.notna(fraction)
        total_C = record["production_kg"] * fraction if known else np.nan
        record.update(total_carbon_kgC=total_C,
            carbon_content_status="known" if known else "unresolved_content",
            origin_status=origin_status, physical_CO2_equivalent_kg=total_C * CO2_PER_C)
        for origin in ORIGINS:
            record[f"share_{origin}"] = shares[origin]
            record[f"carbon_{origin}_kgC"] = total_C * shares[origin]
        record["interpretation"] = "Carbon in products at gate; not an atmospheric removal or storage credit"
        rows.append(record)
    return pd.DataFrame(rows)


def annualize_factory_gate(gate, *, interpolation="linear"):
    """Expand assessed years only within each production series' first/last year.

    Linear interpolation is an explicit scenario assumption, not extra LCA.
    No production is extrapolated beyond the supplied production window.
    """
    if interpolation not in ("linear", "none"):
        raise ValueError("Choose interpolation='linear' or 'none'.")
    keys = ["scenario", "product", "route"]
    columns = ["production_kg", "total_carbon_kgC"] + [f"carbon_{o}_kgC" for o in ORIGINS]
    _require(gate, GATE_KEYS + columns, "Factory-gate carbon")
    if gate.duplicated(GATE_KEYS).any():
        raise ValueError("Duplicate gate coordinates.")
    output = []
    for identity, group in gate.groupby(keys, dropna=False, sort=True):
        group = group.sort_values("year").set_index("year")
        years = np.arange(int(group.index.min()), int(group.index.max()) + 1)
        if interpolation == "none" and len(years) != len(group):
            raise ValueError("Missing intermediate production years; choose explicit linear interpolation or supply annual data.")
        result = group.reindex(years)[columns].copy()
        if interpolation == "linear":
            result = result.interpolate(method="index", limit_area="inside")
        result.index.name = "year"
        result = result.reset_index()
        for key, value in zip(keys, identity): result[key] = value
        result["production_interpolated"] = ~result.year.isin(group.index)
        output.append(result)
    return pd.concat(output, ignore_index=True) if output else pd.DataFrame(columns=GATE_KEYS + columns)


def _lifetime(rule):
    kind = str(rule.get("lifetime_type", "")).strip()
    life = float(rule.get("lifetime_years", np.nan))
    if kind not in ("deterministic", "weibull") or not np.isfinite(life) or life <= 0:
        raise ValueError("Applications require deterministic/weibull lifetime_type and positive lifetime_years.")
    shape = float(rule.get("weibull_shape", np.nan)) if kind == "weibull" else None
    if kind == "weibull" and (not np.isfinite(shape) or shape <= 0):
        raise ValueError("Weibull applications require a positive weibull_shape.")
    scale = life / math.gamma(1 + 1 / shape) if kind == "weibull" else life
    return kind, scale, shape


def _survival(age, spec):
    if age <= 0: return 1.
    kind, scale, shape = spec
    return float(age < math.ceil(scale)) if kind == "deterministic" else math.exp(-((age / scale) ** shape))


def _applications_for(applications, record):
    selected = _rules_for(applications, record, ["scenario", "year", "product"], "application")
    if selected.application.isna().any() or selected.duplicated("application").any():
        raise ValueError(f"Ambiguous application rules for {record}.")
    _number(selected, "share", "Applications", fraction=True)
    if not np.isclose(selected.share.sum(), 1., atol=1e-8, rtol=0):
        raise ValueError(f"Application shares must sum to one for {record}.")
    selected = selected.loc[selected.share.gt(0)].copy()
    for rule in selected.to_dict("records"): _lifetime(rule)
    return selected


def simulate_product_cohorts(gate, applications, fates, *, start_year, end_year,
                            initial_stock=None, assume_zero_initial_stock=False,
                            interpolation="linear", strict_content=True,
                            inflow_basis="independent_production_cohorts",
                            recycling_mode="separate_pool", allow_additive_recycling=False):
    """Track origin-preserving product cohorts and end-of-life carbon fates.

    No LCA burden or avoided-product credit is added. New product cohorts first
    retire in the year after entry. By default recovered carbon is reported as
    a separate secondary-feedstock pool, without adding product demand beyond
    the supplied production. Explicit recycling_mode='loop' follows recovered
    carbon into another cohort next year, retaining origin and root cohort;
    this requires allow_additive_recycling=True when production cohorts are
    independent. It is a screening sensitivity, not demand-cleared ITOM demand.
    Gross factory-gate production must not be called fresh carbon inflow when
    its existing recycled content is unresolved.

    initial_stock: scenario/product/application/origin/amount_kgC/age_years,
    measured immediately before start_year. Passing no initial stock requires
    an explicit zero-stock assumption; it describes only new modelled cohorts.
    """
    start_year, end_year = int(start_year), int(end_year)
    if end_year < start_year:
        raise ValueError("end_year cannot precede start_year.")
    if inflow_basis not in ("independent_production_cohorts", "external_fresh_carbon"):
        raise ValueError("Choose an explicit independent_production_cohorts or external_fresh_carbon inflow basis.")
    if recycling_mode not in ("separate_pool", "loop"):
        raise ValueError("recycling_mode must be separate_pool or loop.")
    if recycling_mode == "loop" and inflow_basis == "independent_production_cohorts" and not allow_additive_recycling:
        raise ValueError("Loop screening adds recovered cohorts beyond gross production; explicitly acknowledge allow_additive_recycling=True or use separate_pool.")
    if initial_stock is None and not assume_zero_initial_stock:
        raise ValueError("Supply initial_stock or explicitly set assume_zero_initial_stock=True for a new-cohort-only view.")
    if initial_stock is not None:
        _require(initial_stock, ["scenario", "product", "application", "origin", "amount_kgC", "age_years"], "Initial stock")
        extra_scenarios = set(initial_stock.scenario) - set(gate.scenario)
        if extra_scenarios:
            raise ValueError(f"Initial stock scenarios lack gate inflows: {sorted(extra_scenarios)}")
    apps, fate_rules = applications.copy(), fates.copy()
    _require(apps, ["product", "application", "share", "lifetime_type", "lifetime_years"], "Applications")
    _require(fate_rules, ["product", "application", "fate", "share"], "End-of-life fates")
    unresolved = gate.loc[gate.production_kg.gt(0) & gate.total_carbon_kgC.isna()].copy()
    if strict_content and not unresolved.empty:
        raise ValueError("Unresolved carbon content prevents stock accounting; resolve it or explicitly select strict_content=False and review coverage.")
    usable = gate.loc[gate.total_carbon_kgC.notna()].copy()
    annual = annualize_factory_gate(usable, interpolation=interpolation)
    if not annual.empty and annual.year.min() < start_year:
        raise ValueError("Gate inflows precede start_year; include their cohorts or explicitly restrict the gate input.")
    scenarios = sorted(set(gate.scenario))
    stock_rows, retirement_rows, treatment_rows, recovery_rows, check_rows, summaries = [], [], [], [], [], []
    serial = 0
    # Rules depend on product/application and only on the context fields that
    # contain explicit values. Cache validated immutable rule records instead
    # of repeatedly filtering DataFrames for every retiring cohort/origin.
    application_fields = [key for key in ("scenario", "year", "product")
                          if key in apps and any(not _wild(value) for value in apps[key])]
    fate_fields = [key for key in CONTEXT + ["product", "application"]
                   if key in fate_rules and any(not _wild(value) for value in fate_rules[key])]
    application_cache, fate_cache, survival_cache = {}, {}, {}

    def application_options(record):
        key = tuple(record[name] for name in application_fields)
        if key not in application_cache:
            selected = _applications_for(apps, record)
            application_cache[key] = [(rule, _lifetime(rule)) for rule in selected.to_dict("records")]
        return application_cache[key]

    def fate_options(record):
        key = tuple(record[name] for name in fate_fields)
        if key not in fate_cache:
            selected = _rules_for(fate_rules, record, CONTEXT + ["product", "application"], "end-of-life")
            if selected.duplicated("fate").any(): raise ValueError("Ambiguous end-of-life fate rules.")
            _number(selected, "share", "End-of-life fates", fraction=True)
            if not np.isclose(selected.share.sum(), 1., atol=1e-8, rtol=0):
                raise ValueError("End-of-life fate shares must sum to one.")
            fate_cache[key] = selected.loc[selected.share.gt(0)].to_dict("records")
        return fate_cache[key]

    def survival(age, spec):
        key = (age, spec)
        if key not in survival_cache:
            survival_cache[key] = _survival(age, spec)
        return survival_cache[key]

    annual_records = {identity: group.to_dict("records")
                      for identity, group in annual.groupby(CONTEXT, sort=False)}
    for scenario in scenarios:
        cohorts, pending, roots = [], [], {}
        scenario_treatment_start = len(treatment_rows)
        initial_total = 0.
        cumulative_new = cumulative_emitted = cumulative_retained = cumulative_exported = cumulative_unresolved = cumulative_secondary = 0.

        def add_cohort(record, amount, entry_year, spec, *, root_id=None, generation=0, initial_age=None):
            nonlocal serial
            if amount <= 0: return
            serial += 1
            ident = f"{scenario}:cohort:{serial}"
            if root_id is None:
                root_id = ident
                roots[root_id] = dict(scenario=scenario, root_cohort_id=root_id,
                    root_entry_year=entry_year, product=record["product"], origin=record["origin"],
                    new_carbon_kgC=amount, initial_stock=initial_age is not None)
            denominator = survival(initial_age, spec) if initial_age is not None else 1.
            if denominator <= 0:
                raise ValueError("Initial stock age is incompatible with its lifetime (no surviving cohort).")
            cohorts.append(dict(scenario=scenario, cohort_id=ident, root_cohort_id=root_id,
                product=record["product"], application=record["application"], origin=record["origin"],
                entry_year=entry_year, amount_kgC=amount / denominator, remaining_kgC=amount,
                lifetime=spec, generation=generation))

        if initial_stock is not None and not initial_stock.empty:
            initial = initial_stock.loc[initial_stock.scenario.eq(scenario)].copy()
            _require(initial, ["scenario", "product", "application", "origin", "amount_kgC", "age_years"], "Initial stock")
            _origins(initial, "Initial stock")
            _number(initial, "amount_kgC", "Initial stock"); _number(initial, "age_years", "Initial stock")
            if (initial.age_years % 1 != 0).any(): raise ValueError("Initial-stock ages must be whole years.")
            for record in initial.to_dict("records"):
                options = application_options(dict(record, year=start_year))
                matches = [(rule, spec) for rule, spec in options if rule["application"] == record["application"]]
                if len(matches) != 1: raise ValueError("Initial stock has no unique application lifetime.")
                age = int(record["age_years"])
                add_cohort(record, record["amount_kgC"], start_year - 1 - age,
                           matches[0][1], initial_age=age)
                initial_total += record["amount_kgC"]

        def emit_treatment(cohort, year, amount, rule, *, stage="direct"):
            nonlocal cumulative_emitted, cumulative_retained, cumulative_exported, cumulative_unresolved, cumulative_secondary
            fate = str(rule.get("fate", "unknown")).strip()
            if fate not in ("mechanical_recycling", "chemical_recycling", "incineration", "landfill", "export", "unknown", "unresolved"):
                raise ValueError(f"Unsupported end-of-life fate: {fate!r}")
            base = {key: cohort[key] for key in ("scenario", "cohort_id", "root_cohort_id", "product", "application", "origin", "generation")}
            row = dict(base, year=year, fate=fate, stage=stage, input_kgC=amount,
                recovered_kgC=0., emitted_CO2_kgC=0., emitted_CH4_kgC=0.,
                retained_residue_kgC=0., exported_kgC=0., unresolved_kgC=0., secondary_pool_kgC=0.)
            if fate in ("mechanical_recycling", "chemical_recycling"):
                raw_yield = rule.get("recycling_yield", np.nan)
                yield_value = float(raw_yield)
                if not np.isfinite(yield_value) or not 0 <= yield_value <= 1:
                    raise ValueError("Recycling requires an explicit yield between zero and one.")
                recovered = amount * yield_value
                row["recovered_kgC"] = recovered
                target_product = rule.get("recovery_product", cohort["product"])
                if _wild(target_product): target_product = cohort["product"]
                target_app = rule.get("recovery_application", cohort["application"])
                if _wild(target_app): target_app = cohort["application"]
                transfer = dict(base, year=year, reentry_year=year+1,
                    recovery_product=target_product, recovery_application=target_app, amount_kgC=recovered,
                    recycling_mode=recycling_mode)
                if recovered > 0:
                    if recycling_mode == "loop":
                        pending.append(transfer)
                    else:
                        transfer["reentry_year"] = np.nan
                        row["secondary_pool_kgC"] = recovered
                        cumulative_secondary += recovered
                    recovery_rows.append(transfer)
                rejected = amount - recovered
                if rejected > 0:
                    reject = rule.get("reject_fate", "unknown")
                    if _wild(reject): reject = "unknown"
                    if reject in ("mechanical_recycling", "chemical_recycling"):
                        raise ValueError("Recycling rejects need a terminal fate, not another recursive recycling rule.")
                    emit_treatment(cohort, year, rejected, dict(fate=reject,
                        oxidation_fraction=rule.get("reject_oxidation_fraction", np.nan),
                        methane_carbon_fraction=rule.get("reject_methane_carbon_fraction", 0.)), stage="recycling_reject")
            elif fate in ("incineration", "landfill"):
                oxidation = float(rule.get("oxidation_fraction", np.nan))
                methane = rule.get("methane_carbon_fraction", 0.)
                methane = 0. if pd.isna(methane) else float(methane)
                if not np.isfinite(oxidation) or not np.isfinite(methane) or min(oxidation, methane) < 0 or oxidation + methane > 1 + 1e-10:
                    raise ValueError("Treatment requires explicit C-to-CO2 oxidation and C-to-CH4 fractions summing to at most one.")
                row["emitted_CO2_kgC"] = amount * oxidation
                row["emitted_CH4_kgC"] = amount * methane
                row["retained_residue_kgC"] = amount * max(0., 1 - oxidation - methane)
                cumulative_emitted += amount * (oxidation + methane)
                cumulative_retained += row["retained_residue_kgC"]
            elif fate == "export":
                row["exported_kgC"] = amount; cumulative_exported += amount
            else:
                row["unresolved_kgC"] = amount; cumulative_unresolved += amount
            row["CO2_emitted_kg"] = row["emitted_CO2_kgC"] * CO2_PER_C
            row["CH4_emitted_kg"] = row["emitted_CH4_kgC"] * CH4_PER_C
            row["atmospheric_uptake_added_kg"] = 0.
            treatment_rows.append(row)

        for year in range(start_year, end_year + 1):
            opening = sum(c["remaining_kgC"] for c in cohorts)
            new_inflow = reentered = retired = 0.
            due = [item for item in pending if item["reentry_year"] == year]
            pending = [item for item in pending if item["reentry_year"] != year]
            # Weibull retirements from multiple generations may reach the same
            # destination in the same year. Aggregate before creating cohorts
            # so recovery loops do not create exponentially many branches.
            due_grouped = {}
            for item in due:
                key = tuple(item[name] for name in ("root_cohort_id", "origin", "recovery_product", "recovery_application"))
                if key not in due_grouped:
                    due_grouped[key] = dict(item)
                else:
                    due_grouped[key]["amount_kgC"] += item["amount_kgC"]
                    due_grouped[key]["generation"] = max(due_grouped[key]["generation"], item["generation"])
            for item in due_grouped.values():
                record = dict(scenario=scenario, year=year, product=item["recovery_product"],
                              application=item["recovery_application"], origin=item["origin"])
                options = application_options(record)
                selected = [(rule, spec) for rule, spec in options if rule["application"] == record["application"]]
                if len(selected) != 1: raise ValueError("Recovered carbon has no unique destination application/lifetime.")
                add_cohort(record, item["amount_kgC"], year, selected[0][1],
                           root_id=item["root_cohort_id"], generation=item["generation"]+1)
                reentered += item["amount_kgC"]
            for record in annual_records.get((scenario, year), ()):
                options = application_options(record)
                for application, spec in options:
                    for origin in ORIGINS:
                        amount = float(record[f"carbon_{origin}_kgC"]) * application["share"]
                        add_cohort(dict(record, application=application["application"], origin=origin),
                                   amount, year, spec)
                        new_inflow += amount
            cumulative_new += new_inflow
            for cohort in cohorts:
                age = year - cohort["entry_year"]
                if age <= 0 or cohort["remaining_kgC"] <= 0: continue
                probability = max(0., survival(age-1, cohort["lifetime"]) - survival(age, cohort["lifetime"]))
                amount = min(cohort["remaining_kgC"], cohort["amount_kgC"] * probability)
                if amount <= 0: continue
                cohort["remaining_kgC"] -= amount; retired += amount
                row = {key: cohort[key] for key in ("scenario", "cohort_id", "root_cohort_id", "product", "application", "origin", "generation", "entry_year")}
                retirement_rows.append(dict(row, year=year, amount_kgC=amount))
                for rule in fate_options(dict(row, year=year)):
                    emit_treatment(cohort, year, amount * rule["share"], rule)
            closing = sum(c["remaining_kgC"] for c in cohorts)
            grouped = {}
            for cohort in cohorts:
                if cohort["remaining_kgC"] > 0:
                    key = tuple(cohort[name] for name in ("product", "application", "origin"))
                    grouped[key] = grouped.get(key, 0.) + cohort["remaining_kgC"]
            for (product, application, origin), amount in grouped.items():
                stock_rows.append(dict(scenario=scenario, year=year, product=product,
                    application=application, origin=origin, stock_kgC=amount))
            pending_total = sum(item["amount_kgC"] for item in pending)
            residual = initial_total + cumulative_new - (closing + pending_total + cumulative_emitted + cumulative_retained + cumulative_exported + cumulative_unresolved + cumulative_secondary)
            flow_residual = opening + new_inflow + reentered - retired - closing
            tolerance = 1e-7 + 1e-8 * max(1., initial_total+cumulative_new)
            if abs(residual) > tolerance or abs(flow_residual) > tolerance:
                raise ValueError(f"Cohort carbon conservation failed in {scenario}/{year}.")
            check_rows.append(dict(scenario=scenario, year=year, opening_stock_kgC=opening,
                new_inflow_kgC=new_inflow, recycled_reentry_kgC=reentered, retired_kgC=retired,
                closing_stock_kgC=closing, pending_recycling_kgC=pending_total,
                cumulative_new_kgC=cumulative_new, initial_stock_kgC=initial_total,
                cumulative_emitted_kgC=cumulative_emitted, cumulative_retained_residue_kgC=cumulative_retained,
                cumulative_exported_kgC=cumulative_exported, cumulative_unresolved_kgC=cumulative_unresolved,
                cumulative_secondary_pool_kgC=cumulative_secondary, inflow_basis=inflow_basis,
                recycling_mode=recycling_mode, demand_cleared=False,
                stock_flow_residual_kgC=flow_residual, whole_system_residual_kgC=residual,
                status="balanced", initial_stock_assumption="supplied" if initial_stock is not None else "explicit_zero_new_cohorts_only"))
        # One stable-order pass preserves the previous floating-point summation
        # order within each root, without scanning every treatment per root.
        treatment_fields = ("CO2_emitted_kg", "CH4_emitted_kg", "retained_residue_kgC",
                            "exported_kgC", "unresolved_kgC", "secondary_pool_kgC")
        by_root = {}
        for row in treatment_rows[scenario_treatment_start:]:
            root_id = row["root_cohort_id"]
            by_root.setdefault(root_id, []).append(row)
        remaining_by_root, waiting_by_root = {}, {}
        for cohort in cohorts:
            root_id = cohort["root_cohort_id"]
            remaining_by_root.setdefault(root_id, []).append(cohort["remaining_kgC"])
        for item in pending:
            root_id = item["root_cohort_id"]
            waiting_by_root.setdefault(root_id, []).append(item["amount_kgC"])
        for root_id, root in roots.items():
            linked = by_root.get(root_id, ())
            # Retain built-in sum and original order, including its compensated
            # float summation on newer Python versions.
            totals = {field: sum(row[field] for row in linked) for field in treatment_fields}
            remaining = sum(remaining_by_root.get(root_id, ()))
            waiting = sum(waiting_by_root.get(root_id, ()))
            summaries.append(dict(root, final_year=end_year, in_use_kgC=remaining,
                pending_recycling_kgC=waiting,
                emitted_CO2_kg=totals["CO2_emitted_kg"],
                emitted_CH4_kg=totals["CH4_emitted_kg"],
                retained_residue_kgC=totals["retained_residue_kgC"],
                exported_kgC=totals["exported_kgC"],
                unresolved_kgC=totals["unresolved_kgC"],
                secondary_pool_kgC=totals["secondary_pool_kgC"],
                atmospheric_uptake_added_kg=0.))
    coverage = unresolved[GATE_KEYS + ["production_kg", "carbon_content_status"]].copy() if not unresolved.empty else pd.DataFrame(columns=GATE_KEYS + ["production_kg", "carbon_content_status"])
    return dict(stocks=pd.DataFrame(stock_rows), retirements=pd.DataFrame(retirement_rows),
        treatments=pd.DataFrame(treatment_rows), recycling_transfers=pd.DataFrame(recovery_rows),
        cohort_summary=pd.DataFrame(summaries), checks=pd.DataFrame(check_rows), coverage=coverage,
        annual_inflows=annual)


def audit_co2_destinations(captured):
    """Separate captured/compressed CO2, reuse, venting and geological storage.

    Fractions geological_fraction/reuse_fraction/vent_fraction/unknown_fraction
    must sum to one. storage_leakage_fraction applies to geological delivery.
    This physical ledger never subtracts captured CO2 from emitted flue gas.
    """
    frame = captured.copy()
    fields = ["geological_fraction", "reuse_fraction", "vent_fraction", "unknown_fraction"]
    _require(frame, CONTEXT + ["process_id", "origin", "captured_CO2_kg"] + fields, "Captured CO2")
    _years(frame, "Captured CO2"); _origins(frame, "Captured CO2")
    _number(frame, "captured_CO2_kg", "Captured CO2")
    for field in fields: _number(frame, field, "Captured CO2", fraction=True)
    if not np.allclose(frame[fields].sum(axis=1), 1., atol=1e-8, rtol=0):
        raise ValueError("Captured CO2 destination fractions must sum to one.")
    if "storage_leakage_fraction" not in frame:
        frame["storage_leakage_fraction"] = 0.
        frame["storage_leakage_assumption"] = "zero screening assumption; not verified"
    else:
        frame["storage_leakage_assumption"] = "explicit supplied fraction; verify supporting evidence"
    _number(frame, "storage_leakage_fraction", "Captured CO2", fraction=True)
    frame["geological_delivered_CO2_kg"] = frame.captured_CO2_kg * frame.geological_fraction
    frame["storage_leakage_CO2_kg"] = frame.geological_delivered_CO2_kg * frame.storage_leakage_fraction
    frame["geological_retained_CO2_kg"] = frame.geological_delivered_CO2_kg - frame.storage_leakage_CO2_kg
    frame["reused_CO2_kg"] = frame.captured_CO2_kg * frame.reuse_fraction
    frame["vented_CO2_kg"] = frame.captured_CO2_kg * frame.vent_fraction
    frame["unresolved_CO2_kg"] = frame.captured_CO2_kg * frame.unknown_fraction
    frame["additional_LCIA_credit_kg"] = 0.
    frame["interpretation"] = "Physical destinations only; geological delivery/storage is provisional unless evidenced"
    return frame


def inventory_carbon_emission_audit(datasets):
    """Read-only review of fixed carbon emission labels and capture services.

    These are candidates for investigation, not instructions to relabel all
    fossil emissions. Feedstock-derived carbon and fuel combustion need
    separate physical evidence before any inventory correction.
    """
    rows = []
    for dataset in datasets:
        identity = {"activity": dataset.get("name"), "reference_product": dataset.get("reference product"),
                    "location": dataset.get("location"), "unit": dataset.get("unit")}
        for index, exchange in enumerate(dataset.get("exchanges", [])):
            name = str(exchange.get("name", ""))
            lower = name.lower()
            carbon_air = exchange.get("type") == "biosphere" and any(
                gas in lower for gas in ("carbon dioxide", "carbon monoxide", "methane"))
            capture_service = exchange.get("type") == "technosphere" and (
                "carbon dioxide" in lower and any(word in lower for word in ("capture", "compression", "storage", "transport")))
            if not carbon_air and not capture_service: continue
            if "in air" in lower and "non-fossil" not in lower:
                issue = "uptake_already_in_inventory_do_not_add_again"
            elif "fossil" in lower and "non-fossil" not in lower:
                issue = "fixed_fossil_label_check_feedstock_vs_fuel_origin"
            elif capture_service:
                issue = "check_capture_vs_compression_transport_storage_boundary"
            else:
                issue = "verify_origin_compartment_and_carbon_balance"
            rows.append(dict(identity, exchange_index=index, exchange_name=name,
                amount=exchange.get("amount"), exchange_unit=exchange.get("unit"),
                categories=str(exchange.get("categories", "")), review_issue=issue,
                action_applied=False))
    return pd.DataFrame(rows)


def write_carbon_outputs(tables, output_root, *, source_paths=(), assumptions=None, run_name="carbon"):
    """Write only new companion artifacts and a source-hashed run manifest.

    A unique directory prevents replacing an earlier result or baseline file.
    Source hashes establish identity, not scientific validity or LCA pairing.
    """
    root = Path(output_root).resolve()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    safe_name = "".join(c if c.isalnum() or c in "_-" else "_" for c in str(run_name))
    directory = root / (safe_name + "_" + stamp + "_" + uuid4().hex[:8])
    directory.mkdir(parents=True, exist_ok=False)
    sources = []
    for source in source_paths:
        path = Path(source).resolve()
        if not path.is_file(): raise FileNotFoundError(path)
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""): digest.update(block)
        sources.append(dict(path=str(path), sha256=digest.hexdigest(), size_bytes=path.stat().st_size))
    outputs = {}
    for name, table in tables.items():
        if not isinstance(table, pd.DataFrame): continue
        if not str(name).replace("_", "").isalnum(): raise ValueError(f"Unsafe companion table name: {name!r}")
        path = directory / (str(name) + ".csv")
        table.to_csv(path, index=False)
        outputs[str(name)] = str(path)
    manifest = dict(created_utc=datetime.now(timezone.utc).isoformat(), workflow="additional_physical_carbon_accounts",
        sources=sources, assumptions=assumptions or {}, tables=outputs,
        corrections_applied=False, LCIA_recalculated=False,
        interpretation=["Physical carbon amounts are not additional negative biosphere exchanges or storage credits.",
            "Product retention, atmospheric release and geological storage are reported separately.",
            "Calendar-year results and cohort-lifetime totals are separate attribution views; do not add them together."])
    manifest_path = directory / "carbon_run_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    return dict(output_dir=str(directory), manifest=str(manifest_path), tables=outputs)


# Read-only ITOM and inventory source adapters.
"""Read-only ITOM adapters for the optional physical carbon workflows.

This fragment is merged into carbon_accounting.py by the parent implementation.
All source paths are read-only. Inferred carbon mixing never changes inventories.
"""

_ITOM_CHEMICAL_FORMULAS = {
    'benzene': 'C6H6', 'butadiene': 'C4H6', 'ethylene': 'C2H4',
    'para_xylene': 'C8H10', 'propylene': 'C3H6', 'toluene': 'C7H8',
    'raw_benzene': 'C6H6', 'raw_toluene': 'C7H8', 'raw_para_xylene': 'C8H10',
    'cracker_butadiene': 'C4H6', 'bisphenol_A': 'C15H16O2',
    'cyclohexane': 'C6H12', 'ethylbenzene': 'C8H10',
    'ethylene_dichloride': 'C2H4Cl2', 'ethylene_glycol': 'C2H6O2',
    'ethylene_oxide': 'C2H4O', 'formaldehyde': 'CH2O',
    'isopropanol': 'C3H8O', 'methane': 'CH4', 'methanol': 'CH4O',
    'MMA': 'C5H8O2', 'propylene_oxide': 'C3H6O', 'PTA': 'C8H6O4',
    'styrene_monomer': 'C8H8', 'succinic_acid': 'C4H6O4',
    'syngas_CO': 'CO', 'syngas_CO2': 'CO2', 'vinyl_chloride_monomer': 'C2H3Cl',
    'ethane': 'C2H6', 'propane': 'C3H8', 'MDI': 'C15H10N2O2',
    'nylon_6_6': 'C12H22N2O2', 'PBR': 'C4H6', 'PBS': 'C8H12O4',
    'PET': 'C10H8O4', 'PLA': 'C3H4O2', 'PMMA': 'C5H8O2',
    'polypropylene': 'C3H6', 'polyvinyl_chloride': 'C2H3Cl',
    'adipic_acid': 'C6H10O4', 'flue_gas_CO2': 'CO2', 'stored_CO2': 'CO2',
    'unavoidable_CO2': 'CO2', 'uncompressed_CO2': 'CO2',
}
_ITOM_ZERO_C = {'electricity', 'HT_heat', 'steam', 'heat_sales', 'hydrogen',
                'cracker_H2', 'syngas_H2', 'chlorine'}
_ITOM_DUMMY_C = {'CO2_allowance', 'avoided_incineration_CO2'}
_ITOM_CO2_STREAMS = {'flue_gas_CO2', 'stored_CO2', 'unavoidable_CO2', 'uncompressed_CO2'}
_ITOM_FINAL_PRODUCTS = {'MDI', 'nylon_6_6', 'PBR', 'PBS', 'PET', 'PLA', 'PMMA',
                        'polyols', 'polypropylene', 'polyvinyl_chloride'}


def _itom_formula_fraction(formula):
    import re
    atoms = {'C': 12.011, 'H': 1.008, 'O': 15.999, 'N': 14.007, 'Cl': 35.45}
    tokens = re.findall(r'([A-Z][a-z]?)(\d*)', formula)
    if ''.join(a + n for a, n in tokens) != formula or any(a not in atoms for a, _ in tokens):
        raise ValueError(f'Unsupported molecular/repeat-unit formula: {formula!r}')
    counts = {a: 0 for a in atoms}
    for atom, number in tokens:
        counts[atom] += int(number or 1)
    return counts['C'] * atoms['C'] / sum(counts[a] * atoms[a] for a in atoms)


def default_itom_carbon_content(products=None):
    """Ideal pure-compound contents; mixtures remain unresolved, never zero."""
    import numpy as np
    import pandas as pd
    rows = []
    names = sorted(set(() if products is None else products) | set(_ITOM_CHEMICAL_FORMULAS) | _ITOM_ZERO_C)
    for product in names:
        formula = _ITOM_CHEMICAL_FORMULAS.get(product, '')
        fraction = _itom_formula_fraction(formula) if formula else (0. if product in _ITOM_ZERO_C else np.nan)
        proxy = product.startswith('raw_') or product == 'cracker_butadiene'
        rows.append(dict(product=product, formula=formula,
            carbon_fraction_kgC_per_kg=fraction,
            material_class=('intermediate' if product in {'MDI', 'polyols', 'adipic_acid'}
                            else 'resin' if product in _ITOM_FINAL_PRODUCTS else 'material'),
            status=('ITOM_target_equivalent_proxy' if proxy else 'ideal_formula' if formula
                    else 'carbon_free' if fraction == 0 else 'unresolved'),
            source='Stoichiometric molecular/repeat-unit formula; standard atomic masses C=12.011 H=1.008 O=15.999 N=14.007 Cl=35.45',
            notes=('Raw stream treated as target-equivalent only, consistent with ITOM 1:1 extraction; not measured purity.'
                   if proxy else 'Neat material only; formulations/additives not included.' if formula
                   else 'Composition required for biomass, mixed waste, syngas mixtures, oils, lignin and polyols.' if product not in _ITOM_ZERO_C else '')))
    return pd.DataFrame(rows)


def default_itom_external_origin_rules():
    """Boundary-source rules; unresolved terminal/trade origins stay unknown."""
    import pandas as pd
    rules = [
        ('atmospheric_crude_oil_distillation_default', '*', 'fossil', 'ITOM refinery boundary source'),
        ('FCC_propylene_dedicated_default', '*', 'fossil', 'ITOM dedicated FCC boundary source'),
        ('naphtha_terminal_BAT', 'naphtha', 'fossil', 'ITOM fossil naphtha product distinction'),
        ('DAC_methanol_terminal_BATplus', 'methanol', 'dac', 'ITOM explicit DAC methanol terminal'),
        ('energy_crops_production', '*', 'biogenic', 'ITOM biomass boundary source; carbon concentration unresolved'),
        ('agricultural_residue_production', '*', 'biogenic', 'ITOM biomass boundary source; carbon concentration unresolved'),
        ('lignocellulosic_residue_production', '*', 'biogenic', 'ITOM biomass boundary source; carbon concentration unresolved'),
    ]
    return pd.DataFrame([dict(technology=t, product=p, origin=o, source=s,
        notes='Only physical boundary carbon; no extra LCIA uptake/credit.') for t, p, o, s in rules])


def read_itom_carbon_inputs(workbooks, content=None, source_rules=None, *, period_years=5):
    """Read physical ITOM volumes, build carbon network, expose unresolved gaps.

    workbooks maps scenario labels to source XLSX paths. Volumes in these exports
    represent five-year periods, not annual flows. Material units are kt/period;
    electricity/steam/HT_heat are TJ/period and have no material carbon here.
    Carbon-free and economic bookkeeping flows never enter the carbon network.
    Missing concentrations remain NaN and create coverage diagnostics. To make
    lineage solvable, shortages of quantified carbon receive an explicit unknown
    boundary source; these are gap diagnostics, not measured imported carbon.
    """
    from pathlib import Path
    import numpy as np
    import pandas as pd
    if not isinstance(workbooks, dict) or not workbooks:
        raise ValueError('workbooks must map explicit scenario labels to XLSX paths.')
    if not np.isfinite(period_years) or period_years <= 0:
        raise ValueError('period_years must be a positive finite period duration.')
    diagnostics, normalized, product_types = [], [], {}
    for scenario, workbook in workbooks.items():
        path = Path(workbook).resolve()
        with pd.ExcelFile(path) as book:
            if 'products' in book.sheet_names:
                types = pd.read_excel(book, sheet_name='products')
                if {'PRODUCT', 'type'}.issubset(types):
                    product_types.update(dict(zip(types.PRODUCT.astype(str), types['type'].astype(str))))
            for sheet, direction in [('production_volume', 'production'), ('use_volume', 'use')]:
                if sheet not in book.sheet_names:
                    raise ValueError(f'{path.name} lacks required {sheet!r} sheet.')
                frame = pd.read_excel(book, sheet_name=sheet)
                value_col = 'LocalProductionByMode' if direction == 'production' else 'LocalUseByMode'
                needed = {'LOCATION', 'TECHNOLOGY', 'PRODUCT', 'MODE_OF_OPERATION', 'YEAR', value_col}
                if needed - set(frame):
                    raise ValueError(f'{path.name}/{sheet}: missing {sorted(needed-set(frame))}')
                frame = frame.loc[frame.LOCATION.astype(str).eq('Rotterdam')].copy()
                keys = ['LOCATION', 'TECHNOLOGY', 'PRODUCT', 'MODE_OF_OPERATION', 'YEAR']
                if frame.duplicated(keys).any():
                    raise ValueError(f'{path.name}/{sheet}: duplicate physical flow coordinates.')
                for row in frame.to_dict('records'):
                    amount = float(row[value_col]); year_float = float(row['YEAR'])
                    if not np.isfinite(amount) or amount < 0 or not np.isfinite(year_float) or year_float % 1:
                        raise ValueError(f'{path.name}/{sheet}: negative/non-finite flow or non-integer year.')
                    mode = str(row['MODE_OF_OPERATION']).removesuffix('.0')
                    normalized.append(dict(scenario=str(scenario), year=int(year_float),
                        technology=str(row['TECHNOLOGY']), product=str(row['PRODUCT']), mode=mode,
                        direction=direction, raw_amount=amount, raw_unit='kt/period' if row['PRODUCT'] not in {'electricity','steam','HT_heat'} else 'TJ/period',
                        period_years=float(period_years),
                        amount_kg_per_year=(amount*1e6/period_years if row['PRODUCT'] not in {'electricity','steam','HT_heat'} else np.nan),
                        amount_MJ_per_year=(amount*1e6/period_years if row['PRODUCT'] in {'electricity','steam','HT_heat'} else np.nan),
                        source_path=str(path), source_sheet=sheet))
            for sheet, direction in [('transport_to_PoR', 'import'), ('transport_from_PoR', 'export')]:
                if sheet not in book.sheet_names:
                    continue
                frame = pd.read_excel(book, sheet_name=sheet)
                needed = {'LOCATION_1', 'LOCATION_2', 'PRODUCT', 'YEAR', 'Transport'}
                if needed - set(frame):
                    raise ValueError(f'{path.name}/{sheet}: missing {sorted(needed-set(frame))}')
                for row in frame.to_dict('records'):
                    if (direction == 'import' and str(row['LOCATION_2']) != 'Rotterdam') or (
                        direction == 'export' and str(row['LOCATION_1']) != 'Rotterdam'):
                        continue
                    amount = float(row['Transport']); year_float=float(row['YEAR'])
                    if not np.isfinite(amount) or amount < 0 or not np.isfinite(year_float) or year_float % 1:
                        raise ValueError(f'{path.name}/{sheet}: invalid transport quantity/year.')
                    normalized.append(dict(scenario=str(scenario), year=int(year_float),
                        technology='EU_trade_import' if direction=='import' else 'EU_trade_export',
                        product=str(row['PRODUCT']),mode='trade', direction=direction,
                        raw_amount=amount,raw_unit='kt/period' if row['PRODUCT'] not in {'electricity','steam','HT_heat'} else 'TJ/period',
                        period_years=float(period_years),
                        amount_kg_per_year=(amount*1e6/period_years if row['PRODUCT'] not in {'electricity','steam','HT_heat'} else np.nan),
                        amount_MJ_per_year=(amount*1e6/period_years if row['PRODUCT'] in {'electricity','steam','HT_heat'} else np.nan),
                        source_path=str(path),source_sheet=sheet))
    volumes=pd.DataFrame(normalized)
    if volumes.empty:
        raise ValueError('No Rotterdam volume records found in source workbooks.')
    catalog=default_itom_carbon_content(volumes['product']) if content is None else content.copy()
    required={'product','carbon_fraction_kgC_per_kg'}
    if required-set(catalog) or catalog['product'].duplicated().any():
        raise ValueError('Carbon content requires unique product and carbon_fraction_kgC_per_kg columns.')
    fractions=pd.to_numeric(catalog['carbon_fraction_kgC_per_kg'],errors='raise')
    if ((fractions.dropna()<0)|(fractions.dropna()>1)).any():
        raise ValueError('Carbon mass fractions must lie in [0,1] or be unresolved (NaN).')
    catalog['carbon_fraction_kgC_per_kg']=fractions
    rules=default_itom_external_origin_rules() if source_rules is None else source_rules.copy()
    if {'technology','product','origin'}-set(rules):
        raise ValueError('External source rules require technology/product/origin.')
    if not rules['origin'].isin(['fossil','biogenic','dac','unknown']).all():
        raise ValueError('Invalid external carbon origin.')
    volumes=volumes.merge(catalog[['product','carbon_fraction_kgC_per_kg']],on='product',how='left',validate='many_to_one')
    volumes['amount_kgC']=volumes.amount_kg_per_year*volumes.carbon_fraction_kgC_per_kg
    volumes.loc[volumes.carbon_fraction_kgC_per_kg.eq(0),'amount_kgC']=0.
    volumes['process_id']='technology::'+volumes.technology+'::mode'+volumes['mode']
    volumes['market_id']='market::'+volumes['product']
    final_mask=volumes['product'].isin(_ITOM_FINAL_PRODUCTS)|volumes['product'].map(product_types).eq('outputs')
    production=volumes.loc[volumes.direction.eq('production')&final_mask,
        ['scenario','year','product','technology','mode','process_id','amount_kg_per_year']].rename(columns={'amount_kg_per_year':'production_kg'})
    production['route']=production.technology+'|mode'+production['mode']
    production['region']='NL'
    transfers=[]; external=[]; raw_flows=[]
    for row in volumes.to_dict('records'):
        context={k:row[k] for k in ['scenario','year']}
        if row['product'] in _ITOM_DUMMY_C:
            if row['raw_amount']:
                diagnostics.append(dict(**context,process_id=row['process_id'],product=row['product'],
                    status='excluded_economic_bookkeeping',amount_kgC=np.nan,notes='ITOM INFO: not a physical emission or removal.'))
            continue
        if row['amount_kg_per_year']==0:
            continue
        if pd.isna(row['amount_kgC']):
            diagnostics.append(dict(**context,process_id=row['process_id'],product=row['product'],
                status='unresolved_carbon_content',amount_kgC=np.nan,
                notes=f"{row['direction']}: {row['amount_kg_per_year']:g} kg/year; carbon concentration required."))
            continue
        amount=float(row['amount_kgC'])
        if amount==0:
            continue
        direction=row['direction']; process=row['process_id']; market=row['market_id']
        if direction=='production':
            transfers.append(dict(**context,supplier_id=process,consumer_id=market,amount_kgC=amount))
            raw_flows += [dict(**context,process_id=process,origin_node_id=process,direction='out',
                stream_type='captured_CO2' if row['product']=='stored_CO2' else 'physical_output',product=row['product'],amount_kgC=amount),
                dict(**context,process_id=market,origin_node_id=process,direction='in',stream_type='physical_output',product=row['product'],amount_kgC=amount)]
        elif direction=='use':
            transfers.append(dict(**context,supplier_id=market,consumer_id=process,amount_kgC=amount))
            raw_flows += [dict(**context,process_id=process,origin_node_id=market,direction='in',stream_type='material_input',product=row['product'],amount_kgC=amount),
                dict(**context,process_id=market,origin_node_id=market,direction='out',stream_type='material_input',product=row['product'],amount_kgC=amount)]
        elif direction=='import':
            external.append(dict(**context,process_id=market,origin='unknown',amount_kgC=amount))
            raw_flows.append(dict(**context,process_id=market,origin_node_id=market,origin='unknown',direction='in',
                stream_type='explicit_EU_import',product=row['product'],amount_kgC=amount))
        elif direction=='export':
            raw_flows.append(dict(**context,process_id=market,origin_node_id=market,direction='out',
                stream_type='explicit_EU_export',product=row['product'],amount_kgC=amount))
        if row['technology']=='para_xylene_terminal_BAT' and row['product']=='raw_para_xylene' and direction=='production':
            diagnostics.append(dict(**context,process_id=process,product=row['product'],amount_kgC=amount,
                status='terminal_purity_response_data_discrepancy',notes='Raw workbook label retained for physical audit; active LCA routes terminal supply as purified based on ITOM clarification.'))
    transfers=pd.DataFrame(transfers,columns=['scenario','year','supplier_id','consumer_id','amount_kgC'])
    external=pd.DataFrame(external,columns=['scenario','year','process_id','origin','amount_kgC'])
    raw_flows=pd.DataFrame(raw_flows)
    raw_physical_flows=raw_flows.copy()
    # Feedstock shortages are explicit unknown sources, never automatic biogenic
    # content. This permits tracing known sources while exposing missing data.
    for (scenario,year), group in transfers.groupby(['scenario','year'],sort=False):
        incoming=group.groupby('consumer_id').amount_kgC.sum()
        outgoing=raw_flows.loc[raw_flows.scenario.eq(scenario)&raw_flows.year.eq(year)&raw_flows.direction.eq('out')].groupby('process_id').amount_kgC.sum()
        ext=external.loc[external.scenario.eq(scenario)&external.year.eq(year)].groupby('process_id').amount_kgC.sum()
        for node in set(incoming.index)|set(outgoing.index):
            deficit=float(outgoing.get(node,0)-incoming.get(node,0)-ext.get(node,0))
            if deficit<=max(1e-6,abs(float(outgoing.get(node,0)))*1e-10):
                continue
            origin='unknown'; status='unresolved_balance_input'
            if node.startswith('technology::'):
                technology=node.split('::')[1]
                candidates=rules.loc[rules.technology.eq(technology)]
                products=set(volumes.loc[volumes.scenario.eq(scenario)&volumes.year.eq(year)&volumes.process_id.eq(node)&volumes.direction.eq('production'),'product'])
                candidates=candidates.loc[candidates['product'].eq('*')|candidates['product'].isin(products)]
                if candidates.origin.nunique()==1 and len(candidates):
                    origin=str(candidates.origin.iloc[0]);status='declared_boundary_source'
            extra=dict(scenario=scenario,year=year,process_id=node,origin=origin,amount_kgC=deficit)
            external=pd.DataFrame([extra]) if external.empty else pd.concat([external,pd.DataFrame([extra])],ignore_index=True)
            flow=pd.DataFrame([dict(extra,origin_node_id=node,direction='in',stream_type=status,product='unresolved_or_boundary_carbon')])
            raw_flows=pd.concat([raw_flows,flow],ignore_index=True)
            if status=='declared_boundary_source':
                raw_physical_flows=pd.concat([raw_physical_flows,flow],ignore_index=True)
            diagnostics.append(dict(scenario=scenario,year=year,process_id=node,product='',amount_kgC=deficit,status=status,
                notes='Boundary source specified explicitly.' if status=='declared_boundary_source' else 'Quantified outputs/uses exceed quantified inputs; unknown carbon added for lineage only, not measured import. Raw physical balance is retained before this padding.'))
    co2_streams=volumes.loc[volumes.direction.eq('production')&volumes['product'].isin(_ITOM_CO2_STREAMS),
        ['scenario','year','technology','mode','process_id','product','amount_kg_per_year']].copy()
    co2_streams=co2_streams.rename(columns={'amount_kg_per_year':'amount_kgCO2'})
    return dict(production=production.reset_index(drop=True),external=external,transfers=transfers,
        flows=raw_physical_flows,lineage_flows=raw_flows,content=catalog,diagnostics=pd.DataFrame(diagnostics),
        coverage=pd.DataFrame([d for d in diagnostics if d['status']=='unresolved_carbon_content']),
        routes=production.copy(),volumes=volumes,co2_streams=co2_streams,
        assumptions={'volume_period_years':float(period_years),'material_volume_unit':'kt/period',
            'carbon_mixing':'Proportional mixing of quantified carbon at each product market and technology; unquantified mixtures remain explicit gaps.',
            'economic_rows_excluded':sorted(_ITOM_DUMMY_C),'ONSITE_transport':'Not added: internal movements are already in production/use flows.'})


def materialize_itom_carbon_flows(raw_flows, origins):
    """Split quantified physical flows by source-node origin; preserve unknowns."""
    import pandas as pd
    records=[]
    shares={(str(s),int(y),str(p)):g.set_index('origin')['share'].to_dict()
            for (s,y,p),g in origins.groupby(['scenario','year','process_id'],sort=False)}
    for row in raw_flows.to_dict('records'):
        origins_for_row=({row['origin']:1.} if pd.notna(row.get('origin')) else
            shares.get((str(row['scenario']),int(row['year']),str(row['origin_node_id'])),{'unknown':1.}))
        for origin,share in origins_for_row.items():
            if share:
                records.append({k:row[k] for k in ['scenario','year','process_id','direction','stream_type','product']}|
                    {'origin':origin,'amount_kgC':float(row['amount_kgC'])*float(share)})
    return pd.DataFrame(records,columns=['scenario','year','process_id','direction','stream_type','product','origin','amount_kgC'])


def audit_carbon_inventory_workbook(path):
    """Find potential carbon-accounting corrections without editing the XLSX."""
    from pathlib import Path
    import openpyxl
    import pandas as pd
    workbook=Path(path).resolve(); activities=[]; current=None; headers=None
    book=openpyxl.load_workbook(workbook,read_only=True,data_only=True)
    try:
        for sheet in book:
            for number,row in enumerate(sheet.values,1):
                if row[0]=='Activity':
                    current={'name':str(row[1]),'sheet':sheet.title,'row':number,'exchanges':[]};activities.append(current);headers=None
                elif current and row[0] in {'reference product','unit','location','comment'}:
                    current[row[0]]=row[1]
                elif current and row[0]=='name' and row[1]=='amount':
                    headers=[str(v) if v is not None else '' for v in row]
                elif current and headers and row[0] is not None:
                    exchange={k:v for k,v in zip(headers,row) if k};exchange['row']=number;current['exchanges'].append(exchange)
    finally:
        book.close()
    findings=[]
    def finding(activity,priority,status,issue,evidence,suggestion,row=None):
        findings.append(dict(source_path=str(workbook),sheet=activity['sheet'],row=row or activity['row'],
            activity=activity['name'],priority=priority,status=status,issue=issue,evidence=evidence,
            proposed_correction=suggestion,applied=False))
    for activity in activities:
        exchanges=activity['exchanges']; name=activity['name'].lower()
        methanol=[e for e in exchanges if e.get('type')=='technosphere' and 'methanol' in str(e.get('name','')).lower()]
        fossil=[e for e in exchanges if e.get('type')=='biosphere' and e.get('name')=='Carbon dioxide, fossil' and float(e.get('amount') or 0)>0]
        if methanol and fossil:
            for e in fossil:
                finding(activity,'high','review_required','fixed_fossil_label_with_methanol_feed',
                    f"Fixed fossil CO2={e['amount']} kg per reference unit; methanol inputs can have scenario-dependent origin.",
                    'Separate feedstock-derived and fuel-combustion CO2 before changing labels; inspect actual transformed suppliers.',e['row'])
        if 'lignin' in name and not any(e.get('type')=='technosphere' and 'lignin' in str(e.get('name','')).lower() for e in exchanges):
            finding(activity,'high','review_required','lignin_recipe_without_lignin_exchange',
                str(activity.get('comment',''))[:350],
                'Reconcile stated lignin quantity and physical carbon input with the inventory; no uptake should be invented in postprocessing.')
        for e in exchanges:
            if e.get('type')=='biosphere' and e.get('name')=='Carbon dioxide, in air' and float(e.get('amount') or 0)>0:
                finding(activity,'information','already_represented','atmospheric_uptake_already_in_inventory',
                    f"Atmospheric resource flow={e['amount']} kg per reference unit.",
                    'Retain and characterize the existing uptake once; do not add another DAC/biomass credit.',e['row'])
            if e.get('type')=='biosphere' and e.get('name')=='Carbon dioxide, fossil' and float(e.get('amount') or 0)<0:
                finding(activity,'high','review_required','standalone_negative_fossil_co2',
                    f"Negative fossil CO2={e['amount']} kg; active use is not established by this workbook alone.",
                    'Check whether source inventory is gross or already net and whether destination is geological storage; preserve inactive legacy activities unless explicitly approved.',e['row'])
            if e.get('type')=='technosphere' and e.get('name')=='carbon dioxide compression, transport and storage' and 'mto' in name:
                finding(activity,'high','assumption_to_verify','captured_co2_geological_destination_assumed',
                    f"Positive storage-service input={e['amount']} kg per allocated reference unit.",
                    'Verify captured destination and compression overlap; ITOM stored_CO2 is captured/compressed, not evidence of injection.',e['row'])
        direct_coal=[e for e in exchanges if e.get('type')=='technosphere' and 'methanol production, from coal' in str(e.get('name','')).lower()]
        for e in direct_coal:
            finding(activity,'high','review_required','direct_coal_methanol_bypasses_market',
                f"Direct coal methanol={e['amount']} kg/reference unit.",
                'Reconcile with ITOM actual feedstock route before substituting; market updates alone do not alter this supplier.',e['row'])
    return pd.DataFrame(findings,columns=['source_path','sheet','row','activity','priority','status','issue','evidence','proposed_correction','applied'])


# Configuration and notebook orchestration. No premise monkey patches.

def carbon_workflow_preflight(config_path=None):
    """Read and validate the additional workflow's connected source files."""
    import yaml
    root = Path(__file__).resolve().parent
    path = (root / "configuration_file/carbon_workflows.yaml" if config_path is None
            else Path(config_path).expanduser().resolve())
    settings = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(settings, dict) or settings.get("schema_version") != 1:
        raise ValueError("Expected carbon-workflow schema_version: 1.")
    if settings.get("apply_inventory_corrections") is not False:
        raise ValueError("These additional workflows cannot apply inventory corrections.")
    required = ("carbon_content", "external_origin_rules", "product_mapping", "applications",
                "end_of_life", "captured_destinations", "treatment_lcia_factors")
    resolved = {}
    for key in required + ("inventory", "output_root"):
        item = Path(settings[key]).expanduser()
        resolved[key] = item.resolve() if item.is_absolute() else (root / item).resolve()
    workbooks = {}
    for case, item in settings["workbooks"].items():
        p = Path(item).expanduser()
        workbooks[str(case)] = p.resolve() if p.is_absolute() else (root / p).resolve()
    if settings.get('initial_stock'):
        stock_path = Path(settings['initial_stock']).expanduser()
        resolved['initial_stock'] = stock_path.resolve() if stock_path.is_absolute() else (root / stock_path).resolve()
    records = []
    for role, p in [("configuration", path), ("engine", Path(__file__).resolve())] + list(resolved.items()) + list(workbooks.items()):
        if role == "output_root": continue
        if not p.is_file(): raise FileNotFoundError(f"Additional carbon workflow {role}: {p}")
        records.append(dict(role=role, path=str(p), size_bytes=p.stat().st_size,
                            sha256=hashlib.sha256(p.read_bytes()).hexdigest()))
    tables = {key: pd.read_csv(resolved[key]) for key in required}
    if 'initial_stock' in resolved:
        tables['initial_stock'] = pd.read_csv(resolved['initial_stock'])
    _require(tables["product_mapping"], ["variable_key", "product", "route", "stock_eligible"], "Product mapping")
    if tables["product_mapping"].variable_key.duplicated().any():
        raise ValueError("Carbon workflow product mapping has duplicate variable keys.")
    for table_name, profile_name in (("applications", "application_profiles"), ("end_of_life", "end_of_life_profiles")):
        _require(tables[table_name], ["profile"], table_name)
        absent = set(settings[profile_name]) - set(tables[table_name].profile)
        if absent: raise ValueError(f"Unknown {table_name} profiles: {sorted(absent)}")
    if int(settings["end_year"]) < int(settings["start_year"]):
        raise ValueError("Carbon horizon ends before it starts.")
    return dict(settings=settings, paths=resolved, workbooks=workbooks, tables=tables,
                sources=pd.DataFrame(records), config_path=str(path))


def pathways_carbon_production(pathways, mapping, *, region="NL", external_index=1,
                              selected_variables=None, cases=("CL", "OCE")):
    """Extract only selected final-output volumes from the same live package."""
    import re
    scenarios = pathways.scenarios
    if "variables" not in scenarios.coords or "pathway" not in scenarios.coords:
        raise ValueError("Pathways scenarios need variables/pathway coordinates.")
    chosen = selected_variables
    calculated = getattr(pathways, "lca_results", None)
    if chosen is None and calculated is not None and "variable" in calculated.coords:
        chosen = calculated.coords["variable"].values.tolist()
    prefix = f"EXT - {int(external_index)} - "
    eligible = {prefix + str(row.variable_key): row for row in mapping.itertuples(index=False)}
    available = [str(v) for v in scenarios.coords["variables"].values]
    chosen = [v for v in available if v in eligible and (chosen is None or v in chosen)]
    if not chosen: raise ValueError("No mapped selected final-output variables in the current Pathways object.")
    frame = scenarios.sel(variables=chosen).to_dataframe(name="production_quantity").reset_index().rename(
        columns={"variables": "variable", "pathway": "scenario_name"})
    frame = frame.loc[frame.region.eq(region)].copy()
    if "model" in frame and frame.model.nunique() != 1:
        raise ValueError("Select one IAM model before pairing carbon production; do not sum models.")
    units = scenarios.attrs.get("units", {})
    conversions = {"kg": 1., "kilogram": 1., "kg/yr": 1., "kg/year": 1.,
                   "t": 1e3, "tonne": 1e3, "t/yr": 1e3, "t/year": 1e3,
                   "kt": 1e6, "kt/yr": 1e6, "kt/year": 1e6,
                   "mt": 1e9, "mt/yr": 1e9, "mt/year": 1e9}
    rows = []
    for record in frame.to_dict("records"):
        name = str(record["scenario_name"])
        matches = [case for case in cases if name == case or name.endswith(" - " + case)]
        if len(matches) != 1: raise ValueError(f"Cannot pair Pathways scenario with one carbon-source workbook: {name}")
        unit = str(units.get(record["variable"], "")).strip().lower().replace(" ", "")
        if unit not in conversions: raise ValueError(f"Unsupported or missing production unit {unit!r} for {record['variable']}")
        rule = eligible[record["variable"]]
        rows.append(dict(scenario=matches[0], scenario_name=name, year=record["year"],
            product=rule.product, route=rule.route, variable=record["variable"],
            production_kg=float(record["production_quantity"]) * conversions[unit]))
    return pd.DataFrame(rows)


def pair_gate_origins(production, source_production, solved_origins):
    """Use source-model origin estimates only where route/year volumes agree."""
    source_production = source_production.copy()
    # Pathways final-output variables aggregate modes of one technology. Keep
    # process IDs distinct while carbon-weighting the source modes together.
    base_routes = source_production.route.astype(str).str.replace(r'\|mode\d+$', '', regex=True)
    aggregate_requested = base_routes.isin(set(production.route))
    source_production.loc[aggregate_requested, 'route'] = base_routes.loc[aggregate_requested]
    weighted = source_production.merge(solved_origins, on=CONTEXT + ["process_id"], how="left", validate="many_to_many")
    weighted["weighted_amount"] = weighted.production_kg * weighted.share
    keys = GATE_KEYS
    shares = weighted.groupby(keys + ["origin"], as_index=False, dropna=False).weighted_amount.sum()
    denominator = source_production.groupby(keys, as_index=False).production_kg.sum().rename(columns={"production_kg": "source_production_kg"})
    shares = shares.merge(denominator, on=keys, validate="many_to_one")
    shares["share"] = shares.weighted_amount / shares.source_production_kg.replace(0, np.nan)
    paired = production[keys + ["production_kg"]].merge(denominator, on=keys, how="left", validate="one_to_one")
    paired["volume_matches"] = np.isclose(paired.production_kg, paired.source_production_kg, rtol=1e-5, atol=1e-3)
    paired["status"] = np.where(paired.volume_matches, "volume_matched_origin_screening", "source_pairing_unresolved")
    output = []
    for row in paired.to_dict("records"):
        subset = shares
        for key in keys: subset = subset.loc[subset[key].eq(row[key])]
        if row["volume_matches"] and row["production_kg"] > 0 and not subset.empty:
            output.extend(subset[keys + ["origin", "share"]].to_dict("records"))
        else:
            output.append(dict({key: row[key] for key in keys}, origin="unknown", share=1.))
    return pd.DataFrame(output), paired


def captured_carbon_accounts(streams, origins, destination_rules):
    """Keep capture-stage streams separate; never aggregate stages into credits."""
    captured = streams.loc[streams['product'].eq('stored_CO2')].copy()
    records = []
    for row in captured.to_dict("records"):
        selected = _rules_for(destination_rules, row, ["technology"], "captured destination")
        if len(selected) != 1: raise ValueError("Captured destination rules must select one row per technology.")
        rule = selected.iloc[0].to_dict()
        matches = origins.loc[origins.scenario.eq(row['scenario']) & origins.year.eq(row['year']) & origins.process_id.eq(row['process_id'])]
        if matches.empty: matches = pd.DataFrame([dict(origin="unknown", share=1.)])
        for origin in matches.to_dict("records"):
            record = dict(row, origin=origin['origin'], captured_CO2_kg=row['amount_kgCO2'] * origin['share'])
            record.update({key: rule[key] for key in ["geological_fraction", "reuse_fraction", "vent_fraction", "unknown_fraction", "storage_leakage_fraction"]})
            record["destination_status"] = rule.get("status", "unverified")
            records.append(record)
    columns = CONTEXT + ["process_id", "origin", "captured_CO2_kg", "geological_fraction", "reuse_fraction", "vent_fraction", "unknown_fraction"]
    return audit_co2_destinations(pd.DataFrame(records)) if records else pd.DataFrame(columns=columns)


def account_treatment_process_burdens(treatments, content, factors):
    """Join optional residual treatment LCIA coefficients without inventing zeros.

    Coefficients must exclude product-carbon CO2/CH4 already tracked physically.
    They can include energy/material inputs, infrastructure and documented energy
    recovery allocation. No virgin-production credit is inferred here.
    """
    if treatments.empty: return pd.DataFrame()
    _require(factors, ["fate", "product", "impact_category", "value_per_kg_treated", "direct_carbon_excluded"], "Treatment LCIA factors")
    climate_names = factors.impact_category.dropna().astype(str)
    if not (climate_names.str.contains('climate change', regex=False) | climate_names.str.contains('GWP', regex=False)).all():
        raise ValueError("This treatment bridge expects climate methods in kg CO2-equivalent.")
    contents = content.set_index("product").carbon_fraction_kgC_per_kg.to_dict()
    methods = factors.impact_category.dropna().unique().tolist()
    records = []
    coefficient_cache = {}
    for row in treatments.to_dict("records"):
        fraction = contents.get(row["product"], np.nan)
        mass = row["input_kgC"] / fraction if pd.notna(fraction) and fraction > 0 else np.nan
        for method in methods:
            context_keys = [key for key in ('scenario', 'year') if key in factors]
            identity = (method, row['fate'], row['product']) + tuple(row[key] for key in context_keys)
            if identity not in coefficient_cache:
                selected = _rules_for(factors.loc[factors.impact_category.eq(method)], row, context_keys + ["fate", "product"], "treatment factors", required=False)
                value = np.nan
                if len(selected) > 1: raise ValueError("Ambiguous treatment LCIA coefficient.")
                if len(selected):
                    factor = selected.iloc[0]
                    if str(factor.direct_carbon_excluded).lower() != "true":
                        raise ValueError("Treatment coefficients must explicitly exclude alreadytracked product-carbon emissions.")
                    if pd.notna(factor.value_per_kg_treated):
                        value = float(factor.value_per_kg_treated)
                        if not np.isfinite(value): raise ValueError("Nonfinite treatment impact coefficient.")
                coefficient_cache[identity] = value
            value = coefficient_cache[identity]
            records.append(dict(row, impact_category=method, treated_material_kg=mass,
                process_burden_kgCO2e=mass * value,
                coverage_status="coefficient_supplied" if pd.notna(value) and pd.notna(mass) else "missing_treatment_inventory_not_zero",
                total_cradle_to_grave_impact=np.nan,
                interpretation="Residual process burden only. Physical releases separate; no complete life-cycle total unless all components validated."))
    return pd.DataFrame(records)


def run_additional_carbon_workflows(pathways=None, *, config_path=None, selected_variables=None,
                                   output_root=None, run_name="por_carbon"):
    """Run all companion accounts; source models and LCIA results remain read-only.

    A live Pathways object is preferred for demand pairing. Without it, the run
    is explicitly an ITOM-only physical screening and cannot claim LCA pairing.
    This is not an endogenous recycling-demand optimizer or dynamic CF model.
    """
    preflight = carbon_workflow_preflight(config_path)
    settings, paths, inputs = preflight['settings'], preflight['paths'], preflight['tables']
    print("Carbon workflows: reading ITOM physical volumes and assumptions...", flush=True)
    source = read_itom_carbon_inputs(preflight['workbooks'], content=inputs['carbon_content'],
        source_rules=inputs['external_origin_rules'], period_years=settings['workbook_period_years'])
    print("Carbon workflows: tracing origins and auditing physical balances...", flush=True)
    traced = propagate_carbon_origins(source['external'], source['transfers'])
    physical = materialize_itom_carbon_flows(source['flows'], traced['origins'])
    balance = audit_carbon_balance(physical)
    start, end = int(settings['start_year']), int(settings['end_year'])
    mapping = inputs['product_mapping']
    if pathways is None:
        eligible = mapping[['product', 'route']].drop_duplicates()
        source_gate = source['production'].copy()
        source_gate['route'] = source_gate.route.astype(str).str.replace(r'\|mode\d+$', '', regex=True)
        production = source_gate.merge(eligible, on=['product', 'route'], how='inner')
        production = production.groupby(GATE_KEYS, as_index=False).production_kg.sum()
        pairing_label = "ITOM source volumes only; not paired to calculated LCIA"
    else:
        production = pathways_carbon_production(pathways, mapping, region=settings['region'],
            external_index=settings['external_index'], selected_variables=selected_variables,
            cases=tuple(preflight['workbooks']))
        pairing_label = "Live Pathways production; ITOM source-origin screening checked by route/year volumes"
    production = production.loc[production.year.ge(start)].copy()
    if production.empty: raise ValueError("No selected production at/after the carbon workflow start year.")
    origin_rules, pairing = pair_gate_origins(production, source['production'], traced['origins'])
    gate = build_factory_gate_carbon(production, inputs['carbon_content'], origin_rules)
    eligible = mapping.loc[mapping.stock_eligible.astype(str).str.lower().eq('true'), 'product'].unique()
    stock_gate = gate.loc[gate['product'].isin(eligible)].copy()
    excluded = gate.loc[~gate['product'].isin(eligible)].copy()
    if end < int(stock_gate.year.max()): raise ValueError("Carbon horizon excludes supplied production years.")
    captured = captured_carbon_accounts(source['co2_streams'], traced['origins'], inputs['captured_destinations'])
    inventory_review = audit_carbon_inventory_workbook(paths['inventory'])
    tables = dict(factory_gate_carbon=gate, gate_origin_rules=origin_rules, production_pairing_audit=pairing,
        excluded_from_product_stocks=excluded, carbon_origin_network=traced['origins'],
        origin_network_diagnostics=traced['diagnostics'], physical_carbon_flows=physical,
        physical_carbon_balance=balance, input_coverage_gaps=source['coverage'],
        lineage_gap_diagnostics=source['diagnostics'], captured_CO2_destinations=captured,
        CO2_process_streams=source['co2_streams'], inventory_correction_review=inventory_review,
        source_files=preflight['sources'])
    runs = {}
    for app_profile in settings['application_profiles']:
        applications = inputs['applications'].loc[inputs['applications'].profile.eq(app_profile)].drop(columns='profile')
        for fate_profile in settings['end_of_life_profiles']:
            key = app_profile + '__' + fate_profile
            print("Carbon workflows: cohort sensitivity " + key, flush=True)
            fates = inputs['end_of_life'].loc[inputs['end_of_life'].profile.eq(fate_profile)].drop(columns='profile')
            result = simulate_product_cohorts(stock_gate, applications, fates,
                start_year=start, end_year=end, initial_stock=inputs.get('initial_stock'),
                assume_zero_initial_stock=bool(settings['assume_zero_initial_stock']),
                interpolation=settings['production_interpolation'], inflow_basis=settings['inflow_basis'],
                recycling_mode=settings['recycling_mode'])
            result['treatment_lcia_coverage'] = account_treatment_process_burdens(result['treatments'], inputs['carbon_content'], inputs['treatment_lcia_factors'])
            runs[key] = result
            for name, table in result.items():
                if isinstance(table, pd.DataFrame): tables[key + '__' + name] = table
    review = inventory_review.copy()
    extras = []
    def flag(priority, issue, evidence, proposed):
        extras.append(dict(priority=priority, status='review_only', issue=issue, evidence=evidence,
                           proposed_correction=proposed, applied=False))
    if (~pairing.volume_matches).any():
        flag('high', 'source_package_volume_mismatch', str(int((~pairing.volume_matches).sum())) + ' selected route/year rows do not match source workbooks',
             'Check source snapshots/converter/package identity before assigning source-derived carbon origins; affected gate rows remain unknown.')
    unresolved = gate.loc[gate.production_kg.gt(0) & gate.carbon_content_status.ne('known')]
    if not unresolved.empty:
        flag('high', 'missing_active_product_carbon_content', ', '.join(sorted(unresolved['product'].unique())),
             'Obtain recipe/measurement for polyols and formulations; update additional assumptions first, then review inventories separately.')
    flag('high', 'end_of_life_treatment_inventory_coverage', 'Default treatment process LCIA coefficients are unresolved, not zero.',
         'Validate treatment inventories, direct-carbon decomposition and energy-recovery allocation before reporting a full cradle-to-grave score.')
    flag('medium', 'application_and_legacy_stock_data_missing', '1-year/20-year profiles are analyst screening contrasts; zero initial stock follows only new cohorts.',
         'Add destination/application shares, lifetime evidence and legacy stocks before interpreting results as actual regional stocks or waste forecasts.')
    flag('medium', 'carbon_partition_and_unknown_mixtures', str(len(source['coverage'])) + ' unresolved material-content records; proportional carbon mixing assumed.',
         'Document feedstock moisture/composition and selective carbon partitioning; reconcile raw balances before any origin-aware emission relabelling.')
    lcia_version = None if pathways is None else str(getattr(pathways, 'ei_version', 'not recorded'))
    inventory_version = str(settings.get('inventory_version', 'not recorded'))
    if lcia_version is not None and lcia_version != inventory_version:
        flag('high', 'inventory_LCIA_version_mismatch', inventory_version + ' inventory context with LCIA factors ' + lcia_version,
             'Align validated method definitions/version and recalculate separately; this workflow does not change factors or results.')
    review = pd.concat([review, pd.DataFrame(extras)], ignore_index=True)
    review['applied'] = False
    tables['correction_review_queue'] = review
    assumptions = dict(source['assumptions'], configuration=settings, production_pairing=pairing_label,
        factor_version=lcia_version, lifecycle_boundary='Additional cohort follow-up; production LCIA unchanged',
        application_data='Illustrative screening profiles, not observed Rotterdam application mixes',
        initial_stock=('Supplied age-specific initial stock' if inputs.get('initial_stock') is not None else
                       'Explicit zero only for newly modelled cohorts; no claim about historical stocks'),
        recycling='Secondary pool reported separately; no extra virgin displacement or atmospheric uptake',
        treatment_LCIA='Missing coefficients remain unresolved; no complete cradle-to-grave score invented',
        carbon_origin='Source-model proportional mixing estimates; not correction of baseline inventory emissions',
        end_horizon='Remaining in-use carbon, residues and recovered secondary pool remain explicit; none is permanent-removal credit',
        characterization='Physical timelines only; no time-discounted or dynamic radiative-forcing credit')
    source_paths = preflight['sources'].path.tolist()
    package = None if pathways is None else getattr(pathways, 'datapackage', None)
    if isinstance(package, (str, Path)) and Path(package).is_file(): source_paths.append(str(package))
    for record in preflight['sources'].to_dict('records'):
        if hashlib.sha256(Path(record['path']).read_bytes()).hexdigest() != record['sha256']:
            raise RuntimeError('An input changed during carbon analysis; no companion run published: ' + record['path'])
    published = write_carbon_outputs(tables, output_root or paths['output_root'],
        source_paths=source_paths, assumptions=assumptions, run_name=run_name)
    changed = [record['path'] for record in preflight['sources'].to_dict('records')
               if hashlib.sha256(Path(record['path']).read_bytes()).hexdigest() != record['sha256']]
    if changed:
        manifest_path = Path(published['manifest'])
        invalid = json.loads(manifest_path.read_text(encoding='utf-8'))
        invalid.update(valid_source_snapshot=False, failure='Input changed during publication', changed_inputs=changed)
        manifest_path.write_text(json.dumps(invalid, indent=2, ensure_ascii=False), encoding='utf-8')
        raise RuntimeError('Inputs changed during publication; companion run marked invalid: ' + str(manifest_path))
    report = Path(published['output_dir']) / 'correction_review.md'
    lines = ['# Carbon modelling correction review', '', 'No corrections have been applied. All entries below require review and supporting evidence.', '']
    for row in review.to_dict('records'):
        lines.extend(['- **' + str(row['priority']) + ': ' + str(row['issue']) + '**',
                      '  Evidence: ' + str(row.get('evidence', '')),
                      '  Proposed next step: ' + str(row.get('proposed_correction', '')), ''])
    report.write_text('\n'.join(lines), encoding='utf-8')
    print('Carbon workflows complete. Baseline inputs/results unchanged. Reports: ' + published['output_dir'], flush=True)
    return dict(published, data=tables, cohorts=runs, correction_review=str(report), preflight=preflight)
