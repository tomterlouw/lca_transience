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
    "itom steel market aggregation",
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


# European ITOM steel market construction. IAMC supplies the volumes; after
# native market creation these quantities select the actual national markets.
STEEL_COUNTRY_SUPPLY_PREFIX = "Production|Steel|Supply from|"
STEEL_COUNTRY_MIX_DESCRIPTOR = "itom_steel_country_market_mix"


def load_itom_steel_country_mix(scenario):
    """Read declared national supply volumes without loading an LCA database."""
    import csv
    import hashlib
    import io
    import json

    selected = []
    for entry in scenario.get("external scenarios", ()):
        package = entry.get("data")
        descriptor = getattr(package, "descriptor", {})
        declaration = descriptor.get(STEEL_COUNTRY_MIX_DESCRIPTOR)
        if declaration is None:
            continue
        if not callable(getattr(package, "get_resource", None)):
            raise ValueError("ITOM country-market aggregation requires the loaded datapackage object.")
        selected.append((entry, package, declaration))
    if not selected:
        return None
    if len(selected) != 1:
        raise ValueError("Only one European ITOM steel country-market mix may be active in a scenario.")
    entry, package, declaration = selected[0]
    if declaration.get("version") != 1 or declaration.get("region") != "RER":
        raise ValueError("Unsupported ITOM steel country-market aggregation contract.")
    prefix = declaration.get("variable_prefix")
    if prefix != STEEL_COUNTRY_SUPPLY_PREFIX:
        raise ValueError("Unsupported ITOM steel country-supply variable prefix.")
    markets = declaration.get("markets", ())
    if (not markets or len({(m.get("name"), m.get("reference product"), m.get("unit")) for m in markets}) != len(markets)
            or any(not m.get("name") or not m.get("reference product") or m.get("unit") != "kilogram" for m in markets)):
        raise ValueError("ITOM country-market targets must have unique names/products and kilogram units.")
    resource = package.get_resource("scenario_data")
    raw = resource.raw_read()
    raw = raw.encode("utf-8") if isinstance(raw, str) else bytes(raw)
    reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig")))
    headers = reader.fieldnames or []
    if not set(METADATA).issubset(headers):
        raise ValueError("ITOM steel IAMC resource is missing required metadata columns.")
    years = sorted(int(key) for key in headers if str(key).isdigit() and len(str(key)) == 4)
    if not years:
        raise ValueError("ITOM steel country-supply export has no years.")
    rows = [row for row in reader if row.get("scenario") == str(entry.get("scenario"))
            and row.get("region") == "RER"
            and (row.get("variables", "").startswith(prefix)
                 or row.get("variables") == declaration.get("total_variable", "Production|Steel"))]
    models = {row["model"] for row in rows}
    if len(models) != 1:
        raise ValueError("ITOM country-market supply must identify exactly one source model for the selected external scenario.")
    country_rows, total_rows = {}, []
    for row in rows:
        if row["unit"] != "Mt/yr":
            raise ValueError("ITOM country-market volumes must be annual Mt/yr.")
        variable = row["variables"]
        if variable.startswith(prefix):
            country = variable[len(prefix):]
            if len(country) != 2 or not country.isalpha() or country != country.upper():
                raise ValueError(f"Invalid national steel supply country: {country!r}.")
            if country in country_rows:
                raise ValueError(f"Duplicate national steel supply row: {country}.")
            country_rows[country] = row
        else:
            total_rows.append(row)
    if not country_rows or len(total_rows) != 1:
        raise ValueError("Regenerate ITOM IAMC: national RER supply rows and one European total are required.")

    def volumes(row):
        values = []
        for year in years:
            text = row.get(str(year))
            if text is None or str(text).strip() == "":
                raise ValueError(f"Missing ITOM steel production for {year}.")
            try:
                amount = float(text)
            except (TypeError, ValueError) as error:
                raise ValueError(f"Invalid ITOM steel production for {year}.") from error
            if not math.isfinite(amount) or amount < 0:
                raise ValueError(f"Non-finite or negative ITOM steel production for {year}.")
            values.append(amount)
        return values

    national = {country: volumes(row) for country, row in country_rows.items()}
    reported_total = volumes(total_rows[0])
    for position, year in enumerate(years):
        if not math.isclose(sum(values[position] for values in national.values()), reported_total[position], rel_tol=1e-9, abs_tol=1e-8):
            raise ValueError(f"ITOM country-market production does not reconcile with RER total in {year}.")
    year = float(scenario["year"])
    if not math.isfinite(year):
        raise ValueError("ITOM country-market scenario year must be finite.")
    effective = min(max(year, years[0]), years[-1])
    upper = next((i for i, point in enumerate(years) if point >= effective), len(years) - 1)
    lower = max(upper - 1, 0)
    fraction = 0. if lower == upper else (effective - years[lower]) / (years[upper] - years[lower])

    def interpolate(values):
        return float(values[lower] + fraction * (values[upper] - values[lower]))

    country_volumes = {country: interpolate(values) for country, values in sorted(national.items())}
    total = sum(country_volumes.values())
    weights = {country: amount / total for country, amount in country_volumes.items() if amount > 0} if total > 0 else {}
    source_hash = hashlib.sha256(raw).hexdigest()
    signature = hashlib.sha256(raw + json.dumps(declaration, sort_keys=True).encode("utf-8")).hexdigest()
    return dict(source_sha256=source_hash, source_signature=signature,
        source=str(getattr(resource, "source", "scenario_data")), model=next(iter(models)),
        scenario=str(entry["scenario"]), year=year, effective_year=effective, region="RER",
        country_volumes=country_volumes, weights=weights, total=total,
        markets=[dict(m) for m in markets], status="ready" if total > 0 else "no_european_production")


