"""Repo-owned non-ferrous market policy, update hooks, and export auditing.

The native baseline preserves the installed premise release's quantities and
suppliers. Explicit sensitivity cases only rescale verified material-supply
recipes; recycling is not an avoided burden. Steel remains endogenous to the
native IAM/EDM-I scenario.

Pure inventory planning and post-update/export integration live together here.
Vendor store/validation APIs are imported only when needed. The workflow
supports legacy caches and native stores without editing installed premise
files, and publishes changed inventories only after all scenarios validate.
"""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime, timezone
from functools import wraps
from importlib.metadata import PackageNotFoundError, version
from hashlib import sha256
import csv
import json
import math
import os
import re
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Iterable


# Policy loading, share-data validation, and pure inventory planning.
ROOT = Path(__file__).resolve().parent
DEFAULT_POLICY_PATH = Path(__file__).resolve().parent / "configuration_file" / "metals_scenario_policy.yaml"
IDENTITY_FIELDS = ("name", "reference product", "unit", "location")
SECONDARY_TERMS = ("treatment of", "used", "recover", "discard", "recycl", "scrap", "waste", "sludge", "secondary")


class MetalPolicyError(ValueError):
    """An explicit metals scenario cannot safely be applied."""


class MissingMetalSupplierError(MetalPolicyError):
    """A required genuine primary/secondary supply recipe is unavailable."""


class StaleMetalPolicyError(MetalPolicyError):
    """The source policy or planned inventory changed after preflight."""


def _json_default(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, tuple):
        return list(value)
    if hasattr(value, "item"):
        return value.item()
    raise TypeError(f"Cannot fingerprint {type(value).__name__} in metals policy")


def _digest(value: Any) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=_json_default).encode("utf-8")).hexdigest()


def activity_identity(dataset: Mapping) -> tuple:
    return tuple(dataset.get(field) for field in IDENTITY_FIELDS)


def _exc_identity(exchange: Mapping) -> tuple:
    return (exchange.get("name"), exchange.get("product", exchange.get("reference product")), exchange.get("unit"), exchange.get("location"))


