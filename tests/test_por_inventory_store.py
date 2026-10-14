"""Legacy/cache and native-store post-update compatibility, without real LCA DBs."""
from copy import deepcopy
import ast
import gc
import logging
import os
from importlib.util import find_spec, module_from_spec, spec_from_file_location
from pathlib import Path
import pickle
import sys
import tempfile
from types import ModuleType, SimpleNamespace
from typing import Any, Dict, List
from dataclasses import replace
import time
import unittest
from unittest.mock import patch
import uuid

from por_supplier_linking import apply_por_supplier_linking, attach_por_supplier_linking, SupplierLinkingError, PorLinkingPolicy
from tests.test_por_supplier_linking import basic_database, policy


class FakeStore:
    backend_name = "compact"

    def __init__(self, database, generation=0, scenario_identity="CL"):
        self._state = SimpleNamespace(activities=dict(enumerate(deepcopy(database))))
        self.generation = generation
        self.scenario_identity = scenario_identity

    def materialize(self, restore_metadata=True):
        return deepcopy(list(self._state.activities.values()))

    def iter_activity_ids(self):
        return iter(self._state.activities)

    def activity(self, activity_id):
        return SimpleNamespace(to_dict=lambda: deepcopy(self._state.activities[activity_id]))

    def fork(self, scenario_identity=None):
        return FakeStore(self.materialize(), self.generation, scenario_identity)

    def transaction(self, label):
        store = self

        class Transaction:
            def __enter__(self): return self
            def __exit__(self, *error): store.generation += 1
            def replace_exchanges(self, activity_id, exchanges):
                store._state.activities[activity_id]["exchanges"] = deepcopy(exchanges)
        return Transaction()

    def checkpoint(self, path):
        path = Path(path)
        path.mkdir(parents=True)
        (path / "fixture.pickle").write_bytes(pickle.dumps((self.materialize(), self.generation)))
        return path

    @classmethod
    def open(cls, path):
        data, generation = pickle.loads((Path(path) / "fixture.pickle").read_bytes())
        return cls(data, generation)


def scenario(**storage):
    return {"model": "remind", "pathway": "test", "year": 2050,
            "external scenarios": [{"scenario": "CL", "data": "datapackage_itom_por.json"}],
            "applied functions": ["external"], **storage}


