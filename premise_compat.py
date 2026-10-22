"""Scoped runtime compatibility fixes for the PoR export workflow.

premise 2.3.8 creates optional ``minimum``/``maximum`` fields containing None
when external markets relink exchanges. A subsequent relink checks only whether
the keys exist, so it attempts None * float. This module changes those two
guards to require a non-null value. Numeric bounds, including zero, retain the
original scaling logic. Installed package files and inventory quantities are
not changed. The Pathways adapter also separates new metals diagnostic records
from activity mappings without discarding genuine dataset entries. Certified
inventory-store access and scoped checkpoint optimizations live here too, so
both export policies share one version-compatibility layer. The reviewed lithium
source exception changes only validation reporting, never its input quantities
or uncertainty distributions.
"""
from __future__ import annotations

import ast
from collections.abc import Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import replace
from functools import update_wrapper, wraps
import hashlib
import inspect
import json
import math
from numbers import Real
from pathlib import Path
import struct
import textwrap
from typing import Callable


class _NonNullBoundGuards(ast.NodeTransformer):
    def __init__(self) -> None:
        self.changed_fields: list[str] = []

    def visit_IfExp(self, node: ast.IfExp) -> ast.IfExp:
        self.generic_visit(node)
        test = node.test
        if (
            isinstance(test, ast.Compare)
            and isinstance(test.left, ast.Constant)
            and test.left.value in ("minimum", "maximum")
            and len(test.ops) == 1
            and isinstance(test.ops[0], ast.In)
            and len(test.comparators) == 1
            and isinstance(test.comparators[0], ast.Name)
            and test.comparators[0].id == "exc"
        ):
            field = test.left.value
            # Checking for None explicitly preserves a real bound of zero.
            node.test = ast.copy_location(
                ast.Compare(
                    left=ast.Call(
                        func=ast.Attribute(value=ast.Name(id="exc", ctx=ast.Load()),
                                           attr="get", ctx=ast.Load()),
                        args=[ast.Constant(value=field)],
                        keywords=[],
                    ),
                    ops=[ast.IsNot()],
                    comparators=[ast.Constant(value=None)],
                ),
                test,
            )
            self.changed_fields.append(field)
        return node


def _compile_none_safe_relink(original: Callable) -> Callable | None:
    """Compile the installed function with only its two null-bound guards fixed."""
    source = textwrap.dedent(inspect.getsource(original))
    tree = ast.parse(source)
    transformer = _NonNullBoundGuards()
    transformer.visit(tree)
    if not transformer.changed_fields:
        # No matching vulnerable guards: leave other/native implementations alone.
        return None
    if sorted(transformer.changed_fields) != ["maximum", "minimum"]:
        raise RuntimeError(
            "The premise external relinking implementation has changed. "
            "Expected exactly one minimum and one maximum guard; "
            f"found {transformer.changed_fields}. Review premise_compat.py."
        )
    ast.fix_missing_locations(tree)
    namespace = original.__globals__.copy()
    filename = f"{__file__}:null_safe_external_relink"
    exec(compile(tree, filename, "exec"), namespace)
    patched = namespace[original.__name__]
    update_wrapper(patched, original)
    patched.__por_null_bounds_fix__ = True
    return patched


def install_premise_null_bounds_fix(external_class=None, *, verbose: bool = True) -> bool:
    """Install once in the current process; return True only when newly applied.

    ``external_class`` is optional so the exact installed relinking function can
    be tested on small datasets without importing premise or touching Brightway.
    Normal notebook callers omit it.
    """
    if external_class is None:
        from premise.external import ExternalScenario
        external_class = ExternalScenario
    original = external_class.relink_to_new_datasets
    if getattr(original, "__por_null_bounds_fix__", False):
        return False
    patched = _compile_none_safe_relink(original)
    if patched is None:
        return False
    external_class.relink_to_new_datasets = patched
    if verbose:
        print("Applied premise external-market compatibility fix for empty uncertainty bounds.")
    return True