def _float(value: Any, label: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as error:
        raise MetalPolicyError(f"{label} must be a finite number, got {value!r}") from error
    if not math.isfinite(result):
        raise MetalPolicyError(f"{label} must be finite, got {value!r}")
    return result


def _strings(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    return tuple(str(item) for item in value)


def _validate_share_specification(value, label, *, regional=True):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if not 0 <= _float(value, label) <= 1:
            raise MetalPolicyError(f"{label}: secondary shares must be in [0, 1]")
        return
    if not isinstance(value, dict) or not value:
        raise MetalPolicyError(f"{label}: expected a share, nonempty year curve or region map")
    numeric_keys = []
    for key in value:
        try:
            numeric_keys.append(_float(key, f"{label} year"))
        except MetalPolicyError:
            numeric_keys.append(None)
    if all(year is not None for year in numeric_keys):
        if len(set(numeric_keys)) != len(numeric_keys):
            raise MetalPolicyError(f"{label}: duplicate target years")
        for year, share in value.items():
            if not isinstance(share, (int, float)) or isinstance(share, bool):
                raise MetalPolicyError(f"{label}/{year}: year-curve values must be numeric secondary shares")
            _validate_share_specification(share, f"{label}/{year}", regional=False)
    elif any(year is not None for year in numeric_keys):
        raise MetalPolicyError(f"{label}: cannot mix year keys and region keys")
    elif regional:
        for region, share in value.items():
            _validate_share_specification(share, f"{label}/{region}", regional=False)
    else:
        raise MetalPolicyError(f"{label}: nested region maps are not supported")


def _unique_yaml_load(data):
    import yaml

    class UniqueLoader(yaml.SafeLoader):
        pass

    def unique_mapping(loader, node, deep=False):
        loader.flatten_mapping(node)
        result = {}
        for key_node, value_node in node.value:
            key = loader.construct_object(key_node, deep=deep)
            try:
                duplicate = key in result
            except TypeError as error:
                raise yaml.constructor.ConstructorError("while constructing a mapping", node.start_mark,
                                                        "unhashable mapping key", key_node.start_mark) from error
            if duplicate:
                raise yaml.constructor.ConstructorError("while constructing a mapping", node.start_mark,
                                                        f"duplicate key {key!r}", key_node.start_mark)
            result[key] = loader.construct_object(value_node, deep=deep)
        return result

    UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_mapping)
    return yaml.load(data, Loader=UniqueLoader)


def _read_share_document(path, source_hashes):
    import yaml

    try:
        data = Path(path).read_bytes()
    except OSError as error:
        raise MetalPolicyError(f"Metals share data are unavailable: {path}. Run ensure_share_outputs() in notebook 1 before loading the policy") from error
    source_hashes[str(path)] = sha256(data).hexdigest()
    try:
        document = _unique_yaml_load(data)
    except yaml.YAMLError as error:
        raise MetalPolicyError(f"Invalid metals share data in {path}: {error}") from error
    if not isinstance(document, dict) or not document:
        raise MetalPolicyError(f"Metals share data are empty or not a mapping: {path}. Generate the named scenario files first")
    return document


def _source_curve(record, rule, filename):
    if not isinstance(record, dict) or record.get("name") != rule["name"] or record.get("reference product") != rule["reference product"]:
        raise MetalPolicyError(f"{filename}: source record for {rule['id']} must match exact market name and reference product; no metal-grade substitution is permitted")
    if record.get("unit", "kilogram") != rule.get("unit", "kilogram"):
        raise MetalPolicyError(f"{filename}/{rule['id']}: source unit must be kilogram; no implicit unit conversion is permitted")
    shares = record.get("shares", {})
    if not isinstance(shares, dict):
        raise MetalPolicyError(f"{filename}/{rule['id']}: missing primary/secondary shares")
    primary, secondary = shares.get("primary"), shares.get("secondary")
    for role, curve in (("primary", primary), ("secondary", secondary)):
        if not isinstance(curve, dict) or not curve:
            raise MetalPolicyError(f"{filename}/{rule['id']}: {role} must be a nonempty year curve")
        _validate_share_specification(curve, f"{filename}/{rule['id']}/{role}", regional=False)
        if any(not isinstance(key, (int, float, str)) for key in curve):
            raise MetalPolicyError(f"{filename}/{rule['id']}: invalid target year")
    primary_by_year = {_float(year, "primary target year"): _float(value, "primary share") for year, value in primary.items()}
    secondary_by_year = {_float(year, "secondary target year"): _float(value, "secondary share") for year, value in secondary.items()}
    if set(primary_by_year) != set(secondary_by_year):
        raise MetalPolicyError(f"{filename}/{rule['id']}: primary and secondary curves must use the same years")
    for year in secondary_by_year:
        if not math.isclose(primary_by_year[year] + secondary_by_year[year], 1, rel_tol=0, abs_tol=1e-6):
            raise MetalPolicyError(f"{filename}/{rule['id']}/{year:g}: primary and secondary shares must sum to 1")
    return deepcopy(secondary)


def _is_steel_rule(rule):
    labels = (rule.get("name"), rule.get("reference product"), *_strings(rule.get("supply_products")))
    return rule.get("sector") == "steel" or any(re.search(r"\bsteel\b", str(label), flags=re.IGNORECASE) for label in labels)


def _validate_supplier_condition(condition, label):
    """Validate the small, explicit selector grammar in premise's data file."""
    if not isinstance(condition, dict) or len(condition) != 1:
        raise MetalPolicyError(f"{label}: supplier conditions need exactly one supported operator")
    operator, value = next(iter(condition.items()))
    if operator in {"equals", "startswith", "contains"}:
        if not isinstance(value, str) or not value.strip():
            raise MetalPolicyError(f"{label}: {operator} needs a nonempty string")
    elif operator in {"all", "any"}:
        if not isinstance(value, list) or not value:
            raise MetalPolicyError(f"{label}: {operator} needs a nonempty condition list")
        for index, child in enumerate(value):
            _validate_supplier_condition(child, f"{label}/{operator}/{index}")
    else:
        raise MetalPolicyError(f"{label}: unsupported supplier operator {operator!r}")


def _matches_supplier_condition(value, condition):
    operator, operand = next(iter(condition.items()))
    if operator == "all":
        return all(_matches_supplier_condition(value, child) for child in operand)
    if operator == "any":
        return any(_matches_supplier_condition(value, child) for child in operand)
    if not isinstance(value, str):
        return False
    if operator == "equals":
        return value == operand
    if operator == "startswith":
        return value.startswith(operand)
    return operand in value


def _validated_supplier_selectors(records, rule, label):
    if not isinstance(records, list) or not records:
        raise MetalPolicyError(f"{label}: a nonempty secondary-provider list is required")
    fields = ("name", "reference product", "location")
    result, seen = [], set()
    allowed_products = _strings(rule.get("supply_products", (rule["reference product"],)))
    for index, record in enumerate(records):
        if not isinstance(record, dict) or set(record) - {*fields, "shares"} or not all(field in record for field in fields):
            raise MetalPolicyError(f"{label}/{index}: secondary providers require name, reference product and location selectors")
        selector = {field: deepcopy(record[field]) for field in fields}
        for field, condition in selector.items():
            _validate_supplier_condition(condition, f"{label}/{index}/{field}")
        if not any(_matches_supplier_condition(product, selector["reference product"]) for product in allowed_products):
            raise MetalPolicyError(f"{label}/{index}: supplier product is not in the explicit supply_products whitelist")
        if "shares" in record:
            _validate_share_specification(record["shares"], f"{label}/{index}/shares", regional=False)
        fingerprint = _digest(selector)
        if fingerprint in seen:
            raise MetalPolicyError(f"{label}: duplicate/ambiguous secondary-provider selectors")
        seen.add(fingerprint)
        result.append(selector)
    return result


def _load_secondary_supply_data(config, policy_path, nonferrous_case, source_hashes):
    """Load pinned provider selectors, never their within-secondary weights.

    Native baseline does not read optional source files. Opted-in sensitivity
    rules use this repo-owned snapshot, rather than whichever premise release
    happens to be installed. Existing technology/location weights are preserved.
    """
    if config.get("nonferrous_cases", {}).get(nonferrous_case, {}).get("mode") == "native":
        return
    rules = [rule for rule in config.get("markets", []) if isinstance(rule, dict) and "secondary_source_product" in rule]
    if not rules:
        return
    specification = config.get("secondary_supply_source")
    if not isinstance(specification, dict) or not specification.get("file"):
        raise MetalPolicyError("Opted-in secondary rules require secondary_supply_source.file")

    def resolve(name):
        path = Path(name)
        return path.resolve() if path.is_absolute() else (policy_path.parent / path).resolve()

    source_path = resolve(specification["file"])
    source = _read_share_document(source_path, source_hashes)
    receipt = {"path": str(source_path), "sha256": source_hashes[str(source_path)]}
    if specification.get("provenance"):
        provenance_path = resolve(specification["provenance"])
        provenance = _read_share_document(provenance_path, source_hashes)
        if provenance.get("schema_version") != 1 or not re.fullmatch(r"[a-fA-F0-9]{40}", str(provenance.get("commit", ""))):
            raise MetalPolicyError("Secondary-supply provenance requires schema_version: 1 and a pinned 40-character commit")
        files = provenance.get("files")
        if not isinstance(files, dict):
            raise MetalPolicyError("Secondary-supply provenance requires a files mapping")
        matching = []
        for entry in files.values():
            if not isinstance(entry, dict) or not entry.get("local_file"):
                raise MetalPolicyError("Secondary-supply provenance file entries require local_file and sha256")
            local = Path(entry["local_file"])
            local = local.resolve() if local.is_absolute() else (provenance_path.parent / local).resolve()
            declared = entry.get("sha256")
            if not isinstance(declared, str) or not re.fullmatch(r"[a-fA-F0-9]{64}", declared):
                raise MetalPolicyError(f"Pinned source {local}: provenance requires a SHA256")
            try:
                observed = sha256(local.read_bytes()).hexdigest()
            except OSError as error:
                raise MetalPolicyError(f"Pinned source is unavailable: {local}") from error
            if observed != declared:
                raise StaleMetalPolicyError(f"Pinned secondary-supply snapshot does not match its provenance SHA256: {local}")
            source_hashes[str(local)] = observed
            if local == source_path:
                matching.append(entry)
        if len(matching) != 1 or matching[0].get("sha256") != receipt["sha256"]:
            raise StaleMetalPolicyError("Pinned secondary-supply snapshot does not match its provenance SHA256")
        receipt.update(commit=provenance["commit"], provenance_path=str(provenance_path),
                       provenance_sha256=source_hashes[str(provenance_path)])
    for rule in rules:
        product = rule["secondary_source_product"]
        if not isinstance(product, str) or product not in source:
            raise MetalPolicyError(f"Secondary-supply source has no product {product!r} for {rule.get('id')!r}")
        rule["_secondary_supply_selectors"] = _validated_supplier_selectors(source[product], rule, product)
        rule["secondary_supply_source"] = {**receipt, "source_product": product,
                                           "selector_count": len(rule["_secondary_supply_selectors"]),
                                           "source_weights_applied": False}
    config["_secondary_supply_provenance"] = receipt


def _reject_exogenous_steel(config, steel_case="baseline"):
    message = "Steel supply remains endogenous to IAM/EDM-I. Exogenous steel cases, rules and share files are not supported; remove the steel override configuration and use steel_case='baseline' only for backward compatibility"
    if steel_case != "baseline":
        raise MetalPolicyError(message)
    cases = config.get("steel_cases")
    if cases is not None and (not isinstance(cases, dict) or any(case != "baseline" for case in cases)
                              or ("baseline" in cases and (not isinstance(cases["baseline"], dict) or cases["baseline"].get("mode") != "native"))):
        raise MetalPolicyError(message)
    if cases and any(key in cases.get("baseline", {}) for key in ("targets", "shares", "reference_shares")):
        raise MetalPolicyError(message)
    rules = config.get("markets", [])
    if isinstance(rules, list) and any(isinstance(rule, dict) and _is_steel_rule(rule) for rule in rules):
        raise MetalPolicyError(message)
    share_data = config.get("share_data", {})
    if isinstance(share_data, dict) and "steel" in share_data:
        raise MetalPolicyError(message)
    if any(key in config for key in ("steel_source", "steel_share_data", "steel_targets", "steel_shares")):
        raise MetalPolicyError(message)


def _load_share_data(config, policy_path, nonferrous_case, source_hashes):
    """Read selected case inputs; never generate or alter scenario files."""
    specification = config.get("share_data")
    if not specification:
        return
    if not isinstance(specification, dict):
        raise MetalPolicyError("share_data must be a mapping")
    directory = Path(specification.get("directory", "../primary_vs_secondary_metals/scenario_data"))
    directory = directory.resolve() if directory.is_absolute() else (policy_path.parent / directory).resolve()
    provenance, selected_paths = [], set()
    documents = {}

    def read(filename):
        path = Path(filename)
        path = path.resolve() if path.is_absolute() else (directory / path).resolve()
        if path not in documents:
            documents[path] = _read_share_document(path, source_hashes)
        selected_paths.add(path)
        return path, documents[path]

    selected = (("nonferrous", nonferrous_case),)
    for sector, case in selected:
        cases = config.get(f"{sector}_cases", {})
        settings = cases.get(case, {})
        mode = settings.get("mode")
        # 'baseline' is the installed premise baseline, not the generated
        # file whose historical scenario happens to be named 'baseline'.
        if mode == "native" or case == "primary_only":
            continue
        sector_data = specification.get(sector)
        if not sector_data:
            continue
        if sector == "nonferrous":
            case_data = sector_data.get(case) if isinstance(sector_data, dict) else None
            if not isinstance(case_data, dict) or not case_data.get("file"):
                raise MetalPolicyError(f"share_data.nonferrous has no file for selected case {case!r}")
            path, document = read(case_data["file"])
            products = document.get("products", document)
            # Combined legacy documents may nest products by scenario. The
            # named product-to-record files remain the preferred interface.
            source_case = case_data.get("scenario")
            if source_case is not None:
                products = products.get(source_case, {})
            for rule in config.get("markets", []):
                if rule.get("sector") != sector:
                    continue
                source_product = rule.get("source_product", rule.get("reference product"))
                if not isinstance(products, dict) or source_product not in products:
                    raise MetalPolicyError(f"{path}: no source product {source_product!r} for {rule.get('id')!r}")
                curve = _source_curve(products[source_product], rule, str(path))
                receipt = {"sector": sector, "case": case, "path": str(path), "sha256": source_hashes[str(path)],
                           "source_product": source_product, "source_curve": deepcopy(curve)}
                if mode == "reference":
                    year = _float(case_data.get("year", 2020), "reference share year")
                    reference = _curve(curve, year, "*", rule["id"], case)
                    rule["reference_shares"] = reference
                    rule.setdefault("targets", {}).pop(case, None)
                    receipt.update(reference_year=year, effective_targets=reference)
                else:
                    rule.setdefault("targets", {})[case] = deepcopy(curve)
                    receipt["effective_targets"] = deepcopy(curve)
                rule["share_data_source"] = receipt
                provenance.append(receipt)

    if not provenance:
        return
    manifest_name = specification.get("manifest", "share_generation_manifest.json")
    manifest_path = (directory / manifest_name).resolve() if manifest_name else None
    if manifest_path and manifest_path.is_file():
        manifest = _read_share_document(manifest_path, source_hashes)
        if manifest.get("schema_version") != 1:
            raise MetalPolicyError(f"Unsupported share-generation manifest schema: {manifest_path}")
        inputs = manifest.get("input_sha256", {})
        if not isinstance(inputs, dict):
            raise MetalPolicyError(f"Share-generation manifest inputs must be a mapping: {manifest_path}")
        for filename, expected in inputs.items():
            path = Path(filename)
            path = path.resolve() if path.is_absolute() else (manifest_path.parent / path).resolve()
            try:
                observed = sha256(path.read_bytes()).hexdigest()
            except OSError as error:
                raise MetalPolicyError(f"Share-generation input is unavailable: {path}") from error
            if observed != expected:
                raise StaleMetalPolicyError(f"Share-generation input changed: {path}. Run ensure_share_outputs() before loading the policy")
            source_hashes[str(path)] = observed
        for receipt in provenance:
            receipt["manifest_path"] = str(manifest_path)
            receipt["manifest_sha256"] = source_hashes[str(manifest_path)]
            declared = manifest.get("output_sha256", {}).get(Path(receipt["path"]).name)
            receipt["matches_generated_manifest"] = declared == receipt["sha256"] if declared else None
        # Carry the selected CSV evidence into package provenance and per-market
        # audits, rather than retaining only a hash and detached source notes.
        # Older manifests without the combined evidence interface still work.
        combined_name = manifest.get("combined_file")
        generator_version = manifest.get("generator_version", 0)
        if not isinstance(generator_version, int) or isinstance(generator_version, bool):
            raise MetalPolicyError(f"{manifest_path}: generator_version must be an integer")
        if generator_version >= 3 and not combined_name:
            raise MetalPolicyError(f"{manifest_path}: version 3 requires a combined evidence file")
        if combined_name:
            combined_path, combined = read(combined_name)
            expected = manifest.get("output_sha256", {}).get(Path(combined_path).name)
            if generator_version >= 3 and not expected:
                raise MetalPolicyError(f"{manifest_path}: version 3 requires a hash for the combined evidence file")
            if expected and source_hashes[str(combined_path)] != expected:
                raise StaleMetalPolicyError(f"Share evidence output changed: {combined_path}. Run ensure_share_outputs() before loading the policy")
            metadata = combined.get("metadata", {})
            if not isinstance(metadata, dict):
                raise MetalPolicyError(f"{combined_path}: metadata must be a mapping")
            product_families = metadata.get("product_evidence", {})
            evidence = combined.get("evidence", {})
            if not isinstance(product_families, dict) or not isinstance(evidence, dict):
                raise MetalPolicyError(f"{combined_path}: evidence and product_evidence must be mappings")
            for rule in config.get("markets", []):
                receipt = rule.get("share_data_source")
                if not receipt:
                    continue
                product = receipt["source_product"]
                family = product_families.get(product)
                if family is None:
                    if generator_version >= 3:
                        raise MetalPolicyError(f"{combined_path}: missing evidence family for {product!r}")
                    continue
                if not isinstance(family, str) or not family.strip():
                    raise MetalPolicyError(f"{combined_path}: evidence family for {product!r} must be a nonempty string")
                record = evidence.get(family)
                if not isinstance(record, dict):
                    raise MetalPolicyError(f"{combined_path}: missing evidence record for {family!r}")
                excluded_products = record.get("excluded_products", [])
                if not isinstance(excluded_products, list) or not all(isinstance(item, str) for item in excluded_products):
                    raise MetalPolicyError(f"{combined_path}: excluded_products for {family!r} must be a list of product names")
                if (config["nonferrous_cases"][nonferrous_case]["mode"] == "minimum"
                        and product in excluded_products):
                    raise MetalPolicyError(f"{product!r} is outside the {family!r} evidence scope; retain native modelling until separately qualified")
                receipt["evidence_family"] = family
                receipt["evidence"] = deepcopy(record)
                receipt["evidence_path"] = str(combined_path)
                receipt["evidence_sha256"] = source_hashes[str(combined_path)]
    elif specification.get("manifest_required"):
        raise MetalPolicyError(f"Share-generation manifest is missing: {manifest_path}. Run ensure_share_outputs() first")
    config["_share_data_provenance"] = provenance


@dataclass(frozen=True)
class MetalSteelPolicy:
    configuration: Mapping
    nonferrous_case: str = "baseline"
    steel_case: str = "baseline"
    path: Path | None = None
    source_hashes: Mapping | None = None
    fingerprint: str = ""

    @classmethod
    def from_file(cls, path=DEFAULT_POLICY_PATH, *, nonferrous_case="baseline", steel_case="baseline"):
        import yaml

        policy_path = Path(path).resolve()
        data = policy_path.read_bytes()
        try:
            configuration = _unique_yaml_load(data)
        except yaml.YAMLError as error:
            raise MetalPolicyError(f"Invalid metals policy in {policy_path}: {error}") from error
        source_hashes = {str(policy_path): sha256(data).hexdigest()}
        if isinstance(configuration, dict):
            _reject_exogenous_steel(configuration, steel_case)
            _load_share_data(configuration, policy_path, nonferrous_case, source_hashes)
            _load_secondary_supply_data(configuration, policy_path, nonferrous_case, source_hashes)
        return cls.from_mapping(configuration, nonferrous_case=nonferrous_case, steel_case=steel_case,
                                path=policy_path, source_hashes=source_hashes)

    @classmethod
    def from_mapping(cls, configuration, *, nonferrous_case="baseline", steel_case="baseline", path=None, source_hashes=None):
        config = deepcopy(configuration)
        if not isinstance(config, dict) or config.get("schema_version", config.get("policy_version")) != 1:
            raise MetalPolicyError("Metals policy must be a mapping with schema_version: 1")
        _reject_exogenous_steel(config, steel_case)
        config["steel_cases"] = {"baseline": {"mode": "native"}}
        for sector, selected in (("nonferrous", nonferrous_case),):
            cases = config.get(f"{sector}_cases", {})
            if selected not in cases:
                raise MetalPolicyError(f"Unknown {sector} case {selected!r}; available cases: {sorted(cases)}")
            if "baseline" in cases and (not isinstance(cases["baseline"], dict) or cases["baseline"].get("mode") != "native"):
                raise MetalPolicyError(f"{sector} case 'baseline' is reserved for mode: native. Choose a distinct custom case name for any supply override so native-baseline outputs are not overwritten")
            for case, settings in cases.items():
                if not isinstance(settings, dict) or settings.get("mode") not in ("native", "reference", "target", "minimum"):
                    raise MetalPolicyError(f"{sector} case {case!r} must declare mode native/reference/target/minimum")
        rules = config.get("markets", [])
        if not isinstance(rules, list):
            raise MetalPolicyError("markets must be a list of exact market rules")
        seen = set()
        for rule in rules:
            if not isinstance(rule, dict):
                raise MetalPolicyError("Each metals market rule must be a mapping")
            if rule.get("id") in seen or not rule.get("id"):
                raise MetalPolicyError("Market rule IDs must be nonempty and unique")
            seen.add(rule["id"])
            if rule.get("sector") != "nonferrous" or not rule.get("name") or not rule.get("reference product"):
                raise MetalPolicyError(f"Rule {rule['id']!r} needs sector, exact name and reference product")
            if rule.get("unit", "kilogram") != "kilogram":
                raise MetalPolicyError(f"Rule {rule['id']!r}: only kilogram material markets can be adjusted")
            if rule["sector"] == "nonferrous" and any(term in rule["reference product"].lower() for term in ("ore", "concentrate", "primary")):
                raise MetalPolicyError(f"Rule {rule['id']!r}: ores, concentrates and explicit-primary products cannot be recycling markets")
            if "strict_secondary_matching" in rule and not isinstance(rule["strict_secondary_matching"], bool):
                raise MetalPolicyError(f"Rule {rule['id']}: strict_secondary_matching must be boolean")
            if "primary_remainder_names" in rule:
                names = rule["primary_remainder_names"]
                if (not isinstance(names, list) or not names
                        or any(not isinstance(name, str) or not name.strip() or name != name.strip() for name in names)
                        or len(set(names)) != len(names)):
                    raise MetalPolicyError(f"Rule {rule['id']}: primary_remainder_names needs unique exact activity names")
                if not isinstance(rule.get("primary_remainder_note"), str) or not rule["primary_remainder_note"].strip():
                    raise MetalPolicyError(f"Rule {rule['id']}: primary_remainder_names requires primary_remainder_note")
                if not rule.get("secondary") or any(_predicate(name, rule["secondary"]) for name in names):
                    raise MetalPolicyError(f"Rule {rule['id']}: primary remainder must not overlap the explicit secondary predicate")
            if "minimum_target_years" in rule:
                years = rule["minimum_target_years"]
                if not isinstance(years, list) or not years or any(isinstance(year, bool) for year in years):
                    raise MetalPolicyError(f"Rule {rule['id']}: minimum_target_years must be a nonempty list of finite unique years")
                normalized = [_float(year, f"{rule['id']}/minimum_target_years") for year in years]
                if len(set(normalized)) != len(normalized):
                    raise MetalPolicyError(f"Rule {rule['id']}: minimum_target_years must contain unique years")
                rule["minimum_target_years"] = normalized
            targets = rule.get("targets", {})
            if not isinstance(targets, dict):
                raise MetalPolicyError(f"Rule {rule['id']}: targets must map case names to share specifications")
            cases = config[f"{rule['sector']}_cases"]
            for case, specification in targets.items():
                if case not in cases:
                    raise MetalPolicyError(f"Rule {rule['id']}: target refers to unknown case {case!r}")
                _validate_share_specification(specification, f"{rule['id']}/{case}")
            for key in ("reference_shares", "reference_share"):
                if key in rule:
                    _validate_share_specification(rule[key], f"{rule['id']}/{key}")
            selected = nonferrous_case if rule["sector"] == "nonferrous" else steel_case
            if "secondary_source_product" in rule:
                if not isinstance(rule["secondary_source_product"], str) or not rule["secondary_source_product"].strip():
                    raise MetalPolicyError(f"Rule {rule['id']}: secondary_source_product must be a nonempty product key")
                products = _strings(rule.get("supply_products", (rule["reference product"],)))
                if any(product != rule["reference product"] for product in products) and not (
                        "supply_products" in rule and isinstance(rule.get("supply_product_note"), str)
                        and rule["supply_product_note"].strip()):
                    raise MetalPolicyError(f"Rule {rule['id']}: a secondary product-grade interface needs explicit supply_products and supply_product_note")
                selectors = rule.get("_secondary_supply_selectors")
                if cases[selected]["mode"] != "native" and selectors is None:
                    raise MetalPolicyError(f"Rule {rule['id']}: load pinned secondary providers with MetalSteelPolicy.from_file() before applying a sensitivity")
                if selectors is not None:
                    _validated_supplier_selectors(selectors, rule, rule["secondary_source_product"])
            if cases[selected]["mode"] in ("minimum", "target") and selected not in targets and selected != "primary_only":
                raise MetalPolicyError(f"Rule {rule['id']}: missing target for selected case {selected!r}")
        fingerprint = _digest({"configuration": config, "nonferrous_case": nonferrous_case, "steel_case": steel_case})
        return cls(config, nonferrous_case, steel_case, Path(path) if path else None, dict(source_hashes or {}), fingerprint)

    @property
    def rules(self):
        return tuple(self.configuration.get("markets", []))

    @property
    def baseline(self):
        return self.case_settings("nonferrous").get("mode") == "native"

    @property
    def case_suffix(self):
        if self.nonferrous_case == "baseline":
            return ""
        safe = lambda value: re.sub(r"[^a-zA-Z0-9_-]+", "-", value)
        return f"__metals-{safe(self.nonferrous_case)}"

    def describe(self):
        return {"nonferrous_case": self.nonferrous_case, "steel_case": self.steel_case,
                "steel_owner": "endogenous IAM/EDM-I",
                "native_baseline": self.baseline, "policy_sha256": self.fingerprint,
                "policy_file": str(self.path) if self.path else None, "output_suffix": self.case_suffix,
                "market_rules": len(self.rules),
                "share_data_sources": deepcopy(self.configuration.get("_share_data_provenance", [])),
                "secondary_supply_sources": [deepcopy(rule["secondary_supply_source"]) for rule in self.rules
                                             if rule.get("secondary_supply_source")],
                "scientific_assumptions": {rule["id"]: {
                    "evidence_ids": list(rule.get("evidence", [])),
                    "evidence_details": {key: deepcopy(self.configuration.get("metadata", {}).get("sources", {}).get(key, {}))
                                         for key in rule.get("evidence", [])},
                    "target_note": rule.get("target_note"),
                    "secondary_matching_note": rule.get("secondary_matching_note"),
                    "primary_remainder_names": list(rule.get("primary_remainder_names", [])),
                    "primary_remainder_note": rule.get("primary_remainder_note")}
                    for rule in self.rules}}

    def case_name(self, sector):
        return self.nonferrous_case if sector == "nonferrous" else self.steel_case

    def case_settings(self, sector):
        return self.configuration[f"{sector}_cases"][self.case_name(sector)]

    def verify_sources(self):
        current = _digest({"configuration": self.configuration, "nonferrous_case": self.nonferrous_case, "steel_case": self.steel_case})
        if current != self.fingerprint:
            raise StaleMetalPolicyError("Metals policy object was modified after preflight; reload the policy")
        for filename, expected in (self.source_hashes or {}).items():
            try:
                observed = sha256(Path(filename).read_bytes()).hexdigest()
            except OSError as error:
                raise StaleMetalPolicyError(f"Metals policy source is unavailable: {filename}") from error
            if observed != expected:
                raise StaleMetalPolicyError(f"Metals policy source changed: {filename}; reload before updating/exporting")


def matching_market_rule(dataset: Mapping, policy: MetalSteelPolicy):
    matches = []
    for rule in policy.rules:
        locations = _strings(rule.get("locations", ("*",)))
        if (dataset.get("name") == rule["name"] and dataset.get("reference product") == rule["reference product"]
                and dataset.get("unit") == rule.get("unit", "kilogram")
                and ("*" in locations or dataset.get("location") in locations)):
            matches.append(rule)
    if len(matches) > 1:
        raise MetalPolicyError(f"Overlapping market rules for {activity_identity(dataset)!r}")
    return matches[0] if matches else None


def is_policy_market(dataset: Mapping, policy: MetalSteelPolicy) -> bool:
    return matching_market_rule(dataset, policy) is not None


def _predicate(name: str, settings: Mapping | None) -> bool:
    if not settings:
        return False
    text = name.lower()
    excludes = _strings(settings.get("name_excludes", settings.get("name_excludes_contains")))
    if any(term.lower() in text for term in excludes):
        return False
    exact = _strings(settings.get("names", settings.get("name_equals")))
    contains = _strings(settings.get("name_contains"))
    starts = _strings(settings.get("name_prefix", settings.get("name_startswith")))
    return (name in exact or any(term.lower() in text for term in contains)
            or any(text.startswith(term.lower()) for term in starts))


def classify_supply(exchange: Mapping, rule: Mapping) -> str | None:
    """Classify actual material inputs, never transport or negative coproducts.

    Steel routes are intentionally outside this exogenous policy's scope.
    """
    if _is_steel_rule(rule):
        raise MetalPolicyError("Steel routes remain endogenous to IAM/EDM-I; no exogenous steel supply classification is applied")
    if exchange.get("type") != "technosphere" or exchange.get("unit") != rule.get("unit", "kilogram"):
        return None
    products = _strings(rule.get("supply_products", (rule["reference product"],)))
    if exchange.get("product", exchange.get("reference product")) not in products:
        return None
    try:
        if _float(exchange.get("amount", 0), "supply amount") < 0:
            return None
    except MetalPolicyError:
        return "invalid"
    name = str(exchange.get("name", ""))
    text = name.lower()
    if text.startswith(("market for ", "market group for ")):
        return "aggregate"
    # A documented exact-name exception can retain unresolved refinery residues
    # in the non-secondary remainder without pretending they are recycled metal.
    if name in rule.get("primary_remainder_names", ()):
        return "primary"
    selectors = rule.get("_secondary_supply_selectors")
    if selectors is not None:
        values = {"name": name, "reference product": exchange.get("product", exchange.get("reference product")),
                  "location": exchange.get("location")}
        matches = [selector for selector in selectors if all(
            _matches_supplier_condition(values[field], condition) for field, condition in selector.items())]
        if len(matches) > 1:
            raise MetalPolicyError(f"Rule {rule.get('id')}: ambiguous secondary-provider match for {_exc_identity(exchange)!r}")
        if matches:
            # A local exact-name predicate may narrow a broad upstream selector;
            # it can never bypass the pinned product/location criteria.
            if rule.get("secondary") and not _predicate(name, rule["secondary"]):
                return "unknown"
            return "secondary"
        # An unlisted recycling process is not evidence of primary production.
        # Fail the plan rather than silently shifting that stream to primary.
        if any(term in text for term in SECONDARY_TERMS):
            return "unknown"
    elif _predicate(name, rule.get("secondary")) or (not rule.get("secondary") and any(term in text for term in SECONDARY_TERMS)):
        return "secondary"
    if rule.get("strict_secondary_matching") and any(term in text for term in SECONDARY_TERMS):
        return "unknown"
    if _predicate(name, rule.get("primary")) or not rule.get("primary"):
        return "primary"
    return "unknown"


def _curve(value, year, location, rule_id, case):
    if isinstance(value, (int, float)):
        share = _float(value, f"{rule_id}/{case} share")
    elif isinstance(value, dict) and value:
        # A region map is distinguished from a year curve by nonnumeric keys.
        try:
            points = sorted((_float(k, "target year"), _float(v, "secondary target")) for k, v in value.items())
        except MetalPolicyError:
            selected = value.get(location, value.get("*"))
            if selected is None:
                raise MetalPolicyError(f"Rule {rule_id}/{case}: no target for region {location!r}")
            return _curve(selected, year, location, rule_id, case)
        if year <= points[0][0]:
            share = points[0][1]
        elif year >= points[-1][0]:
            share = points[-1][1]
        else:
            for (left, low), (right, high) in zip(points, points[1:]):
                if left <= year <= right:
                    share = low + (high - low) * (year - left) / (right - left)
                    break
        if any(not 0 <= point[1] <= 1 for point in points):
            raise MetalPolicyError(f"Rule {rule_id}/{case}: secondary shares must be in [0, 1]")
    else:
        raise MetalPolicyError(f"Rule {rule_id}/{case}: missing secondary-share target curve")
    if not 0 <= share <= 1:
        raise MetalPolicyError(f"Rule {rule_id}/{case}: secondary share {share} is outside [0, 1]")
    return share


def _target(rule, settings, case, year, location, observed, source_market):
    mode = settings["mode"]
    value = rule.get("targets", {}).get(case)
    if mode == "reference" and value is None:
        value = rule.get("reference_shares", rule.get("reference_share"))
        if value is None and source_market is not None:
            groups, _ = _groups(source_market.get("exchanges", []), rule)
            total = sum(float(exc["amount"]) for _, exc in groups["primary"] + groups["secondary"])
            if total > 0:
                value = sum(float(exc["amount"]) for _, exc in groups["secondary"]) / total
    if mode in ("target", "minimum") and value is None and case == "primary_only":
        value = 0.0
    target = _curve(value, year, location, rule["id"], case)
    return max(observed, target) if mode == "minimum" else target


def _region_targeted(rule, settings, case, location):
    value = rule.get("targets", {}).get(case)
    if value is None and settings["mode"] == "reference":
        value = rule.get("reference_shares", rule.get("reference_share"))
    if not isinstance(value, dict) or not value:
        return True
    try:
        for key in value:
            _float(key, "target year")
    except MetalPolicyError:
        return location in value or "*" in value
    return True


def _share_data_audit(rule, sources=None):
    receipt = rule.get("share_data_source", {})
    provider_source = rule.get("secondary_supply_source", {})
    return {"share_data_case": receipt.get("case"), "share_data_path": receipt.get("path"),
            "share_data_sha256": receipt.get("sha256"), "share_data_source_product": receipt.get("source_product"),
            "share_data_targets": deepcopy(receipt.get("effective_targets")),
            "share_data_manifest_sha256": receipt.get("manifest_sha256"),
            "share_data_evidence_family": receipt.get("evidence_family"),
            "share_data_evidence": deepcopy(receipt.get("evidence")),
            "share_data_evidence_sha256": receipt.get("evidence_sha256"),
            "secondary_supply_source_path": provider_source.get("path"),
            "secondary_supply_source_sha256": provider_source.get("sha256"),
            "secondary_supply_source_commit": provider_source.get("commit"),
            "secondary_supply_source_product": provider_source.get("source_product"),
            "secondary_source_weights_applied": provider_source.get("source_weights_applied"),
            "evidence_ids": list(rule.get("evidence", [])),
            "evidence_details": {key: deepcopy((sources or {}).get(key, {})) for key in rule.get("evidence", [])},
            "target_note": rule.get("target_note"),
            "secondary_matching_note": rule.get("secondary_matching_note"),
            "primary_remainder_names": list(rule.get("primary_remainder_names", [])),
            "primary_remainder_note": rule.get("primary_remainder_note")}


def _audit_suppliers(groups):
    return {f"applied_{role}_suppliers": [
        {"name": exc.get("name"), "reference product": exc.get("product", exc.get("reference product")),
         "location": exc.get("location"), "unit": exc.get("unit"), "amount": exc.get("amount")}
        for _, exc in groups[role] if float(exc.get("amount", 0)) > 0]
        for role in ("primary", "secondary")}


def _groups(exchanges, rule):
    groups = {key: [] for key in ("primary", "secondary", "aggregate", "unknown", "invalid")}
    warnings = []
    for index, exchange in enumerate(exchanges):
        role = classify_supply(exchange, rule)
        if role is not None:
            groups[role].append((index, exchange))
    if groups["unknown"]:
        warnings.append("Unrecognised same-product material suppliers")
    if groups["invalid"]:
        warnings.append("Invalid/non-finite material-supply amounts")
    return groups, warnings


def _supplier_index(database):
    result = {}
    for dataset in database:
        result.setdefault(activity_identity(dataset), []).append(dataset)
    return result


def _available_supplier(exchange, index, label):
    candidates = index.get(_exc_identity(exchange), [])
    if not candidates:
        raise MissingMetalSupplierError(f"{label}: provider {_exc_identity(exchange)!r} is absent from the updated inventory; provide a compatible, burden-carrying LCI or keep baseline")
    if len(candidates) > 1:
        link = exchange.get("input")
        linked = [ds for ds in candidates if link and tuple(link) == (ds.get("database"), ds.get("code"))]
        if len(linked) != 1:
            raise MissingMetalSupplierError(f"{label}: provider {_exc_identity(exchange)!r} is ambiguous; resolve supplier identity explicitly")
        return linked[0]
    return candidates[0]


def _scale_exchange(exchange, factor):
    result = deepcopy(dict(exchange))
    if factor == 1:
        return result
    result["amount"] = float(exchange["amount"]) * factor
    raw_uncertainty = result.get("uncertainty type", 0)
    if factor == 0:
        uncertainty = 0
    else:
        try:
            uncertainty = int(raw_uncertainty or 0)
            if float(raw_uncertainty or 0) != uncertainty:
                raise ValueError("non-integral distribution ID")
        except (TypeError, ValueError) as error:
            raise MetalPolicyError(f"Cannot rescale invalid uncertainty type {raw_uncertainty!r}") from error
        # Dimensionful location/scale families are closed under positive
        # multiplication. Bernoulli/discrete and beta shape parameters are
        # not: multiplying a beta parameter changes its distribution shape.
        if uncertainty not in (0, 1, 2, 3, 4, 5, 8, 9, 11, 12):
            raise MetalPolicyError(f"Cannot safely rescale uncertainty type {uncertainty} on {exchange.get('name')!r}. Supply a supported amount distribution or keep the native baseline")
    for field in ("minimum", "maximum", "loc"):
        value = result.get(field)
        if value is None:
            continue
        if field == "loc" and uncertainty == 2:
            result[field] = _float(value, field) + math.log(factor) if factor > 0 else 0.0
        else:
            result[field] = _float(value, field) * factor
    if result.get("scale") is not None and uncertainty not in (0, 2):
        result["scale"] = _float(result["scale"], "scale") * factor
    if factor == 0:
        result["uncertainty type"] = 0
        for field in ("loc", "scale", "shape", "minimum", "maximum", "negative"):
            result.pop(field, None)
    # A static scenario override must not leave a formula that restores an old amount.
    if "formula" in result:
        result["por original formula"] = result.pop("formula")
    return result


def _source_market(dataset, source_database, rule):
    if source_database is None:
        return None
    matches = [ds for ds in source_database if activity_identity(ds) == activity_identity(dataset)]
    if not matches:
        # A source World market may not exist in unmodified ecoinvent. Select
        # only an explicitly configured reference location, not an arbitrary country.
        for location in _strings(rule.get("reference_locations")):
            matches = [ds for ds in source_database if activity_identity(ds)[:3] == activity_identity(dataset)[:3]
                       and ds.get("location") == location]
            if matches:
                break
        if not matches:
            return None
    if len(matches) != 1:
        raise MissingMetalSupplierError(f"Reference market {activity_identity(dataset)!r} is ambiguous")
    return matches[0]


def _seed_recipe(role, rule, source_market, provider_index, label):
    if source_market is None:
        raise MissingMetalSupplierError(f"{label}: a nonzero {role} share is required but no positive recipe exists. Supply a verified matching reference market with compatible providers, or keep baseline")
    groups, warnings = _groups(source_market.get("exchanges", []), rule)
    if warnings or groups["aggregate"]:
        raise MissingMetalSupplierError(f"{label}: reference market is not a verified direct material-supply recipe")
    result = []
    for _, original in groups[role]:
        if float(original.get("amount", 0)) <= 0:
            continue
        supplier = _available_supplier(original, provider_index, label)
        exchange = deepcopy(dict(original))
        # Never carry a source-database UUID into the transformed inventory.
        if supplier.get("code") and supplier.get("database"):
            exchange["input"] = (supplier["database"], supplier["code"])
        else:
            exchange.pop("input", None)
        exchange.pop("output", None)
        exchange["por metals recipe source"] = "verified matching reference market"
        result.append(exchange)
    if not result:
        raise MissingMetalSupplierError(f"{label}: reference market has no positive {role} recipe with suppliers available in the updated inventory")
    return result


@dataclass(frozen=True)
class MarketChange:
    dataset_index: int
    identity: tuple
    old_exchanges_sha256: str
    new_exchanges: tuple


@dataclass(frozen=True)
class MetalScenarioPlan:
    changes: tuple[MarketChange, ...]
    audit: tuple[dict, ...]
    stats: Mapping
    policy_fingerprint: str
    plan_sha256: str

    @property
    def changed_indices(self):
        return tuple(change.dataset_index for change in self.changes)

    def verify(self):
        if _plan_digest(self.changes, self.policy_fingerprint) != self.plan_sha256:
            raise StaleMetalPolicyError("Planned metals exchanges were modified after validation")


def _plan_digest(changes, fingerprint):
    return _digest({"policy": fingerprint, "changes": [
        {"index": item.dataset_index, "identity": item.identity, "before": item.old_exchanges_sha256, "after": item.new_exchanges}
        for item in changes]})


def plan_database(database: Iterable[Mapping], policy: MetalSteelPolicy, *, scenario=None, source_database=None) -> MetalScenarioPlan:
    """Audit native markets or plan all explicit overrides without mutation.

    Activity mappings may be lazy: only identity fields are read on unrelated
    activities. Only exact policy markets need exchange dictionaries. Baseline
    is audit-only and does not reject native anomalies or require source data.
    """
    policy.verify_sources()
    activities = list(database)
    references = list(source_database) if source_database is not None and not policy.baseline else None
    provider_index = None

    def suppliers():
        nonlocal provider_index
        if provider_index is None:
            provider_index = _supplier_index(activities)
        return provider_index
    context = dict(scenario or {})
    if context.get("system_model") == "consequential" and not policy.baseline:
        raise MetalPolicyError("Primary/secondary supply sensitivities require attributional/cut-off modelling. Consequential scenarios must retain the native baseline; recycled-input fractions are not marginal suppliers")
    year = _float(context.get("year", 2025), "scenario year")
    changes, audit = [], []
    observed_rules = set()
    for dataset_index, dataset in enumerate(activities):
        rule = matching_market_rule(dataset, policy)
        if rule is None:
            continue
        # Storage adapters may supply identity-only records for providers.
        # A regional child-market identity is not a loaded market recipe.
        if "exchanges" not in dataset:
            continue
        observed_rules.add(rule["id"])
        settings = policy.case_settings(rule["sector"])
        case = policy.case_name(rule["sector"])
        exchanges = list(dataset.get("exchanges", []))
        groups, warnings = _groups(exchanges, rule)
        primary = sum(float(exc.get("amount", 0)) for _, exc in groups["primary"])
        secondary = sum(float(exc.get("amount", 0)) for _, exc in groups["secondary"])
        total = primary + secondary
        aggregate_total = sum(float(exc.get("amount", 0)) for _, exc in groups["aggregate"])
        native_share = secondary / total if total > 0 else None
        row = {"rule_id": rule["id"], "sector": rule["sector"], "case": case, "mode": settings["mode"],
               "name": dataset.get("name"), "reference product": dataset.get("reference product"),
               "unit": dataset.get("unit"), "location": dataset.get("location"), "year": year,
               "model": context.get("model"), "pathway": context.get("pathway"),
               "native_primary_amount": primary, "native_secondary_amount": secondary,
               "native_supply_total": total + aggregate_total, "native_direct_supply_total": total,
               "aggregate_supply_amount": aggregate_total, "native_secondary_share": native_share,
               "target_secondary_share": native_share, "applied_secondary_share": native_share,
               "status": "native_preserved", "warnings": "; ".join(warnings), "seeded_roles": "",
               "recipe_source_market": None, "recipe_source_exchanges_sha256": None,
               "policy_sha256": policy.fingerprint,
               **_share_data_audit(rule, policy.configuration.get("metadata", {}).get("sources")),
               **_audit_suppliers(groups)}
        if settings["mode"] == "native":
            if groups["aggregate"]:
                row["status"] = "native_aggregate_preserved"
                row["warnings"] += ("; " if row["warnings"] else "") + "Regional market hierarchy retained; aggregate inputs do not establish a recycled fraction"
            if not math.isclose(total, 1, rel_tol=0, abs_tol=1e-6) and not groups["aggregate"]:
                row["warnings"] += ("; " if row["warnings"] else "") + "Native material inputs do not sum to 1 kg (not changed)"
            audit.append(row)
            continue
        evidence_years = rule.get("minimum_target_years")
        if settings["mode"] == "minimum" and evidence_years is not None and year not in evidence_years:
            row["status"] = "evidence_year_not_covered"
            row["minimum_target_years"] = list(evidence_years)
            row["warnings"] += ("; " if row["warnings"] else "") + "No reviewed minimum-share target for this year; native supply retained without interpolation"
            audit.append(row)
            continue
        if not _region_targeted(rule, settings, case, dataset.get("location")):
            row["status"] = "region_not_targeted"
            row["warnings"] += ("; " if row["warnings"] else "") + "No explicit target for this region; native suppliers and quantities retained"
            audit.append(row)
            continue
        if groups["aggregate"]:
            if groups["primary"] or groups["secondary"] or groups["unknown"]:
                raise MetalPolicyError(f"{rule['id']} at {dataset.get('location')}: mixed aggregate and direct suppliers cannot be safely rescaled")
            row["status"] = "aggregate_preserved"
            row["warnings"] = "Regional market hierarchy retained; adjust direct route markets, not region weights"
            audit.append(row)
            continue
        if warnings or groups["unknown"] or groups["invalid"]:
            raise MetalPolicyError(f"{rule['id']} at {dataset.get('location')}: {row['warnings']}; provide exact supplier predicates")
        if total < 1 - 1e-6:
            raise MetalPolicyError(f"{rule['id']} at {dataset.get('location')}: material supply is {total} kg; expected at least 1 kg for a 1 kg market output before an override. Resolve missing supply routes first")
        label = f"{rule['id']} at {dataset.get('location')} in {year:g} ({case})"
        reference = _source_market(dataset, references, rule)
        target = _target(rule, settings, case, year, dataset.get("location"), native_share, reference)
        row["target_secondary_share"] = target
        production = [exc for exc in exchanges if exc.get("type") == "production" and exc.get("product", exc.get("reference product")) == rule["reference product"]]
        if len(production) != 1 or production[0].get("unit") != "kilogram" or not math.isclose(_float(production[0].get("amount"), "market output"), 1, abs_tol=1e-6):
            raise MetalPolicyError(f"{label}: expected one 1 kg reference-production exchange")
        # Exact numerical identity avoids touching uncertainty/supplier metadata.
        if math.isclose(target, native_share, rel_tol=0, abs_tol=1e-12):
            row["status"] = "already_satisfied"
            audit.append(row)
            continue
        revised = [deepcopy(dict(exc)) for exc in exchanges]
        seeded_roles = []
        # A market may include distribution losses: keep its gross material
        # demand, rather than normalising 1.01 kg supplied to 1 kg output.
        for role, desired in (("primary", (1 - target) * total), ("secondary", target * total)):
            recipe = [(index, exc) for index, exc in groups[role] if float(exc.get("amount", 0)) > 0]
            if desired > 0 and not recipe:
                seeds = _seed_recipe(role, rule, reference, suppliers(), label)
                recipe = [(len(revised) + offset, exc) for offset, exc in enumerate(seeds)]
                revised.extend(deepcopy(seeds))
                seeded_roles.append(role)
            recipe_total = sum(float(exc["amount"]) for _, exc in recipe)
            for index, exc in recipe:
                _available_supplier(exc, suppliers(), label)
                if role == "secondary" and any(term in str(exc.get("name", "")).lower() for term in ("burden-free", "burden free")):
                    raise MissingMetalSupplierError(f"{label}: explicitly burden-free secondary provider is not a valid recycling LCI for this sensitivity")
                revised[index] = _scale_exchange(exc, desired / recipe_total if recipe_total else 0)
        revised_groups, _ = _groups(revised, rule)
        after_primary = sum(float(exc["amount"]) for _, exc in revised_groups["primary"])
        after_secondary = sum(float(exc["amount"]) for _, exc in revised_groups["secondary"])
        after_total = after_primary + after_secondary
        if not math.isclose(after_total, total, rel_tol=0, abs_tol=1e-9) or not math.isclose(after_secondary / after_total, target, rel_tol=0, abs_tol=1e-9):
            raise MetalPolicyError(f"{label}: planned material balance did not conserve native gross supply")
        row.update(status="planned", applied_secondary_share=after_secondary / after_total, applied_supply_total=after_total,
                   seeded_roles=", ".join(seeded_roles), changed_exchange_count=sum(a != b for a, b in zip(exchanges, revised)) + abs(len(revised) - len(exchanges)))
        row.update(_audit_suppliers(revised_groups))
        if seeded_roles:
            row["recipe_source_market"] = activity_identity(reference)
            row["recipe_source_exchanges_sha256"] = _digest(list(reference.get("exchanges", [])))
        changes.append(MarketChange(dataset_index, activity_identity(dataset), _digest(exchanges), tuple(revised)))
        audit.append(row)
    for rule in policy.rules:
        if rule["id"] not in observed_rules:
            audit.append({"rule_id": rule["id"], "sector": rule["sector"], "case": policy.case_name(rule["sector"]),
                          "status": "market_not_present", "name": rule["name"], "reference product": rule["reference product"],
                          "year": year, "policy_sha256": policy.fingerprint,
                          **_share_data_audit(rule, policy.configuration.get("metadata", {}).get("sources"))})
    stats = {"matched_markets": len([row for row in audit if row["status"] != "market_not_present"]),
             "changed_markets": len(changes), "changed_exchanges": sum(row.get("changed_exchange_count", 0) for row in audit),
             "native_baseline": policy.baseline, "nonferrous_case": policy.nonferrous_case, "steel_case": policy.steel_case,
             "steel_owner": "endogenous IAM/EDM-I",
             "statuses": {status: sum(row["status"] == status for row in audit) for status in sorted({row["status"] for row in audit})}}
    return MetalScenarioPlan(tuple(changes), tuple(audit), stats, policy.fingerprint, _plan_digest(changes, policy.fingerprint))


def apply_database(database: Iterable[Mapping], plan: MetalScenarioPlan, *, policy: MetalSteelPolicy | None = None):
    """Return a list with only planned markets copied; never mutate originals.

    Every change is validated before any copied market is constructed, so a
    stale later market cannot cause a partial application. Native baseline
    returns references to the original activity dictionaries unchanged.
    """
    if policy is not None:
        policy.verify_sources()
        if policy.fingerprint != plan.policy_fingerprint:
            raise StaleMetalPolicyError("Plan belongs to a different metals policy")
    plan.verify()
    result = list(database)
    for change in plan.changes:
        if change.dataset_index >= len(result):
            raise StaleMetalPolicyError("Inventory size changed after metals preflight")
        dataset = result[change.dataset_index]
        if activity_identity(dataset) != change.identity or _digest(list(dataset.get("exchanges", []))) != change.old_exchanges_sha256:
            raise StaleMetalPolicyError(f"Market changed after metals preflight: {change.identity!r}")
    for change in plan.changes:
        dataset = deepcopy(dict(result[change.dataset_index]))
        dataset["exchanges"] = deepcopy(list(change.new_exchanges))
        result[change.dataset_index] = dataset
    return result


# Post-update integration: native stores, legacy caches, and export provenance.
# Keep workflow receipts separate from the stricter pure-planner fingerprints.
WORKFLOW_VERSION = 1


class MetalPolicyWorkflowError(RuntimeError):
    """The selected policy cannot safely be applied to the updated inventory."""


def _workflow_digest(value):
    return sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"),
        default=lambda item: dict(item) if isinstance(item, Mapping) else
        item.item() if hasattr(item, "item") else str(item),
    ).encode("utf-8")).hexdigest()


