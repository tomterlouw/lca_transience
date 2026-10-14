"""Scoped checkpoint-reader regressions; never write a real Brightway database."""
from copy import deepcopy
import ast
import gc
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
from typing import Any, Dict, List
import unittest
from unittest.mock import patch
import uuid

import premise_compat as compatibility
import por_supplier_linking as por_linking
from por_supplier_linking import attach_por_supplier_linking, SupplierLinkingError
from tests.test_por_supplier_linking import basic_database, policy


EXPORT_METHODS = ("write_db_to_brightway", "write_db_to_matrices", "write_db_to_simapro")


def fake_api(events):
    """Reproduce the vendor's nested base-open contract without its imports."""
    class InventoryStore:
        @classmethod
        def open(cls, path):
            events.append(("native_open", str(path)))
            if str(path) == "scenario-delta":
                return cls.open("legacy-base")
            return "legacy dictionary base"

    def open_checkpoint(path, *, prefer_columnar=False):
        events.append(("columnar_open", str(path), prefer_columnar))
        if not prefer_columnar:
            raise AssertionError("Nested bases must be opened columnarly")
        return "compact columnar base"

    return SimpleNamespace(InventoryStore=InventoryStore, _open_checkpoint=open_checkpoint,
                           mark_checkpoint=lambda path: events.append(("marked", str(path))))


def fake_builder(api, events, *, failure=None):
    builder = SimpleNamespace(scenarios=[], update=lambda: "updated")
    for method_name in EXPORT_METHODS:
        def export(*args, _method=method_name, **options):
            events.append(("export", _method, args, options))
            result = api.InventoryStore.open("scenario-delta")
            if failure:
                raise failure
            return result
        setattr(builder, method_name, export)
    return builder