def disable_legacy_por_regionalization(external_class=None, *, verbose: bool = True) -> bool:
    """Disable the old user-added constructor hook in memory, when present.

    The new repo-owned post-update step replaces this broad NL relinking pass.
    Clean/new premise installations without the legacy method need no change.
    """
    if external_class is None:
        from premise.external import ExternalScenario
        external_class = ExternalScenario
    legacy = getattr(external_class, "fully_regionalize_created_inventories", None)
    if legacy is None or getattr(legacy, "__por_legacy_disabled__", False):
        return False
    source = inspect.getsource(legacy)
    if "relink_technosphere_exchanges_full" not in source or "get_recursively" not in source:
        raise RuntimeError("An unfamiliar fully_regionalize_created_inventories hook exists; review it before disabling it.")

    def disabled(self, *args, **kwargs):
        return None

    update_wrapper(disabled, legacy)
    disabled.__por_legacy_disabled__ = True
    external_class.fully_regionalize_created_inventories = disabled
    if verbose:
        print("Disabled legacy pre-market NL relinking; supplier linking is managed by the repo after update.")
    return True


PATHWAYS_MAPPING_GUARD_VERSION = 3

# premise stores reporting metadata beside the metals provider lists. Keep this
# allowlist sector-scoped: a same-named external scenario variable is still a
# genuine activity mapping, and unfamiliar malformed entries must fail loudly.
PATHWAYS_METALS_DIAGNOSTIC_KEYS = frozenset({
    "material decisions",
    "material update metrics",
    "applied market shares",
    "market diagnostics",
    "resource diagnostics",
    "methodology",
})


def pathways_activity_mapping_views(scenarios):
    """Validate/snapshot tiny provider identities before matrix export mutates data."""
    views, omitted = [], 0
    for ordinal, scenario in enumerate(scenarios, 1):
        context = f"scenario {ordinal} ({scenario.get('model', '?')}/{scenario.get('pathway', '?')}/{scenario.get('year', '?')})"
        mapping = scenario.get("mapping")
        if not isinstance(mapping, Mapping):
            raise ValueError(f"Pathways {context} has no valid sector mapping after update.")
        projected = {}
        for sector, variables in mapping.items():
            if not isinstance(variables, Mapping):
                raise ValueError(f"Invalid Pathways sector mapping in {context}: {sector!r}.")
            projected[sector] = {}
            for variable, providers in variables.items():
                if sector == "metals" and variable in PATHWAYS_METALS_DIAGNOSTIC_KEYS:
                    omitted += 1
                    continue
                location = f"{context}, {sector!r}/{variable!r}"
                if not isinstance(providers, (list, tuple)):
                    raise ValueError(f"Pathways mapping {location} must be a sequence of activities.")
                records = []
                for position, provider in enumerate(providers):
                    if not isinstance(provider, Mapping):
                        raise ValueError(f"Invalid dataset in Pathways mapping {location}, entry {position}.")
                    missing = [field for field in ("name", "reference product", "unit") if not provider.get(field)]
                    if missing:
                        raise ValueError(f"Pathways dataset mapping {location}, entry {position}, missing {missing}.")
                    record = {field: provider[field] for field in ("name", "reference product", "unit")}
                    if "lhv" in provider:
                        record["lhv"] = provider["lhv"]
                    records.append(record)
                projected[sector][variable] = records
        views.append(projected)
    return views, omitted