def _version():
    try:
        return version("premise")
    except PackageNotFoundError:
        return "unavailable"


def _preflight_system_model(builder, policy):
    system_model = str(getattr(builder, "system_model", "cutoff") or "cutoff").strip().lower()
    if not policy.baseline and system_model not in {"cutoff", "apos"}:
        raise MetalPolicyWorkflowError(
            f"Alternative recycled-content policies support cutoff/apos inventories, not {system_model!r}. "
            "Keep native baseline for consequential/substitution models or develop a separate consequential policy.")


def _audit_summary(plan):
    summary = dict(plan.stats)
    summary.pop("steel_case", None)
    summary["steel_owner"] = "endogenous"
    summary["warning_rows"] = sum(bool(str(row.get("warnings") or "").strip()) for row in plan.audit)
    return summary


def _policy_description(policy):
    description = dict(policy.describe())
    description.pop("steel_case", None)
    description.update(steel_owner="endogenous", steel_model="premise native IAM/EDM-I")
    return description


def _inventory_store_api():
    from premise import inventory_store
    return inventory_store


def _inventory_validation_api():
    from premise import validation_framework
    return validation_framework


def _has_native_inventory(scenario):
    return any(scenario.get(key) is not None for key in (
        "_inventory_store", "_inventory_checkpoint", "_inventory_export_handoff",
    ))


