"""Targeted, auditable supplier linking after premise scenario updates.

No installed package files are edited. All changes are 1:1 supplier substitutions
with unchanged quantities/units and uncertainty data. Imported routes and fixed
renewable mixes are protected using the original inventory workbook. Scenario
cache references are committed only after every selected scenario is validated.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import wraps
import hashlib
import json
import math
from numbers import Number
from pathlib import Path
import re
import uuid

import yaml

from supplier_resolution import (
    Key, SupplierLinkingError, dataset_key, exchange_key,
    unique_supplier as _unique, substitute_supplier as _substitute,
    ReferenceSupplierResolver,
)

ROOT = Path(__file__).resolve().parent
LINKING_POLICY_VERSION = 3  # v3 also replaces valid generic proxies with appropriate regional suppliers.
Pair = tuple[str, str]
_POLICIES: dict[str, "PorLinkingPolicy"] = {}


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _matches(record: dict, selector: dict) -> bool:
    operator = selector.get("operator", "equals")
    if operator not in ("equals", "contains"):
        raise SupplierLinkingError(f"Unsupported replacement operator: {operator}")
    compare = (lambda actual, wanted: actual == wanted) if operator == "equals" else (
        lambda actual, wanted: isinstance(actual, str) and isinstance(wanted, str) and wanted in actual)
    for key in ("name", "product", "reference product", "location", "unit"):
        if key in selector and not compare(record.get(key), selector[key]):
            return False
    for key, wanted in selector.get("excludes", {}).items():
        if compare(record.get(key), wanted):
            return False
    return True


def _import_pathway(alias: str, entry: dict) -> bool:
    variable = str(entry.get("production volume", {}).get("variable", ""))
    name = str(entry.get("ecoinvent alias", {}).get("name", "")).lower()
    return variable.startswith("Trade|") or "terminal" in variable.lower() or "_import" in alias or ", imported" in name


def _fixed_supply(name: str) -> bool:
    return name.startswith("electricity supply, ") and "for DAC methanol (por)" in name


def read_inventory_profiles(path: Path, protected_pairs: set[Pair] | None = None) -> dict[Key, list[dict]]:
    """Read supplier identities/amounts from the existing Brightway-style workbook."""
    import openpyxl

    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    profiles = {}
    try:
        for sheet in workbook:
            activity = header = None

            def finish():
                wanted = activity and (protected_pairs is None or dataset_key(activity)[:2] in protected_pairs
                                       or _fixed_supply(str(activity.get("name", ""))))
                if wanted and all(dataset_key(activity)):
                    key = dataset_key(activity)
                    if key in profiles:
                        raise SupplierLinkingError(f"Duplicate source activity identity in inventory: {key}")
                    profiles[key] = activity["exchanges"]

            for row in sheet.iter_rows(values_only=True):
                if not row or row[0] is None:
                    continue
                if row[0] == "Activity":
                    finish()
                    activity = {"name": row[1], "exchanges": []}
                    header = None
                elif activity is not None and row[0] == "name":
                    header = row
                elif activity is not None and header is not None:
                    record = {key: value for key, value in zip(header, row) if key}
                    wanted = protected_pairs is None or dataset_key(activity)[:2] in protected_pairs or _fixed_supply(str(activity.get("name", "")))
                    if wanted and record.get("type") == "technosphere":
                        record["product"] = record.pop("reference product", None)
                        if not all(record.get(key) for key in ("name", "unit", "location")):
                            raise SupplierLinkingError(
                                f"Incomplete source supplier at {sheet.title}: {activity['name']}. "
                                "Calculate/save workbook formulas before export.")
                        activity["exchanges"].append(record)
                elif activity is not None:
                    activity[row[0]] = row[1]
            finish()
    finally:
        workbook.close()
    return profiles


@dataclass
class PorLinkingPolicy:
    config: dict
    profiles: dict[Key, list[dict]] = field(default_factory=dict)
    locations: tuple[str, ...] = ("NL",)
    config_path: Path | None = None
    inventory_path: Path | None = None
    source_hashes: dict[str, str] = field(default_factory=dict)

    def __post_init__(self):
        self.domestic_pairs: set[Pair] = set()
        self.import_pairs: set[Pair] = set()
        for alias, entry in self.config.get("production pathways", {}).items():
            provider = entry.get("ecoinvent alias", {})
            pair = (provider.get("name"), provider.get("reference product"))
            if not all(pair):
                continue
            (self.import_pairs if _import_pathway(alias, entry) else self.domestic_pairs).add(pair)
        for provider in self.config.get("regionalize", {}).get("datasets", []):
            pair = (provider.get("name"), provider.get("reference product"))
            if all(pair):
                self.domestic_pairs.add(pair)
        self.markets = self.config.get("markets", [])
        self.market_pairs = {(market["name"], market["reference product"]) for market in self.markets}
        self.market_names = {market["name"] for market in self.markets}
        for market in self.markets:
            for provider in market.get("add", []):
                pair = (provider.get("name"), provider.get("reference product"))
                if all(pair):
                    self.domestic_pairs.add(pair)
        self.market_additions = {
            (market["name"], market["reference product"]):
            {(entry["name"], entry["reference product"]) for entry in market.get("add", [])}
            for market in self.markets
        }
        for market in self.markets:
            if float(market.get("replacement ratio", 1)) != 1:
                raise SupplierLinkingError("Post-update linking supports 1:1 replacements only; encode other quantity changes in the LCI.")

    @classmethod
    def from_files(cls, config_path=ROOT / "configuration_file/config_itom_por.yaml",
                   inventory_path=ROOT / "inventories/lci-itom_por.xlsx"):
        config_path, inventory_path = Path(config_path).resolve(), Path(inventory_path).resolve()
        hashes = {str(path): _digest(path) for path in (config_path, inventory_path)}
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        policy = cls(config, config_path=config_path, inventory_path=inventory_path,
                     source_hashes=hashes)
        policy.profiles = read_inventory_profiles(inventory_path, policy.import_pairs)
        policy.verify_sources()
        return policy

    def verify_sources(self):
        for name, digest in self.source_hashes.items():
            if not Path(name).exists() or _digest(Path(name)) != digest:
                raise SupplierLinkingError(f"Model input changed during the update: {name}. Recreate the builder.")

    def role(self, dataset: dict) -> str | None:
        pair = dataset_key(dataset)[:2]
        if pair in self.import_pairs:
            return "import"
        if _fixed_supply(str(dataset.get("name", ""))):
            return "fixed_power"
        if dataset.get("location") not in self.locations:
            return None
        if pair in self.market_pairs:
            return "constructed_market"
        if pair in self.domestic_pairs:
            if str(dataset.get("name", "")).startswith(("market for ", "market group for ")):
                return "existing_market"
            return "domestic_process"
        return None


def _note(dataset, row, old, new, reason, status="changed"):
    return {"activity": dataset["name"], "reference_product": dataset["reference product"],
            "activity_location": dataset["location"], "exchange_index": row,
            "old_name": old.get("name"), "old_product": old.get("product"), "old_location": old.get("location"),
            "new_name": new.get("name"), "new_product": new.get("product"), "new_location": new.get("location"),
            "amount_before": old.get("amount"), "amount_after": new.get("amount"),
            "unit": old.get("unit"), "reason": reason, "status": status}


def _source_profile(dataset, policy, index):
    profile = policy.profiles.get(dataset_key(dataset))
    if profile is None:
        candidates = [value for key, value in policy.profiles.items() if key[:3] == dataset_key(dataset)[:3]]
        if len(candidates) != 1:
            raise SupplierLinkingError(f"No unique original inventory profile for protected activity {dataset_key(dataset)}")
        profile = candidates[0]  # A regionalized copy retains its original import recipe.
    normalized = []
    for source in profile:
        source = dict(source)
        if not source.get("product"):
            products = {key[1] for key in index if key[0] == source.get("name")
                        and key[2] == source.get("unit") and key[3] == source.get("location")}
            if len(products) != 1:
                raise SupplierLinkingError(f"Cannot identify original supplier product: {source}")
            source["product"] = products.pop()
        normalized.append(source)
    return normalized


def _protected_target(dataset, exchange, role, policy, index):
    profile = _source_profile(dataset, policy, index)
    current_key = exchange_key(exchange)
    source_exact = [source for source in profile if exchange_key(source) == current_key]
    if source_exact:
        return current_key
    needs_restore = role == "fixed_power" or exchange.get("name") in policy.market_names
    if role == "import" and exchange.get("location") in policy.locations:
        needs_restore = True
    if not needs_restore:
        return current_key  # Retain a legitimate future foreign supplier.
    sources = {exchange_key(source): source for source in profile
               if source.get("product") == exchange.get("product") and source.get("unit") == exchange.get("unit")}
    if len(sources) != 1:
        raise SupplierLinkingError(f"Cannot uniquely restore protected supplier in {dataset_key(dataset)}: {current_key}")
    source_key = next(iter(sources))
    if _unique(index, source_key) is None:
        raise SupplierLinkingError(f"Protected source supplier is missing: {source_key}. Do not substitute an NL producer.")
    return source_key


def _regionalized_market_supply(dataset, exchange, policy, generic_regions):
    """Whether a copied NL market's product supply has a declared local route.

    This does not rebuild a market: each existing technology exchange keeps its
    quantity. Ancillary transport, unconfigured producers, explicit country
    imports and the scenario's constructed PoR market mix are not eligible.
    """
    key = exchange_key(exchange)
    pair = key[:2]
    return (
        key[1] == dataset.get("reference product")
        and key[2] == dataset.get("unit")
        and pair in policy.domestic_pairs
        and pair not in policy.import_pairs
        and not key[0].startswith(("market for ", "market group for "))
        and not _fixed_supply(key[0])
        and key[3] != dataset.get("location")
        and key[3] in generic_regions
    )


def _regional_target(dataset, exchange, policy, resolver, generic_regions):
    """Resolve generic inputs without relocating intentional foreign suppliers."""
    key = exchange_key(exchange)
    if key[:2] in policy.import_pairs or _fixed_supply(key[0]):
        return key, "protected_supply_route"
    if key[3] == dataset["location"]:
        if _unique(resolver.index, key) is not None:
            return key, "kept_local_supplier"
        # A missing local electricity group can have an exact-voltage market
        # equivalent; don't reject the alias before trying that provider.
        target, reason = resolver.regional_candidate(exchange, dataset["location"], exclude=dataset_key(dataset))
        return (target, "geographic_" + reason) if target is not None else (key, "kept_existing_supplier")
    is_market = key[0].startswith(("market for ", "market group for "))
    # Operational energy is consumed at the copied plant, not imported from
    # the source inventory's country. Imported/fixed-energy routes have already
    # been excluded above, and their consuming datasets bypass this helper.
    operational_energy = is_market and (
        (key[1].startswith("electricity, ") and key[2] == "kilowatt hour")
        or (key[1].startswith("heat, ") and key[2] == "megajoule")
    )
    # Explicit country/subnational supplies stay pinned. Aggregate reference
    # regions are geographic proxies, not evidence of a specific trade origin.
    explicit_country = bool(re.fullmatch(r"[A-Z]{2}(?:-.+)?", key[3]))
    if explicit_country and not operational_energy:
        return key, "preserved_specific_foreign_supplier"
    generic = operational_energy or key[3] in generic_regions or (
        not explicit_country and resolver.geography.faces(key[3]) is not None
    )
    missing_market = is_market and not explicit_country and _unique(resolver.index, key) is None
    if not generic and not missing_market:
        return key, "preserved_specific_foreign_supplier"
    target, reason = resolver.regional_candidate(exchange, dataset["location"], exclude=dataset_key(dataset))
    if target is not None:
        return target, "geographic_" + reason
    if _unique(resolver.index, key) is None and is_market:
        for location in ("World", "GLO", "RoW"):
            for name in resolver._names(key[0]):
                candidate = (name, key[1], key[2], location)
                if candidate != dataset_key(dataset) and _unique(resolver.index, candidate) is not None:
                    return candidate, "missing_market_global_fallback"
    return key, "retained_geographic_proxy" if key[3] in ("RoW", "GLO", "World") or operational_energy else "kept_existing_supplier"


def link_por_database(database: list[dict], policy: PorLinkingPolicy, *, model="remind"):
    """Return a validated replacement list and audit; never mutate the input list."""
    resolver = ReferenceSupplierResolver(database, model=model)
    topology, topology_path, topology_hash = resolver.geography.topology, resolver.geography.path, resolver.geography.sha256
    generic_regions = set(topology) | {
        "RER", "EUR", "RoW", "GLO", "World", "Europe without Switzerland", "RER w/o RU",
    }
    index = resolver.index
    result = list(database)
    audit = []
    selected = 0
    for position, dataset in enumerate(database):
        role = policy.role(dataset)
        if role is None:
            continue
        selected += 1
        if not all(dataset_key(dataset)):
            raise SupplierLinkingError(f"Incomplete selected activity identity: {dataset_key(dataset)}")
        # A shallow dataset copy plus copied changed exchanges preserves unrelated metadata.
        updated = dict(dataset)
        exchanges = []
        for row, original in enumerate(dataset.get("exchanges", [])):
            if original.get("type") != "technosphere":
                exchanges.append(original)
                continue
            amount = original.get("amount")
            if not isinstance(amount, Number) or isinstance(amount, bool) or not math.isfinite(float(amount)):
                raise SupplierLinkingError(f"Invalid exchange amount in {dataset_key(dataset)}: {original}")
            if amount == 0:
                exchanges.append(original)
                continue
            key = exchange_key(original)
            if not all(key):
                raise SupplierLinkingError(f"Incomplete supplier in {dataset_key(dataset)}: {key}")
            target, reason = key, "kept_existing_supplier"
            if role in ("import", "fixed_power"):
                target = _protected_target(dataset, original, role, policy, index)
                if target != key:
                    reason = "restored_protected_source_supplier"
                if role == "fixed_power":
                    matching = [source for source in _source_profile(dataset, policy, index) if exchange_key(source) == target]
                    if not matching or not math.isclose(float(amount), float(matching[0]["amount"]), rel_tol=1e-12, abs_tol=1e-12):
                        raise SupplierLinkingError(f"Fixed renewable supply share changed in {dataset_key(dataset)}")
            elif role == "existing_market" and _regionalized_market_supply(
                dataset, original, policy, generic_regions
            ):
                candidate = (*key[:3], dataset["location"])
                if _unique(index, candidate) is not None:
                    target, reason = candidate, "regionalized_market_product_supply"
                else:
                    audit.append(_note(dataset, row, original, original,
                                       f"Declared local market supplier not created: {candidate}",
                                       "unavailable_local_supplier"))
            elif role == "existing_market" and (
                key[1] != dataset["reference product"] or key[2] != dataset["unit"]
            ):
                # Regionalize ancillary services (e.g. transport), not the
                # unconfigured technologies making up the market's product mix.
                target, reason = _regional_target(dataset, original, policy, resolver, generic_regions)
            elif role == "domestic_process" or (
                role == "constructed_market" and (key[0], key[1]) in policy.market_additions[dataset_key(dataset)[:2]]
            ):
                if not _fixed_supply(key[0]):
                    targets = set()
                    for market in policy.markets:
                        # Match premise's AND semantics for 'replaces in'.
                        if not all(_matches(dataset, selector) for selector in market.get("replaces in", [])):
                            continue
                        if any(_matches(original, selector) for selector in market.get("replaces", [])):
                            candidate = (market["name"], market["reference product"], market["unit"], dataset["location"])
                            if _unique(index, candidate) is not None:
                                targets.add(candidate)
                            else:
                                audit.append(_note(dataset, row, original, original,
                                                   f"Market not created in this scenario: {candidate}", "unavailable_market"))
                    if len(targets) > 1:
                        raise SupplierLinkingError(f"Conflicting PoR replacement rules for {dataset_key(dataset)}: {key}")
                    if targets:
                        target, reason = targets.pop(), "configured_por_market"
                    else:
                        target, reason = _regional_target(dataset, original, policy, resolver, generic_regions)
            if reason == "retained_geographic_proxy":
                audit.append(_note(dataset, row, original, original, reason, "retained_geographic_proxy"))
            supplier = _unique(index, target)
            if supplier is None:
                raise SupplierLinkingError(f"Unlinked selected exchange in {dataset_key(dataset)}: {target}")
            if target == dataset_key(dataset):
                raise SupplierLinkingError(f"Direct self-supply would be introduced in {target}")
            if target != key:
                exchange = _substitute(original, target)
                audit.append(_note(dataset, row, original, exchange, reason))
            else:
                exchange = original
                address = original.get("input")
                if address is not None and supplier.get("code") and (
                    not isinstance(address, (tuple, list)) or len(address) != 2 or address[1] != supplier["code"]
                ):
                    exchange = _substitute(original, target)
                    audit.append(_note(dataset, row, original, exchange, "removed_stale_supplier_address"))
            if exchange["amount"] != amount or exchange["unit"] != original["unit"]:
                raise SupplierLinkingError("Supplier linking changed an input quantity or unit.")
            for field_name in ("uncertainty type", "loc", "scale", "minimum", "maximum", "negative"):
                left, right = exchange.get(field_name), original.get(field_name)
                same = left is right or left == right
                if not same and isinstance(left, Number) and isinstance(right, Number):
                    same = math.isnan(float(left)) and math.isnan(float(right))
                if not same:
                    raise SupplierLinkingError(f"Supplier linking changed uncertainty field {field_name}")
            exchanges.append(exchange)
        updated["exchanges"] = exchanges
        result[position] = updated

    # Do not allow an import provider to consume one of the PoR markets supplying it.
    linked_index = {dataset_key(dataset): dataset for dataset in result}
    for market in policy.markets:
        for location in policy.locations:
            key = (market["name"], market["reference product"], market["unit"], location)
            dataset = linked_index.get(key)
            if dataset is None:
                continue
            for exchange in dataset.get("exchanges", []):
                imported = linked_index.get(exchange_key(exchange)) if exchange.get("type") == "technosphere" else None
                if imported is not None and policy.role(imported) == "import":
                    if any(exchange_key(feed) == key and feed.get("amount", 0) != 0
                           for feed in imported.get("exchanges", []) if feed.get("type") == "technosphere"):
                        raise SupplierLinkingError(f"Circular market/import supply: {key} -> {dataset_key(imported)} -> {key}")
    summary = {"selected_activities": selected,
               "linking_policy_version": LINKING_POLICY_VERSION,
               "changed_exchanges": sum(row["status"] == "changed" for row in audit),
               "unavailable_markets": sum(row["status"] == "unavailable_market" for row in audit),
               "unavailable_local_suppliers": sum(row["status"] == "unavailable_local_supplier" for row in audit),
               "retained_geographic_proxies": sum(row["status"] == "retained_geographic_proxy" for row in audit),
               "topology": str(topology_path), "topology_sha256": topology_hash,
               "ecoinvent_topology": str(resolver.geography.ecoinvent_path),
               "ecoinvent_topology_sha256": resolver.geography.ecoinvent_sha256}
    return result, audit, summary


def configure_por_export_support(*, verbose=True):
    from premise_compat import disable_legacy_por_regionalization, install_premise_null_bounds_fix
    install_premise_null_bounds_fix(verbose=verbose)
    disable_legacy_por_regionalization(verbose=verbose)
    install_por_export_geography_guard(verbose=verbose)


def _register_policy(policy):
    token = getattr(policy, "_runtime_token", None)
    if token is None:
        token = uuid.uuid4().hex
        policy._runtime_token = token
    _POLICIES[token] = policy
    return token


def _policy_for_marker(marker):
    if marker.get("linking_policy_version") != LINKING_POLICY_VERSION:
        raise SupplierLinkingError(
            "Prepared PoR scenario uses outdated supplier-linking rules. "
            "Restart the kernel, recreate the builder and run update with the repo hook before exporting.")
    for path_field, hash_field in (("topology", "topology_sha256"),
                                   ("ecoinvent_topology", "ecoinvent_topology_sha256")):
        if marker.get(path_field) and marker.get(hash_field):
            if _digest(Path(marker[path_field])) != marker[hash_field]:
                raise SupplierLinkingError("Geographic definitions changed after supplier linking; rebuild the prepared scenario.")
    policy = _POLICIES.get(marker.get("policy_token"))
    if policy is not None:
        policy.verify_sources()
        return policy
    if not marker.get("config_path") or not marker.get("inventory_path"):
        raise SupplierLinkingError("No supplier-linking policy is available for this prepared scenario.")
    policy = PorLinkingPolicy.from_files(marker["config_path"], marker["inventory_path"])
    if policy.source_hashes != marker.get("source_sha256"):
        raise SupplierLinkingError("Prepared scenario sources differ from the current files; recreate the builder.")
    _register_policy(policy)
    return policy


def install_por_export_geography_guard(export_module=None, *, verbose=True):
    """Keep validated repo links through premise's later geographic export check."""
    if export_module is None:
        import premise.export as export_module
    original = export_module.check_geographical_linking
    if getattr(original, "__por_supplier_link_guard__", False):
        return False

    @wraps(original)
    def preserve_validated_links(scenario, original_database):
        marker = scenario.get("por supplier linking")
        if not marker:
            return original(scenario, original_database)
        policy = _policy_for_marker(marker)
        before_database = scenario["database"]
        pins = []
        for dataset in scenario["database"]:
            if policy.role(dataset) is None:
                continue
            for row, exchange in enumerate(dataset.get("exchanges", [])):
                if exchange.get("type") == "technosphere":
                    pins.append((dataset, row, exchange, dict(exchange)))
        prepared = original(scenario, original_database)
        if prepared.get("database") is not before_database:
            raise SupplierLinkingError("premise's geographical export check replaced the database object; review the adapter.")
        restored = []
        for dataset, row, exchange, saved in pins:
            if exchange.get("amount") != saved.get("amount") or exchange.get("unit") != saved.get("unit"):
                raise SupplierLinkingError("premise export preparation changed a validated input amount/unit.")
            if exchange_key(exchange) != exchange_key(saved):
                attempted = dict(exchange)
                for key in ("name", "product", "unit", "location"):
                    exchange[key] = saved[key]
                exchange.pop("input", None)
                exchange.pop("database", None)
                restored.append(_note(dataset, row, attempted, exchange,
                                      "preserved_repo_link_during_export", "preserved_on_export"))
        if restored:
            marker["prevented_export_geography_changes"] = marker.get("prevented_export_geography_changes", 0) + len(restored)
            report_path = marker.get("change_report_path")
            if report_path:
                path = Path(report_path)
                with path.open("r", encoding="utf-8", newline="") as stream:
                    fields = next(csv.reader(stream))
                with path.open("a", encoding="utf-8", newline="") as stream:
                    writer = csv.DictWriter(stream, fieldnames=fields)
                    for row in restored:
                        writer.writerow({"model": marker.get("model"), "pathway": marker.get("pathway"),
                                         "year": marker.get("year"),
                                         "external_scenarios": " + ".join(map(str, marker.get("external_scenarios", []))), **row})
                path.with_suffix(".json").write_text(json.dumps(marker, indent=2), encoding="utf-8")
        return prepared

    preserve_validated_links.__por_supplier_link_guard__ = True
    export_module.check_geographical_linking = preserve_validated_links
    if verbose:
        print("Applied export guard for scenarios with validated repo-managed PoR supplier links.")
    return True