def write_pathways_activity_mapping(package, original, *args, snapshot=None, verbose=False, **kwargs):
    """Give only the mapper an independent export view, restoring all diagnostics."""
    scenarios = package.datapackage.scenarios
    if snapshot is None:
        views, omitted = pathways_activity_mapping_views(scenarios)
    else:
        views, omitted = snapshot
        if len(views) != len(scenarios):
            raise ValueError("Pathways scenario count changed after mapping preflight; recreate the package builder.")
    saved = [(scenario, scenario.get("mapping")) for scenario in scenarios]
    try:
        for scenario, view in zip(scenarios, views):
            scenario["mapping"] = view
        if omitted and verbose:
            print(f"Pathways mapping: excluded {omitted} metals diagnostic entries; activity mappings retained.", flush=True)
        return original(*args, **kwargs)
    finally:
        for scenario, mapping in saved:
            scenario["mapping"] = mapping


def install_premise_pathways_mapping_fix(pathways_class=None, *, verbose: bool = True) -> bool:
    """Separate new metals diagnostics from the activity-only Pathways mapping.

    premise 2.5.2 stores decisions/metrics alongside activity mappings; 2.5.5
    also stores applied shares, market/resource diagnostics and methodology.
    The Pathways writer expects name/product/unit on every entry. Use a temporary
    export view; retain original diagnostics and never drop malformed real LCIs.
    Older activity-only mappings follow the original method unchanged.
    """
    if pathways_class is None:
        from premise.pathways import PathwaysDataPackage
        pathways_class = PathwaysDataPackage
    original = pathways_class._add_variables_mapping
    if getattr(original, "__por_pathways_mapping_guard_version__", None) == PATHWAYS_MAPPING_GUARD_VERSION:
        return False
    while getattr(original, "__por_pathways_mapping_fix__", False) and hasattr(original, "__wrapped__"):
        original = original.__wrapped__

    def export_activity_mapping(self, *args, **kwargs):
        return write_pathways_activity_mapping(self, lambda *a, **k: original(self, *a, **k),
                                               *args, verbose=verbose, **kwargs)

    update_wrapper(export_activity_mapping, original)
    export_activity_mapping.__por_pathways_mapping_fix__ = True
    export_activity_mapping.__por_pathways_mapping_guard_version__ = PATHWAYS_MAPPING_GUARD_VERSION
    pathways_class._add_variables_mapping = export_activity_mapping
    if verbose:
        print("Installed repo Pathways mapping compatibility guard for metals diagnostics.", flush=True)
    return True


# ---------------------------------------------------------------------------
# Reviewed source-inventory validation compatibility
# ---------------------------------------------------------------------------

_LITHIUM_ACTIVITY = (
    "lithium carbonate production, from Salar del Hombre Muerto",
    "lithium carbonate, battery grade", "AR",
)
_LITHIUM_ELECTRICITY = (
    "market for electricity, high voltage", "electricity, high voltage", "AR",
)
_LITHIUM_SOURCE_SHA256 = "415eced99a20665d7790d404db62dccbec1206c8c1ff95d0e8d7e47637c02355"
_SOURCE_DUPLICATE_GUARD_VERSION = 1
_LITHIUM_SOURCE_PROFILES = (
    # amount, lognormal loc, scale: original workbook rows 92, 95 and 97.
    (0.014999999999999996, -6.907755278982132, 0.8950564214316266),
    (0.015, -6.907755278982135, 0.895056421431627),
    (0.3, -1.2039728043259361, 0.9066475442341566),
)
_LITHIUM_SOURCE_EXPLANATION = (
    "Repo-reviewed premise lci-lithium.xlsx source components (Salar del Hombre "
    "Muerto, rows 92 and 95). Inventory/cache float32 trimming makes the two "
    "original lognormal 0.015 kWh AR high-voltage electricity rows identical. "
    "Both rows and their uncertainty are retained, together with the separate "
    "0.30 kWh row (0.33 kWh total). This acknowledges a source-inventory finding; "
    "it does not establish that the source recipe is free of double counting. "
    "Source SHA256=" + _LITHIUM_SOURCE_SHA256
)


def _float32(value):
    return struct.unpack("f", struct.pack("f", value))[0]