def _open_store(scenario, api):
    from premise_compat import open_inventory_store

    try:
        return open_inventory_store(scenario, api)
    except (ValueError, TypeError, RuntimeError) as error:
        raise MetalPolicyWorkflowError(f"Updated native inventory is missing or unsupported; no reference fallback is allowed: {error}") from error


class _LazyActivity(Mapping):
    """Keep background metadata light; materialize exchanges only on demand."""

    def __init__(self, store, activity_id, metadata):
        self.store, self.activity_id = store, activity_id
        # The shared metadata iterator returns the original read-only mapping.
        # Keeping it avoids copying every background activity's metadata.
        self.metadata = metadata
        self.payload = None

    def __getitem__(self, key):
        if key == "exchanges":
            if self.payload is None:
                self.payload = self.store.activity(self.activity_id).to_dict()
                # Reopened native checkpoints can return columnar Mapping
                # proxies even from to_dict(); flatten only this selected
                # market's rows for engine hashing and uncertainty checks.
                self.payload["exchanges"] = [dict(exchange) for exchange in self.payload.get("exchanges", ())]
            return self.payload.get("exchanges", ())
        return self.metadata[key]

    def __iter__(self):
        return iter((*self.metadata, "exchanges"))

    def __len__(self):
        return len(self.metadata) + ("exchanges" not in self.metadata)


