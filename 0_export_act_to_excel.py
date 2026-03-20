from __future__ import annotations

import difflib
from pathlib import Path

import pandas as pd
import bw2data as bd
import premise
import bw2data

from config import NAME_REF_DB, PROJECT_NAME
from regionalization import relink_technosphere_exchanges_full
import wurst as w


# =========================
# Config
# =========================
EI_DB_NAME = NAME_REF_DB    # change if needed
OUTPUT_FILE = "data\ecoinvent_312_selected_plastics.xlsx"
TARGET_LOCATION = "NL"
bw2data.projects.set_current(PROJECT_NAME)

REF_DB_W = w.extract_brightway2_databases(EI_DB_NAME, add_properties=False, add_identifiers=False)

TARGETS = [
    {
        "your_product": "BTX",
        "target_name": " BTX production, from pyrolysis gas, average",
        "target_reference_product": "benzene",
        "target_location": "RER",
    },

    {
        "your_product": "BTX",
        "target_name": " BTX production, from pyrolysis gas, average",
        "target_reference_product": "toluene, liquid",
        "target_location": "RER",
    },

    {
        "your_product": "BTX",
        "target_name": " BTX production, from pyrolysis gas, average",
        "target_reference_product": "p-xylene",
        "target_location": "RER",
    },


    {
        "your_product": "aniline",
        "target_name": "aniline production",
        "target_reference_product": "aniline",
        "target_location": "RER",
    },

    {
        "your_product": "butadiene",
        "target_name": "polybutadiene production, solution polymerization",
        "target_reference_product": "polybutadiene",
        "target_location": "RER",
    },
    {
        "your_product": "butadiene",
        "target_name": "market for butadiene",
        "target_reference_product": "butadiene",
        "target_location": "RER w/o RU",
    },
    {
        "your_product": "benzene",
        "target_name": "market for benzene",
        "target_reference_product": "benzene",
        "target_location": "RER",
    },

    {
        "your_product": "MDI",
        "target_name": "methylene diphenyl diisocyanate production",
        "target_reference_product": "methylene diphenyl diisocyanate",
        "target_location": "RER",
    },
    {
        "your_product": "PBR",
        "target_name": "butadiene rubber production",
        "target_reference_product": "butadiene rubber",
        "target_location": "RER",
    },
    {
        "your_product": "PBS",
        "target_name": "polybutylene succinate production",
        "target_reference_product": "polybutylene succinate",
        "target_location": "RER",
    },
    {
        "your_product": "PET",
        "target_name": "polyethylene terephthalate production, granulate",
        "target_reference_product": "polyethylene terephthalate",
        "target_location": "RER",
    },
    {
        "your_product": "PLA",
        "target_name": "polylactide production",
        "target_reference_product": "polylactide",
        "target_location": "RER",
    },
    {
        "your_product": "PMMA",
        "target_name": "polymethyl methacrylate production",
        "target_reference_product": "polymethyl methacrylate",
        "target_location": "RER",
    },
    {
        "your_product": "PTA",
        "target_name": "terephthalic acid production, purified",
        "target_reference_product": "terephthalic acid",
        "target_location": "RER",
    },
    {
        "your_product": "nylon_6_6",
        "target_name": "nylon 6-6 production",
        "target_reference_product": "nylon 6-6",
        "target_location": "RER",
    },
    {
        "your_product": "polyols",
        "target_name": "polyether polyol production",
        "target_reference_product": "polyether polyol",
        "target_location": "RER",
        "variant": "polyol_default",
    },
    {
        "your_product": "polypropylene",
        "target_name": "polypropylene production, granulate",
        "target_reference_product": "polypropylene",
        "target_location": "RER",
    },
    {
        "your_product": "polyvinyl_chloride",
        "target_name": "polyvinylchloride production, bulk polymerised",
        "target_reference_product": "polyvinylchloride",
        "target_location": "RER",
    },
    {
        "your_product": "ethylene",
        "target_name": "light olefins production, from methanol-to-olefins conversion, ethylene-to-propylene ratio of 1.51",
        "target_reference_product": "ethylene",
        "target_location": "CN",
    },
    {
        "your_product": "unsaturated hydrocarbons production, steam cracking operation, average",
        "target_name": "unsaturated hydrocarbons production, steam cracking operation, average",
        "target_reference_product": "ethylene",
        "target_location": "RER w/o RU",
    },
]

LOCATION_FALLBACKS = ["NL", "CH", "RER", "Europe without Switzerland", "RoW", "GLO"]


# =========================
# Setup
# =========================
bd.projects.set_current(PROJECT_NAME)
print("premise version:", premise.__version__)
print("project:", PROJECT_NAME)

if EI_DB_NAME not in bd.databases:
    raise ValueError(
        f"Database '{EI_DB_NAME}' not found in project '{PROJECT_NAME}'. "
        f"Available databases: {list(bd.databases)}"
    )

