"""Guard only the reviewed lithium source-input finding; never rewrite the LCI."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
from importlib.util import find_spec, module_from_spec, spec_from_file_location
from pathlib import Path
import struct
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

import premise_compat as compat


LITHIUM_NAME = "lithium carbonate production, from Salar del Hombre Muerto"
LITHIUM_PRODUCT = "lithium carbonate, battery grade"
ELECTRICITY_NAME = "market for electricity, high voltage"
ELECTRICITY_PRODUCT = "electricity, high voltage"
REVIEWED_SOURCE_SHA256 = "415eced99a20665d7790d404db62dccbec1206c8c1ff95d0e8d7e47637c02355"


def float32(value):
    return struct.unpack("f", struct.pack("f", value))[0]


def source_exchanges():
    def electricity(amount, loc, scale):
        return {
            "name": ELECTRICITY_NAME, "product": ELECTRICITY_PRODUCT,
            "location": "AR", "unit": "kilowatt hour", "type": "technosphere",
            "amount": float32(amount), "uncertainty type": 2,
            "loc": float32(loc), "scale": float32(scale),
        }

    first = electricity(.015, -6.907755278982132, .8950564214316266)
    third = electricity(.3, -1.2039728043259361, .9066475442341566)
    return [first, deepcopy(first), third]


class Activity(dict):
    def __init__(self, payload, activity_id, exchange_ids):
        super().__init__(payload)
        self.id = activity_id
        self.exchange_ids = tuple(exchange_ids)


class Exchange(dict):
    def __init__(self, payload, exchange_id, activity_id=0):
        super().__init__(payload)
        self.id = exchange_id
        self.activity_id = activity_id


class Store:
    generation = 0

    def __init__(self, rows=None, **identity):
        rows = source_exchanges() if rows is None else rows
        self.rows = {100 + position: Exchange(deepcopy(row), 100 + position)
                     for position, row in enumerate(rows)}
        self.record = Activity({
            "name": LITHIUM_NAME, "reference product": LITHIUM_PRODUCT,
            "location": "AR", "unit": "kilogram", **identity,
        }, 0, self.rows)

    def activity(self, activity_id):
        if activity_id != 0:
            raise KeyError(activity_id)
        return self.record

    def exchange(self, exchange_id):
        return self.rows[exchange_id]

    def iter_activities(self, query=None):
        return iter((self.record,))

    def find_one(self, query):
        if not all(self.record.get(key) == value for key, value in query.items()):
            raise ValueError("No matching fixture activity")
        return self.record


@dataclass(frozen=True)
class Issue:
    rule_id: str = "GRAPH.DUPLICATE_SUPPLIER"
    severity: str = "error"
    message: str = "Activity contains an exact duplicate supplier exchange."
    activity_id: int = 0
    activity_key: tuple = (LITHIUM_NAME, LITHIUM_PRODUCT, "AR")
    activity_code: str | None = None
    exchange_id: int = 101
    expected: str = "one supplier exchange"
    actual: object = None
    suppressed: bool = False
    suppression_explanation: str | None = None


@dataclass(frozen=True)
class Result:
    rule_id: str = "GRAPH.DUPLICATE_SUPPLIER"
    issues: tuple = ()


def finding(**changes):
    values = {
        "actual": {
            "amount": float32(.015), "first_exchange_id": 100,
            "supplier": (ELECTRICITY_NAME, ELECTRICITY_PRODUCT, "AR"),
        },
        **changes,
    }
    return Issue(**values)


class SourceDuplicateGuardTest(unittest.TestCase):
    def setUp(self):
        self.source = Path(__file__).parent / "synthetic-lci-lithium.xlsx"
        source_reader = patch.object(Path, "read_bytes",
                                     return_value=b"explicit synthetic source-hash test fixture")
        source_reader.start()
        self.addCleanup(source_reader.stop)
        self.native_calls = []
        calls = self.native_calls

        class Validator:
            def __init__(self, store, version="3.12", system_model="cutoff"):
                self.store, self.version, self.system_model = store, version, system_model

            def _apply_suppressions(self, result):
                calls.append(result)
                return result

        self.Validator = Validator
        self.api = SimpleNamespace(InventoryGraphValidator=Validator)

    def install(self, *, reviewed_source=True):
        # Production checks the real workbook's pinned SHA256. The tiny fixture
        # explicitly substitutes that digest, never package data or test rows.
        if reviewed_source:
            digest = patch.object(compat, "_LITHIUM_SOURCE_SHA256",
                                  hashlib.sha256(self.source.read_bytes()).hexdigest())
            digest.start()
            self.addCleanup(digest.stop)
        return compat.install_premise_source_duplicate_guard(
            self.api, source_path=self.source, verbose=False,
        )

    def apply(self, store=None, issue=None, **validator_options):
        store = Store() if store is None else store
        issue = finding() if issue is None else issue
        return self.Validator(store, **validator_options)._apply_suppressions(
            Result(issues=(issue,)),
        ).issues[0]

    def test_reviewed_pair_is_documented_without_changing_any_exchange(self):
        self.assertTrue(self.install())
        store = Store()
        before = deepcopy(store.rows)
        issue = self.apply(store)
        self.assertTrue(issue.suppressed)
        self.assertTrue(issue.suppression_explanation)
        self.assertEqual(store.rows, before)
        self.assertEqual(store.generation, 0)
        self.assertAlmostEqual(sum(row["amount"] for row in store.rows.values()), .33, places=7)
        self.assertEqual(len(self.native_calls), 1)

    def test_installation_is_idempotent(self):
        self.assertTrue(self.install())
        installed = self.Validator._apply_suppressions
        self.assertFalse(compat.install_premise_source_duplicate_guard(
            self.api, source_path=self.source, verbose=False,
        ))
        self.assertIs(self.Validator._apply_suppressions, installed)

    def test_changed_source_workbook_does_not_receive_the_exception(self):
        self.install(reviewed_source=False)
        self.assertFalse(self.apply().suppressed)

    def test_changed_or_missing_source_after_installation_fails_closed(self):
        # Install without substituting the digest and only substitute it during
        # one application, establishing that source validation is not stale.
        self.install(reviewed_source=False)
        with patch.object(compat, "_LITHIUM_SOURCE_SHA256",
                          hashlib.sha256(self.source.read_bytes()).hexdigest()):
            self.assertTrue(self.apply().suppressed)
        with patch.object(Path, "read_bytes", side_effect=FileNotFoundError):
            self.assertFalse(self.apply().suppressed)

    def test_wrong_version_and_system_model_remain_unsuppressed(self):
        self.install()
        for options in ({"version": "3.11"}, {"system_model": "consequential"}):
            with self.subTest(options=options):
                self.assertFalse(self.apply(**options).suppressed)

    def test_changed_activity_identity_remains_unsuppressed(self):
        self.install()
        for field, value in (("location", "CL"), ("reference product", "lithium carbonate"),
                             ("name", LITHIUM_NAME + " modified"), ("unit", "tonne")):
            with self.subTest(field=field):
                store = Store(**{field: value})
                self.assertFalse(self.apply(store).suppressed)

    def test_changed_amount_or_uncertainty_remains_unsuppressed(self):
        self.install()
        changes = ({"amount": .02}, {"loc": -5.0}, {"scale": .2},
                   {"uncertainty type": 0}, {"unit": "megajoule"},
                   {"location": "RoW"}, {"product": "electricity, medium voltage"},
                   {"name": "market for electricity, medium voltage"},
                   {"amount": True}, {"amount": float("nan")}, {"scale": float("inf")},
                   {"negative": True}, {"minimum": 0}, {"maximum": 1}, {"shape": 1})
        for change in changes:
            with self.subTest(change=change):
                rows = source_exchanges()
                rows[0].update(change)
                rows[1].update(change)
                self.assertFalse(self.apply(Store(rows)).suppressed)

    def test_changed_third_electricity_input_remains_unsuppressed(self):
        self.install()
        for change in ({"amount": .4}, {"scale": .5}, {"uncertainty type": 0}):
            with self.subTest(change=change):
                rows = source_exchanges()
                rows[2].update(change)
                self.assertFalse(self.apply(Store(rows)).suppressed)

    def test_missing_third_input_and_triple_pair_remain_unsuppressed(self):
        self.install()
        rows = source_exchanges()
        self.assertFalse(self.apply(Store(rows[:2])).suppressed)
        self.assertFalse(self.apply(Store(rows + [deepcopy(rows[0])])).suppressed)

    def test_duplicate_of_larger_input_remains_unsuppressed(self):
        self.install()
        rows = source_exchanges()
        rows.append(deepcopy(rows[2]))
        issue = finding(exchange_id=103, actual={
            "first_exchange_id": 102, "amount": float32(.3),
            "supplier": (ELECTRICITY_NAME, ELECTRICITY_PRODUCT, "AR"),
        })
        self.assertFalse(self.apply(Store(rows), issue).suppressed)

    def test_invalid_exchange_ids_and_issue_supplier_remain_unsuppressed(self):
        self.install()
        issues = (
            finding(exchange_id=102), finding(exchange_id=999), finding(activity_id=999),
            finding(actual={"first_exchange_id": 999, "amount": float32(.015),
                            "supplier": (ELECTRICITY_NAME, ELECTRICITY_PRODUCT, "AR")}),
            finding(actual={"first_exchange_id": 100, "amount": float32(.015),
                            "supplier": (ELECTRICITY_NAME, ELECTRICITY_PRODUCT, "RoW")}),
        )
        for issue in issues:
            with self.subTest(issue=issue):
                self.assertFalse(self.apply(issue=issue).suppressed)

    def test_other_rules_and_activities_are_preserved(self):
        self.install()
        other_rule = finding(rule_id="GRAPH.FINITE_NUMERIC")
        result = self.Validator(Store())._apply_suppressions(Result(
            rule_id="GRAPH.FINITE_NUMERIC", issues=(other_rule,),
        ))
        self.assertIs(result.issues[0], other_rule)
        other_activity = finding(activity_key=("another lithium activity", LITHIUM_PRODUCT, "AR"))
        self.assertFalse(self.apply(issue=other_activity).suppressed)

    def test_native_suppression_is_retained_and_other_findings_not_silenced(self):
        self.install()
        existing = finding(suppressed=True, suppression_explanation="native exception")
        unrelated = finding(rule_id="GRAPH.REQUIRED_EXCHANGE_FIELDS")
        result = self.Validator(Store())._apply_suppressions(Result(issues=(existing, unrelated)))
        self.assertEqual(result.issues[0].suppression_explanation, "native exception")
        self.assertFalse(result.issues[1].suppressed)

    def test_unsupported_validation_api_is_a_noop(self):
        self.assertFalse(compat.install_premise_source_duplicate_guard(
            SimpleNamespace(), source_path=self.source, verbose=False,
        ))

    def test_old_premise_missing_only_optional_validation_module_is_a_noop(self):
        error = ModuleNotFoundError("No validation framework", name="premise.validation_framework")
        with patch("importlib.import_module", side_effect=error):
            self.assertFalse(compat.install_premise_source_duplicate_guard(verbose=False))

    def test_missing_transitive_dependency_is_not_silenced(self):
        error = ModuleNotFoundError("Missing required dependency", name="pyarrow")
        with patch("importlib.import_module", side_effect=error):
            with self.assertRaises(ModuleNotFoundError):
                compat.install_premise_source_duplicate_guard(verbose=False)


class NativeSourceDuplicateIntegrationTest(unittest.TestCase):
    """Run the actual installed graph validator without importing Brightway."""
    @classmethod
    def setUpClass(cls):
        package = find_spec("premise")
        if package is None:
            raise unittest.SkipTest("Installed premise source is unavailable")
        cls.directory = Path(next(iter(package.submodule_search_locations)))
        if not all((cls.directory / name).is_file()
                   for name in ("inventory_store.py", "validation_framework.py")):
            raise unittest.SkipTest("Older premise has no native graph validation API")
        cls.package_name = "_por_source_duplicate_validation_test"
        package_module = ModuleType(cls.package_name)
        package_module.__path__ = [str(cls.directory)]
        cleanup = ModuleType(cls.package_name + ".cache_cleanup")
        cleanup.mark_checkpoint = lambda path, **kwargs: None
        constants = ModuleType(cls.package_name + ".filesystem_constants")
        constants.DATA_DIR = cls.directory / "data"
        cls.module_names = [cls.package_name, cleanup.__name__, constants.__name__]
        sys.modules[cls.package_name] = package_module
        sys.modules[cleanup.__name__] = cleanup
        sys.modules[constants.__name__] = constants
        for name in ("inventory_copy", "inventory_store", "validation_framework"):
            spec = spec_from_file_location(cls.package_name + "." + name,
                                           cls.directory / (name + ".py"))
            module = module_from_spec(spec)
            sys.modules[spec.name] = module
            cls.module_names.append(spec.name)
            spec.loader.exec_module(module)
            setattr(cls, name, module)
        cls.source = cls.directory / "data" / "additional_inventories" / "lci-lithium.xlsx"
        if not cls.source.is_file():
            cls.tearDownClass()
            raise unittest.SkipTest("Installed lithium source workbook is unavailable")

    @classmethod
    def tearDownClass(cls):
        for name in cls.module_names:
            sys.modules.pop(name, None)

    def database(self, extra_rows=()):
        target = {
            "name": LITHIUM_NAME, "reference product": LITHIUM_PRODUCT,
            "location": "AR", "unit": "kilogram",
            "exchanges": [{
                "name": LITHIUM_NAME, "product": LITHIUM_PRODUCT, "location": "AR",
                "unit": "kilogram", "type": "production", "amount": 1,
            }, *source_exchanges(), *deepcopy(extra_rows)],
        }
        electricity = {
            "name": ELECTRICITY_NAME, "reference product": ELECTRICITY_PRODUCT,
            "location": "AR", "unit": "kilowatt hour", "exchanges": [{
                "name": ELECTRICITY_NAME, "product": ELECTRICITY_PRODUCT,
                "location": "AR", "unit": "kilowatt hour", "type": "production", "amount": 1,
            }],
        }
        return [target, electricity]

    def certify(self, database, *, guarded=True, backend="legacy"):
        api = self.inventory_store
        validation = self.validation_framework
        if guarded:
            compat.install_premise_source_duplicate_guard(
                validation, source_path=self.source, verbose=False,
            )
        store = api.create_inventory_store(database, backend=backend, scenario_identity="reference")
        report = validation.InventoryGraphValidator(
            store, scenario_identity="reference", version="3.12", system_model="cutoff",
            source_fingerprint="source-test", iam_fingerprint="IAM-test",
        ).validate()
        return store, report

    def test_actual_native_finding_is_visible_and_lci_remains_unchanged(self):
        # This assertion binds the integration test to the reviewed bundled data,
        # rather than accepting an arbitrary workbook through the pure fixture.
        if hashlib.sha256(self.source.read_bytes()).hexdigest() != REVIEWED_SOURCE_SHA256:
            self.skipTest("Installed lithium workbook differs from reviewed source")
        database = self.database()
        before = deepcopy(database)
        validator_class = self.validation_framework.InventoryGraphValidator
        native_apply = validator_class._apply_suppressions
        while getattr(native_apply, "__repo_source_duplicate_guard_version__", None):
            native_apply = native_apply.__wrapped__
        with patch.object(validator_class, "_apply_suppressions", native_apply):
            _, unreviewed = self.certify(database, guarded=False)
        self.assertEqual([issue.rule_id for issue in unreviewed.errors], ["GRAPH.DUPLICATE_SUPPLIER"])
        for backend in ("legacy", "compact"):
            with self.subTest(backend=backend):
                store, report = self.certify(database, backend=backend)
                self.assertEqual(database, before)
                self.assertEqual(store.materialize(), before)
                self.assertFalse(report.errors)
                reviewed = [issue for result in report.rule_results for issue in result.issues
                            if issue.rule_id == "GRAPH.DUPLICATE_SUPPLIER"]
                self.assertEqual(len(reviewed), 1)
                self.assertTrue(reviewed[0].suppressed)
                self.assertIn("0.33", reviewed[0].suppression_explanation)
                report.raise_for_errors()

    def test_actual_validator_still_rejects_another_duplicate_in_same_activity(self):
        rows = [{
            "name": "market for example input", "product": "example input",
            "location": "GLO", "unit": "kilogram", "type": "technosphere",
            "amount": .2,
        }] * 2
        database = self.database(rows)
        database.append({
            "name": "market for example input", "reference product": "example input",
            "location": "GLO", "unit": "kilogram", "exchanges": [{
                "name": "market for example input", "product": "example input",
                "location": "GLO", "unit": "kilogram", "type": "production", "amount": 1,
            }],
        })
        _, report = self.certify(database)
        errors = [issue for issue in report.errors if issue.rule_id == "GRAPH.DUPLICATE_SUPPLIER"]
        self.assertEqual(len(errors), 2)
        with self.assertRaises(self.validation_framework.PremiseValidationError):
            report.raise_for_errors()


if __name__ == "__main__":
    unittest.main()