def _selected_store_inventory(store, *, policy=None, markets_only=False, diagnostics=None):
    from premise_compat import iter_activity_metadata

    database, activity_ids = [], []
    scanned = 0
    for activity_id, metadata in iter_activity_metadata(store):
        scanned += 1
        if markets_only and (policy is None or not is_policy_market(metadata, policy)):
            continue
        database.append(_LazyActivity(store, activity_id, metadata))
        activity_ids.append(activity_id)
    if scanned == 0 or (not database and not markets_only) or len(set(activity_ids)) != len(activity_ids):
        raise MetalPolicyWorkflowError("Updated native inventory is empty or has duplicate activity IDs.")
    if diagnostics is not None:
        diagnostics.update(metadata_activity_count=scanned, selected_activity_count=len(database))
    return database, activity_ids


def _source_inventory(builder, policy):
    """Capture the source's market recipes without triggering native .database."""
    source_store = getattr(builder, "_source_inventory_store", None)
    if source_store is not None:
        database = _selected_store_inventory(source_store, policy=policy, markets_only=True)[0]
        # Keep only small market recipes: holding lazy source activity proxies
        # here would prevent premise from releasing its full reference graph.
        return [dict(dataset) for dataset in database]
    source_checkpoint = getattr(builder, "_compact_source_checkpoint", None)
    if source_checkpoint is not None:
        database = _selected_store_inventory(_open_store({"_inventory_checkpoint": source_checkpoint}, _inventory_store_api()),
                                            policy=policy, markets_only=True)[0]
        return [dict(dataset) for dataset in database]
    state = vars(builder)
    for field in ("_database", "database"):
        if isinstance(state.get(field), (list, tuple)):
            return state[field]
    return None


