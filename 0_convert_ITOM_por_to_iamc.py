"""
TRANSIENCE – IAMC export (single sheet) + category flag + LCI mapping candidate helper

What this script does (to avoid LCA double counting mistakes later):
- Exports ONE IAMC-style table (wide years) for Production variables from ITOM for the PoR scenario
- Adds an extra column `product_category` to flag each variable as:
    - "final_output" (products with exogenous demand, i.e., functional unit candidates)
    - "intermediate" (internal flows; do NOT put in final demand)
    - "energy" (electricity/steam/HT_heat; usually not final demand here)
    - "skip" (CO2/ETS bookkeeping etc.; should not be mapped to LCI)

Then:
- Generates `mapping_candidates.csv` with candidate ecoinvent activities for rows that are not "skip".

IMPORTANT:
IAMC has no official "product_category" column, but adding it is a practical and safe extension
for QA and for driving your later LCA workflow.
"""

from __future__ import annotations

import re
import pandas as pd
from pathlib import Path


# ----------------------------
# Configuration
# ----------------------------

ENERGY_PRODUCTS_TJ = {"electricity", "steam", "HT_heat"}

DEFAULT_REGION_MAP = {
    "Rotterdam": "NL",  # or "West"
}

# Products with defined quantitative demand ("outputs" in your documentation).
# Use EXACT names as they appear in your PRODUCT column.
OUTPUT_PRODUCTS = {
    # Polymers
    "HDPE", "LDPE", "LLDPE", "PVC", 'polyvinyl_chloride', 
    "PS", "PS-E", "PET", "PC", "PA 6", "PA 6.6", #"PTA",
    "ABS", "PMMA", "PBR", "SBR", "PUR", "polyols", "TDI", "MDI", "nylon_6_6", "PLA",

    # Demanded intermediates
    "acrylonitrile", "adipic_acid", "ethylene_oxide", "isopropanol", 'polypropylene'
}

# Bookkeeping / dummy products that you typically do NOT map to ecoinvent
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
    """Return IAMC unit string based on product naming convention."""
    return "TJ/yr" if str(product) in ENERGY_PRODUCTS_TJ else "kt/yr"


def classify_product_category(product: str, technology: str | None = None) -> str:
    """
    Category flag used to prevent LCA mistakes later.
    - skip: bookkeeping, ETS, CO2 accounting
    - energy: electricity/steam/HT_heat carriers
    - final_output: products with exogenous demand
    - intermediate: everything else (internal chain flows by default)
    """
    p = str(product)
    t = str(technology) if technology is not None else ""

    if p in SKIP_PRODUCTS or t == "EU_ETS":
        return "skip"
    if p in ENERGY_PRODUCTS_TJ:
        return "energy"
    if p in OUTPUT_PRODUCTS:
        return "final_output"
    return "intermediate"

def build_variable(
    product: str,
    technology: str,
    mode: str | int | None,
    location: str | None,
    include_mode: bool,
    include_location_in_variable: bool,
    product_type_map: dict[str, str],          # FIXED type
    output_products: set[str] | None = None,   # optional fallback
) -> str:
    """
    Production|<Category>|<product>|<technology>[|Mode x][|<location>]
    Category is primarily derived from product_type_map (your products sheet).
    """

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
        # fallback if missing
        category = "Final Product" if (output_products and product in output_products) else "Intermediate Product"

    base = f"{category}|{product}|{technology}"

    if include_mode and mode is not None:
        base += f"|Mode {mode}"
    if include_location_in_variable and location is not None:
        base += f"|{location}"

    return base

# ----------------------------
# IAMC export (single sheet with category column)
# ----------------------------

def to_iamc_production_volume(
    df: pd.DataFrame,
    product_type_map: dict[str, str],          # <-- ADD THIS (FIX)
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
        raise ValueError(f"Missing required columns: {sorted(missing)}")

    d = df.copy()
    d[year_col] = pd.to_numeric(d[year_col], errors="raise").astype(int)
    d[value_col] = pd.to_numeric(d[value_col], errors="coerce")

    if annualize_time_step_years and annualize_time_step_years != 1:
        d[value_col] = d[value_col] / float(annualize_time_step_years)

    d["region"] = d[location_col].map(region_map).fillna(d[location_col])
    d["unit"] = d[product_col].apply(infer_unit_from_product)

    # FIX: pass product_type_map; REMOVE unsupported kwargs
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

    d["product_category"] = d.apply(
        lambda r: classify_product_category(r[product_col], r[tech_col]),
        axis=1,
    )

    d["model"] = model
    d["scenario"] = f"{scenario}|{run_id}" if run_id else scenario

    iamc = (
        d.pivot_table(
            index=["model", "scenario", "region", "variables", "unit"],#, "product_category"],
            columns=year_col,
            values=value_col,
            aggfunc="sum",
        )
        .reset_index()
    )

    year_cols = sorted([c for c in iamc.columns if isinstance(c, int)])
    return iamc[["model", "scenario", "region", "variables", "unit"] + year_cols]


# ----------------------------
# Main
# ----------------------------

if __name__ == "__main__":
    # ---- 1) Build IAMC (single sheet, with product_category column)
    INPUT_XLSX = "scenario_data/TRANSIENCE-WP8_ITOM-petchem_data_CL-scenario_2026-02-19.xlsx"
    SHEET = "production_volume"

    df = pd.read_excel(INPUT_XLSX, sheet_name=SHEET)
    products_df = pd.read_excel(INPUT_XLSX, sheet_name="products")
    product_type_map = dict(zip(products_df["PRODUCT"], products_df["type"]))

    iamc = to_iamc_production_volume(
        df,
        product_type_map=product_type_map,   # <-- FIX
        region_map=DEFAULT_REGION_MAP,
        include_mode=False,
        include_location_in_variable=False,
        annualize_time_step_years=5,
        model="Petchem",
        scenario="Carbon Looping (CL)",
        run_id="Petchem_CL_240826_11",
    )
    iamc = iamc.fillna(0)

    iamc["scenario"] = iamc["scenario"].replace({"Carbon Looping (CL)|Petchem_CL_240826_11": "CL"})

    IAMC_OUT = "scenario_data/scenario_data_itom_por.csv"
    iamc.to_csv(IAMC_OUT, index=False)
    print(f"Wrote {IAMC_OUT}")