class StoreCompatibilityTest(unittest.TestCase):
    def apply(self, builder, directory):
        api = SimpleNamespace(InventoryStore=FakeStore)
        validation = SimpleNamespace(ValidationIntent=SimpleNamespace,
                                     inventory_baseline_snapshot=lambda store: ({}, frozenset()),
                                     inventory_cycle_signatures=lambda store: frozenset())
        with patch("por_supplier_linking._inventory_store_api", return_value=api), patch(
                "por_supplier_linking._inventory_validation_api", return_value=validation):
            return apply_por_supplier_linking(builder, policy=policy(), report_dir=Path(directory) / "reports",
                                              verbose=False)

    def test_live_store_is_linked_in_a_fork_with_quantities_and_uncertainty_preserved(self):
        data = basic_database()
        data[0]["exchanges"][0].update({"minimum": 0, "maximum": None, "scale": .2, "uncertainty type": 2})
        original = FakeStore(data)
        before = original.materialize()
        builder = SimpleNamespace(scenarios=[scenario(_inventory_store=original)])
        with tempfile.TemporaryDirectory() as directory:
            reports = self.apply(builder, directory)
        updated = builder.scenarios[0]["_inventory_store"]
        self.assertIsNot(updated, original)
        self.assertEqual(original.materialize(), before)
        self.assertEqual(original.generation, 0)
        exc = updated.materialize()[0]["exchanges"][0]
        self.assertEqual((exc["name"], exc["amount"], exc["minimum"], exc["maximum"], exc["scale"]),
                         ("market for methanol (por)", 2, 0, None, .2))
        self.assertEqual(reports[0]["inventory_storage"], "inventory_store")
        self.assertNotIn("database", builder.scenarios[0])

    def test_checkpoint_changes_survive_reload_and_stale_handoff_is_removed(self):
        with tempfile.TemporaryDirectory() as directory:
            old_path = FakeStore(basic_database()).checkpoint(Path(directory) / "old.inventory-store")
            # Simulate the old one-use handoff already having been consumed.
            builder = SimpleNamespace(scenarios=[scenario(_inventory_checkpoint=old_path,
                                                          _inventory_export_handoff=FakeStore([]))])
            reports = self.apply(builder, directory)
            updated = builder.scenarios[0]
            self.assertNotEqual(updated["_inventory_checkpoint"], old_path)
            self.assertTrue(old_path.exists())
            self.assertNotIn("_inventory_export_handoff", updated)
            self.assertNotIn("_inventory_store", updated)
            self.assertNotIn("database filepath", updated)
            self.assertEqual(FakeStore.open(updated["_inventory_checkpoint"]).materialize()[0]["exchanges"][0]["name"],
                             "market for methanol (por)")
            self.assertEqual(FakeStore.open(old_path).materialize()[0]["exchanges"][0]["name"], "market for methanol")
            self.assertEqual(reports[0]["inventory_storage"], "inventory_checkpoint")

    def test_native_storage_does_not_read_the_reference_database(self):
        class Builder:
            scenarios = [scenario(_inventory_store=FakeStore(basic_database()))]
            @property
            def database(self): raise AssertionError("Reference database must never be read")
        with tempfile.TemporaryDirectory() as directory:
            self.apply(Builder(), directory)

    def test_native_validation_is_rerun_with_a_targeted_post_update_intent(self):
        original = FakeStore(basic_database())
        calls = []
        def certify(working, store, *, exhaustive, intent):
            self.assertTrue(exhaustive)
            self.assertTrue(intent.targeted)
            self.assertEqual(intent.transformation, "por_supplier_linking")
            self.assertEqual(intent.affected_activity_ids, frozenset({0}))
            self.assertEqual(intent.expected_match_count, 1)
            self.assertEqual(store.materialize()[0]["exchanges"][0]["name"], "market for methanol (por)")
            calls.append(store.generation)
            working["_validation_report"] = {"store_generation": store.generation}
        builder = SimpleNamespace(scenarios=[scenario(_inventory_store=original,
                                                      _validation_report={"store_generation": 0})],
                                  _certify_scenario_store=certify)
        with tempfile.TemporaryDirectory() as directory:
            self.apply(builder, directory)
        self.assertEqual(calls, [1])
        self.assertEqual(builder.scenarios[0]["_validation_report"], {"store_generation": 1})

    def test_certificate_failure_preserves_source_store(self):
        original = FakeStore(basic_database())
        definition = scenario(_inventory_store=original)
        def reject(*args, **kwargs): raise RuntimeError("native validation failed")
        builder = SimpleNamespace(scenarios=[definition], _certify_scenario_store=reject)
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RuntimeError, "native validation failed"):
                self.apply(builder, directory)
        self.assertIs(definition["_inventory_store"], original)
        self.assertNotIn("por supplier linking", definition)
        self.assertEqual(original.generation, 0)

    def test_later_scenario_failure_does_not_commit_earlier_store(self):
        good, bad = FakeStore(basic_database()), FakeStore(basic_database()[:1])
        definitions = [scenario(_inventory_store=good), scenario(_inventory_store=bad)]
        builder = SimpleNamespace(scenarios=definitions)
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(SupplierLinkingError): self.apply(builder, directory)
        self.assertIs(definitions[0]["_inventory_store"], good)
        self.assertIs(definitions[1]["_inventory_store"], bad)
        self.assertFalse(any("por supplier linking" in d for d in definitions))

    def test_corrupt_checkpoint_is_not_replaced_by_reference_inventory(self):
        builder = SimpleNamespace(scenarios=[scenario(_inventory_checkpoint=Path("missing.inventory-store"))])
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(FileNotFoundError): self.apply(builder, directory)

    def test_missing_and_empty_stores_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            for storage in ({}, {"_inventory_store": FakeStore([])}):
                with self.subTest(storage=list(storage)):
                    with self.assertRaises(SupplierLinkingError):
                        self.apply(SimpleNamespace(scenarios=[scenario(**storage)]), directory)

    def test_stale_certificate_cannot_be_exported_without_native_validation(self):
        definition = scenario(_inventory_store=FakeStore(basic_database()), _validation_report={"store_generation": 0})
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(SupplierLinkingError, "certificate"):
                self.apply(SimpleNamespace(scenarios=[definition]), directory)
        self.assertEqual(definition["_inventory_store"].generation, 0)

    def test_store_activity_order_mismatch_is_rejected(self):
        class ReversedStore(FakeStore):
            def fork(self, scenario_identity=None):
                # A different iteration order alone is safe when records are
                # selected by stable IDs. Simulate an actual broken fork that
                # reassigns those IDs to different activities instead.
                return FakeStore(list(reversed(self.materialize(restore_metadata=True))),
                                 self.generation, scenario_identity)
        definition = scenario(_inventory_store=ReversedStore(basic_database()))
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(SupplierLinkingError, "activity order"):
                self.apply(SimpleNamespace(scenarios=[definition]), directory)
        self.assertEqual(definition["_inventory_store"].generation, 0)

    def test_unchanged_generation_does_not_reaudit_background(self):
        data = basic_database()
        data[0]["exchanges"][0]["name"] = "market for methanol (por)"
        data[0]["exchanges"][0]["location"] = "NL"
        def certify(*args, **kwargs): raise AssertionError("No mutation: retain the existing certificate")
        builder = SimpleNamespace(scenarios=[scenario(_inventory_store=FakeStore(data), _validation_report={"store_generation": 0})],
                                  _certify_scenario_store=certify)
        with tempfile.TemporaryDirectory() as directory:
            reports = self.apply(builder, directory)
        self.assertEqual(reports[0]["native_validation_scope"], "unchanged_store_generation")
        self.assertEqual(builder.scenarios[0]["_inventory_store"].generation, 0)

    def test_both_scenarios_use_the_attached_update_hook(self):
        builder = SimpleNamespace(scenarios=[scenario(_inventory_store=FakeStore(basic_database())) for _ in range(2)])
        calls = []
        builder.update = lambda **kwargs: calls.append(kwargs) or "native update result"
        with tempfile.TemporaryDirectory() as directory, patch(
                "por_supplier_linking._inventory_store_api", return_value=SimpleNamespace(InventoryStore=FakeStore)):
            attach_por_supplier_linking(builder, policy=policy(), report_dir=Path(directory) / "reports",
                                       install_support=False, verbose=False)
            self.assertEqual(builder.update(persist=False), "native update result")
            self.assertEqual(len(builder.por_supplier_linking_reports), 2)
        self.assertEqual(calls, [{"persist": False}])


