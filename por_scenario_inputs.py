"""Scenario input preparation: fresh premise definitions and ITOM flow checks.

premise mutates the dictionaries supplied to NewDatabase, and Pathways copies
those dictionaries across years. Reconstruct inputs rather than copying an
updated dictionary, which can retain applied-sector flags and inventory stores.

ITOM material-flow validation and derived p-xylene supply quantities are also
prepared here, before the scenario inventories are constructed or linked.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
import math


_INPUT_FIELDS = (
    "model", "pathway", "year", "filepath", "exclude", "external scenarios",
)
_RUNTIME_FIELDS = frozenset({
    "applied functions", "cache", "configurations", "database",
    "database filepath", "database metadata cache filepath",
    "database metadata filepath", "database name", "external data",
    "heat diagnostics", "iam data", "index",
    "inventories metadata cache filepath", "mapping", "por supplier linking", "metal steel policy",
    "unmatched category flows", "additional valid regions",
})


def _copy_input_containers(value):
    """Copy plain input containers without copying opaque resource objects."""
    if type(value) is dict:
        return {key: _copy_input_containers(item) for key, item in value.items()}
    if type(value) is list:
        return [_copy_input_containers(item) for item in value]
    if type(value) is tuple:
        return tuple(_copy_input_containers(item) for item in value)
    if type(value) is set:
        return {_copy_input_containers(item) for item in value}
    if type(value) is frozenset:
        return frozenset(_copy_input_containers(item) for item in value)
    return value


def fresh_scenario_definitions(scenarios: Iterable[Mapping]) -> list[dict]:
    """Return independent user inputs for each new Brightway/Pathways build.

    Public fields supported by the installed legacy and native-store premise
    versions are retained, including optional ``filepath`` and ``exclude``.
    Known premise outputs and private runtime fields are discarded without
    reading their values. Unknown public fields raise instead of silently
    discarding a potential configuration option after a premise upgrade.

    External entry dictionaries and their input containers are copied, but
    each ``data`` resource is kept by identity; it may contain open readers or
    large datapackage objects. No premise import, cache access or DB write is
    needed. The source definitions are never modified.
    """
    fresh = []
    for position, scenario in enumerate(scenarios):
        if not isinstance(scenario, Mapping):
            raise TypeError(f"Scenario {position} must be a mapping.")
        unknown = [
            key for key in scenario
            if key not in _INPUT_FIELDS and key not in _RUNTIME_FIELDS
            and not (isinstance(key, str) and key.startswith("_"))
        ]
        if unknown:
            raise ValueError(
                f"Scenario {position} has unsupported public input fields: "
                f"{unknown!r}. Supported fields are {_INPUT_FIELDS!r}; review "
                "new premise options before adding them to this adapter."
            )

        definition = {
            key: _copy_input_containers(scenario[key])
            for key in _INPUT_FIELDS
            if key != "external scenarios" and key in scenario
        }
        if "external scenarios" in scenario:
            external = scenario["external scenarios"]
            if not isinstance(external, list):
                raise TypeError(f"Scenario {position}: external scenarios must be a list.")
            entries = []
            for entry_position, entry in enumerate(external):
                if not isinstance(entry, Mapping):
                    raise TypeError(
                        f"Scenario {position}, external entry {entry_position} "
                        "must be a mapping."
                    )
                entries.append({
                    key: item if key == "data" else _copy_input_containers(item)
                    for key, item in entry.items()
                })
            definition["external scenarios"] = entries
        fresh.append(definition)
    return fresh


# ITOM p-xylene purity reconciliation
# ---------------------------------
# Raw source rows remain immutable. Derived market quantities split the original
# local extraction output by proportional mixing of the raw-labelled supply pool.
# This follows the supplied ITOM purity clarification, not an inferred raw label.
# Pandas is imported only by material-flow functions; premise input isolation
# remains lightweight and does not depend on Pandas or a running LCA database.
TERMINAL_RAW = "Intermediate Product|raw_para_xylene|para_xylene_terminal_BAT"
EXTRACTION_GROSS = "Intermediate Product|para_xylene|para_xylene_extraction"
TERMINAL_PURE_LOCAL = "Intermediate Product|para_xylene|para_xylene_terminal_BAT|purified_local_supply"
EXTRACTION_NONTERMINAL = "Intermediate Product|para_xylene|para_xylene_extraction|nonterminal_supply"
DERIVED_VARIABLES = (TERMINAL_PURE_LOCAL, EXTRACTION_NONTERMINAL)
METADATA = ["model", "scenario", "region", "variables", "unit"]
CONTEXT = METADATA[:3]
TOLERANCE = 1e-8  # kt/year; matches the converter's existing mode reconciliation


def _number(value, label):
    if isinstance(value, bool):
        raise ValueError(f"{label}: boolean is not a physical p-xylene flow")
    try:
        number = float(value)
    except (ValueError, TypeError) as error:
        raise ValueError(f"{label}: missing/non-numeric p-xylene flow") from error
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"{label}: p-xylene physical flows must be finite and nonnegative")
    return number


def verify_px_extraction_yield(input_factors, output_factors):
    """Require the supplied 1:1 extraction definition; no inferred efficiency."""
    import pandas as pd

    observed = []
    for table, product, value in ((input_factors, "raw_para_xylene", "InputActivityRatio"),
                                  (output_factors, "para_xylene", "OutputActivityRatio")):
        selected = table.loc[table.TECHNOLOGY.eq("para_xylene_extraction") & table.PRODUCT.eq(product)].copy()
        if selected.empty:
            raise ValueError(f"Missing para_xylene_extraction {product} coefficients")
        keys = ["REGION", "MODE_OF_OPERATION", "YEAR"]
        selected["MODE_OF_OPERATION"] = pd.to_numeric(selected.MODE_OF_OPERATION, errors="raise")
        if selected.duplicated(keys).any():
            raise ValueError("Duplicate p-xylene extraction coefficients")
        ratios = pd.to_numeric(selected[value], errors="raise")
        if not ratios.map(lambda q: math.isfinite(float(q)) and math.isclose(float(q), 1, rel_tol=0, abs_tol=1e-12)).all():
            raise ValueError("Terminal purity correction requires verified 1:1 p-xylene extraction")
        observed.append(set(map(tuple, selected[keys].itertuples(index=False, name=None))))
    if observed[0] != observed[1]:
        raise ValueError("P-xylene extraction input/output coefficient coverage differs")


def validate_px_source_flows(table, amount_column):
    """Check original material rows before the legacy converter coerces NaNs."""
    selected = table.loc[table.PRODUCT.isin(["raw_para_xylene", "para_xylene"])]
    for amount in selected[amount_column]:
        _number(amount, "original p-xylene " + amount_column)


def annual_raw_px_use(use_volume, *, model, scenario, region_map, time_step_years=5):
    """Read actual process use, including terminal mode-2 exports, for balance."""
    import pandas as pd

    if not math.isfinite(time_step_years) or time_step_years <= 0:
        raise ValueError("P-xylene time-step duration must be positive and finite")
    required = {"LOCATION", "TECHNOLOGY", "PRODUCT", "YEAR", "LocalUseByMode"}
    if not required <= set(use_volume.columns):
        raise ValueError(f"Missing raw p-xylene use columns: {sorted(required - set(use_volume.columns))}")
    selected = use_volume.loc[use_volume.PRODUCT.eq("raw_para_xylene")].copy()
    rows = []
    for record in selected.to_dict("records"):
        total = _number(record["LocalUseByMode"], "raw p-xylene use") / time_step_years
        rows.append({"model": model, "scenario": scenario,
                     "region": region_map.get(record["LOCATION"], record["LOCATION"]),
                     "year": int(record["YEAR"]), "technology": record["TECHNOLOGY"],
                     "amount": total})
    return pd.DataFrame(rows, columns=CONTEXT + ["year", "technology", "amount"])


def reconcile_px_terminal_purity(iamc, raw_use):
    """Return source rows plus two audited, annual local-market supply rows.

    For supply S = terminal T + other raw production + raw EU imports, and
    original local extraction E, local terminal supply is E*T/S. Only E*(1-T/S)
    needs purification. E is preserved: no gross import is added on top of it.
    Terminal material leaving the local pool is T-E*T/S, never treated as local
    purified supply. Other uses/exports are checked against the full raw ledger.
    """
    import pandas as pd

    missing = set(METADATA) - set(iamc.columns)
    if missing:
        raise ValueError(f"Missing IAMC metadata for p-xylene reconciliation: {sorted(missing)}")
    years = sorted((column for column in iamc.columns if str(column).isdigit()), key=int)
    if not years:
        raise ValueError("No years in p-xylene reconciliation")
    if iamc[METADATA].isna().any().any():
        raise ValueError("Missing p-xylene IAMC identities/units")
    if not set(CONTEXT + ["year", "technology", "amount"]) <= set(raw_use.columns):
        raise ValueError("Missing p-xylene physical-use ledger columns")
    source = iamc.loc[~iamc.variables.isin(DERIVED_VARIABLES)].copy()
    native_pure_terminal = source.loc[source.variables.eq("Intermediate Product|para_xylene|para_xylene_terminal_BAT")]
    if not native_pure_terminal.empty and any(_number(value, "native purified terminal") > 0
                                            for value in native_pure_terminal[years].to_numpy().flat):
        raise ValueError("ITOM already labels terminal p-xylene purified; revise the legacy-label correction before converting")
    additions, audit = [], []
    for context, group in source.groupby(CONTEXT, sort=False, dropna=False):
        parts = group.variables.str.split("|")
        raw_production = parts.str.len().eq(3) & parts.str[1].eq("raw_para_xylene") & ~group.variables.str.startswith("Trade|")
        # Transfer/hub/demand technologies are not additional physical supply.
        raw_production &= ~parts.str[2].fillna("").str.startswith(("pipeline_transfert_", "transport_hub_", "demand_of_"))
        chosen = group.loc[raw_production | group.variables.isin([
            TERMINAL_RAW, EXTRACTION_GROSS, "Trade|To PoR|raw_para_xylene", "Trade|From PoR|raw_para_xylene"])]
        if chosen.empty:
            continue
        if chosen.duplicated(["variables"]).any():
            raise ValueError(f"Duplicate p-xylene source rows for {context}")
        if not chosen.unit.eq("kt/yr").all():
            raise ValueError(f"P-xylene source quantities must use kt/yr: {context}")
        amounts = {variable: {} for variable in DERIVED_VARIABLES}
        uses = raw_use
        for field, value in zip(CONTEXT, context):
            uses = uses.loc[uses[field].eq(value)]
        for year in years:
            label = f"{context}/{year}"
            def flow(variable, *, export=False):
                row = chosen.loc[chosen.variables.eq(variable)]
                if row.empty:
                    return 0.0
                value = row.iloc[0][year]
                return _number(-float(value) if export else value, label + "/" + variable)
            terminal = flow(TERMINAL_RAW)
            extraction = flow(EXTRACTION_GROSS)
            imported_raw = flow("Trade|To PoR|raw_para_xylene")
            exported_raw = flow("Trade|From PoR|raw_para_xylene", export=True)
            domestic = sum(_number(row[year], label + "/" + row["variables"])
                           for row in chosen.loc[raw_production & ~group.variables.eq(TERMINAL_RAW)].to_dict("records"))
            pool = terminal + domestic + imported_raw
            local_uses = uses.loc[uses.year.eq(int(year))]
            used_extraction = sum(_number(q, label + "/extraction use") for q in
                                  local_uses.loc[local_uses.technology.eq("para_xylene_extraction"), "amount"])
            used_other = sum(_number(q, label + "/other raw use") for q in
                            local_uses.loc[~local_uses.technology.eq("para_xylene_extraction"), "amount"])
            if not math.isclose(used_extraction, extraction, rel_tol=0, abs_tol=TOLERANCE):
                raise ValueError(f"{label}: p-xylene extraction use/output does not reconcile 1:1")
            if not math.isclose(pool, extraction + used_other + exported_raw, rel_tol=0, abs_tol=TOLERANCE):
                raise ValueError(f"{label}: raw p-xylene supply/use/export balance does not close")
            if extraction > pool + TOLERANCE:
                raise ValueError(f"{label}: extraction exceeds raw-labelled supply")
            fraction = terminal / pool if pool else 0.0
            purified_local = extraction * fraction
            remaining_extraction = extraction - purified_local
            terminal_other = terminal - purified_local
            if min(purified_local, remaining_extraction, terminal_other) < -TOLERANCE:
                raise ValueError(f"{label}: negative derived p-xylene supply")
            amounts[TERMINAL_PURE_LOCAL][year] = max(0.0, purified_local)
            amounts[EXTRACTION_NONTERMINAL][year] = max(0.0, remaining_extraction)
            audit.append({**dict(zip(CONTEXT, context)), "year": int(year), "unit": "kt/yr",
                          "method": "proportional_pool_origin_allocation", "purity_basis": "ITOM team clarification: terminal imports purified",
                          "raw_labelled_pool": pool, "terminal_gross": terminal,
                          "other_raw_production": domestic, "raw_eu_imports": imported_raw,
                          "raw_trade_exports": exported_raw, "other_raw_use_including_terminal_exports": used_other,
                          "extraction_original": extraction, "terminal_local_purified": purified_local,
                          "extraction_nonterminal": remaining_extraction,
                          "terminal_other_use_or_export": max(0.0, terminal_other),
                          "local_supply_difference": purified_local + remaining_extraction - extraction})
        for variable, values in amounts.items():
            additions.append({**dict(zip(CONTEXT, context)), "variables": variable, "unit": "kt/yr", **values})
    derived = pd.DataFrame(additions, columns=iamc.columns)
    old_derived = iamc.loc[iamc.variables.isin(DERIVED_VARIABLES)]
    if not old_derived.empty:
        for _, row in old_derived.iterrows():
            matching = derived
            for field in METADATA:
                matching = matching.loc[matching[field].eq(row[field])]
            if len(matching) != 1 or any(not math.isclose(_number(row[year], "existing derived supply"),
                    float(matching.iloc[0][year]), rel_tol=0, abs_tol=TOLERANCE) for year in years):
                raise ValueError("Conflicting pre-existing derived p-xylene rows; regenerate from original ITOM inputs")
        if old_derived.duplicated(METADATA).any():
            raise ValueError("Duplicate derived p-xylene rows")
    return pd.concat([source, derived], ignore_index=True, sort=False), audit
