"""Prepare the separate FORECAST-sites steel input for premise.

Read the production workbook without modifying it. National route volumes feed
national markets; RER supply rows select those markets by country production.
BOF and NG-DRI CCS use native CCS inventories. Scrap-EAF CCS remains unresolved.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pycountry

ROOT = Path(__file__).resolve().parent
DEFAULT_INPUT = ROOT / "scenario_data/scenario_data_forecast_steel_production.xlsx"
DEFAULT_OUTPUT = ROOT / "scenario_data/scenario_data_forecast_steel_iamc.csv"
MAPPING_FILE = ROOT / "configuration_file/forecast_steel_routes.yaml"
META = ["model", "scenario", "region", "variables", "unit"]
PREFIX = "Production|Steel|"
SUPPLY_PREFIX = PREFIX + "Supply from|"
SCENARIO_PREFIX = "forecast_steel_"
# Whole-country European coverage, matching the existing RER steel workflow.
EUROPEAN_COUNTRIES = frozenset(
    "AT BE BG HR CY CZ DK EE FI FR DE GR HU IE IT LV LT LU MT NL PL PT RO SK SI ES SE "
    "AD AL AX BA BY CH GB GG GI IM IS JE LI MC MD ME MK NO RS SJ SM UA VA".split()
)


def route_definitions(path=MAPPING_FILE):
    import yaml
    definitions = yaml.safe_load(Path(path).read_text(encoding="utf-8"))["routes"]
    for field in ("input_variable", "variable", "alias", "activity"):
        if len({r[field] for r in definitions}) != len(definitions):
            raise ValueError(f"FORECAST steel route {field} must be unique.")
    return definitions


def convert_frame(frame, definitions=None, *, blank_policy=None):
    """Return IAMC and country-share audit, conserving every reported tonne."""
    definitions = route_definitions() if definitions is None else definitions
    if blank_policy is None:
        import yaml
        blank_policy = yaml.safe_load(MAPPING_FILE.read_text(encoding="utf-8")).get("source_blank_policy", "error")
    if blank_policy not in ("error", "zero_inactive_route"):
        raise ValueError("Choose blank_policy='error' or 'zero_inactive_route'.")
    data = frame.copy()
    data.columns = [str(c).strip().lower() for c in data.columns]
    if len(set(data.columns)) != len(data.columns):
        raise ValueError("Duplicate FORECAST workbook headers.")
    if set(["model", "scenario", "region", "variable", "unit"]) - set(data):
        raise ValueError("Expected model/scenario/region/variable/unit workbook columns.")
    years = sorted([c for c in data if c.isdigit() and len(c) == 4], key=int)
    if not years or set(data) != {"model", "scenario", "region", "variable", "unit", *years}:
        raise ValueError("Only IAMC metadata and four-digit year columns are supported.")
    if data.empty:
        raise ValueError("Empty FORECAST steel workbook.")
    for field in ["model", "scenario", "region", "variable", "unit"]:
        if data[field].isna().any() or data[field].astype(str).str.strip().eq("").any():
            raise ValueError(f"Missing {field} metadata.")
        data[field] = data[field].astype(str).str.strip()
    data["model"] = data.model.str.lower()
    # premise reads CSV with pandas inference, even when the descriptor declares
    # scenario as a string. Numeric-looking case IDs otherwise become integers
    # and do not match the required string external-scenario selection.
    data["scenario"] = SCENARIO_PREFIX + data.scenario

    def country(value):
        try:
            code = pycountry.countries.lookup(value).alpha_2
        except LookupError as error:
            raise ValueError(f"Unrecognised FORECAST country {value!r}.") from error
        if code not in EUROPEAN_COUNTRIES:
            raise ValueError(f"Outside the reviewed European country boundary: {code}.")
        return code

    data["region"] = data.region.map(country)
    if data.duplicated(["model", "scenario", "region", "variable"]).any():
        raise ValueError("Duplicate FORECAST steel country/route identities.")
    if not data.unit.isin(["Mt/year", "Mt/yr"]).all():
        raise ValueError("FORECAST steel production must be Mt/year or Mt/yr.")
    known = {r["input_variable"]: r["variable"] for r in definitions}
    unknown = set(data.variable) - set(known)
    if unknown:
        raise ValueError(f"Unmapped steel routes: {sorted(unknown)}. Review the route YAML.")
    data[years] = data[years].apply(pd.to_numeric, errors="raise")
    if blank_policy == "zero_inactive_route":
        data[years] = data[years].fillna(0.)
    if not np.isfinite(data[years].to_numpy(dtype=float)).all() or (data[years] < 0).any().any():
        raise ValueError("Missing, non-finite or negative steel production; blank cells are not zero.")
    data["variables"] = data.variable.map(known)
    rows, audit = [], []
    for (model, scenario), group in data.groupby(["model", "scenario"], sort=True):
        countries = sorted(group.region.unique())
        grid = pd.MultiIndex.from_product([countries, list(known.values())], names=["region", "variables"])
        routes = group.set_index(["region", "variables"])[years].reindex(grid, fill_value=0.)
        # Missing route rows mean zero activity within supplied countries only.
        totals = routes.groupby(level="region").sum()
        european = totals.sum()
        for (region, variable), quantities in routes.iterrows():
            rows.append(dict(model=model, scenario=scenario, region=region, variables=variable,
                             unit="Mt/yr", **quantities.to_dict()))
        for region, quantities in totals.iterrows():
            rows.append(dict(model=model, scenario=scenario, region=region, variables="Production|Steel",
                             unit="Mt/yr", **quantities.to_dict()))
            rows.append(dict(model=model, scenario=scenario, region="RER", variables=SUPPLY_PREFIX + region,
                             unit="Mt/yr", **quantities.to_dict()))
            for year in years:
                total = float(european[year])
                audit.append(dict(model=model, scenario=scenario, year=int(year), source_country=region,
                    country_production_Mt_per_year=float(quantities[year]), rer_production_Mt_per_year=total,
                    country_share=float(quantities[year]) / total if total > 0 else None))
        for variable, quantities in routes.groupby(level="variables").sum().iterrows():
            rows.append(dict(model=model, scenario=scenario, region="RER", variables=variable,
                             unit="Mt/yr", **quantities.to_dict()))
        rows.append(dict(model=model, scenario=scenario, region="RER", variables="Production|Steel",
                         unit="Mt/yr", **european.to_dict()))
        if not np.allclose(european.to_numpy(), group[years].sum().to_numpy(), rtol=1e-12, atol=1e-10):
            raise ValueError("FORECAST conversion did not conserve source production.")
    return pd.DataFrame(rows)[META + years], pd.DataFrame(audit)


def transform_forecast_steel(input_path=DEFAULT_INPUT, output_path=DEFAULT_OUTPUT, *, strict_blanks=False):
    """Publish independent IAMC, country weights and an input/assumption record."""
    import yaml
    source, output = Path(input_path), Path(output_path)
    if output.resolve() == source.resolve():
        raise ValueError("The input workbook must remain unchanged.")
    with pd.ExcelFile(source) as workbook:
        if len(workbook.sheet_names) != 1:
            raise ValueError("Select a reviewed one-sheet production workbook; multiple sheets are ambiguous.")
        raw = pd.read_excel(workbook, sheet_name=0).dropna(how="all")
    policy = "error" if strict_blanks else yaml.safe_load(MAPPING_FILE.read_text(encoding="utf-8")).get("source_blank_policy", "error")
    iamc, shares = convert_frame(raw, blank_policy=policy)
    output.parent.mkdir(parents=True, exist_ok=True)
    iamc.to_csv(output, index=False, encoding="utf-8-sig")
    shares.to_csv(output.with_name(output.stem + "_country_shares.csv"), index=False)
    definitions = route_definitions()
    record = dict(source=str(source.resolve()), source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        route_mapping_sha256=hashlib.sha256(MAPPING_FILE.read_bytes()).hexdigest(),
        scenarios=sorted(iamc.scenario.unique()),
        source_scenarios={case.removeprefix(SCENARIO_PREFIX): case for case in sorted(iamc.scenario.unique())},
        countries=sorted(set(iamc.region) - {"RER"}),
        years=[int(c) for c in iamc if c.isdigit()], input_rows=len(raw), iamc_rows=len(iamc),
        blank_numeric_cells=int(raw[[c for c in raw if str(c).isdigit()]].isna().sum().sum()),
        source_blank_policy=policy,
        assumptions=["Missing route rows within a reported country are zero.",
            "Sparse numeric blanks mean inactive route under the explicit YAML assumption; --strict-blanks rejects them.",
            "RER covers supplied European countries, including GB, CH and NO. Missing countries are not estimated.",
            "RER and national production are alternative views and must not be summed.",
            "RER markets buy national same-grade steel markets using annual national production weights.",
            "Intermediate years interpolate production volumes before calculating country shares.",
            "BOF and NG-DRI CCS routes use native CCS inventories and their existing capture/energy assumptions.",
            "Scrap-EAF CCS remains an uncaptured scrap-route proxy; its capture benefit is unresolved.",
            "No additional capture percentage, energy penalty or storage credit is inferred from production labels.",
            "Unalloyed demand uses low-alloyed production inventories as a grade proxy.",
            "Native premise backgrounds remain; FORECAST production volumes do not replace process energy coefficients."],
        ccs_routes=[r["variable"] for r in definitions if r["ccs"]],
        ccs_inventories=[dict(variable=r["variable"], activity=r["activity"],
                             representation=r.get("ccs_inventory", "unresolved_proxy"))
                         for r in definitions if r["ccs"]])
    output.with_suffix(".json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    if output.resolve() == DEFAULT_OUTPUT.resolve():
        write_integration_files(iamc, definitions)
    return dict(iamc_path=str(output), scenario_names=record["scenarios"],
                country_count=len(record["countries"]), iamc_rows=len(iamc))


def write_integration_files(iamc, definitions):
    """Keep the premise mapping and descriptor connected to the route YAML."""
    import yaml
    extra = yaml.safe_load(MAPPING_FILE.read_text(encoding="utf-8")).get("regionalize_extra", [])
    originals = list(dict.fromkeys([r["provider"] for r in definitions] + extra))
    paths = {r["alias"]: {
        "production volume": {"variable": r["variable"]},
        "ecoinvent alias": {"name": r["activity"], "reference product": "steel, low-alloyed",
                             "exists in original database": r.get("exists_in_original_database", False),
                             **({"regionalize": True} if not r.get("exists_in_original_database", False) else {})},
    } for r in definitions}
    markets = []
    for grade in ("low-alloyed", "unalloyed"):
        product = f"steel, {grade}"
        markets.append({"name": f"market for {product} (FORECAST-sites)", "reference product": product,
            "unit": "kilogram", "except regions": ["World"], "includes": list(paths),
            "replaces": [{"name": f"market for {product}", "product": product}],
            "replaces in": [{"excludes": {"location": loc}} for loc in ("GLO", "RoW", "World")]})
    config = {"regionalize": {"datasets": [{"name": name, "reference product": "steel, low-alloyed",
              "exists in original database": True} for name in originals]},
              "production pathways": paths, "markets": markets}
    header = ("# Generated by 0_convert_FORECAST_steel_to_IAMC.py from forecast_steel_routes.yaml.\n"
              "# BOF/NG-DRI CCS use native CCS inventories. Scrap-EAF CCS remains unresolved.\n"
              "# Native capture assumptions are proxies pending FORECAST-specific coefficients.\n"
              "# RER country supply is applied by the repository post-update export hook.\n"
              "# RER is a second view of country production, never additional output.\n")
    config_path = ROOT / "configuration_file/config_forecast_steel.yaml"
    config_path.write_text(header + yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    descriptor = {"profile": "data-package", "name": "forecast-steel",
        "title": "FORECAST-sites national steel production and European market mix",
        "description": "Country steel route outputs and production-weighted national markets at RER. Native BOF/NG-DRI CCS inventories; scrap-EAF CCS remains unresolved.",
        "version": "0.2.0", "dependencies": {"premise": ">=2.3.8"},
        "ecoinvent": {"version": "3.12", "system model": "cut-off"},
        "scenarios": sorted(iamc.scenario.unique()),
        "source_scenarios": {case.removeprefix(SCENARIO_PREFIX): case for case in sorted(iamc.scenario.unique())},
        "steel_country_market_mix": {"version": 1, "region": "RER", "variable_prefix": SUPPLY_PREFIX,
            "total_variable": "Production|Steel", "markets": [
                {k: m[k] for k in ("name", "reference product", "unit")} for m in markets]},
        "resources": [
            {"path": str(DEFAULT_OUTPUT.relative_to(ROOT)).replace("\\", "/"),
             "profile": "tabular-data-resource", "name": "scenario_data", "format": "csv",
             "mediatype": "text/csv", "encoding": "utf-8-sig",
             "schema": {"fields": [{"name": c, "type": "number" if c.isdigit() else "string"} for c in iamc],
                        "missingValues": [""]}},
            {"path": "configuration_file/config_forecast_steel.yaml", "profile": "data-resource",
             "name": "config", "format": "yaml", "mediatype": "text/yaml", "encoding": "utf-8"},
            {"path": "inventories/lci-forecast_steel.xlsx", "profile": "data-resource", "name": "inventories",
             "format": "xlsx", "mediatype": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"},
        ]}
    (ROOT / "datapackages/datapackage_forecast_steel.json").write_text(
        json.dumps(descriptor, indent=2) + "\n", encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--strict-blanks", action="store_true", help="Reject numeric blanks instead of the declared inactive-route assumption.")
    options = parser.parse_args(argv)
    print(json.dumps(transform_forecast_steel(options.input, options.output, strict_blanks=options.strict_blanks), indent=2))


if __name__ == "__main__":
    main()