class NativeStoreIntegrationTest(unittest.TestCase):
    """Exercise the installed native checkpoint code in an isolated module namespace."""
    @classmethod
    def setUpClass(cls):
        package = find_spec("premise")
        if package is None: raise unittest.SkipTest("premise source unavailable")
        cls.directory = Path(next(iter(package.submodule_search_locations)))
        if not (cls.directory / "inventory_store.py").exists():
            raise unittest.SkipTest("Older premise has no native inventory store; legacy tests apply")
        cls.package_name = "_por_native_store_test"
        package_module = ModuleType(cls.package_name)
        package_module.__path__ = [str(cls.directory)]
        cleanup_module = ModuleType(cls.package_name + ".cache_cleanup")
        cleanup_module.mark_checkpoint = lambda path, **kwargs: None
        cls.module_names = [cls.package_name, cls.package_name + ".cache_cleanup",
                            cls.package_name + ".inventory_copy", cls.package_name + ".inventory_store",
                            cls.package_name + ".filesystem_constants", cls.package_name + ".validation_framework"]
        sys.modules[cls.package_name] = package_module
        sys.modules[cleanup_module.__name__] = cleanup_module
        for name in ("inventory_copy", "inventory_store"):
            spec = spec_from_file_location(cls.package_name + "." + name, cls.directory / (name + ".py"))
            module = module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
        cls.api = module
        constants = ModuleType(cls.package_name + ".filesystem_constants")
        constants.DATA_DIR = cls.directory / "data"
        sys.modules[constants.__name__] = constants
        spec = spec_from_file_location(cls.package_name + ".validation_framework", cls.directory / "validation_framework.py")
        cls.validation = module_from_spec(spec)
        sys.modules[spec.name] = cls.validation
        spec.loader.exec_module(cls.validation)
        # Sparse checkpoints need cache normalization functions only, not the
        # installed utils module's premise/Brightway initialization.
        import numpy as np
        import pandas as pd
        from typing import MutableMapping
        utils = ModuleType(cls.package_name + ".utils")
        utils.__dict__.update(Any=Any, MutableMapping=MutableMapping, np=np, pd=pd)
        tree = ast.parse((cls.directory / "utils.py").read_text(encoding="utf-8"))
        constants = {"_SCENARIO_TRIMMED_DATASET_FIELDS", "_SCENARIO_TRIMMED_EXCHANGE_FIELDS", "_CACHE_VALUE_CONTAINER_TYPES", "_CACHE_VALUE_INTEGER_TYPES"}
        functions = {"_has_cache_value", "_scenario_metadata_value_is_restored", "_normalize_scenario_cache_activity",
                     "_scenario_cache_exchange_field_is_restored", "_normalize_scenario_cache_exchange"}
        nodes = [node for node in tree.body if (isinstance(node, ast.FunctionDef) and node.name in functions)
                 or (isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id in constants for target in node.targets))]
        exec(compile(ast.Module(body=nodes, type_ignores=[]), "native_cache_normalization", "exec"), utils.__dict__)
        sys.modules[utils.__name__] = utils
        cls.module_names.append(utils.__name__)

    def validating_builder(self, definitions):
        """Run the installed NewDatabase certification method, without initialization."""
        parsed = ast.parse((self.directory / "new_database.py").read_text(encoding="utf-8"))
        cls = next(n for n in parsed.body if isinstance(n, ast.ClassDef) and n.name == "NewDatabase")
        fn = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "_certify_scenario_store")
        ns = vars(self.validation).copy()
        ns.update(InventoryStore=self.api.InventoryStore, time=time, replace=replace, logger=logging.getLogger("por_fixture"))
        exec(compile(ast.Module(body=[fn], type_ignores=[]), "native_certify_scenario_store", "exec"), ns)
        builder = SimpleNamespace(scenarios=definitions, _validation_enabled=True, system_model="cutoff", version="3.12",
                                  _scenario_identity=lambda definition: "CL",
                                  _validation_source_fingerprint=lambda: "fixture source",
                                  _validation_iam_fingerprint=lambda definition: "fixture IAM",
                                  _ensure_validation_baseline_cycles=lambda: frozenset())
        builder._certify_scenario_store = lambda *args, **kwargs: ns["_certify_scenario_store"](builder, *args, **kwargs)
        return builder

    @staticmethod
    def valid_database():
        data = basic_database()
        for dataset in data:
            dataset["exchanges"].insert(0, {"name": dataset["name"], "product": dataset["reference product"],
                                            "unit": dataset["unit"], "location": dataset["location"],
                                            "type": "production", "amount": 1})
        return data

    @classmethod
    def tearDownClass(cls):
        for name in cls.module_names: sys.modules.pop(name, None)

    def test_real_checkpoint_mapping_and_native_export_loader_use_the_new_links(self):
        api = self.api
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            store = api.create_inventory_store(basic_database(), backend="compact", scenario_identity="CL")
            original = store.checkpoint(Path(directory) / "original.inventory-store")
            mapping = api._compact_scenario_mapping({"Petchem": {"FE_PPP": [store._state.activities[0]]}}, store, original)
            definition = scenario(_inventory_checkpoint=original, _inventory_export_handoff=store, mapping=mapping)
            builder = SimpleNamespace(scenarios=[definition])
            with patch("por_supplier_linking._inventory_store_api", return_value=api):
                apply_por_supplier_linking(builder, policy=policy(), report_dir=Path(directory) / "reports", verbose=False)
            updated = builder.scenarios[0]
            self.assertNotEqual(updated["_inventory_checkpoint"], original)
            # Native lazy mappings expose activity metadata, not exchanges.
            reference = updated["mapping"]["Petchem"]["FE_PPP"][0]
            self.assertEqual(reference._resolver.checkpoint, updated["_inventory_checkpoint"])
            reopened = api.InventoryStore.open(updated["_inventory_checkpoint"])
            payloads = dict(zip(reopened.iter_activity_ids(), reopened.materialize()))
            hydrated = api._hydrate_scenario_mapping(updated["mapping"], payloads)
            self.assertEqual(hydrated["Petchem"]["FE_PPP"][0]["exchanges"][0]["name"],
                             "market for methanol (por)")
            # Execute the installed loader unchanged, without importing premise/Brightway.
            source = ast.parse((self.directory / "utils.py").read_text(encoding="utf-8"))
            fn = next(n for n in source.body if isinstance(n, ast.FunctionDef) and n.name == "load_database")
            ns = {"__package__": self.package_name, "Dict": Dict, "List": List, "Any": Any, "uuid": uuid}
            exec(compile(ast.Module(body=[fn], type_ignores=[]), "native_load_database", "exec"), ns)
            loaded = ns["load_database"](scenario=updated, original_database=[], delete=False, load_metadata=True, warning=False)
            self.assertEqual(loaded["database"][0]["exchanges"][0]["name"], "market for methanol (por)")
            self.assertEqual(api.InventoryStore.open(original).materialize()[0]["exchanges"][0]["name"], "market for methanol")
            self.assertNotIn("database", updated)
            del reopened, payloads, hydrated, loaded, updated, definition, builder, store, mapping, reference
            gc.collect()  # Release native Windows memory maps before temp cleanup.

    def test_real_certification_does_not_misattribute_unrelated_background_errors(self):
        import numpy as np
        api, validation = self.api, self.validation
        data = self.valid_database()
        bad = deepcopy(data[0])
        bad.update(name="unrelated background process", **{"reference product": "other product"})
        bad["exchanges"][0].update(name=bad["name"], product="other product")
        bad["exchanges"] += [deepcopy(bad["exchanges"][1]),
                             dict(bad["exchanges"][1], amount=np.array(.0216101)),
                             dict(bad["exchanges"][1], amount=0, **{"uncertainty type": 2, "loc": 0, "scale": .2}),
                             dict(bad["exchanges"][1], name=bad["name"], product="other product", location="NL", amount=.1)]
        data.append(bad)
        original = api.create_inventory_store(data, scenario_identity="CL")
        # Reproduce all four rule families reported in the user's traceback.
        broad = validation.InventoryGraphValidator(original, suppressions=()).validate()
        self.assertTrue({"GRAPH.DUPLICATE_SUPPLIER", "GRAPH.FINITE_NUMERIC", "GRAPH.UNCERTAINTY",
                         "GRAPH.NEW_FORBIDDEN_CYCLE"} <= {issue.rule_id for issue in broad.errors})
        before = validation.inventory_activity_fingerprints(original)
        builder = self.validating_builder([scenario(_inventory_store=original)])
        with tempfile.TemporaryDirectory() as directory, patch("por_supplier_linking._inventory_store_api", return_value=api), patch(
                "por_supplier_linking._inventory_validation_api", return_value=validation):
            reports = apply_por_supplier_linking(builder, policy=policy(), report_dir=Path(directory) / "reports", verbose=False)
        updated = builder.scenarios[0]
        report = validation.ValidationReport.from_dict(updated["_validation_report"])
        self.assertFalse(report.errors)
        self.assertEqual(report.store_generation, updated["_inventory_store"].generation)
        self.assertEqual(reports[0]["native_validation_targets"], 1)
        self.assertEqual(reports[0]["native_validation_baseline_cycles"], 1)
        self.assertEqual(before, validation.inventory_activity_fingerprints(original))
        # The unrelated defects are not edited, hidden or claimed to be fixed.
        self.assertTrue(validation.InventoryGraphValidator(updated["_inventory_store"], suppressions=()).validate().errors)

    def test_real_global_cycle_check_rejects_a_new_cycle_through_unchanged_suppliers(self):
        api, validation = self.api, self.validation
        data = self.valid_database()
        data[2]["exchanges"].append({"name": data[0]["name"], "product": data[0]["reference product"],
                                     "unit": "kilogram", "location": "NL", "type": "technosphere", "amount": .1})
        original = api.create_inventory_store(data, scenario_identity="CL")
        builder = self.validating_builder([scenario(_inventory_store=original)])
        with tempfile.TemporaryDirectory() as directory, patch("por_supplier_linking._inventory_store_api", return_value=api), patch(
                "por_supplier_linking._inventory_validation_api", return_value=validation):
            with self.assertRaisesRegex(SupplierLinkingError, "new technosphere cycle"):
                apply_por_supplier_linking(builder, policy=policy(), report_dir=Path(directory) / "reports", verbose=False)
        self.assertIs(builder.scenarios[0]["_inventory_store"], original)
        self.assertEqual(original.generation, 0)

    def test_original_duplicate_demands_remain_unchanged_and_are_explicitly_reported(self):
        api, validation = self.api, self.validation
        data = self.valid_database()
        data[0]["exchanges"].append(dict(data[0]["exchanges"][1]))
        original = api.create_inventory_store(data, scenario_identity="CL")
        builder = self.validating_builder([scenario(_inventory_store=original)])
        with tempfile.TemporaryDirectory() as directory, patch("por_supplier_linking._inventory_store_api", return_value=api), patch(
                "por_supplier_linking._inventory_validation_api", return_value=validation):
            reports = apply_por_supplier_linking(builder, policy=policy(), report_dir=Path(directory) / "reports", verbose=False)
        report = validation.ValidationReport.from_dict(builder.scenarios[0]["_validation_report"])
        self.assertFalse(report.errors)
        self.assertTrue(report.suppressed_issues)
        self.assertEqual(reports[0]["inherited_duplicate_supplier_exchanges"], 1)
        self.assertEqual([e["amount"] for e in builder.scenarios[0]["_inventory_store"].materialize()[0]["exchanges"][1:]], [2, 2])
        self.assertEqual(original.generation, 0)

    def test_accidental_duplicate_created_by_the_post_step_still_fails(self):
        api, validation = self.api, self.validation
        original = api.create_inventory_store(self.valid_database(), scenario_identity="CL")
        builder = self.validating_builder([scenario(_inventory_store=original)])
        def introduce_duplicate(database, linked, audit):
            linked[0]["exchanges"].append(dict(linked[0]["exchanges"][1]))
            return 0
        with tempfile.TemporaryDirectory() as directory, patch("por_supplier_linking._inventory_store_api", return_value=api), patch(
                "por_supplier_linking._inventory_validation_api", return_value=validation), patch(
                "por_supplier_linking._preserve_relinked_source_components", side_effect=introduce_duplicate):
            with self.assertRaises(SupplierLinkingError) as caught:
                apply_por_supplier_linking(builder, policy=policy(), report_dir=Path(directory) / "reports", verbose=False)
        self.assertIn("exchange count", str(caught.exception))
        self.assertIs(builder.scenarios[0]["_inventory_store"], original)

    def test_distinct_source_components_keep_amounts_and_uncertainty_after_relinking(self):
        api, validation = self.api, self.validation
        data = self.valid_database()
        data[0]["exchanges"][1].update({"uncertainty type": 2, "loc": .6931471805599453, "scale": .2})
        data[0]["exchanges"].append(dict(data[0]["exchanges"][1], name="market for methanol (por)", location="NL"))
        original = api.create_inventory_store(data, scenario_identity="CL")
        builder = self.validating_builder([scenario(_inventory_store=original)])
        with tempfile.TemporaryDirectory() as directory, patch("por_supplier_linking._inventory_store_api", return_value=api), patch(
                "por_supplier_linking._inventory_validation_api", return_value=validation):
            reports = apply_por_supplier_linking(builder, policy=policy(), report_dir=Path(directory) / "reports", verbose=False)
        feeds = builder.scenarios[0]["_inventory_store"].materialize()[0]["exchanges"][1:]
        self.assertEqual([e["amount"] for e in feeds], [2, 2])
        self.assertEqual([e["scale"] for e in feeds], [.2, .2])
        self.assertEqual({e["name"] for e in feeds}, {"market for methanol (por)"})
        self.assertEqual(len({e["por source component"]["supplier"][3] for e in feeds}), 2)
        self.assertEqual(reports[0]["preserved_source_components"], 2)
        self.assertFalse(validation.ValidationReport.from_dict(builder.scenarios[0]["_validation_report"]).errors)

    def test_real_background_component_extension_is_recorded_for_review(self):
        api, validation = self.api, self.validation
        data = self.valid_database()
        background = deepcopy(data[0])
        background.update(name="background capital supply", **{"reference product": "capital service"})
        background["exchanges"] = [dict(background["exchanges"][0], name=background["name"], product="capital service"),
                                   {"name": background["name"], "product": "capital service", "unit": "kilogram",
                                    "location": "NL", "type": "technosphere", "amount": .01},
                                   {"name": data[0]["name"], "product": data[0]["reference product"], "unit": "kilogram",
                                    "location": "NL", "type": "technosphere", "amount": .01}]
        data[2]["exchanges"].append(dict(background["exchanges"][1], amount=.01))
        data.append(background)
        original = api.create_inventory_store(data, scenario_identity="CL")
        builder = self.validating_builder([scenario(_inventory_store=original)])
        with tempfile.TemporaryDirectory() as directory, patch("por_supplier_linking._inventory_store_api", return_value=api), patch(
                "por_supplier_linking._inventory_validation_api", return_value=validation):
            reports = apply_por_supplier_linking(builder, policy=policy(), report_dir=Path(directory) / "reports", verbose=False)
        self.assertEqual(reports[0]["native_validation_background_cycle_reconfigurations"], 1)
        self.assertEqual(reports[0]["native_validation_new_cycles"], 0)
        self.assertTrue(reports[0]["native_validation_cycle_review"])
        self.assertFalse(validation.ValidationReport.from_dict(builder.scenarios[0]["_validation_report"]).errors)

    def test_optional_existing_checkpoint_in_memory_without_modifying_source(self):
        """Opt-in real-cache check; default tests never read a user's scenario cache."""
        checkpoint = os.environ.get("POR_NATIVE_CHECKPOINT")
        if not checkpoint:
            self.skipTest("Set POR_NATIVE_CHECKPOINT for an opt-in read-only real-cache check")
        api, validation = self.api, self.validation
        original = api.InventoryStore.open(Path(checkpoint))
        generation = original.generation
        fingerprint = validation.inventory_store_fingerprint(original)
        builder = self.validating_builder([scenario(_inventory_store=original)])
        with tempfile.TemporaryDirectory() as directory, patch("por_supplier_linking._inventory_store_api", return_value=api), patch(
                "por_supplier_linking._inventory_validation_api", return_value=validation):
            reports = apply_por_supplier_linking(builder, policy=PorLinkingPolicy.from_files(),
                                                report_dir=Path(directory) / "reports", verbose=False)
        self.assertEqual(original.generation, generation)
        self.assertEqual(validation.inventory_store_fingerprint(original), fingerprint)
        self.assertFalse(validation.ValidationReport.from_dict(builder.scenarios[0]["_validation_report"]).errors)
        print("Read-only native cache check:", {k: reports[0].get(k) for k in (
            "selected_activities", "changed_exchanges", "native_validation_targets",
            "native_validation_background_cycle_reconfigurations", "native_validation_reviewed_target_cycles",
            "linking_seconds", "phase_seconds")})
        del original, builder
        gc.collect()


if __name__ == "__main__": unittest.main()
