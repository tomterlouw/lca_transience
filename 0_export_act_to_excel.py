from __future__ import annotations

import argparse
import difflib
import json
import sys
from pathlib import Path

import pandas as pd
from config import NAME_REF_DB, PROJECT_NAME
from supplier_resolution import ReferenceSupplierResolver


# =========================
# Config
# =========================

EI_DB_NAME = NAME_REF_DB
ROOT = Path(__file__).resolve().parent
OUTPUT_FILE = ROOT / "data/ecoinvent_312_selected_plastics.xlsx"
IAM_MODEL = "remind"

# Used when "regionalize_to" is not explicitly provided for a TARGET.
# Set to None if you prefer no regionalization by default.
DEFAULT_REGIONALIZE_TO = "NL"

# =========================
# Target activities
# =========================
#
# target_location:
#     Location of the ORIGINAL activity that should preferably be
#     selected from ecoinvent.
#
# regionalize_to:
#     Location to which the copied activity should be regionalized.
#
# Examples:
#     "NL"  -> regionalize to Netherlands
#     "MA"  -> regionalize to Morocco
#     "DE"  -> regionalize to Germany
#     None  -> keep original location and do not relink
#
# If regionalize_to is omitted, DEFAULT_REGIONALIZE_TO is used.
# =========================