def _has_por_scenario(scenario: dict) -> bool:
    if "external scenarios" not in scenario:
        return "external" in scenario.get("applied functions", [])
    for entry in scenario["external scenarios"]:
        data = entry.get("data")
        if isinstance(data, (str, Path)) and "itom_por" in str(data):
            return True
        if getattr(data, "descriptor", {}).get("name") == "itom_por":
            return True
    return False


def apply_por_supplier_linking(builder, *, policy=None, report_dir=ROOT / "export/por_supplier_linking",
                               stage="export", load_database_func=None, dump_database_func=None,
                               verbose=True):
    """Reload updated scenario caches, validate changes, then commit new references."""
    policy = policy or PorLinkingPolicy.from_files()
    policy.verify_sources()
    scenarios = getattr(builder, "scenarios", None)
    if not isinstance(scenarios, list):
        raise SupplierLinkingError("Expected a premise NewDatabase object with a scenarios list.")
    selected = [scenario for scenario in scenarios if _has_por_scenario(scenario)]
    if not selected:
        return []
    if load_database_func is None or dump_database_func is None:
        from premise.utils import load_database, dump_database
        load_database_func = load_database_func or load_database
        dump_database_func = dump_database_func or dump_database
    pending = []
    for scenario in selected:
        if "external" not in scenario.get("applied functions", []):
            raise SupplierLinkingError("PoR post-linking must run after the external scenario update.")
        if scenario.get("database") is None and not scenario.get("database filepath"):
            raise SupplierLinkingError("Updated scenario data/cache is missing; refusing to use the unmodified reference database.")
        working = dict(scenario)
        if working.get("database") is None:
            working = load_database_func(scenario=working, original_database=builder.database,
                                         delete=False, load_metadata=False, warning=False)
        database, audit, summary = link_por_database(working["database"], policy, model=str(scenario.get("model", "remind")))
        working["database"] = database
        # Keep original caches/pointers intact until every scenario and report succeeds.
        dump_database_func(working)
        if working.get("database") is not None or not working.get("database filepath"):
            raise SupplierLinkingError("premise dump_database did not persist the modified scenario as expected.")
        pending.append((scenario, working, audit, summary))
    policy.verify_sources()
    report_dir = Path(report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    reports = []
    for ordinal, (scenario, working, audit, summary) in enumerate(pending):
        label = f"{stage}_{scenario.get('model')}_{scenario.get('year')}_{ordinal}_{timestamp}"
        label = re.sub(r"[^A-Za-z0-9_.-]+", "-", label)
        csv_path = report_dir / f"{label}.csv"
        fields = ["model", "pathway", "year", "external_scenarios", "activity", "reference_product",
                  "activity_location", "exchange_index", "old_name", "old_product", "old_location",
                  "new_name", "new_product", "new_location", "amount_before", "amount_after", "unit", "reason", "status"]
        external_labels = [entry.get("scenario") for entry in scenario.get("external scenarios", [])]
        with csv_path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            for row in audit:
                writer.writerow({"model": scenario.get("model"), "pathway": scenario.get("pathway"),
                                 "year": scenario.get("year"), "external_scenarios": " + ".join(map(str, external_labels)), **row})
        metadata = {"model": scenario.get("model"), "pathway": scenario.get("pathway"), "year": scenario.get("year"),
                    "external_scenarios": external_labels, "stage": stage, **summary,
                    "source_sha256": policy.source_hashes, "change_report": csv_path.name,
                    "change_report_path": str(csv_path.resolve()), "policy_token": _register_policy(policy),
                    "config_path": str(policy.config_path) if policy.config_path else None,
                    "inventory_path": str(policy.inventory_path) if policy.inventory_path else None,
                    "policy": "Explicit PoR replacement rules; protected imports/fixed power; quantities and uncertainty retained"}
        csv_path.with_suffix(".json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        working["por supplier linking"] = metadata
        reports.append(metadata)
    for original, working, _, _ in pending:
        original.clear()
        original.update(working)
    if verbose:
        print(f"PoR supplier linking: {len(reports)} scenario(s), {sum(report['changed_exchanges'] for report in reports)} changed exchange(s). Reports: {report_dir}")
    return reports


def attach_por_supplier_linking(builder, *, policy=None, report_dir=ROOT / "export/por_supplier_linking",
                                stage="export", install_support=True, verbose=True,
                                load_database_func=None, dump_database_func=None):
    """Wrap one builder's update so both public export paths run the same post-step."""
    if getattr(builder, "_por_supplier_linking_attached", False):
        return False
    if install_support:
        configure_por_export_support(verbose=verbose)
    policy = policy or PorLinkingPolicy.from_files()
    original_update = builder.update

    @wraps(original_update)
    def update_with_por_links(*args, **kwargs):
        policy.verify_sources()
        result = original_update(*args, **kwargs)
        builder.por_supplier_linking_reports = apply_por_supplier_linking(
            builder, policy=policy, report_dir=report_dir, stage=stage,
            load_database_func=load_database_func, dump_database_func=dump_database_func,
            verbose=verbose,
        )
        return result

    builder.update = update_with_por_links
    builder._por_supplier_linking_attached = True
    return True