def _lithium_profile_kind(exchange):
    """Recognize only the reviewed original/float32 amount-and-uncertainty tuples."""
    if (exchange.get("type") != "technosphere"
            or exchange.get("unit") != "kilowatt hour"
            or exchange.get("uncertainty type") != 2
            or bool(exchange.get("negative", False))
            or any(exchange.get(field) is not None for field in ("minimum", "maximum", "shape"))):
        return None
    values = tuple(exchange.get(field) for field in ("amount", "loc", "scale"))
    if any(isinstance(value, bool) or not isinstance(value, Real)
           or not math.isfinite(value) for value in values):
        return None
    values = tuple(float(value) for value in values)
    for position, expected in enumerate(_LITHIUM_SOURCE_PROFILES):
        if values in (expected, tuple(_float32(value) for value in expected)):
            return "small" if position < 2 else "large"
    return None


def _is_reviewed_lithium_duplicate(validator, issue, issues, source_path):
    """Accept one known source pair, never a rule-wide or activity-wide waiver."""
    if (issue.rule_id != "GRAPH.DUPLICATE_SUPPLIER"
            or tuple(issue.activity_key or ()) != _LITHIUM_ACTIVITY
            or str(validator.version) != "3.12"
            or validator.system_model != "cutoff"
            or not isinstance(issue.actual, Mapping)):
        return False
    # A third copy or any other duplicate in this activity revokes the exception.
    if sum(other.activity_id == issue.activity_id for other in issues) != 1:
        return False
    if tuple(issue.actual.get("supplier", ())) != _LITHIUM_ELECTRICITY:
        return False
    try:
        if hashlib.sha256(source_path.read_bytes()).hexdigest() != _LITHIUM_SOURCE_SHA256:
            return False
        activity = validator.store.activity(issue.activity_id)
        if (tuple(activity.get(field) for field in ("name", "reference product", "location"))
                != _LITHIUM_ACTIVITY or activity.get("unit") != "kilogram"):
            return False
        first_id = issue.actual.get("first_exchange_id")
        pair_ids = {first_id, issue.exchange_id}
        if len(pair_ids) != 2 or not pair_ids.issubset(set(activity.exchange_ids)):
            return False
        first, second = (validator.store.exchange(exchange_id) for exchange_id in (first_id, issue.exchange_id))
        if dict(first) != dict(second) or first.get("amount") != issue.actual.get("amount"):
            return False
        electricity = []
        for exchange_id in activity.exchange_ids:
            exchange = validator.store.exchange(exchange_id)
            identity = (exchange.get("name"), exchange.get("product", exchange.get("reference product")),
                        exchange.get("location"))
            if exchange.get("type") == "technosphere" and identity == _LITHIUM_ELECTRICITY:
                electricity.append((exchange_id, _lithium_profile_kind(exchange)))
        return (len(electricity) == 3
                and [kind for _, kind in electricity].count("large") == 1
                and {exchange_id for exchange_id, kind in electricity if kind == "small"} == pair_ids)
    except (OSError, KeyError, AttributeError, TypeError, ValueError):
        # Unavailable/changed source evidence keeps the original native error.
        return False