TARGETS = [

    {
        "your_product": "BTX",
        "target_name": " BTX production, from pyrolysis gas, average",
        "target_reference_product": "benzene",
        "target_location": "RER",
        "regionalize_to": "NL",
    },

    {
        "your_product": "BTX",
        "target_name": " BTX production, from pyrolysis gas, average",
        "target_reference_product": "toluene, liquid",
        "target_location": "RER",
        "regionalize_to": "NL",
    },

    {
        "your_product": "BTX",
        "target_name": " BTX production, from pyrolysis gas, average",
        "target_reference_product": "p-xylene",
        "target_location": "RER",
        "regionalize_to": "NL",
    },

    {
        "your_product": "aniline",
        "target_name": "aniline production",
        "target_reference_product": "aniline",
        "target_location": "RER",
        "regionalize_to": "NL",
    },

    {
        "your_product": "butadiene",
        "target_name": "polybutadiene production, solution polymerization",
        "target_reference_product": "polybutadiene",
        "target_location": "RER",
        "regionalize_to": "NL",
    },

    {
        "your_product": "butadiene",
        "target_name": "market for butadiene",
        "target_reference_product": "butadiene",
        "target_location": "RER w/o RU",
        "regionalize_to": "NL",
    },

    {
        "your_product": "benzene",
        "target_name": "market for benzene",
        "target_reference_product": "benzene",
        "target_location": "RER",
        "regionalize_to": "NL",
    },

    {
        "your_product": "MDI",
        "target_name": "methylene diphenyl diisocyanate production",
        "target_reference_product": "methylene diphenyl diisocyanate",
        "target_location": "RER",
        "regionalize_to": "NL",
    },

    {
        "your_product": "PBR",
        "target_name": "polybutadiene production, solution polymerization",
        "target_reference_product": "polybutadiene",
        "target_location": "RER",
        "regionalize_to": "NL",
    },

    {
        "your_product": "PBS",
        "target_name": "polybutylene succinate production",
        "target_reference_product": "polybutylene succinate",
        "target_location": "RER",
        "regionalize_to": "NL",
    },

    {
        "your_product": "PET",
        "target_name": "polyethylene terephthalate production, granulate, amorphous",
        "target_reference_product": "polyethylene terephthalate, granulate, amorphous",
        "target_location": "RER",
        "regionalize_to": "NL",
    },

    {
        "your_product": "PLA",
        "target_name": "polylactic acid production, granulate",
        "target_reference_product": "polylactic acid, granulate",
        "target_location": "GLO",
        "regionalize_to": "NL",
    },

    {
        "your_product": "PMMA",
        "target_name": "polymethyl methacrylate production",
        "target_reference_product": "polymethyl methacrylate",
        "target_location": "RER",
        "regionalize_to": "NL",
    },

    {
        "your_product": "PTA",
        "target_name": "purified terephthalic acid production",
        "target_reference_product": "purified terephthalic acid",
        "target_location": "RER",
        "regionalize_to": "NL",
    },

    {
        "your_product": "nylon_6_6",
        "target_name": "nylon 6-6 production",
        "target_reference_product": "nylon 6-6",
        "target_location": "RER",
        "regionalize_to": "NL",
    },

    {
        "your_product": "polyols",
        "target_name": "polyether polyols production, long chain",
        "target_reference_product": "polyether polyols, long chain",
        "target_location": "RER",
        "regionalize_to": "NL",
        "variant": "polyol_default",
    },

    {
        "your_product": "polypropylene",
        "target_name": "polypropylene production, granulate",
        "target_reference_product": "polypropylene, granulate",
        "target_location": "RER",
        "regionalize_to": "NL",
    },

    {
        "your_product": "polyvinyl_chloride",
        "target_name": "polyvinylchloride production, bulk polymerised",
        "target_reference_product": "polyvinylchloride",
        "target_location": "RER",
        "regionalize_to": "NL",
    },

    {
        "your_product": "ethylene",
        "target_name": (
            "light olefins production, from methanol-to-olefins conversion, "
            "ethylene-to-propylene ratio of 1.51"
        ),
        "target_reference_product": "ethylene",
        "target_location": "CN",
        "regionalize_to": "NL",
    },

    {
        "your_product": (
            "unsaturated hydrocarbons production, steam cracking operation, average"
        ),
        "target_name": (
            "unsaturated hydrocarbons production, steam cracking operation, average"
        ),
        "target_reference_product": "ethylene",
        "target_location": "RER w/o RU",
        "regionalize_to": "NL",
    },

    {
        "your_product": (
            "polyvinyl chloride production, suspension polymerisation"
        ),
        "target_name": (
            "polyvinyl chloride production, suspension polymerisation"
        ),
        "target_reference_product": (
            "polyvinyl chloride, suspension polymerised"
        ),
        "target_location": "RER",
        "regionalize_to": "NL",
    },

    # ---------------------------------------------------------
    # DAC-based e-methanol import chain
    #
    # Source inventories are selected from RER where available,
    # but production is regionalized to Morocco (MA), representing
    # a renewable-resource-rich non-EU exporting region.
    # ---------------------------------------------------------

    {
        "your_product": (
            "methanol distillation, hydrogen from electrolysis, CO2 from DAC"
        ),
        "target_name": (
            "methanol distillation, hydrogen from electrolysis, CO2 from DAC"
        ),
        "target_reference_product": "methanol, purified",
        "target_location": "RER",
        "regionalize_to": "MA",
    },

    {
        "your_product": (
            "methanol synthesis, hydrogen from electrolysis, CO2 from DAC"
        ),
        "target_name": (
            "methanol synthesis, hydrogen from electrolysis, CO2 from DAC"
        ),
        "target_reference_product": "methanol, unpurified",
        "target_location": "RER",
        "regionalize_to": "MA",
    },

    {
        "your_product": (
            "carbon dioxide, captured, with a sorbent-based direct air "
            "capture system, 100ktCO2, with heat pump heat, and grid electricity"
        ),
        "target_name": (
            "carbon dioxide, captured, with a sorbent-based direct air "
            "capture system, 100ktCO2, with heat pump heat, and grid electricity"
        ),
        "target_reference_product": "carbon dioxide, captured",
        "target_location": "RER",
        "regionalize_to": "MA",
    },

    {
        "your_product": "carbon dioxide compression, transport and storage",
        "target_name": "carbon dioxide compression, transport and storage",
        "target_reference_product": "carbon dioxide, stored",
        "target_location": "RER",
        "regionalize_to": "NL",
    },

    {
        "your_product": (
            "steam production, as energy carrier, in chemical industry"
        ),
        "target_name": (
            "steam production, as energy carrier, in chemical industry"
        ),
        "target_reference_product": (
            "heat, from steam, in chemical industry"
        ),
        "target_location": "RER",
        "regionalize_to": "NL",
    },
]


# Locations used when ranking alternative source datasets.
LOCATION_FALLBACKS = [
    "NL",
    "CH",
    "RER",
    "Europe without Switzerland",
    "RoW",
    "GLO",
]


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


