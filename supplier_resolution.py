"""Shared supplier identities and reference-inventory geography.

Reference preparation never constructs PoR markets or invents supply shares.
Scenario-specific replacements remain in ``por_supplier_linking``.
"""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from functools import lru_cache
import hashlib
import json
import math
from numbers import Number
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent
TOPOLOGY_DIR = ROOT / "data/iam_variables_mapping/topologies"
Key = tuple[str, str, str, str]  # name, product, unit, location


class SupplierLinkingError(ValueError):
    """A supplier cannot be resolved without an unsupported assumption."""


def dataset_key(dataset: dict) -> Key:
    return (dataset.get("name"), dataset.get("reference product"),
            dataset.get("unit"), dataset.get("location"))


def exchange_key(exchange: dict) -> Key:
    return (exchange.get("name"), exchange.get("product", exchange.get("reference product")),
            exchange.get("unit"), exchange.get("location"))


def unique_supplier(index: dict, key: Key) -> dict | None:
    matches = index.get(key, [])
    if len(matches) > 1:
        raise SupplierLinkingError(f"Ambiguous supplier identity ({len(matches)} activities): {key}")
    return matches[0] if matches else None


def substitute_supplier(exchange: dict, target: Key) -> dict:
    result = deepcopy(exchange)
    result.update(name=target[0], product=target[1], unit=target[2], location=target[3])
    if "reference product" in result:
        result["reference product"] = target[1]
    # Supplier addresses are stale after either a geographic or a name change.
    for field in ("input", "database", "code"):
        result.pop(field, None)
    return result


def load_iam_topology(model="remind", topology_dir=TOPOLOGY_DIR) -> tuple[dict, Path, str]:
    """Read country definitions (IAM or additional ecoinvent), independent of cwd.

    REMIND and IMAGE are both supported, but are never merged under bare labels.
    Additional models need their own ``<model>-topology.json`` in this directory.
    Read afresh so a changed topology cannot silently use a stale definition.
    """
    model = str(model).lower()
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", model):
        raise SupplierLinkingError(f"Invalid IAM model name: {model!r}")
    path = Path(topology_dir).resolve() / f"{model}-topology.json"
    if not path.is_file():
        raise SupplierLinkingError(f"Missing {model} topology: {path}")
    raw = path.read_bytes()
    topology = json.loads(raw.decode("utf-8"))
    if not isinstance(topology, dict) or not topology or any(
        not isinstance(region, str) or not isinstance(countries, list) or not countries
        or any(not isinstance(country, str) for country in countries)
        for region, countries in topology.items()
    ):
        raise SupplierLinkingError(f"Invalid IAM country definitions: {path}")
    return topology, path, hashlib.sha256(raw).hexdigest()


@lru_cache(maxsize=8)
def _geomatcher(model, topology_json, ecoinvent_json):
    from constructive_geometries import Geomatcher
    geometry = Geomatcher(backwards_compatible=True)
    # Namespace prevents collisions between IAM regions and ISO country codes
    # (e.g. IMAGE ME) and between different IAM definitions.
    geometry.add_definitions(json.loads(topology_json), namespace=model, relative=True)
    # Supplied by premise 2.3.8's ei312 topology, mirrored in the repo so package
    # updates cannot silently change these additional ecoinvent definitions.
    geometry.add_definitions(json.loads(ecoinvent_json), namespace="ecoinvent", relative=True)
    return geometry


class SupplierGeography:
    def __init__(self, model="remind", *, topology_dir=TOPOLOGY_DIR):
        self.model = str(model).lower()
        self.topology, self.path, self.sha256 = load_iam_topology(self.model, topology_dir)
        self.ecoinvent_topology, self.ecoinvent_path, self.ecoinvent_sha256 = load_iam_topology("ei312")
        self.geometry = _geomatcher(self.model, json.dumps(self.topology, sort_keys=True),
                                    json.dumps(self.ecoinvent_topology, sort_keys=True))
        self._face_cache = {}

    def faces(self, location, *, iam=False):
        cache_key = (location, iam)
        if cache_key in self._face_cache:
            return self._face_cache[cache_key]
        # ecoinvent's regional label is not present in every version of
        # constructive-geometries. Resolve its explicit geographic definition
        # instead of treating it as unknown and retaining a RoW supplier.
        if location == "RER w/o RU" and not iam:
            result = self.faces("RER").difference(self.faces("RU"))
            self._face_cache[cache_key] = result
            return result
        # A bare ISO country code stays a country (IMAGE ME also names an IAM
        # region). IAM iteration explicitly requests its namespaced definition.
        if iam or (location in self.topology and location not in self.geometry):
            key = (self.model, location)
        else:
            key = location
        try:
            result = self.geometry[key]
        except KeyError:
            result = None
        self._face_cache[cache_key] = result
        return result

    def containing_regions(self, location):
        faces = self.faces(location)
        if not faces:
            return ()
        return tuple(region for region in self.topology if region != "World"
                     if faces.issubset(self.faces(region, iam=True)))

    def covering_locations(self, location, candidates):
        """Smallest wholly covering region first; no intersecting-country mix."""
        faces = self.faces(location)
        if not faces:
            return ()
        covering = []
        for candidate in set(candidates) - {"GLO", "RoW", "World", location}:
            other = self.faces(candidate)
            if other and faces.issubset(other):
                covering.append((len(other), candidate))
        return tuple(candidate for _, candidate in sorted(covering))

    def fallback_locations(self, location):
        faces, europe = self.faces(location), self.faces("RER")
        european = ("RER", "Europe without Switzerland", "RER w/o RU") if (
            faces and europe and faces.issubset(europe)) else ()
        return tuple(dict.fromkeys((location, *self.containing_regions(location),
                                   *european, "World", "GLO", "RoW")))