def _native_patch(store, database, activity_ids, changes):
    """Patch existing exchange IDs; append genuine missing recipe components."""
    with store.transaction("repo:nonferrous_metal_policy") as transaction:
        patch = getattr(transaction, "patch_exchange", None)
        add = getattr(transaction, "add_exchange", None)
        for change in changes:
            activity_id = activity_ids[change.dataset_index]
            before = database[change.dataset_index]
            if activity_identity(before) != activity_identity(store.activity(activity_id).to_dict()):
                raise MetalPolicyWorkflowError("Native market activity IDs no longer match the policy plan.")
            old, new = list(before.get("exchanges", ())), list(change.new_exchanges)
            if _workflow_digest(old) != change.old_exchanges_sha256:
                raise MetalPolicyWorkflowError("Native metals exchanges changed after policy preflight.")
            ids = getattr(getattr(store, "_state", None), "activity_exchanges", {}).get(activity_id)
            if not callable(patch) or ids is None:
                raise MetalPolicyWorkflowError("premise exchange-ID patch API is unavailable; refusing an unsafe store rewrite.")
            if len(ids) != len(old) or len(new) < len(old):
                raise MetalPolicyWorkflowError("Policy removed exchanges or native IDs differ; cannot preserve the checkpoint graph.")
            address_fields = ("type", "name", "product", "reference product", "unit", "location", "input", "database")
            for exchange_id, previous, proposed in zip(ids, old, new):
                if any(previous.get(field) != proposed.get(field) for field in address_fields):
                    raise MetalPolicyWorkflowError("Policy reordered/replaced an existing supplier; cannot safely preserve native exchange IDs.")
                if previous != proposed:
                    patch(exchange_id, dict(proposed), delete_fields=set(previous) - set(proposed), activity_id=activity_id)
            if len(new) > len(old):
                if not callable(add):
                    raise MetalPolicyWorkflowError("premise cannot append the policy's verified supplier recipe.")
                for exchange in new[len(old):]:
                    add(activity_id, dict(exchange))