def install_premise_source_duplicate_guard(validation_api=None, *, source_path=None, verbose=True):
    """Opt in to one fingerprinted source exception, without changing LCI data.

    Installed package files are never edited. Old premise without this native
    validation API remains unchanged; missing transitive dependencies still fail.
    Every accepted finding remains visible as an explicitly suppressed issue.
    """
    if validation_api is None:
        from importlib import import_module
        try:
            validation_api = import_module("premise.validation_framework")
        except ModuleNotFoundError as error:
            if error.name != "premise.validation_framework":
                raise
            return False
    validator_class = getattr(validation_api, "InventoryGraphValidator", None)
    original = getattr(validator_class, "_apply_suppressions", None)
    if not callable(original):
        return False
    if getattr(original, "__repo_source_duplicate_guard_version__", None) == _SOURCE_DUPLICATE_GUARD_VERSION:
        return False
    if source_path is None:
        source_path = Path(validation_api.__file__).parent / "data" / "additional_inventories" / "lci-lithium.xlsx"
    source_path = Path(source_path)

    @wraps(original)
    def apply_reviewed_source_exception(self, result):
        native = original(self, result)
        if native.rule_id != "GRAPH.DUPLICATE_SUPPLIER" or not native.issues:
            return native
        issues = tuple(
            replace(issue, suppressed=True, suppression_explanation=_LITHIUM_SOURCE_EXPLANATION)
            if not issue.suppressed and _is_reviewed_lithium_duplicate(self, issue, native.issues, source_path)
            else issue for issue in native.issues
        )
        return replace(native, issues=issues)

    apply_reviewed_source_exception.__repo_source_duplicate_guard_version__ = _SOURCE_DUPLICATE_GUARD_VERSION
    validator_class._apply_suppressions = apply_reviewed_source_exception
    if verbose:
        print("Enabled guarded lithium source-inventory duplicate review; quantities and uncertainty are unchanged.")
    return True


def preflight_reference_database_export(builder, *, verbose=True):
    """Certify a fresh reference builder before deleting its existing BW target.

    This deliberately performs no scenario update. Native validation remains
    enabled, with the reviewed source exception installed by the repo setup.
    Older premise without semantic certification uses its normal export checks.
    """
    install_premise_source_duplicate_guard(verbose=verbose)
    attach_native_inventory_export_guard(builder)
    certify = getattr(builder, "_ensure_semantic_certification", None)
    if not callable(certify):
        return []
    reports = []
    for scenario in builder.scenarios:
        report = certify(scenario)
        if report is not None:
            report.raise_for_errors()
            reports.append(report)
    return reports


# ---------------------------------------------------------------------------
# Certified native inventory access and scoped checkpoint optimizations
# ---------------------------------------------------------------------------

_BOUND_STORES = ContextVar("por_bound_native_stores", default=None)
_CAPTURE_ATTRIBUTE = "_repo_native_store_capture"


def iter_activity_metadata(store):
    """Yield identities without allocating every activity's exchange-ID tuple."""
    underlying = getattr(store, "_store", store)
    state = getattr(underlying, "_state", None)
    activities = getattr(state, "activities", None)
    if isinstance(activities, Mapping):
        order = getattr(state, "activity_order", None)
        ids = order if order is not None else store.iter_activity_ids()
        for activity_id in ids:
            yield activity_id, activities[activity_id]
        return
    iterator = getattr(underlying, "_iter_storage_activities", None)
    if callable(iterator):
        for activity_id, metadata, _ in iterator():
            yield activity_id, metadata
        return
    for activity_id in store.iter_activity_ids():
        record = store.activity(activity_id)
        if not isinstance(record, Mapping):
            record = record.to_dict()
        yield activity_id, record


def _file_token(path):
    stat = path.stat()
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


def checkpoint_token(scenario):
    """Small freshness receipt, never a substitute for initial native validation.

    The capture hook binds only the store returned by the native publisher after
    its certification/checkpoint write. Hash its manifest/checksum documents and
    stat every referenced payload (including a delta's base), so changed sources
    invalidate that binding/prepared results before publication.
    """
    checkpoint = scenario.get("_inventory_checkpoint")
    if checkpoint is None:
        store = scenario.get("_inventory_store")
        if store is None:
            store = scenario.get("_inventory_export_handoff")
        if store is None:
            return ("missing",)
        return ("live", id(store), getattr(store, "generation", None))
    path = Path(checkpoint).resolve()
    visited = set()

    def token(directory):
        if directory in visited:
            raise ValueError("Cyclic native checkpoint base references")
        visited.add(directory)
        if directory.is_file():
            return (str(directory), _file_token(directory))  # lightweight test/legacy adapters
        manifest_path, checksum_path = directory / "manifest.json", directory / "checksums.json"
        manifest_bytes, checksum_bytes = manifest_path.read_bytes(), checksum_path.read_bytes()
        manifest, checksums = json.loads(manifest_bytes), json.loads(checksum_bytes)
        files = tuple((name, _file_token(directory / name)) for name in sorted(checksums))
        base = manifest.get("base_checkpoint")
        base_token = token((directory.parent / base).resolve()) if isinstance(base, str) else None
        return (str(directory), hashlib.sha256(manifest_bytes).hexdigest(),
                hashlib.sha256(checksum_bytes).hexdigest(), files, base_token)

    live = scenario.get("_inventory_store")
    live_token = (id(live), getattr(live, "generation", None)) if live is not None else None
    return ("checkpoint", token(path), live_token)