db = bd.Database(EI_DB_NAME)


# =========================
# Helpers
# =========================
def convert_tuple_to_string(value) -> str:
    if isinstance(value, (tuple, list)):
        return "::".join(str(x) for x in value)
    return "" if value is None else str(value)


def normalize(text: str) -> str:
    return " ".join(str(text).strip().lower().split())


def safe_get(obj, key, default=""):
    try:
        return obj.get(key, default)
    except Exception:
        return default


def location_penalty(location: str, target_location: str | None) -> int:
    if not location:
        return 999
    if target_location and location == target_location:
        return 0
    if location in LOCATION_FALLBACKS:
        return LOCATION_FALLBACKS.index(location) + 1
    return 100


def score_activity(
    act,
    target_name: str,
    target_reference_product: str | None = None,
    target_location: str | None = None,
) -> float:
    act_name = normalize(act.get("name", ""))
    tgt_name = normalize(target_name)

    act_ref = normalize(act.get("reference product", ""))
    tgt_ref = normalize(target_reference_product) if target_reference_product else None

    act_loc = act.get("location", "")
    score = 0.0

    name_sim = difflib.SequenceMatcher(None, act_name, tgt_name).ratio()
    score += 5.0 * name_sim
    if act_name == tgt_name:
        score += 5.0
    elif tgt_name in act_name or act_name in tgt_name:
        score += 1.0

    if tgt_ref:
        ref_sim = difflib.SequenceMatcher(None, act_ref, tgt_ref).ratio()
        score += 2.5 * ref_sim
        if act_ref == tgt_ref:
            score += 2.5
        elif tgt_ref in act_ref or act_ref in tgt_ref:
            score += 0.5

    if target_location:
        if act_loc == target_location:
            score += 4.0
        else:
            penalty = location_penalty(act_loc, target_location)
            score += max(0.0, 2.0 - 0.2 * penalty)

    if "production" in tgt_name and "market for" in act_name:
        score -= 2.0

    return score


def find_best_activity(
    database: bd.Database,
    target_name: str,
    target_reference_product: str | None = None,
    target_location: str | None = None,
):
    all_acts = list(database)
    target_name_norm = normalize(target_name)
    target_ref_norm = normalize(target_reference_product) if target_reference_product else None

    candidates = [
        act for act in all_acts
        if normalize(act.get("name", "")) == target_name_norm
        and (target_location is None or act.get("location", "") == target_location)
    ]
    if candidates:
        if target_ref_norm:
            candidates.sort(
                key=lambda a: normalize(a.get("reference product", "")) != target_ref_norm
            )
        return candidates[0], "exact_name_exact_location"

    candidates = [
        act for act in all_acts
        if normalize(act.get("name", "")) == target_name_norm
    ]
    if candidates:
        candidates.sort(
            key=lambda a: (
                location_penalty(a.get("location", ""), target_location),
                normalize(a.get("reference product", "")) != target_ref_norm if target_ref_norm else False,
            )
        )
        return candidates[0], "exact_name"

    candidates = [
        act for act in all_acts
        if target_name_norm in normalize(act.get("name", ""))
    ]
    if candidates:
        candidates.sort(
            key=lambda a: score_activity(
                a,
                target_name=target_name,
                target_reference_product=target_reference_product,
                target_location=target_location,
            ),
            reverse=True,
        )
        return candidates[0], "contains"

    scored = [
        (
            act,
            score_activity(
                act,
                target_name=target_name,
                target_reference_product=target_reference_product,
                target_location=target_location,
            ),
        )
        for act in all_acts
    ]
    scored.sort(key=lambda x: x[1], reverse=True)

    if scored and scored[0][1] >= 4.0:
        return scored[0][0], "fuzzy"

    return None, "not found"


def activity_to_dataset_dict(activity) -> dict:
    """
    Convert Brightway activity to a mutable dataset dict for regionalization.
    """
    ds = {
        "database": activity.key[0],
        "code": activity.key[1],
        "name": activity.get("name", ""),
        "reference product": activity.get("reference product", ""),
        "unit": activity.get("unit", ""),
        "location": activity.get("location", ""),
        "comment": activity.get("comment", ""),
        "production amount": activity.get("production amount", 1),
        "exchanges": [],
    }

    for exc in activity.exchanges():
        inp = exc.input
        exc_type = exc.get("type", "")

        input_name = safe_get(inp, "name", "")

        # Replace market group for electricity -> market for electricity
        if exc_type != "biosphere" and input_name.startswith("market group for electricity"):
            input_name = input_name.replace(
                "market group for electricity",
                "market for electricity",
                1,
            )

        exc_dict = {
            "name": input_name,
            "amount": safe_get(exc, "amount", ""),
            "location": safe_get(inp, "location", ""),
            "unit": safe_get(inp, "unit", safe_get(exc, "unit", "")),
            "type": exc_type,
            "comment": safe_get(exc, "comment", ""),
            "database": inp.key[0] if hasattr(inp, "key") else "",
            "code": inp.key[1] if hasattr(inp, "key") else "",
        }

        if exc_type == "biosphere":
            exc_dict["categories"] = safe_get(inp, "categories", ())
        else:
            exc_dict["reference product"] = safe_get(inp, "reference product", "")
            exc_dict["product"] = safe_get(inp, "reference product", "")

        ds["exchanges"].append(exc_dict)

    return ds