def location_penalty(
    location: str,
    target_location: str | None,
) -> int:

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

    act_ref = normalize(
        act.get("reference product", "")
    )

    tgt_ref = (
        normalize(target_reference_product)
        if target_reference_product
        else None
    )

    act_loc = act.get("location", "")

    score = 0.0

    # --------------------------------
    # Activity name
    # --------------------------------

    name_sim = difflib.SequenceMatcher(
        None,
        act_name,
        tgt_name,
    ).ratio()

    score += 5.0 * name_sim

    if act_name == tgt_name:
        score += 5.0

    elif tgt_name in act_name or act_name in tgt_name:
        score += 1.0

    # --------------------------------
    # Reference product
    # --------------------------------

    if tgt_ref:

        ref_sim = difflib.SequenceMatcher(
            None,
            act_ref,
            tgt_ref,
        ).ratio()

        score += 2.5 * ref_sim

        if act_ref == tgt_ref:
            score += 2.5

        elif tgt_ref in act_ref or act_ref in tgt_ref:
            score += 0.5

    # --------------------------------
    # Location
    # --------------------------------

    if target_location:

        if act_loc == target_location:
            score += 4.0

        else:
            penalty = location_penalty(
                act_loc,
                target_location,
            )

            score += max(
                0.0,
                2.0 - 0.2 * penalty,
            )

    # Avoid selecting a market when a production
    # activity was explicitly requested.
    if (
        "production" in tgt_name
        and "market for" in act_name
    ):
        score -= 2.0

    return score


def find_best_activity(
    database,
    target_name: str,
    target_reference_product: str | None = None,
    target_location: str | None = None,
):

    all_acts = list(database)

    target_name_norm = normalize(target_name)

    target_ref_norm = (
        normalize(target_reference_product)
        if target_reference_product
        else None
    )

    # A similar activity name is never enough to substitute another chemical.
    # Constrain every exact/contains/fuzzy stage to the requested product.
    if target_ref_norm:
        all_acts = [act for act in all_acts
                    if normalize(act.get("reference product", "")) == target_ref_norm]

    # --------------------------------
    # 1. Exact name + exact location
    # --------------------------------

    candidates = [
        act
        for act in all_acts
        if normalize(act.get("name", ""))
        == target_name_norm
        and (
            target_location is None
            or act.get("location", "")
            == target_location
        )
    ]

    if candidates:

        if target_ref_norm:

            candidates.sort(
                key=lambda a: (
                    normalize(
                        a.get(
                            "reference product",
                            "",
                        )
                    )
                    != target_ref_norm
                )
            )

        return (
            candidates[0],
            "exact_name_exact_location",
        )

    # --------------------------------
    # 2. Exact name, other location
    # --------------------------------

    candidates = [
        act
        for act in all_acts
        if normalize(act.get("name", ""))
        == target_name_norm
    ]

    if candidates:

        candidates.sort(
            key=lambda a: (
                location_penalty(
                    a.get("location", ""),
                    target_location,
                ),
                (
                    normalize(
                        a.get(
                            "reference product",
                            "",
                        )
                    )
                    != target_ref_norm
                    if target_ref_norm
                    else False
                ),
            )
        )

        return candidates[0], "exact_name"

    # --------------------------------
    # 3. Name contains target
    # --------------------------------

    candidates = [
        act
        for act in all_acts
        if target_name_norm
        in normalize(act.get("name", ""))
    ]

    if candidates:

        candidates.sort(
            key=lambda a: score_activity(
                a,
                target_name=target_name,
                target_reference_product=(
                    target_reference_product
                ),
                target_location=target_location,
            ),
            reverse=True,
        )

        return candidates[0], "contains"

    # --------------------------------
    # 4. Fuzzy match
    # --------------------------------

    scored = [
        (
            act,
            score_activity(
                act,
                target_name=target_name,
                target_reference_product=(
                    target_reference_product
                ),
                target_location=target_location,
            ),
        )
        for act in all_acts
    ]

    scored.sort(
        key=lambda x: x[1],
        reverse=True,
    )

    if scored and scored[0][1] >= 4.0:
        return scored[0][0], "fuzzy"

    return None, "not found"