@contextmanager
def legacy_delta_base_columnar_scope(api, *, reader_bypasses_inventory_open=False):
    """Open nested scenario-delta bases columnarly for the duration of a call."""
    inventory_store_type = api.InventoryStore
    if not isinstance(inventory_store_type, type):
        yield
        return
    descriptor = inventory_store_type.__dict__["open"]
    if not isinstance(descriptor, classmethod):
        raise TypeError("InventoryStore.open must be a classmethod")
    original_open = descriptor.__func__
    depth = ContextVar("por_inventory_checkpoint_open_depth", default=0)

    @wraps(original_open)
    def open_with_columnar_delta_base(cls, path):
        nested_open = depth.get() > 0
        token = depth.set(depth.get() + 1)
        try:
            if nested_open:
                store = api._open_checkpoint(Path(path), prefer_columnar=True)
                api.mark_checkpoint(Path(path))
                return store
            return original_open(cls, path)
        finally:
            depth.reset(token)

    inventory_store_type.open = classmethod(open_with_columnar_delta_base)
    try:
        if reader_bypasses_inventory_open:
            token = depth.set(1)
            try:
                yield
            finally:
                depth.reset(token)
        else:
            yield
    finally:
        inventory_store_type.open = descriptor


def open_checkpoint_with_legacy_base(api, path, reader=None):
    """Open a checkpoint while making any scenario-delta base read columnar."""
    if reader is None:
        with legacy_delta_base_columnar_scope(api):
            return api.InventoryStore.open(path)
    with legacy_delta_base_columnar_scope(api, reader_bypasses_inventory_open=True):
        return reader(path)


@contextmanager
def native_inventory_export_scope(*, api_loader=None):
    """Keep native delta bases columnar during an otherwise unchanged export.

    premise 2.3.8 has no inventory-store module. Only that exact missing module
    is a no-op; missing dependencies and native corruption errors still fail.
    The installed reader is restored on success and failure, including nested
    package/builder scopes. No checkpoint files or validation rules are changed.
    """
    if api_loader is None:
        from importlib import import_module
        api_loader = lambda: import_module("premise.inventory_store")
    try:
        api = api_loader()
    except ModuleNotFoundError as error:
        if error.name != "premise.inventory_store":
            raise
        yield
        return
    with legacy_delta_base_columnar_scope(api):
        yield


def attach_native_inventory_export_guard(builder, *, api_loader=None):
    """Guard this builder's native exports, without rerunning its update.

    Compose with existing instance-bound metals/export validation wrappers.
    Import the optional native API only when exporting, not when attaching.
    """
    changed = False
    for name in ("write_db_to_brightway", "write_db_to_matrices", "write_db_to_simapro"):
        original = getattr(builder, name, None)
        if not callable(original) or getattr(original, "__repo_native_export_guard_version__", None) == 1:
            continue

        def guard(export):
            @wraps(export)
            def export_with_columnar_bases(*args, **kwargs):
                with native_inventory_export_scope(api_loader=api_loader):
                    return export(*args, **kwargs)
            export_with_columnar_bases.__repo_native_export_guard_version__ = 1
            return export_with_columnar_bases

        setattr(builder, name, guard(original))
        changed = True
    return changed