class ScopedExportGuardTests(unittest.TestCase):
    def test_all_builder_exports_use_scoped_reader_and_preserve_call_arguments(self):
        events = []
        api = fake_api(events)
        descriptor = api.InventoryStore.__dict__["open"]
        builder = fake_builder(api, events)
        compatibility.attach_native_inventory_export_guard(builder, api_loader=lambda: api)
        for method_name in EXPORT_METHODS:
            with self.subTest(method=method_name):
                events.clear()
                self.assertEqual(getattr(builder, method_name)("selected-output", version="fixture"),
                                 "compact columnar base")
                self.assertEqual(events[0], ("export", method_name, ("selected-output",), {"version": "fixture"}))
                self.assertEqual(sum(event[0] == "export" for event in events), 1)
                self.assertIn(("columnar_open", "legacy-base", True), events)
                self.assertIs(api.InventoryStore.__dict__["open"], descriptor)
        # The installed reader remains unchanged between export calls.
        self.assertEqual(api.InventoryStore.open("legacy-base"), "legacy dictionary base")

    def test_exception_restores_reader_and_does_not_call_native_export_twice(self):
        events = []
        api = fake_api(events)
        descriptor = api.InventoryStore.__dict__["open"]
        builder = fake_builder(api, events, failure=RuntimeError("writer rejected fixture"))
        compatibility.attach_native_inventory_export_guard(builder, api_loader=lambda: api)
        with self.assertRaisesRegex(RuntimeError, "writer rejected fixture"):
            builder.write_db_to_brightway(name=["fixture"])
        self.assertIs(api.InventoryStore.__dict__["open"], descriptor)
        self.assertEqual(sum(event[0] == "export" for event in events), 1)

    def test_guard_is_idempotent_and_only_changes_the_selected_instance(self):
        events = []
        api = fake_api(events)
        builder = fake_builder(api, events)
        untouched = fake_builder(api, events)
        compatibility.attach_native_inventory_export_guard(builder, api_loader=lambda: api)
        wrapped = builder.write_db_to_brightway
        compatibility.attach_native_inventory_export_guard(builder, api_loader=lambda: api)
        self.assertIs(builder.write_db_to_brightway, wrapped)
        self.assertEqual(untouched.write_db_to_brightway(name="other"), "legacy dictionary base")
        events.clear()
        self.assertEqual(builder.write_db_to_brightway(name="fixture"), "compact columnar base")
        self.assertEqual(sum(event[0] == "export" for event in events), 1)

    def test_por_attachment_guards_brightway_before_any_update(self):
        events = []
        api = fake_api(events)
        builder = fake_builder(api, events)
        with patch("por_supplier_linking._inventory_store_api", return_value=api):
            self.assertTrue(attach_por_supplier_linking(builder, policy=policy(), install_support=False, verbose=False))
            self.assertFalse(attach_por_supplier_linking(builder, policy=policy(), install_support=False, verbose=False))
            self.assertEqual(builder.write_db_to_brightway(name=["fixture"]), "compact columnar base")
        self.assertEqual(sum(event[0] == "export" for event in events), 1)

    def test_export_guard_composes_with_the_existing_metals_policy_guard(self):
        from metals_scenario_policy import attach_metal_steel_policy
        from tests.test_metals_policy_workflow import fixture_policy
        events = []
        api = fake_api(events)
        builder = fake_builder(api, events)
        selected_policy = fixture_policy()
        with patch("metals_scenario_policy.verify_metal_steel_policy", return_value=True) as verify, patch(
                "por_supplier_linking._inventory_store_api", return_value=api):
            attach_metal_steel_policy(builder, policy=selected_policy, verbose=False)
            attach_por_supplier_linking(builder, policy=policy(), install_support=False, verbose=False)
            self.assertEqual(builder.write_db_to_brightway(name=["fixture"]), "compact columnar base")
        verify.assert_called_once_with(builder, selected_policy, load_database_func=None)
        self.assertEqual(sum(event[0] == "export" for event in events), 1)

    def test_older_premise_without_native_api_exports_normally(self):
        calls = []
        def unavailable():
            raise ModuleNotFoundError("No module named 'premise.inventory_store'", name="premise.inventory_store")
        builder = SimpleNamespace(write_db_to_brightway=lambda **options: calls.append(options) or "legacy exported")
        compatibility.attach_native_inventory_export_guard(builder, api_loader=unavailable)
        self.assertEqual(builder.write_db_to_brightway(name=["legacy"]), "legacy exported")
        self.assertEqual(calls, [{"name": ["legacy"]}])

    def test_missing_dependency_is_not_treated_as_older_premise(self):
        calls = []
        def unavailable():
            raise ModuleNotFoundError("No module named 'pyarrow'", name="pyarrow")
        builder = SimpleNamespace(write_db_to_brightway=lambda **options: calls.append(options))
        compatibility.attach_native_inventory_export_guard(builder, api_loader=unavailable)
        with self.assertRaises(ModuleNotFoundError) as caught:
            builder.write_db_to_brightway(name=["fixture"])
        self.assertEqual(caught.exception.name, "pyarrow")
        self.assertEqual(calls, [])

    def test_nested_scopes_restore_both_descriptors_on_failure(self):
        events = []
        api = fake_api(events)
        descriptor = api.InventoryStore.__dict__["open"]
        with self.assertRaisesRegex(RuntimeError, "fixture failure"):
            with compatibility.native_inventory_export_scope(api_loader=lambda: api):
                outer_descriptor = api.InventoryStore.__dict__["open"]
                try:
                    with compatibility.native_inventory_export_scope(api_loader=lambda: api):
                        self.assertEqual(api.InventoryStore.open("scenario-delta"), "compact columnar base")
                        raise RuntimeError("fixture failure")
                finally:
                    self.assertIs(api.InventoryStore.__dict__["open"], outer_descriptor)
        self.assertIs(api.InventoryStore.__dict__["open"], descriptor)

    def test_legacy_preflight_accepts_existing_list_and_cache_without_mutation(self):
        database = basic_database()
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / "fixture.pickle"
            cache.write_bytes(b"availability-only fixture")
            definitions = [{"database": database}, {"database filepath": cache}]
            before = deepcopy(definitions)
            builder = SimpleNamespace(scenarios=definitions)
            por_linking.preflight_por_brightway_export(builder)
            self.assertEqual(definitions, before)
            self.assertEqual(cache.read_bytes(), b"availability-only fixture")

    def test_legacy_preflight_refuses_a_missing_updated_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing.pickle"
            builder = SimpleNamespace(scenarios=[{"database filepath": missing}])
            with self.assertRaises(SupplierLinkingError):
                por_linking.preflight_por_brightway_export(builder)


