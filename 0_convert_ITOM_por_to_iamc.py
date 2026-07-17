from __future__ import annotations

import pandas as pd


# ----------------------------
# Configuration
# ----------------------------

ENERGY_PRODUCTS_TJ = {"electricity", "steam", "HT_heat"}

DEFAULT_REGION_MAP = {
    "Rotterdam": "NL",
}

OUTPUT_PRODUCTS = {
    "HDPE", "LDPE", "LLDPE", "PVC", "polyvinyl_chloride",
    "PS", "PS-E", "PET", "PC", "PA 6", "PA 6.6",
    "ABS", "PMMA", "PBR", "SBR", "PUR", "polyols", "TDI", "MDI",
    "nylon_6_6", "PLA",
    "acrylonitrile", "adipic_acid", "ethylene_oxide",
    "isopropanol", "polypropylene",
}

SKIP_PRODUCTS = {
    "CO2_allowance",
    "avoided_incineration_CO2",
    "stored_CO2",
    "unavoidable_CO2",
    "flue_gas_CO2",
    "uncompressed_CO2",
    "syngas_CO",
    "syngas_CO2",
    "syngas_H2",
}


# ----------------------------
# Helpers
# ----------------------------

def infer_unit_from_product(product: str) -> str:
    return "TJ/yr" if str(product) in ENERGY_PRODUCTS_TJ else "kt/yr"


def build_variable(
    product: str,
    technology: str,
    mode: str | int | None,
    location: str | None,
    include_mode: bool,
    include_location_in_variable: bool,
    product_type_map: dict[str, str],
    output_products: set[str] | None = None,
) -> str:

    ptype = (product_type_map.get(product) or "").strip()

    if ptype == "outputs":
        category = "Final Product"
    elif ptype == "intermediates, by-products":
        category = "Intermediate Product_by_product"
    elif ptype == "HVC":
        category = "Intermediate Product"
    elif ptype == "material input":
        category = "Material Input"
    elif ptype == "energy input":
        category = "Energy Input"
    elif ptype == "CO2-related":
        category = "CO2 Related"
    else:
        category = (
            "Final Product"
            if output_products and product in output_products
            else "Intermediate Product"
        )

    base = f"{category}|{product}|{technology}"

    if include_mode and mode is not None:
        base += f"|Mode {mode}"

    if include_location_in_variable and location is not None:
        base += f"|{location}"

    return base


# ----------------------------
# Production IAMC export
# ----------------------------

def to_iamc_production_volume(
    df: pd.DataFrame,
    product_type_map: dict[str, str],
    value_col: str = "LocalProductionByMode",
    location_col: str = "LOCATION",
    tech_col: str = "TECHNOLOGY",
    product_col: str = "PRODUCT",
    mode_col: str = "MODE_OF_OPERATION",
    year_col: str = "YEAR",
    model: str = "Petchem",
    scenario: str = "Carbon Looping (CL)",
    run_id: str | None = "Petchem_CL_240826_11",
    region_map: dict[str, str] | None = None,
    annualize_time_step_years: int = 5,
    include_mode: bool = True,
    include_location_in_variable: bool = False,
) -> pd.DataFrame:

    region_map = region_map or DEFAULT_REGION_MAP

    required = {location_col, tech_col, product_col, year_col, value_col}
    if include_mode:
        required.add(mode_col)

    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Missing required production columns: {sorted(missing)}")

    d = df.copy()
    d[year_col] = pd.to_numeric(d[year_col], errors="raise").astype(int)
    d[value_col] = pd.to_numeric(d[value_col], errors="coerce")

    if annualize_time_step_years and annualize_time_step_years != 1:
        d[value_col] = d[value_col] / float(annualize_time_step_years)

    d["region"] = d[location_col].map(region_map).fillna(d[location_col])
    d["unit"] = d[product_col].apply(infer_unit_from_product)

    d["variables"] = d.apply(
        lambda r: build_variable(
            product=r[product_col],
            technology=r[tech_col],
            mode=r[mode_col] if include_mode else None,
            location=r[location_col],
            include_mode=include_mode,
            include_location_in_variable=include_location_in_variable,
            product_type_map=product_type_map,
            output_products=OUTPUT_PRODUCTS,
        ),
        axis=1,
    )

    d["model"] = model
    d["scenario"] = f"{scenario}|{run_id}" if run_id else scenario

    iamc = (
        d.pivot_table(
            index=["model", "scenario", "region", "variables", "unit"],
            columns=year_col,
            values=value_col,
            aggfunc="sum",
        )
        .reset_index()
    )

    year_cols = sorted([c for c in iamc.columns if isinstance(c, int)])
    return iamc[["model", "scenario", "region", "variables", "unit"] + year_cols]