def _certify(builder, scenario, store, database, activity_ids, changes, summary):
    certify = getattr(builder, "_certify_scenario_store", None)
    if not callable(certify):
        if scenario.get("_validation_report") is not None:
            raise MetalPolicyWorkflowError("Native certificate cannot be refreshed after the metals policy; refusing stale validation.")
        summary["native_validation_scope"] = "no_native_validation_api"
        return
    validation = _inventory_validation_api()
    source_store = _open_store(scenario, _inventory_store_api())
    # Reuse the supplier adapter's native baseline/certification machinery; it
    # handles inherited duplicate exchanges explicitly, not blanket suppression.
    from por_supplier_linking import _graph_baseline, _certify_linked_store, _consumer_edges
    graph_context = _graph_baseline(source_store, validation)
    if graph_context is None:
        fingerprints, cycles = validation.inventory_baseline_snapshot(source_store)
    else:
        fingerprints, cycles = graph_context[:2]
    if graph_context is None:
        after_cycles = validation.inventory_cycle_signatures(store)
    else:
        _, _, before_graph, graph_keys, identities, identifiers = graph_context
        after_graph = dict(before_graph)
        for change in changes:
            after_graph[activity_ids[change.dataset_index]] = _consumer_edges(
                change.new_exchanges, identities, identifiers)
        after_cycles = validation._cycle_signatures(after_graph, graph_keys)
    changed_components = after_cycles - cycles
    # SCC signatures describe whole components, not individual cycles. Removing
    # or reweighting a route can split an inherited background component; do
    # not misreport such a split as a new isolated foreground feedback loop.
    background_reconfigurations = frozenset(component for component in changed_components
                                            if any(component & previous for previous in cycles))
    added_cycles = changed_components - background_reconfigurations
    if added_cycles:
        raise MetalPolicyWorkflowError("Metals policy introduced a new technosphere cycle; the staged graph was not published.")
    target_ids = frozenset(activity_ids[change.dataset_index] for change in changes)
    intent = validation.ValidationIntent(
        transformation="repo_nonferrous_metal_policy", targeted=True,
        affected_activity_ids=target_ids, expected_match_count=len(changes),
        baseline_fingerprints=fingerprints, baseline_cycles=cycles,
    )
    if cycles:
        # A targeted native validation sees an induced subgraph, whose cycle
        # signature may be smaller than the complete background SCC above.
        def target_cycles(inventory):
            if graph_context is not None:
                graph = before_graph if inventory is source_store else after_graph
                return set(validation._cycle_signatures(
                    {activity_id: graph.get(activity_id, set()) for activity_id in target_ids}, graph_keys))
            cycle_intent = validation.ValidationIntent(
                transformation="repo_nonferrous_metal_policy", targeted=True,
                affected_activity_ids=target_ids, expected_match_count=len(changes))
            report = validation.InventoryGraphValidator(
                inventory, intent=cycle_intent, version=str(getattr(builder, "version", "unknown")),
                system_model=str(getattr(builder, "system_model", "cutoff")), suppressions=()).validate()
            return {frozenset(tuple(key) for key in issue.actual)
                    for issue in report.issues if issue.rule_id == "GRAPH.NEW_FORBIDDEN_CYCLE"}
        known_target_cycles = target_cycles(source_store)
        reviewed_target_cycles = {cycle for cycle in target_cycles(store) - known_target_cycles
                                  if any(cycle <= component for component in background_reconfigurations)}
        if known_target_cycles or reviewed_target_cycles:
            from dataclasses import replace
            intent = replace(intent, baseline_cycles=frozenset(
                set(cycles) | known_target_cycles | reviewed_target_cycles))
    _certify_linked_store(builder, scenario, store, validation, intent,
                          activity_ids, database, summary)
    summary.update(native_validation_scope="post_update_changed_metals_markets",
                   native_validation_targets=len(changes), native_validation_new_cycles=0,
                   native_validation_background_cycle_reconfigurations=len(background_reconfigurations))


def _stage_native(builder, original, policy, source_database, directory, *, verbose=False):
    timings, diagnostics = {}, {}
    started = time.perf_counter()
    api = _inventory_store_api()
    store = _open_store(original, api)
    timings["checkpoint_acquisition"] = round(time.perf_counter() - started, 6)
    if verbose:
        print("  Native metals audit: scanning activity metadata (no background exchanges)", flush=True)
    started = time.perf_counter()
    database, activity_ids = _selected_store_inventory(store, policy=policy, markets_only=policy.baseline, diagnostics=diagnostics)
    timings["identity_scan"] = round(time.perf_counter() - started, 6)
    if verbose:
        print(f"  Native metals audit: planning {len(database)} selected identity record(s)", flush=True)
    started = time.perf_counter()
    plan = plan_database(database, policy, scenario=original, source_database=source_database)
    timings["policy_planning"] = round(time.perf_counter() - started, 6)
    summary = _audit_summary(plan)
    summary.update(phase_seconds=timings, **diagnostics)
    if not plan.changes:
        summary.update(inventory_storage="inventory_checkpoint" if original.get("_inventory_checkpoint") is not None else "inventory_store",
                       native_validation_scope="unchanged_store_generation",
                       store_generation=getattr(store, "generation", None))
        return dict(original), plan, summary, []
    plan.verify()
    fork = getattr(store, "fork", None)
    if not callable(fork):
        raise MetalPolicyWorkflowError("premise native inventory cannot be forked safely.")
    staged = fork(scenario_identity=store.scenario_identity)
    _native_patch(staged, database, activity_ids, plan.changes)
    working = dict(original)
    _certify(builder, working, staged, database, activity_ids, plan.changes, summary)
    for key in ("database", "database filepath", "database metadata filepath",
                "_inventory_export_handoff", "_inventory_working_copy"):
        working.pop(key, None)
    working["_inventory_store"] = staged
    persisted = original.get("_inventory_checkpoint") is not None
    checkpoint = None
    if persisted:
        if callable(getattr(api, "_scenario_delta_base_storage", None)):
            staged._scenario_cache_compatibility = True
        destination = directory / "inventory_stores" / f"metals_{uuid.uuid4().hex}.inventory-store"
        checkpoint = staged.checkpoint(destination)
        if checkpoint is None:
            raise MetalPolicyWorkflowError("premise did not persist the staged metals inventory.")
        working["_inventory_checkpoint"] = checkpoint
    if original.get("mapping"):
        hydrate, compact = getattr(api, "_hydrate_scenario_mapping", None), getattr(api, "_compact_scenario_mapping", None)
        activities = getattr(getattr(staged, "_state", None), "activities", None)
        if not callable(hydrate) or activities is None or (persisted and not callable(compact)):
            raise MetalPolicyWorkflowError("Native scenario-mapping API cannot rebind the metals policy's graph.")
        mapping = hydrate(original["mapping"], activities)
        working["mapping"] = compact(mapping, staged, checkpoint) if persisted else mapping
    if persisted:
        working.pop("_inventory_store", None)
    else:
        working.pop("_inventory_checkpoint", None)
    receipts = [{"activity_id": activity_ids[change.dataset_index],
                 "identity": list(activity_identity(database[change.dataset_index])),
                 "exchanges_sha256": _workflow_digest(staged.activity(activity_ids[change.dataset_index]).to_dict().get("exchanges", ()))
                } for change in plan.changes]
    summary.update(inventory_storage="inventory_checkpoint" if persisted else "inventory_store",
                   store_generation=getattr(staged, "generation", None))
    return working, plan, summary, receipts


def _legacy_helpers(load_database_func, dump_database_func):
    if load_database_func is None or dump_database_func is None:
        from premise.utils import load_database, dump_database
        load_database_func = load_database_func or load_database
        dump_database_func = dump_database_func or dump_database
    return load_database_func, dump_database_func


def _load_legacy(scenario, loader):
    working = dict(scenario)
    if working.get("database") is None:
        if not working.get("database filepath"):
            raise MetalPolicyWorkflowError("Updated inventory/cache is missing; refusing the unmodified reference database.")
        working = loader(scenario=working, original_database=[], delete=False,
                         load_metadata=False, warning=False)
    if not isinstance(working.get("database"), (list, tuple)) or not working["database"]:
        raise MetalPolicyWorkflowError("Updated legacy inventory is empty or unsupported.")
    return working


def _stage_legacy(original, policy, source_database, loader, dumper):
    started = time.perf_counter()
    working = _load_legacy(original, loader)
    acquisition = round(time.perf_counter() - started, 6)
    started = time.perf_counter()
    plan = plan_database(working["database"], policy, scenario=original, source_database=source_database)
    summary = _audit_summary(plan)
    summary["phase_seconds"] = {"checkpoint_acquisition": acquisition, "identity_scan": 0.0,
                                "policy_planning": round(time.perf_counter() - started, 6)}
    if not plan.changes:
        # Loading was into a temporary scenario dictionary only. Keep the exact
        # original cache/list and all pointers for the native baseline.
        summary["inventory_storage"] = "legacy_cache" if original.get("database") is None else "legacy_list"
        return dict(original), plan, summary, []
    working["database"] = apply_database(working["database"], plan, policy=policy)
    receipts = [{"identity": list(change.identity),
                 "exchanges_sha256": _workflow_digest(change.new_exchanges)} for change in plan.changes]
    if original.get("database") is None:
        original_path = original.get("database filepath")
        dumper(working)
        if working.get("database") is not None or not working.get("database filepath") or working.get("database filepath") == original_path:
            raise MetalPolicyWorkflowError("premise did not persist a new metals-policy cache; original cache was not published over.")
        summary["inventory_storage"] = "legacy_cache"
    else:
        summary["inventory_storage"] = "legacy_list"
    return working, plan, summary, receipts


def _write_reports(directory, rows):
    """Publish reports only after all inventory plans/validation have succeeded."""
    reports = []
    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".reports-", dir=directory) as staging:
        staging = Path(staging)
        for ordinal, (scenario, working, plan, summary, receipts, common) in enumerate(rows):
            name = re.sub(r"[^A-Za-z0-9_.-]+", "-",
                          f"{common['stage']}_{scenario.get('model')}_{scenario.get('year')}_{ordinal}")
            csv_path, json_path = directory / f"{name}.csv", directory / f"{name}.json"
            audit = list(plan.audit)
            fields = ["model", "pathway", "year", "external_scenarios"]
            fields += sorted({key for row in audit for key in row} - set(fields))
            with (staging / csv_path.name).open("w", encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=fields)
                writer.writeheader()
                for row in audit:
                    writer.writerow({"model": scenario.get("model"), "pathway": scenario.get("pathway"),
                                     "year": scenario.get("year"),
                                     "external_scenarios": " + ".join(str(entry.get("scenario")) for entry in scenario.get("external scenarios", [])),
                                     **row})
            report = {**common, "model": scenario.get("model"), "pathway": scenario.get("pathway"),
                      "year": scenario.get("year"),
                      "external_scenarios": [entry.get("scenario") for entry in scenario.get("external scenarios", [])],
                      **summary, "protected_market_receipts": receipts,
                      "change_report_path": str(csv_path.resolve()), "report_path": str(json_path.resolve())}
            (staging / json_path.name).write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
            reports.append(report)
        for path in staging.iterdir():
            os.replace(path, directory / path.name)
    return reports