def open_inventory_store(scenario, api):
    """Current live store > freshly bound checkpoint > native open > handoff."""
    store = scenario.get("_inventory_store")
    if store is not None:
        return store
    checkpoint = scenario.get("_inventory_checkpoint")
    if checkpoint is not None:
        bindings = _BOUND_STORES.get()
        path = str(Path(checkpoint).resolve())
        bound = bindings.get(path) if bindings else None
        if bound is not None:
            receipt, generation, store = bound
            if getattr(store, "generation", None) == generation and checkpoint_token(scenario) == receipt:
                return store
        # Outside a publication callback, retain the vendor's full corruption
        # and checksum checks. Do not trust arbitrary export handoffs.
        reporting_open = getattr(api.InventoryStore, "open_for_reporting", None)
        if callable(reporting_open):
            # Native legacy Arrow checkpoints are otherwise eagerly expanded
            # into every exchange dictionary. The public reporting reader keeps
            # them columnar, with the SAME vendor checksum/corruption checks.
            # Consumers mutate only a separate writable fork of this view.
            return open_checkpoint_with_legacy_base(api, checkpoint, reporting_open)
        return open_checkpoint_with_legacy_base(api, checkpoint)
    return scenario.get("_inventory_export_handoff")


@contextmanager
def capture_updated_stores(builder, callback, *, priority=10):
    """Call a small/staging repo callback once per successfully published store.

    Hooks exist only on this builder and only inside update(), and are removed
    even on failure. Nested metals/PoR scopes share one hook; deterministic
    priorities retain their required order. Native reports are restored after
    staging: each adapter publishes staged reports with its prepared inventory
    only after all scenarios and audits succeeded. Never retain stores here.
    """
    publisher = getattr(builder, "_store_updated_scenario", None)
    state = getattr(builder, _CAPTURE_ATTRIBUTE, None)
    if state is None:
        if not callable(publisher):
            yield False
            return
        try:
            signature = inspect.signature(publisher)
        except (ValueError, TypeError):
            yield False
            return
        if "scenario_definition" not in signature.parameters:
            yield False
            return
        state = {"original": publisher, "signature": signature, "callbacks": []}

        @wraps(publisher)
        def publish_and_capture(*args, **kwargs):
            store = publisher(*args, **kwargs)
            if store is None or not callable(getattr(store, "activity", None)):
                return store
            definition = signature.bind(*args, **kwargs).arguments["scenario_definition"]
            bindings = {}
            if definition.get("_inventory_checkpoint") is not None:
                path = str(Path(definition["_inventory_checkpoint"]).resolve())
                bindings[path] = (checkpoint_token(definition), getattr(store, "generation", None), store)
            scope = _BOUND_STORES.set(bindings)
            present = hasattr(builder, "_validation_reports")
            registry = getattr(builder, "_validation_reports", None)
            associations = dict(registry) if isinstance(registry, dict) else None
            try:
                for _, active_callback in sorted(state["callbacks"], key=lambda entry: entry[0]):
                    active_callback(definition, store)
            finally:
                _BOUND_STORES.reset(scope)
                if associations is not None:
                    registry.clear()
                    registry.update(associations)
                if present:
                    builder._validation_reports = registry
                elif hasattr(builder, "_validation_reports"):
                    del builder._validation_reports
            return store

        state["wrapper"] = publish_and_capture
        setattr(builder, _CAPTURE_ATTRIBUTE, state)
        builder._store_updated_scenario = publish_and_capture
    entry = (priority, callback)
    state["callbacks"].append(entry)
    try:
        yield True
    finally:
        state["callbacks"].remove(entry)
        if not state["callbacks"]:
            builder._store_updated_scenario = state["original"]
            delattr(builder, _CAPTURE_ATTRIBUTE)