def get_exchange_row(exc: dict) -> dict:
    exc_type = exc.get("type", "")

    row = {
        "name": exc.get("name", ""),
        "amount": exc.get("amount", ""),
        "location": "",
        "unit": exc.get("unit", ""),
        "categories": "",
        "type": exc_type,
        "reference product": "",
        "comment": exc.get("comment", ""),
    }

    if exc_type == "biosphere":
        row["categories"] = convert_tuple_to_string(exc.get("categories", ""))
    else:
        row["location"] = exc.get("location", "")
        row["reference product"] = exc.get("reference product", exc.get("product", ""))

    return row


def build_metadata(activity_ds: dict, source: str) -> dict:
    return {
        "Activity": activity_ds.get("name", ""),
        "reference product": activity_ds.get("reference product", ""),
        "unit": activity_ds.get("unit", ""),
        "location": activity_ds.get("location", ""),
        "comment": activity_ds.get("comment", ""),

    }


def set_dataset_location(ds: dict, new_location: str) -> dict:
    """
    Update dataset location and production exchange location(s).
    """
    ds["location"] = new_location

    for exc in ds.get("exchanges", []):
        if exc.get("type") == "production":
            exc["location"] = new_location

    return ds


# =========================
# Export
# =========================
with pd.ExcelWriter(OUTPUT_FILE, engine="xlsxwriter") as writer:
    workbook = writer.book
    worksheet = workbook.add_worksheet("lci")
    writer.sheets["lci"] = worksheet

    bold = workbook.add_format({"bold": True})
    row_offset = 0
    max_widths = {}

    for item in TARGETS:
        target_name = item["target_name"]
        target_reference_product = item.get("target_reference_product")
        target_location = item.get("target_location")

        activity, match_type = find_best_activity(
            db,
            target_name=target_name,
            target_reference_product=target_reference_product,
            target_location=target_location,
        )

        if activity is None:
            print(f"[NOT FOUND] {target_name} | {target_location}")
            continue

        print(
            f"[FOUND] {activity.get('name', '')} | "
            f"{activity.get('reference product', '')} | "
            f"{activity.get('location', '')} | {match_type}"
        )

        # Convert to mutable dataset
        ds = activity_to_dataset_dict(activity)

        # Change activity and production exchange location
        ds = set_dataset_location(ds, TARGET_LOCATION)

        # Relink technosphere exchanges using imported function
        ds = relink_technosphere_exchanges_full(
            ds=ds,
            data=REF_DB_W,
            contained=False,
        )

        metadata = build_metadata(
            ds,
            source=f"{EI_DB_NAME}; match_type={match_type}; regionalized_to={TARGET_LOCATION}",
        )

        exchanges_df = pd.DataFrame([get_exchange_row(exc) for exc in ds["exchanges"]])

        for idx, (key, value) in enumerate(metadata.items()):
            fmt = bold if key == "Activity" else None
            worksheet.write(row_offset + idx, 0, key, fmt)
            worksheet.write(row_offset + idx, 1, value, fmt)
            max_widths[0] = max(max_widths.get(0, 0), len(str(key)))
            max_widths[1] = max(max_widths.get(1, 0), len(str(value)))

        row_offset += len(metadata)

        worksheet.write(row_offset, 0, "Exchanges", bold)
        max_widths[0] = max(max_widths.get(0, 0), len("Exchanges"))
        row_offset += 1

        for col_idx, column in enumerate(exchanges_df.columns):
            worksheet.write(row_offset, col_idx, column, bold)
            max_widths[col_idx] = max(max_widths.get(col_idx, 0), len(str(column)))

        for row_idx, row in enumerate(exchanges_df.itertuples(index=False)):
            for col_idx, cell_value in enumerate(row):
                worksheet.write(row_offset + 1 + row_idx, col_idx, cell_value)
                max_widths[col_idx] = max(max_widths.get(col_idx, 0), len(str(cell_value)))

        row_offset += 1 + len(exchanges_df)
        row_offset += 1

    for col_idx, width in max_widths.items():
        worksheet.set_column(col_idx, col_idx, min(width + 2, 45))

print(f"\nExported regionalized activities to: {Path(OUTPUT_FILE).resolve()}")