def activity_to_dataset_dict(activity) -> dict:
    """
    Convert Brightway activity to a mutable dataset dictionary
    suitable for regionalization.
    """

    ds = {
        "database": activity.key[0],
        "code": activity.key[1],
        "name": activity.get("name", ""),
        "reference product": activity.get(
            "reference product",
            "",
        ),
        "unit": activity.get("unit", ""),
        "location": activity.get("location", ""),
        "comment": activity.get("comment", ""),
        "production amount": activity.get(
            "production amount",
            1,
        ),
        "exchanges": [],
    }

    for exc in activity.exchanges():

        inp = exc.input
        exc_type = exc.get("type", "")

        input_name = safe_get(
            inp,
            "name",
            "",
        )

        exc_dict = {
            "name": input_name,
            "amount": safe_get(
                exc,
                "amount",
                "",
            ),
            "location": safe_get(
                inp,
                "location",
                "",
            ),
            "unit": safe_get(
                inp,
                "unit",
                safe_get(
                    exc,
                    "unit",
                    "",
                ),
            ),
            "type": exc_type,
            "comment": safe_get(
                exc,
                "comment",
                "",
            ),
            "database": (
                inp.key[0]
                if hasattr(inp, "key")
                else ""
            ),
            "code": (
                inp.key[1]
                if hasattr(inp, "key")
                else ""
            ),
        }

        for field in ("uncertainty type", "loc", "scale", "shape", "minimum", "maximum", "negative"):
            if field in exc:
                exc_dict[field] = exc[field]

        if exc_type == "biosphere":

            exc_dict["categories"] = safe_get(
                inp,
                "categories",
                (),
            )

        else:

            exc_dict["reference product"] = (
                safe_get(
                    inp,
                    "reference product",
                    "",
                )
            )

            exc_dict["product"] = safe_get(
                inp,
                "reference product",
                "",
            )

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

        row["categories"] = (
            convert_tuple_to_string(
                exc.get(
                    "categories",
                    "",
                )
            )
        )

    else:

        row["location"] = exc.get(
            "location",
            "",
        )

        row["reference product"] = (
            exc.get(
                "reference product",
                exc.get(
                    "product",
                    "",
                ),
            )
        )

    for field in ("uncertainty type", "loc", "scale", "shape", "minimum", "maximum", "negative"):
        if field in exc:
            row[field] = exc[field]
    return row


def build_metadata(
    activity_ds: dict,
    source: str,
) -> dict:

    return {
        "Activity": activity_ds.get(
            "name",
            "",
        ),
        "reference product": (
            activity_ds.get(
                "reference product",
                "",
            )
        ),
        "unit": activity_ds.get(
            "unit",
            "",
        ),
        "location": activity_ds.get(
            "location",
            "",
        ),
        "comment": activity_ds.get(
            "comment",
            "",
        ),
        "source": source,
    }


def set_dataset_location(
    ds: dict,
    new_location: str,
) -> dict:
    """
    Update dataset location and production exchange location(s).
    """

    ds["location"] = new_location

    for exc in ds.get(
        "exchanges",
        [],
    ):

        if exc.get("type") == "production":
            exc["location"] = new_location

    return ds


# =========================
# Reference preparation and export
# =========================