class ActualNativeBrightwayLoaderTests(unittest.TestCase):
    """Execute the vendor loader unchanged, with temporary native checkpoints."""
    @classmethod
    def setUpClass(cls):
        from tests import test_por_inventory_store
        cls.fixture = test_por_inventory_store.NativeStoreIntegrationTest
        cls.fixture.setUpClass()
        cls.api = cls.fixture.api
        tree = ast.parse((cls.fixture.directory / "utils.py").read_text(encoding="utf-8"))
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "load_database")
        namespace = {"__package__": cls.fixture.package_name, "Dict": Dict, "List": List, "Any": Any, "uuid": uuid}
        exec(compile(ast.Module(body=[function], type_ignores=[]), "isolated_native_load_database", "exec"), namespace)
        cls.load_database = staticmethod(namespace["load_database"])

    @classmethod
    def tearDownClass(cls):
        cls.fixture.tearDownClass()

    def make_delta(self, directory):
        data = basic_database()
        for dataset in data:
            dataset["exchanges"].insert(0, {"type": "production", "name": dataset["name"],
                                            "product": dataset["reference product"], "unit": dataset["unit"],
                                            "location": dataset["location"], "amount": 1})
        original = self.api.create_inventory_store(data, backend="legacy", scenario_identity="fixture",
                                                   scenario_cache_compatibility=True)
        base = original.checkpoint(Path(directory) / "base.inventory-store")
        reader = self.api.InventoryStore.open_for_reporting(base)
        writable = reader.fork(scenario_identity="fixture linked")
        with writable.transaction("fixture supplier substitution") as transaction:
            exchange_id = writable._state.activity_exchanges[0][1]
            transaction.patch_exchange(exchange_id, {"name": "market for methanol (por)", "location": "NL"}, activity_id=0)
        # Match the repository's post-linking checkpoint publication path.
        writable._scenario_cache_compatibility = True
        delta = writable.checkpoint(Path(directory) / "linked.inventory-store")
        self.assertEqual(json.loads((base / "manifest.json").read_text(encoding="utf-8"))["backend"], "legacy")
        self.assertIn(json.loads((delta / "manifest.json").read_text(encoding="utf-8"))["columnar_format"],
                      ("scenario-delta-v1", "scenario-delta-v2"))
        del transaction, writable, reader, original
        gc.collect()
        return base, delta, data

    @staticmethod
    def checkpoint_hashes(directory):
        return {str(path.relative_to(directory)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in Path(directory).rglob("*") if path.is_file()}

    def test_real_delta_over_legacy_arrow_base_uses_native_consume_path(self):
        api = self.api
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            base, delta, original_data = self.make_delta(directory)
            definition = {"_inventory_checkpoint": delta}
            before = deepcopy(definition)
            files_before = self.checkpoint_hashes(directory)
            with self.assertRaisesRegex(api.InventoryStoreCorruptionError, "not a compact columnar checkpoint"):
                self.load_database(scenario=definition, original_database=[], load_metadata=True,
                                   warning=False, consume_compact=True)
            # A stand-in writer executes exactly the loader called by the real
            # Brightway export, without opening a project or writing SQL.
            calls = []
            def export(*, name):
                calls.append(name)
                return self.load_database(scenario=definition, original_database=[], load_metadata=True,
                                          warning=False, consume_compact=True)
            builder = SimpleNamespace(write_db_to_brightway=export)
            descriptor = api.InventoryStore.__dict__["open"]
            compatibility.attach_native_inventory_export_guard(builder, api_loader=lambda: api)
            # The ownership-transfer list normally installs a Wurst search
            # accelerator. It is unrelated to checkpoint loading, and importing
            # Wurst here would initialize its Brightway integration needlessly.
            with patch.object(api, "install_wurst_query_engine", return_value=False):
                loaded = builder.write_db_to_brightway(name=["isolated fixture"])
            feed = loaded["database"][0]["exchanges"][1]
            self.assertEqual((feed["name"], feed["location"], feed["amount"]),
                             ("market for methanol (por)", "NL", 2))
            self.assertEqual(calls, [["isolated fixture"]])
            self.assertIs(api.InventoryStore.__dict__["open"], descriptor)
            self.assertEqual(definition, before)
            self.assertEqual(self.checkpoint_hashes(directory), files_before)
            self.assertEqual(original_data[0]["exchanges"][1]["location"], "RER")
            self.assertTrue(base.exists())
            del feed, loaded, builder
            gc.collect()

    def test_native_checksum_validation_still_rejects_a_corrupt_base(self):
        api = self.api
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            base, delta, _ = self.make_delta(directory)
            checksums = json.loads((base / "checksums.json").read_text(encoding="utf-8"))
            payload = base / next(name for name in checksums if name != "manifest.json")
            content = payload.read_bytes()
            payload.write_bytes(content + b"corrupt fixture")
            builder = SimpleNamespace(write_db_to_brightway=lambda **options: self.load_database(
                scenario={"_inventory_checkpoint": delta}, original_database=[], load_metadata=True,
                warning=False, consume_compact=True))
            descriptor = api.InventoryStore.__dict__["open"]
            compatibility.attach_native_inventory_export_guard(builder, api_loader=lambda: api)
            with self.assertRaises(api.InventoryStoreCorruptionError):
                builder.write_db_to_brightway(name=["never written"])
            self.assertIs(api.InventoryStore.__dict__["open"], descriptor)
            del builder
            gc.collect()

    def test_preflight_reads_saved_delta_without_consuming_or_materializing_it(self):
        api = self.api
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            _, delta, _ = self.make_delta(directory)
            definition = {"model": "remind", "pathway": "fixture", "year": 2050,
                          "_inventory_checkpoint": delta,
                          "_inventory_store": object(), "_inventory_export_handoff": object()}
            before = dict(definition)
            files_before = self.checkpoint_hashes(directory)
            builder = SimpleNamespace(scenarios=[definition])
            with patch("por_supplier_linking._inventory_store_api", return_value=api), patch.object(
                    api.CompactInventoryStore, "materialize", side_effect=AssertionError("Preflight must remain lazy")), patch.object(
                    api.CompactInventoryStore, "_checkout_materialized", side_effect=AssertionError("Preflight must not consume inventories")):
                por_linking.preflight_por_brightway_export(builder)
            self.assertEqual(definition, before)
            self.assertEqual(self.checkpoint_hashes(directory), files_before)
            del builder
            gc.collect()

if __name__ == "__main__":
    unittest.main()
