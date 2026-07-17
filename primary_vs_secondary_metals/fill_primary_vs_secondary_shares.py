#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
import copy
import yaml
import pandas as pd

replace_premise_output_file = True
OUTPUT_DIR_PREMISE = Path("C:/Users/terlouw_t/.conda/envs/premise_pathways/Lib/site-packages/premise/data/metals")
OUTPUT_DIR_PREMISE = Path("C:/Users/terlouw_t/.conda/envs/premise_pathways_new/Lib/site-packages/premise/data/metals")

def load_yaml(path: Path):
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def dump_yaml(obj, path: Path):
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(obj, f, sort_keys=False, allow_unicode=True)


def infer_family(product: str, mapping_df: pd.DataFrame) -> str:
    match = mapping_df.loc[mapping_df["product"] == product, "family"]
    if not match.empty:
        return str(match.iloc[0])
    return "base_low"


def ensure_share_dict(values: dict) -> dict:
    shares = values.get("shares", {})
    primary = shares.get("primary", {}) or {}
    secondary = shares.get("secondary", {}) or {}

    return {
        "primary": dict(primary),
        "secondary": dict(secondary),
    }


def build_outputs(
    baseline_yaml: Path,
    evidence_csv: Path,
    mapping_csv: Path,
    output_dir: Path,
) -> None:
    baseline = load_yaml(baseline_yaml)
    evidence = pd.read_csv(evidence_csv)
    mapping_df = pd.read_csv(mapping_csv)

    evidence_map = {
        row["family"]: {
            "conservative": float(row["secondary_share_2050_conservative"]),
            "baseline": float(row["secondary_share_2050_baseline"]),
            "optimistic": float(row["secondary_share_2050_optimistic"]),
            "basis_type": row["basis_type"],
            "confidence": row["confidence"],
            "source_short": row["source_short"],
            "source_url": row["source_url"],
            "notes": row["notes"],
        }
        for _, row in evidence.iterrows()
    }

    scenarios = ["conservative", "baseline", "optimistic"]
    combined = {
        "metadata": {
            "description": (
                "2050 primary/secondary share scenarios built from a curated "
                "literature evidence table and a product-to-family mapping."
            ),
            "baseline_input": str(baseline_yaml),
            "evidence_input": str(evidence_csv),
            "mapping_input": str(mapping_csv),
            "scenarios": scenarios,
        },
        "evidence": evidence_map,
        "products": {},
    }

    output_dir.mkdir(parents=True, exist_ok=True)

    for scenario in scenarios:
        scenario_out = {}

        for product, values in baseline.items():
            family = infer_family(product, mapping_df)
            secondary_2050 = evidence_map[family][scenario]
            primary_2050 = round(1.0 - secondary_2050, 6)
            secondary_2050 = round(secondary_2050, 6)

            # Start from baseline entry so existing information stays intact
            entry = copy.deepcopy(values)

            # Preserve all existing years (2020, 2025, etc.)
            shares = ensure_share_dict(entry)
            shares["primary"][2050] = primary_2050
            shares["secondary"][2050] = secondary_2050
            entry["shares"] = shares

            # Optional provenance fields
            #entry["source_family"] = family

            scenario_out[product] = entry

        combined["products"][scenario] = scenario_out
        dump_yaml(
            scenario_out,
            output_dir / f"primary_secondary_split.yaml",
        )

        if replace_premise_output_file and scenario == "optimistic":
            # Also write the baseline scenario to the file that Premise reads from
            dump_yaml(
                scenario_out,
                OUTPUT_DIR_PREMISE / f"primary_secondary_split.yaml",
            )

    dump_yaml(combined, output_dir / "metal_shares_2050_scenarios.yaml")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--baseline-yaml",
        default="scenario_data/metal_primary_secondary_shares.yaml",
        help="Existing baseline YAML with current shares and product names.",
    )
    parser.add_argument(
        "--evidence-csv",
        default="scenario_data/metal_2050_literature_evidence.csv",
        help="Curated literature table with scenario values by metal family.",
    )
    parser.add_argument(
        "--mapping-csv",
        default="scenario_data/metal_product_family_mapping.csv",
        help="Product-to-family mapping used to assign literature values to products.",
    )
    parser.add_argument(
        "--output-dir",
        default="scenario_data",
        help="Directory where the 2050 scenario YAMLs should be written.",
    )
    args, _ = parser.parse_known_args()

    build_outputs(
        baseline_yaml=Path(args.baseline_yaml),
        evidence_csv=Path(args.evidence_csv),
        mapping_csv=Path(args.mapping_csv),
        output_dir=Path(args.output_dir),
    )
    print(f"Wrote scenario YAMLs to {args.output_dir}")


if __name__ == "__main__":
    main()