def fallback_locations(location, model="remind"):
    return SupplierGeography(model).fallback_locations(location)


class ReferenceSupplierResolver:
    """Prepare copied reference inventories with deterministic 1:1 suppliers.

    Prefer a local provider, then the active IAM region, then the smallest
    covering geography. Keep an explicit foreign *process* provider; markets
    can use local equivalents. Retain a valid original proxy when no regional
    equivalent exists. Never split exchanges or delete unresolved inputs.
    """
    def __init__(self, database, model="remind", *, topology_dir=TOPOLOGY_DIR):
        self.geography = SupplierGeography(model, topology_dir=topology_dir)
        self.index = defaultdict(list)
        self.routes = defaultdict(set)
        for supplier in database:
            key = dataset_key(supplier)
            if all(key):
                self.index[key].append(supplier)
                self.routes[key[:3]].add(key[3])

    @staticmethod
    def _names(name):
        # Only this documented electricity-market family is interchangeable.
        # Product, voltage, unit and a real provider must still match exactly.
        if name.startswith("market group for electricity"):
            return (name, name.replace("market group for electricity", "market for electricity", 1))
        if name.startswith("market for electricity"):
            return (name, name.replace("market for electricity", "market group for electricity", 1))
        return (name,)

    def regional_candidate(self, exchange, location, *, exclude=None):
        """Find an exact-product/unit local or wholly covering regional route.

        Original supplier validity does not suppress this geographic search.
        No global fallback or foreign country selection happens here.
        """
        key = exchange_key(exchange)
        names = self._names(key[0])
        locations = set().union(*(self.routes[(name, key[1], key[2])] for name in names))
        ranked = tuple(dict.fromkeys((location, *self.geography.containing_regions(location),
                                      *self.geography.covering_locations(location, locations))))
        for candidate_location in ranked:
            for name in names:
                target = (name, key[1], key[2], candidate_location)
                if target != exclude and unique_supplier(self.index, target) is not None:
                    reason = "local_supplier" if candidate_location == location else "covering_regional_supplier"
                    return target, reason
        return None, "no_covering_regional_supplier"

    def resolve(self, exchange, location, *, source_location=None, exclude=None):
        key = exchange_key(exchange)
        if not all(key):
            raise SupplierLinkingError(f"Incomplete reference supplier: {key}")
        original = unique_supplier(self.index, key)
        is_market = key[0].startswith(("market for ", "market group for "))
        foreign_country = (len(key[3]) == 2 and key[3].isupper()
                           and key[3] not in self.geography.topology
                           and key[3] not in (source_location, location))
        if not is_market and foreign_country:
            if original is None:
                raise SupplierLinkingError(f"Missing pinned foreign process supplier: {key}")
            return key, "preserved_foreign_process"

        target, reason = self.regional_candidate(exchange, location, exclude=exclude)
        if target is not None:
            return target, reason
        if original is not None and key != exclude:
            return key, "retained_reference_proxy"
        for candidate_location in ("GLO", "RoW"):
            for name in self._names(key[0]):
                target = (name, key[1], key[2], candidate_location)
                if target != exclude and unique_supplier(self.index, target) is not None:
                    return target, "global_reference_proxy"
        locations = set().union(*(self.routes[(name, key[1], key[2])] for name in self._names(key[0])))
        raise SupplierLinkingError(f"No valid reference supplier for {key} in {location}; available={sorted(locations)}")

    def regionalize(self, dataset, location, *, source_location=None):
        """Return a copy and an audit, without mutating the source database."""
        if self.geography.faces(location) is None:
            raise SupplierLinkingError(f"Unknown target geography for {self.geography.model}: {location}")
        source_location = source_location or dataset.get("location")
        result = deepcopy(dataset)
        result["location"] = location
        own_key = dataset_key(result)
        audit = []
        for row, exchange in enumerate(result.get("exchanges", [])):
            if exchange.get("type") == "production":
                exchange["location"] = location
                continue
            if exchange.get("type") != "technosphere":
                continue
            amount = exchange.get("amount")
            if not isinstance(amount, Number) or not math.isfinite(float(amount)):
                raise SupplierLinkingError(f"Non-finite/nonnumeric exchange in {own_key}: {exchange}")
            if amount == 0:
                continue
            old = exchange_key(exchange)
            target, reason = self.resolve(exchange, location, source_location=source_location, exclude=own_key)
            if target != old:
                result["exchanges"][row] = substitute_supplier(exchange, target)
            audit.append({"activity": result["name"], "reference_product": result["reference product"],
                          "source_location": source_location, "activity_location": location,
                          "exchange_index": row, "old_name": old[0], "old_product": old[1],
                          "old_location": old[3], "new_name": target[0], "new_product": target[1],
                          "new_location": target[3], "amount_before": amount, "amount_after": amount,
                          "unit": exchange["unit"], "reason": reason,
                          "status": "changed" if target != old else "retained"})
        return result, audit