def plan_itom_steel_country_markets(database, spec):
    """Return a nonmutating RER recipe plan using same-grade national markets."""
    from collections import defaultdict

    if spec is None:
        return list(database), {}, []
    region = spec["region"]
    if region != "RER":
        raise ValueError("ITOM steel country-market aggregation only targets RER.")
    weights, volumes = spec["weights"], spec["country_volumes"]
    total = float(spec["total"])
    if (not math.isfinite(total) or total < 0 or any(not math.isfinite(float(v)) or v < 0 for v in volumes.values())
            or not math.isclose(sum(volumes.values()), total, rel_tol=1e-9, abs_tol=1e-8)):
        raise ValueError("Invalid national steel market production totals.")
    expected_weights = {c: amount / total for c, amount in volumes.items() if amount > 0} if total > 0 else {}
    if (set(weights) != set(expected_weights)
            or any(not math.isfinite(float(weights[c])) or not math.isclose(weights[c], expected_weights[c], rel_tol=1e-10, abs_tol=1e-12) for c in expected_weights)):
        raise ValueError("ITOM country-market weights do not match the production volumes.")
    index = defaultdict(list)

    def key(dataset):
        return (dataset.get("name"), dataset.get("reference product"), dataset.get("unit"), dataset.get("location"))

    for position, dataset in enumerate(database):
        index[key(dataset)].append((position, dataset))
    result, changed, planned = list(database), [], []
    for market in spec["markets"]:
        target_key = (market["name"], market["reference product"], market["unit"], region)
        targets = index.get(target_key, ())
        if total == 0:
            if any(exc.get("type") == "technosphere" and exc.get("amount", 0) != 0
                   for _, target in targets for exc in target.get("exchanges", ())):
                raise ValueError("A populated RER ITOM steel market exists despite zero European production.")
            continue
        if len(targets) != 1:
            raise ValueError(f"Expected one RER ITOM steel market, found {len(targets)}: {target_key}")
        position, target = targets[0]
        production = [exc for exc in target.get("exchanges", ()) if exc.get("type") == "production"]
        if len(production) != 1 or not math.isclose(float(production[0]["amount"]), 1., rel_tol=1e-10):
            raise ValueError(f"RER steel market must produce one kilogram: {target_key}")

        def verify_volume(dataset, expected):
            for exc in dataset.get("exchanges", ()):
                if exc.get("type") == "production" and "production volume" in exc:
                    volume = float(exc["production volume"])
                    if not math.isfinite(volume) or not math.isclose(volume, expected, rel_tol=1e-6, abs_tol=1e-8):
                        raise ValueError(f"Native steel production disagrees with exported IAMC for {key(dataset)}: {volume} versus {expected}")

        verify_volume(target, total)
        supplies = []
        for country, weight in sorted(weights.items()):
            if len(country) != 2 or country in (region, "World", "GLO", "RoW"):
                raise ValueError(f"Invalid country in RER steel market: {country!r}")
            supplier_key = (market["name"], market["reference product"], market["unit"], country)
            suppliers = index.get(supplier_key, ())
            if len(suppliers) != 1:
                raise ValueError(f"Positive steel production requires one national ITOM market, found {len(suppliers)}: {supplier_key}")
            supplier = suppliers[0][1]
            verify_volume(supplier, volumes[country])
            if any(exc.get("type") == "technosphere" and exc.get("name") == market["name"]
                   and exc.get("location") == region and exc.get("amount", 0) != 0 for exc in supplier.get("exchanges", ())):
                raise ValueError(f"National steel market consumes its RER aggregate: {supplier_key}")
            supplies.append(dict(name=market["name"], product=market["reference product"],
                unit="kilogram", location=country, amount=float(weight), type="technosphere", **{"uncertainty type": 0}))
        if not math.isclose(sum(exc["amount"] for exc in supplies), 1., rel_tol=1e-10, abs_tol=1e-10):
            raise ValueError("RER national-market supply does not sum to one kilogram.")
        def commodity(exc):
            return (exc.get("type") == "technosphere" and exc.get("unit") == "kilogram"
                    and exc.get("product", exc.get("reference product")) in ("steel, low-alloyed", "steel, unalloyed"))
        old_commodity = [exc for exc in target["exchanges"] if commodity(exc)]
        unchanged = len(old_commodity) == len(supplies) and all(
            left.get("name") == right["name"] and left.get("product", left.get("reference product")) == right["product"]
            and left.get("location") == right["location"]
            and math.isclose(float(left.get("amount", 0)), right["amount"], rel_tol=1e-6, abs_tol=1e-10)
            for left, right in zip(old_commodity, supplies))
        if not unchanged:
            new_exchanges, inserted = [], False
            for exc in target["exchanges"]:
                if commodity(exc):
                    if not inserted:
                        new_exchanges.extend(supplies)
                        inserted = True
                else:
                    new_exchanges.append(exc)
            if not inserted:
                new_exchanges.extend(supplies)
            result[position] = dict(target, exchanges=new_exchanges)
            changed.append(position)
        planned.append(dict(name=market["name"], reference_product=market["reference product"],
            location=region, countries=len(supplies), total_input=sum(exc["amount"] for exc in supplies)))
    receipt = {key: spec[key] for key in ("source_sha256", "source_signature", "source", "model", "scenario", "year",
        "effective_year", "region", "country_volumes", "weights", "total", "status") if key in spec}
    receipt.update(markets=planned, changed_markets=len(changed),
        method="national production volumes; direct same-grade national ITOM market suppliers")
    return result, receipt, changed


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