# ----------------------------
# Trade / transport IAMC export
# ----------------------------

def to_iamc_trade_volume(
    df: pd.DataFrame,
    value_col: str = "Transport",
    product_col: str = "PRODUCT",
    year_col: str = "YEAR",
    model: str = "Petchem",
    scenario: str = "Carbon Looping (CL)",
    run_id: str | None = "Petchem_CL_240826_11",
    region: str = "NL",
    annualize_time_step_years: int = 5,
    trade_direction: str = "To PoR",
) -> pd.DataFrame:

    required = {product_col, year_col, value_col}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Missing required trade columns: {sorted(missing)}")

    d = df.copy()
    d[year_col] = pd.to_numeric(d[year_col], errors="raise").astype(int)
    d[value_col] = pd.to_numeric(d[value_col], errors="coerce")

    # Exports from PoR are negative
    if trade_direction.lower() == "from por":
        d[value_col] *= -1

    if annualize_time_step_years and annualize_time_step_years != 1:
        d[value_col] = d[value_col] / float(annualize_time_step_years)

    d["model"] = model
    d["scenario"] = f"{scenario}|{run_id}" if run_id else scenario
    d["region"] = region
    d["unit"] = "kt/yr"

    # Important:
    # We only keep product-level trade variables.
    # LOCATION_1, LOCATION_2 and TRANSPORTMODE are intentionally excluded,
    # so all routes and carriers are summed together.
    d["variables"] = (
        "Trade|"
        + trade_direction
        + "|"
        + d[product_col].astype(str)
    )

    iamc_trade = (
        d.pivot_table(
            index=["model", "scenario", "region", "variables", "unit"],
            columns=year_col,
            values=value_col,
            aggfunc="sum",
        )
        .reset_index()
    )

    year_cols = sorted([c for c in iamc_trade.columns if isinstance(c, int)])
    return iamc_trade[["model", "scenario", "region", "variables", "unit"] + year_cols]


# ----------------------------
# Main
# ----------------------------

if __name__ == "__main__":

    INPUT_XLSX = "scenario_data/TRANSIENCE-WP8_ITOM-petchem_data_CL-scenario_2026-04-15.xlsx"

    PRODUCTION_SHEET = "production_volume"
    TRADE_TO_POR_SHEET = "transport_to_PoR"
    TRADE_FROM_POR_SHEET = "transport_from_PoR"

    MODEL = "Petchem"
    SCENARIO = "Carbon Looping (CL)"
    RUN_ID = "Petchem_CL_240826_11"

    df_prod = pd.read_excel(INPUT_XLSX, sheet_name=PRODUCTION_SHEET)

    products_df = pd.read_excel(INPUT_XLSX, sheet_name="products")
    product_type_map = dict(zip(products_df["PRODUCT"], products_df["type"]))

    iamc_prod = to_iamc_production_volume(
        df_prod,
        product_type_map=product_type_map,
        region_map=DEFAULT_REGION_MAP,
        include_mode=False,
        include_location_in_variable=False,
        annualize_time_step_years=5,
        model=MODEL,
        scenario=SCENARIO,
        run_id=RUN_ID,
    )

    df_trade_to_por = pd.read_excel(INPUT_XLSX, sheet_name=TRADE_TO_POR_SHEET)

    iamc_trade_to_por = to_iamc_trade_volume(
        df_trade_to_por,
        model=MODEL,
        scenario=SCENARIO,
        run_id=RUN_ID,
        region="NL",
        annualize_time_step_years=5,
        trade_direction="To PoR",
    )

    df_trade_from_por = pd.read_excel(INPUT_XLSX, sheet_name=TRADE_FROM_POR_SHEET)

    iamc_trade_from_por = to_iamc_trade_volume(
        df_trade_from_por,
        model=MODEL,
        scenario=SCENARIO,
        run_id=RUN_ID,
        region="NL",
        annualize_time_step_years=5,
        trade_direction="From PoR",
    )

    iamc = pd.concat(
        [iamc_prod, iamc_trade_to_por, iamc_trade_from_por],
        ignore_index=True,
        sort=False,
    )

    iamc = iamc.fillna(0)

    iamc["scenario"] = iamc["scenario"].replace({
        "Carbon Looping (CL)|Petchem_CL_240826_11": "CL"
    })

    IAMC_OUT = "scenario_data/scenario_data_itom_por.csv"
    iamc.to_csv(IAMC_OUT, index=False)

    print(f"Wrote {IAMC_OUT}")
    print(f"Production variables: {len(iamc_prod)}")
    print(f"Trade-to-PoR variables: {len(iamc_trade_to_por)}")
    print(f"Trade-from-PoR variables: {len(iamc_trade_from_por)}")
    print(f"Total IAMC rows: {len(iamc)}")