def prepare_reference_inventories(
    database, reference_database, *, targets=None, model=IAM_MODEL,
    reference_name=EI_DB_NAME, verbose=True, strict_targets=False,
):
    """Resolve all selected copies before writing; never alter Brightway data."""
    resolver = ReferenceSupplierResolver(reference_database, model=model)
    prepared, audit, missing_targets = [], [], []
    # Reuse source activities rather than scanning Brightway for every target.
    activities = sorted(database, key=lambda act: (
        act.get("name", ""), act.get("reference product", ""),
        act.get("location", ""), str(getattr(act, "key", "")),
    ))
    for target_index, item in enumerate(TARGETS if targets is None else targets):
        activity, match_type = find_best_activity(
            activities, target_name=item["target_name"],
            target_reference_product=item.get("target_reference_product"),
            target_location=item.get("target_location"),
        )
        if activity is None:
            missing = {"target_index": target_index, "name": item["target_name"],
                       "reference_product": item.get("target_reference_product"),
                       "source_location": item.get("target_location"),
                       "reason": "No suitable activity with the requested reference product"}
            missing_targets.append(missing)
            message = f"Target not found: {item['target_name']} | {item.get('target_reference_product')}"
            if strict_targets:
                raise ValueError(message)
            if verbose:
                print(f"[NOT FOUND] {message}; skipped, recorded in JSON audit.")
            continue
        requested_product = item.get("target_reference_product")
        if requested_product and normalize(activity.get("reference product", "")) != normalize(requested_product):
            raise ValueError(f"Wrong reference product selected for {item['target_name']}: "
                             f"{activity.get('reference product')} != {requested_product}")
        source_location = activity.get("location", "")
        regionalize_to = item.get("regionalize_to", DEFAULT_REGIONALIZE_TO)
        dataset = activity_to_dataset_dict(activity)
        if regionalize_to is not None:
            dataset, rows = resolver.regionalize(dataset, regionalize_to, source_location=source_location)
            audit.extend(dict(row, target_index=target_index, match_type=match_type) for row in rows)
            regionalization_text = f"regionalized_to={regionalize_to}"
        else:
            regionalization_text = "not regionalized"
        metadata = build_metadata(dataset, source=(
            f"{reference_name}; match_type={match_type}; source_location={source_location}; "
            f"{regionalization_text}; iam_model={resolver.geography.model}; "
            f"topology_sha256={resolver.geography.sha256}; "
            f"ecoinvent_topology_sha256={resolver.geography.ecoinvent_sha256}"
        ))
        prepared.append({"dataset": dataset, "metadata": metadata})
        if verbose:
            print(f"[FOUND] {dataset['name']} | {dataset['reference product']} | "
                  f"source={source_location} | match={match_type} | regionalize_to={regionalize_to}")
    provenance = {"iam_model": resolver.geography.model, "topology": str(resolver.geography.path),
                  "topology_sha256": resolver.geography.sha256, "reference_database": reference_name,
                  "inventories": len(prepared), "checked_exchanges": len(audit),
                  "changed_exchanges": sum(row["status"] == "changed" for row in audit),
                  "ecoinvent_topology": str(resolver.geography.ecoinvent_path),
                  "ecoinvent_topology_sha256": resolver.geography.ecoinvent_sha256,
                  "missing_targets": missing_targets}
    return prepared, audit, provenance


