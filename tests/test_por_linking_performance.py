"""Native PoR linking stays sparse and avoids full graph materialisation.

These fixtures execute the installed premise store/validator source in an
isolated namespace. They neither initialize premise nor read user databases.
Older premise releases without native stores skip this module's integration
tests; the list/pickle compatibility tests remain authoritative for them.
"""
from copy import deepcopy
import ast
import gc
import json
from pathlib import Path
import sys
import tempfile
from types import ModuleType
from typing import Any, MutableMapping
import unittest
from unittest.mock import patch

from por_supplier_linking import apply_por_supplier_linking, _graph_baseline
import tests.test_por_inventory_store as native_fixtures
from tests.test_por_supplier_linking import activity, policy


class NativeLinkingPerformanceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        native_fixtures.NativeStoreIntegrationTest.setUpClass.__func__(cls)
        # Sparse writers import cache normalization helpers. Execute only those
        # installed functions/constants, not utils.py's Brightway/cache setup.
        import numpy as np
        import pandas as pd
        module = ModuleType(cls.package_name + ".utils")
        module.__dict__.update(Any=Any, MutableMapping=MutableMapping, np=np, pd=pd)
        tree = ast.parse((cls.directory / "utils.py").read_text(encoding="utf-8"))
        constants = {"_SCENARIO_TRIMMED_DATASET_FIELDS", "_SCENARIO_TRIMMED_EXCHANGE_FIELDS",
                     "_CACHE_VALUE_CONTAINER_TYPES", "_CACHE_VALUE_INTEGER_TYPES"}
        functions = {"_has_cache_value", "_scenario_metadata_value_is_restored",
                     "_normalize_scenario_cache_activity", "_scenario_cache_exchange_field_is_restored",
                     "_normalize_scenario_cache_exchange"}
        nodes = [node for node in tree.body
                 if (isinstance(node, ast.FunctionDef) and node.name in functions)
                 or (isinstance(node, ast.Assign)
                     and any(isinstance(target, ast.Name) and target.id in constants for target in node.targets))]
        exec(compile(ast.Module(body=nodes, type_ignores=[]), "native_cache_normalization", "exec"), module.__dict__)
        sys.modules[module.__name__] = module
        cls.module_names.append(module.__name__)

    @classmethod
    def tearDownClass(cls):
        native_fixtures.NativeStoreIntegrationTest.tearDownClass.__func__(cls)

    validating_builder = native_fixtures.NativeStoreIntegrationTest.validating_builder
    valid_database = staticmethod(native_fixtures.NativeStoreIntegrationTest.valid_database)

    def native_apply(self, builder, directory):
        with patch("por_supplier_linking._inventory_store_api", return_value=self.api), patch(
            "por_supplier_linking._inventory_validation_api", return_value=self.validation
        ):
            return apply_por_supplier_linking(
                builder,
                policy=policy(),
                report_dir=Path(directory) / "reports",
                verbose=False,
            )

    @staticmethod
    def exchange_ids(store):
        return {
            activity_id: tuple(store._state.activity_exchanges[activity_id])
            for activity_id in store.iter_activity_ids()
        }

    def test_live_native_linking_patches_rows_without_changing_exchange_ids(self):
        data = self.valid_database()
        data[0]["exchanges"][1].update(
            {"input": ("old database", "stale supplier"), "database": "old database", "code": "old code",
             "uncertainty type": 3, "loc": 2, "scale": .1, "minimum": 0, "maximum": 4}
        )
        original = self.api.create_inventory_store(data, backend="compact", scenario_identity="CL")
        original_ids = self.exchange_ids(original)
        original_feed = deepcopy(original.activity(0).to_dict()["exchanges"][1])
        builder = self.validating_builder([native_fixtures.scenario(_inventory_store=original)])
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            self.native_apply(builder, directory)
        linked = builder.scenarios[0]["_inventory_store"]
        self.assertEqual(self.exchange_ids(linked), original_ids)
        self.assertEqual(linked._state.next_exchange_id, original._state.next_exchange_id)
        self.assertEqual(len(linked._state.exchanges), len(original._state.exchanges))
        self.assertEqual(original.activity(0).to_dict()["exchanges"][1], original_feed)
        exchange = linked.activity(0).to_dict()["exchanges"][1]
        self.assertEqual((exchange["name"], exchange["location"]), ("market for methanol (por)", "NL"))
        for field in ("input", "database", "code"):
            self.assertNotIn(field, exchange)
        for field in ("amount", "unit", "uncertainty type", "loc", "scale", "minimum", "maximum"):
            self.assertEqual(exchange[field], original_feed[field])
        self.assertEqual(original.generation, 0)
        self.assertEqual(linked.generation, 1)

    def test_only_selected_native_activity_payloads_are_materialized(self):
        data = self.valid_database()
        background = activity("unrelated background supplier", "background product", "RoW")
        background["comment"] = "Background metadata must not be copied by the supplier linker. " * 100
        background["exchanges"] = [{"name": background["name"], "product": background["reference product"],
                                    "location": background["location"], "unit": "kilogram",
                                    "type": "production", "amount": 1}]
        data.append(background)
        original = self.api.create_inventory_store(data, backend="compact", scenario_identity="CL")
        builder = self.validating_builder([native_fixtures.scenario(_inventory_store=original)])
        seen = []
        reader = self.api.CompactInventoryStore.activity

        def record_activity(store, activity_id):
            seen.append(activity_id)
            return reader(store, activity_id)

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory, patch.object(
            self.api.InventoryStore, "materialize", side_effect=AssertionError("Full inventory materialization is forbidden")
        ), patch.object(self.api.CompactInventoryStore, "activity", record_activity):
            reports = self.native_apply(builder, directory)
        self.assertNotIn(1, seen)  # Foreign methanol market is a metadata-only resolver entry.
        self.assertNotIn(3, seen)  # Completely unrelated background supplier.
        self.assertEqual(reports[0]["changed_exchanges"], 1)
        linked = builder.scenarios[0]["_inventory_store"]
        self.assertEqual(linked.activity(3).to_dict(), original.activity(3).to_dict())

    def test_baseline_matches_native_and_clean_linking_validates_only_once(self):
        data = self.valid_database()
        background = activity("cyclic background supplier", "background product", "RoW")
        background["exchanges"] = [
            {"name": background["name"], "product": background["reference product"],
             "location": background["location"], "unit": "kilogram", "type": "production", "amount": 1},
            {"name": background["name"], "product": background["reference product"],
             "location": background["location"], "unit": "kilogram", "type": "technosphere", "amount": .01},
        ]
        data.append(background)
        original = self.api.create_inventory_store(data, backend="compact", scenario_identity="CL")
        expected_fingerprints, expected_cycles = self.validation.inventory_baseline_snapshot(original)
        self.assertEqual(len(expected_cycles), 1)
        optimized = _graph_baseline(original, self.validation)
        self.assertIsNotNone(optimized)
        self.assertEqual(optimized[0], expected_fingerprints)
        self.assertEqual(optimized[1], expected_cycles)
        calls = []
        validate = self.validation.InventoryGraphValidator.validate

        def count_validation(validator):
            calls.append(validator.generation)
            return validate(validator)

        builder = self.validating_builder([native_fixtures.scenario(_inventory_store=original)])
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory, patch.object(
            self.validation, "inventory_baseline_snapshot",
            side_effect=AssertionError("The shared graph baseline must not be recomputed"),
        ), patch.object(
            self.validation, "inventory_cycle_signatures",
            side_effect=AssertionError("The changed adjacency must supply the post-link cycle check"),
        ), patch.object(self.validation.InventoryGraphValidator, "validate", count_validation):
            reports = self.native_apply(builder, directory)
        self.assertEqual(calls, [1])
        linked = builder.scenarios[0]["_inventory_store"]
        self.assertEqual(linked.activity(0).to_dict()["exchanges"][1]["name"], "market for methanol (por)")
        self.assertEqual(reports[0]["native_validation_baseline_cycles"], 1)
        self.assertEqual(reports[0]["native_validation_new_cycles"], 0)
        self.assertFalse(self.validation.ValidationReport.from_dict(builder.scenarios[0]["_validation_report"]).errors)
        self.assertEqual(self.validation.inventory_cycle_signatures(linked), expected_cycles)

    def test_linked_persisted_scenario_remains_sparse_and_reloads_with_stable_ids(self):
        api = self.api
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            directory = Path(directory)
            source = api.create_inventory_store(self.valid_database(), backend="compact", scenario_identity="CL")
            base_path = source.checkpoint(directory / "base.inventory-store")
            columnar = api.InventoryStore.open(base_path)
            if not callable(getattr(api, "_scenario_delta_base_storage", None)):
                self.skipTest("Installed native store does not expose sparse scenario checkpoint support")
            columnar._scenario_cache_compatibility = True
            initial_path = columnar.checkpoint(directory / "scenario.inventory-store")
            initial_manifest = json.loads((initial_path / "manifest.json").read_text(encoding="utf-8"))
            self.assertTrue(initial_manifest["columnar_format"].startswith("scenario-delta"))
            initial = api.InventoryStore.open(initial_path)
            original_ids = self.exchange_ids(initial)
            mapping = api._compact_scenario_mapping(
                {"Petchem": {"FE_PPP": [columnar._state.activities[0]]}}, columnar, initial_path
            )
            builder = self.validating_builder([
                native_fixtures.scenario(_inventory_checkpoint=initial_path, mapping=mapping)
            ])
            reports = self.native_apply(builder, directory)
            updated = builder.scenarios[0]
            new_path = updated["_inventory_checkpoint"]
            manifest = json.loads((new_path / "manifest.json").read_text(encoding="utf-8"))
            self.assertTrue(manifest["columnar_format"].startswith("scenario-delta"))
            self.assertEqual((new_path.parent / manifest["base_checkpoint"]).resolve(), base_path.resolve())
            reopened = api.InventoryStore.open(new_path)
            self.assertEqual(self.exchange_ids(reopened), original_ids)
            self.assertEqual(reopened.activity(0).to_dict()["exchanges"][1]["name"], "market for methanol (por)")
            self.assertEqual(initial.activity(0).to_dict()["exchanges"][1]["name"], "market for methanol")
            self.assertEqual(reopened.generation, initial.generation + 1)
            self.assertEqual(reports[0]["changed_exchanges"], 1)
            reference = updated["mapping"]["Petchem"]["FE_PPP"][0]
            self.assertEqual(reference._resolver.checkpoint, new_path)
            hydrated = api._hydrate_scenario_mapping(updated["mapping"], reopened._state.activities)
            self.assertIs(hydrated["Petchem"]["FE_PPP"][0], reopened._state.activities[0])
            self.assertNotIn("_inventory_store", updated)
            self.assertNotIn("_inventory_export_handoff", updated)
            del source, columnar, initial, reopened, builder, updated, mapping, reference, hydrated
            gc.collect()  # Close Arrow memory maps before Windows removes the test directory.


if __name__ == "__main__":
    unittest.main()