def apply_metal_steel_policy(builder, *, policy, report_dir=ROOT / "export/metals_policy",
                             stage="export", verbose=True, source_database=None,
                             load_database_func=None, dump_database_func=None, prepared_baselines=None):
    """Stage every scenario, then atomically switch scenario references together."""
    policy.verify_sources()
    _preflight_system_model(builder, policy)
    scenarios = getattr(builder, "scenarios", None)
    if not isinstance(scenarios, list) or not scenarios:
        raise MetalPolicyWorkflowError("Expected an updated premise builder with a nonempty scenarios list.")
    for scenario in scenarios:
        if scenario.get("metal steel policy"):
            marker = scenario["metal steel policy"]
            if marker.get("policy_fingerprint") != policy.fingerprint:
                raise MetalPolicyWorkflowError("A different metals policy is already applied; recreate the builder.")
            raise MetalPolicyWorkflowError("Metals policy was already applied; recreate the builder instead of applying it twice.")
        if not scenario.get("applied functions"):
            raise MetalPolicyWorkflowError("Metals policy must run after premise.update(), not on the unmodified reference inventory.")
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "_" + uuid.uuid4().hex[:8]
    directory = Path(report_dir) / run_id
    common = {"workflow_version": WORKFLOW_VERSION, "stage": stage,
              "premise_version": _version(), "ecoinvent_version": str(getattr(builder, "version", "unknown")),
              "system_model": str(getattr(builder, "system_model", "unknown")),
              "nonferrous_case": policy.nonferrous_case, "steel_owner": "endogenous",
              "policy_fingerprint": policy.fingerprint, "source_sha256": dict(policy.source_hashes),
              "policy_description": _policy_description(policy),
              "excluded_scope": deepcopy(policy.configuration.get("excluded_scope", ())),
              "supported_market_rules": [{key: deepcopy(rule.get(key)) for key in
                                           ("id", "sector", "name", "reference product", "unit", "locations")}
                                          for rule in policy.rules]}
    pending = []
    had_validation_reports = hasattr(builder, "_validation_reports")
    saved_validation_reports = getattr(builder, "_validation_reports", None)
    # Native ValidationReport/Issue objects are immutable, and their nested
    # MappingProxyType payloads cannot be deep-copied. Certification replaces
    # registry entries with new reports; snapshot the associations, not the
    # live reports or inventory proxies. Preserve registry identity on rollback.
    original_validation_reports = dict(saved_validation_reports) if isinstance(saved_validation_reports, dict) else None
    try:
        for ordinal, scenario in enumerate(scenarios, 1):
            started = time.perf_counter()
            if verbose:
                print(f"Non-ferrous metals policy {ordinal}/{len(scenarios)} — {scenario.get('year')} {scenario.get('pathway')}: planning {policy.nonferrous_case}; steel remains endogenous", flush=True)
            if _has_native_inventory(scenario):
                prepared = (prepared_baselines or {}).get(id(scenario)) if policy.baseline else None
                if prepared is not None:
                    from premise_compat import checkpoint_token
                    try:
                        current_token = checkpoint_token(scenario)
                    except (OSError, ValueError, TypeError):
                        current_token = None
                    if current_token != prepared["checkpoint_token"] or prepared["plan"].policy_fingerprint != policy.fingerprint:
                        prepared = None
                if prepared is None:
                    working, plan, summary, receipts = _stage_native(builder, scenario, policy, source_database, directory, verbose=verbose)
                else:
                    prepared["plan"].verify()
                    working, plan = dict(scenario), prepared["plan"]
                    summary = deepcopy(prepared["summary"])
                    summary["baseline_capture_reused"] = True
                    receipts = []
            else:
                load_database_func, dump_database_func = _legacy_helpers(load_database_func, dump_database_func)
                working, plan, summary, receipts = _stage_legacy(scenario, policy, source_database, load_database_func, dump_database_func)
            summary["elapsed_seconds"] = round(time.perf_counter() - started, 3)
            pending.append((scenario, working, plan, summary, receipts, common))
        policy.verify_sources()
        reports = _write_reports(directory, pending)
        policy.verify_sources()
    except BaseException:
        if original_validation_reports is not None:
            saved_validation_reports.clear()
            saved_validation_reports.update(original_validation_reports)
        if had_validation_reports:
            builder._validation_reports = saved_validation_reports
        elif hasattr(builder, "_validation_reports"):
            del builder._validation_reports
        raise
    for (original, working, _, _, _, _), report in zip(pending, reports):
        working["metal steel policy"] = report
        original.clear()
        original.update(working)
    if verbose:
        print(f"Non-ferrous metals policy: {len(reports)} scenario(s). Native baseline is audit-only; steel is unchanged. Reports: {directory}", flush=True)
    return reports


def verify_metal_steel_policy(builder, policy, *, load_database_func=None):
    """Verify policy sources and final changed-market recipes before export."""
    policy.verify_sources()
    _preflight_system_model(builder, policy)
    for scenario in getattr(builder, "scenarios", []):
        marker = scenario.get("metal steel policy")
        if not marker or marker.get("policy_fingerprint") != policy.fingerprint:
            raise MetalPolicyWorkflowError("Scenario has no current metals-policy receipt; update a freshly configured builder before export.")
        receipts = marker.get("protected_market_receipts", ())
        if not receipts and not (_has_native_inventory(scenario) and scenario.get("_validation_report") is not None):
            continue
        if _has_native_inventory(scenario):
            store = _open_store(scenario, _inventory_store_api())
            for receipt in receipts:
                dataset = store.activity(receipt["activity_id"]).to_dict()
                if list(activity_identity(dataset)) != receipt["identity"] or _workflow_digest(dataset.get("exchanges", ())) != receipt["exchanges_sha256"]:
                    raise MetalPolicyWorkflowError("A metals market changed after policy validation; recreate the builder.")
            generation = getattr(store, "generation", None)
            certificate = scenario.get("_validation_report")
            if certificate is not None and certificate.get("store_generation") != generation:
                raise MetalPolicyWorkflowError("Native inventory certificate is stale after a later post-update step.")
        else:
            if scenario.get("database") is None and load_database_func is None:
                from premise.utils import load_database
                load_database_func = load_database
            working = _load_legacy(scenario, load_database_func)
            identities = {activity_identity(dataset): dataset for dataset in working["database"]}
            for receipt in receipts:
                dataset = identities.get(tuple(receipt["identity"]))
                if dataset is None or _workflow_digest(dataset.get("exchanges", ())) != receipt["exchanges_sha256"]:
                    raise MetalPolicyWorkflowError("A metals market changed after policy validation; recreate the builder.")
    return True


def attach_metal_steel_policy(builder, *, policy, report_dir=ROOT / "export/metals_policy",
                              stage="export", verbose=True, source_database=None,
                              load_database_func=None, dump_database_func=None):
    """Wrap one builder, so Brightway and Pathways share exactly the same step."""
    previous = getattr(builder, "_metal_steel_policy_fingerprint", None)
    if previous is not None:
        if previous != policy.fingerprint:
            raise MetalPolicyWorkflowError("Cannot switch policy on a live builder; recreate it for the new non-ferrous metals case.")
        return False
    if getattr(builder, "_por_supplier_linking_attached", False):
        raise MetalPolicyWorkflowError("Attach the non-ferrous metals policy before PoR supplier linking, so classifications capture the final graph.")
    policy.verify_sources()
    _preflight_system_model(builder, policy)
    if source_database is None and policy.nonferrous_case != "baseline":
        source_database = _source_inventory(builder, policy)
    original_update = builder.update

    @wraps(original_update)
    def update_with_metal_steel_policy(*args, **kwargs):
        policy.verify_sources()
        _preflight_system_model(builder, policy)
        prepared = {}
        try:
            if policy.baseline:
                from premise_compat import capture_updated_stores, checkpoint_token

                def prepare_baseline(definition, _published_store):
                    # The shared capture scope binds only the vendor's freshly
                    # certified store. Retain small results, never store/views.
                    _, plan, summary, _ = _stage_native(builder, definition, policy, None,
                                                       Path(report_dir), verbose=verbose)
                    if plan.changes:
                        raise MetalPolicyWorkflowError("Native baseline capture unexpectedly changed inventory exchanges")
                    prepared[id(definition)] = {"plan": plan, "summary": summary,
                                                "checkpoint_token": checkpoint_token(definition)}

                with capture_updated_stores(builder, prepare_baseline, priority=10):
                    result = original_update(*args, **kwargs)
            else:
                result = original_update(*args, **kwargs)
            builder.metal_steel_policy_reports = apply_metal_steel_policy(
                builder, policy=policy, report_dir=report_dir, stage=stage, verbose=verbose,
                source_database=source_database, prepared_baselines=prepared,
                load_database_func=load_database_func, dump_database_func=dump_database_func)
            return result
        finally:
            prepared.clear()

    builder.update = update_with_metal_steel_policy
    for name in ("write_db_to_brightway", "write_db_to_matrices", "write_db_to_simapro"):
        original_export = getattr(builder, name, None)
        if not callable(original_export):
            continue
        def guard(original):
            @wraps(original)
            def export_with_policy_guard(*args, **kwargs):
                verify_metal_steel_policy(builder, policy, load_database_func=load_database_func)
                return original(*args, **kwargs)
            return export_with_policy_guard
        setattr(builder, name, guard(original_export))
    builder._metal_steel_policy_fingerprint = policy.fingerprint
    builder._metal_steel_policy_nonferrous_case = policy.nonferrous_case
    if verbose:
        print(f"Attached non-ferrous metals policy ({stage}): {policy.nonferrous_case}; native baseline and endogenous steel remain unchanged.", flush=True)
    return True


def attach_metal_steel_package_provenance(package, policy, *, verbose=True):
    """Include a small policy receipt in the native archive, before it is built.

    No matrix, scenario table, classifications file, or numerical resource is
    changed.  This is instance-scoped and works with both premise exporters.
    """
    previous = getattr(package, "_metal_steel_provenance_fingerprint", None)
    if previous is not None:
        if previous != policy.fingerprint:
            raise MetalPolicyWorkflowError("Cannot change the non-ferrous metals provenance policy on a live package.")
        return False
    original_build = getattr(package, "_build_datapackage", None)
    if not callable(original_build):
        raise MetalPolicyWorkflowError("premise package archive builder API is unavailable.")

    @wraps(original_build)
    def build_with_provenance(*args, **kwargs):
        policy.verify_sources()
        builder = getattr(package, "datapackage", None)
        reports = getattr(builder, "metal_steel_policy_reports", None)
        if not reports or any(report.get("policy_fingerprint") != policy.fingerprint for report in reports):
            raise MetalPolicyWorkflowError("No validated metals-policy reports are available for package provenance.")
        path = Path.cwd() / "pathways_temp" / "metal_steel_policy.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"workflow_version": WORKFLOW_VERSION,
                   "nonferrous_case": policy.nonferrous_case, "steel_owner": "endogenous",
                   "policy_fingerprint": policy.fingerprint,
                   "source_sha256": dict(policy.source_hashes), "premise_version": _version(),
                   "policy_description": _policy_description(policy),
                   "configuration_snapshot": deepcopy(policy.configuration),
                   "baseline_semantics": "Each installed premise version's native non-ferrous behaviour; audit-only for baseline. Steel always retains native IAM/EDM-I routes.",
                   "scenario_reports": reports}
        descriptor, temporary = tempfile.mkstemp(prefix=".metal-policy-", suffix=".json", dir=path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, indent=2, default=str)
            os.replace(temporary, path)
        finally:
            if Path(temporary).exists():
                Path(temporary).unlink()
        package.metal_steel_policy_provenance = payload
        return original_build(*args, **kwargs)

    package._build_datapackage = build_with_provenance
    package._metal_steel_provenance_fingerprint = policy.fingerprint
    if verbose:
        print("Attached non-ferrous metals package provenance; endogenous steel ownership is recorded in JSON metadata.", flush=True)
    return True