def write_inventory_workbook(prepared, output_file=OUTPUT_FILE):
    """Keep the existing metadata/Exchanges layout expected by ExcelImporter."""
    output_file = Path(output_file).resolve()
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(output_file, engine="xlsxwriter") as writer:
        workbook = writer.book

        worksheet = workbook.add_worksheet(
            "lci"
        )

        writer.sheets["lci"] = worksheet

        bold = workbook.add_format(
            {"bold": True}
        )

        # The importer requires a Database section. Candidate database name is
        # deliberately separate from the reference database and final PoR LCI.
        worksheet.write(0, 0, "Database", bold)
        worksheet.write(0, 1, output_file.stem, bold)
        row_offset = 2  # blank separator before the first Activity section
        max_widths = {}

        for record in prepared:
            metadata = record["metadata"]
            exchanges_df = pd.DataFrame([get_exchange_row(exc) for exc in record["dataset"]["exchanges"]])

            # --------------------------------
            # Write activity metadata
            # --------------------------------

            for idx, (
                key,
                value,
            ) in enumerate(
                metadata.items()
            ):

                fmt = (
                    bold
                    if key == "Activity"
                    else None
                )

                worksheet.write(
                    row_offset + idx,
                    0,
                    key,
                    fmt,
                )

                worksheet.write(
                    row_offset + idx,
                    1,
                    value,
                    fmt,
                )

                max_widths[0] = max(
                    max_widths.get(
                        0,
                        0,
                    ),
                    len(str(key)),
                )

                max_widths[1] = max(
                    max_widths.get(
                        1,
                        0,
                    ),
                    len(str(value)),
                )

            row_offset += len(metadata)

            # --------------------------------
            # Exchanges heading
            # --------------------------------

            worksheet.write(
                row_offset,
                0,
                "Exchanges",
                bold,
            )

            max_widths[0] = max(
                max_widths.get(
                    0,
                    0,
                ),
                len("Exchanges"),
            )

            row_offset += 1

            # --------------------------------
            # Exchange column headers
            # --------------------------------

            for col_idx, column in enumerate(
                exchanges_df.columns
            ):

                worksheet.write(
                    row_offset,
                    col_idx,
                    column,
                    bold,
                )

                max_widths[col_idx] = max(
                    max_widths.get(
                        col_idx,
                        0,
                    ),
                    len(str(column)),
                )

            # --------------------------------
            # Exchange rows
            # --------------------------------

            for row_idx, row in enumerate(
                exchanges_df.itertuples(
                    index=False
                )
            ):

                for col_idx, cell_value in enumerate(
                    row
                ):

                    worksheet.write(
                        row_offset
                        + 1
                        + row_idx,
                        col_idx,
                        None if pd.isna(cell_value) else cell_value,
                    )

                    max_widths[col_idx] = max(
                        max_widths.get(
                            col_idx,
                            0,
                        ),
                        len(
                            str(
                                cell_value
                            )
                        ),
                    )

            row_offset += (
                1
                + len(
                    exchanges_df
                )
            )

            # Blank row between activities
            row_offset += 1

        # =========================
        # Column widths
        # =========================

        for (
            col_idx,
            width,
        ) in max_widths.items():

            worksheet.set_column(
                col_idx,
                col_idx,
                min(
                    width + 2,
                    45,
                ),
            )

    return output_file


def export_reference_inventories(*, output_file=OUTPUT_FILE, model=IAM_MODEL,
                                 database_name=EI_DB_NAME, project_name=PROJECT_NAME, strict_targets=False):
    """Read the configured reference database, prepare copies, write an audit."""
    # These imports and project selection happen only on explicit execution.
    import bw2data as bd
    bd.projects.set_current(project_name)
    if database_name not in bd.databases:
        raise ValueError(f"Database {database_name!r} not found in project {project_name!r}. "
                         f"Available databases: {list(bd.databases)}")
    # The resolver needs provider identities, not every background inventory.
    # Only the selected source activities' exchanges are read below.
    activities = list(bd.Database(database_name))
    reference = [dict(activity) for activity in activities]
    prepared, audit, provenance = prepare_reference_inventories(
        activities, reference, model=model, reference_name=database_name,
        strict_targets=strict_targets,
    )
    if not prepared:
        raise ValueError("No requested reference inventories were found; refusing to write an empty workbook.")
    output = write_inventory_workbook(prepared, output_file)
    audit_file = output.with_name(output.stem + "_supplier_audit.csv")
    provenance_file = output.with_name(output.stem + "_supplier_audit.json")
    pd.DataFrame(audit).to_csv(audit_file, index=False, encoding="utf-8-sig")
    provenance_file.write_text(json.dumps(provenance, indent=2) + "\n", encoding="utf-8")
    print(f"\nExported {len(prepared)} regionalized inventories to: {output}")
    print(f"Supplier audit: {audit_file}")
    return output, audit_file, provenance_file


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog=Path(__file__).name,
        description="Extract reference LCIs with auditable supplier geography.",
    )
    parser.add_argument("--model", default=IAM_MODEL, help="IAM topology name (remind or image).")
    parser.add_argument("--output", type=Path, default=OUTPUT_FILE, help="Candidate workbook output path.")
    parser.add_argument("--strict-targets", action="store_true", help="Fail if any requested reference activity is missing.")
    if argv is None and Path(sys.argv[0]).stem == "ipykernel_launcher":
        # VS Code's interactive window passes the running kernel's connection
        # file via sys.argv. Accept only this host option; keep typo checking
        # strict, and preserve explicit exporter options and main([...]) calls.
        parser.add_argument("-f", "--f", dest="_kernel_connection", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    return export_reference_inventories(output_file=args.output, model=args.model, strict_targets=args.strict_targets)


if __name__ == "__main__":
    main()
