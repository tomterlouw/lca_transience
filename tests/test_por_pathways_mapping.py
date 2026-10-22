"""Exercise the installed Pathways mapper without importing or updating databases."""
from __future__ import annotations

import ast
from collections.abc import Mapping
from copy import deepcopy
from importlib.util import find_spec
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

import yaml

from premise_compat import (
    PATHWAYS_MAPPING_GUARD_VERSION,
    install_premise_pathways_mapping_fix,
    pathways_activity_mapping_views,
)


def installed_mapper():
    package = find_spec("premise")
    if package is None:
        raise unittest.SkipTest("Installed premise source is required")
    source = Path(next(iter(package.submodule_search_locations))) / "pathways.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    cls = next(node for node in tree.body
               if isinstance(node, ast.ClassDef) and node.name == "PathwaysDataPackage")
    method = next(node for node in cls.body
                  if isinstance(node, ast.FunctionDef) and node.name == "_add_variables_mapping")
    namespace = {"Path": Path, "json": json, "yaml": yaml}
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(source), "exec"), namespace)
    return namespace["_add_variables_mapping"]


def activity(name="steel activity", product="steel", **extra):
    return {"name": name, "reference product": product, "unit": "kilogram", **extra}


class LazyActivity(Mapping):
    """Common identity fields are resident; enumerating a native reference is costly."""

    def __init__(self, payload):
        self.payload = payload

    def __getitem__(self, field):
        return self.payload[field]

    def __iter__(self):
        raise AssertionError("The mapper should only read resident activity metadata")

    def __len__(self):
        return len(self.payload)


class PathwaysMappingCompatibilityTest(unittest.TestCase):
    def package(self, scenarios, *, fixed=True):
        cls = type("Package", (), {"_add_variables_mapping": installed_mapper()})
        if fixed:
            self.assertTrue(install_premise_pathways_mapping_fix(cls, verbose=False))
        package = cls()
        package.datapackage = SimpleNamespace(scenarios=scenarios)
        package.variables_name_change = {}
        return package

    def export_mapping(self, package):
        with tempfile.TemporaryDirectory() as directory, patch.object(Path, "cwd", return_value=Path(directory)):
            result = package._add_variables_mapping()
            self.assertIsNone(result)
            return yaml.safe_load((Path(directory) / "pathways_temp/mapping/mapping.yaml").read_text())

    def diagnostics(self, compacted=False):
        metrics = {"compiled pairs": 1, "executed pairs": 1, "provider index lookups": 2}
        return {
            "material decisions": [{"activity": activity(), "status": "changed",
                                    "reason code": "MATERIAL", "explanation": "Updated material input"}],
            "material update metrics": list(metrics) if compacted else metrics,
        }

    def diagnostics_255(self):
        return {
            **self.diagnostics(),
            "applied market shares": {
                "copper": {"primary share": 0.55, "secondary share": 0.45,
                           "primary suppliers": [], "secondary suppliers": []},
            },
            "market diagnostics": [{"reason": "preserved source market", "metal": "copper"}],
            "resource diagnostics": [{"name": "copper scrap refining", "product": "copper, cathode",
                                      "location": "RER", "reason": "secondary route preserved"}],
            "methodology": {"system model": "cutoff", "recycling": "source-market shares",
                            "market decisions": {"preserved source market": 1}},
        }

    def test_premise_255_metadata_is_excluded_without_changing_the_source_mapping(self):
        diagnostics = self.diagnostics_255()
        provider = activity("copper refining", "copper, cathode")
        mapping = {"metals": {"transformed activities": [provider], **diagnostics}}
        scenarios = [{"model": "remind", "pathway": "CL", "year": 2025, "mapping": mapping}]
        before = deepcopy(mapping)
        views, omitted = pathways_activity_mapping_views(scenarios)
        self.assertEqual(omitted, 6)
        self.assertEqual(views, [{"metals": {"transformed activities": [provider]}}])
        package = self.package(scenarios)
        exported = self.export_mapping(package)
        self.assertEqual(exported, {"SE - metals - transformed activities": {"dataset": [provider]}})
        self.assertEqual(mapping, before)
        self.assertIs(scenarios[0]["mapping"], mapping)
        for key, value in diagnostics.items():
            self.assertIs(mapping["metals"][key], value)
            self.assertNotIn(key, package.variables_name_change)

    def test_premise_255_diagnostic_names_in_other_sectors_remain_real_variables(self):
        diagnostics = self.diagnostics_255()
        external = {key: [activity("PoR activity", "PoR product")] for key in diagnostics}
        package = self.package([{"mapping": {"metals": diagnostics, "external_0": external}}])
        exported = self.export_mapping(package)
        self.assertEqual(set(exported), {f"EXT - 0 - {key}" for key in diagnostics})
        for value in exported.values():
            self.assertEqual(value["dataset"], [activity("PoR activity", "PoR product")])

    def test_unknown_metals_metadata_is_not_silently_discarded(self):
        for providers in ({"count": 1}, [{"reason": "not an activity"}]):
            with self.subTest(providers=providers):
                mapping = {"metals": {**self.diagnostics_255(), "unknown variable": providers}}
                with self.assertRaisesRegex(ValueError, "metals.*unknown variable"):
                    pathways_activity_mapping_views([{"year": 2030, "mapping": mapping}])

    def test_installed_mapper_reproduces_the_reported_keyerror(self):
        package = self.package([{"mapping": {"metals": self.diagnostics()}}], fixed=False)
        with self.assertRaisesRegex(KeyError, "name"):
            package._add_variables_mapping()

    def test_excludes_diagnostics_preserves_union_lhv_and_original_objects(self):
        for compacted in (False, True):
            with self.subTest(compacted=compacted):
                fuel_a = activity("fuel A", "fuel", lhv=42.0, location="NL", exchanges=[{"amount": 1}])
                fuel_b = activity("fuel B", "fuel", lhv=40.0, location="RER")
                metal = activity()
                mappings = [
                    {"fuels": {"fuel variable": [fuel_a, fuel_a]},
                     "metals": {"transformed activities": [metal], **self.diagnostics(compacted)},
                     "final energy": {"heating": [activity("heat", "heat")]},
                     "external_0": {"PoR output": [activity("PoR activity", "PoR product")]}},
                    {"fuels": {"fuel variable": [fuel_a, fuel_b]},
                     "metals": {"transformed activities": [metal], **self.diagnostics(compacted)}},
                ]
                scenarios = [{"mapping": mapping} for mapping in mappings]
                original = deepcopy(mappings)
                metals = [mapping["metals"] for mapping in mappings]
                diagnostics = [mapping["material decisions"] for mapping in metals]
                package = self.package(scenarios)
                exported = self.export_mapping(package)
                self.assertEqual(set(exported), {
                    "SE - fuels - fuel variable", "SE - metals - transformed activities",
                    "FE - final energy - heating", "EXT - 0 - PoR output",
                })
                expected_fuels = [activity("fuel A", "fuel", lhv=42.0), activity("fuel B", "fuel", lhv=40.0)]
                self.assertCountEqual(exported["SE - fuels - fuel variable"]["dataset"], expected_fuels)
                self.assertEqual(exported["SE - metals - transformed activities"]["dataset"], [metal])
                self.assertNotIn("material decisions", package.variables_name_change)
                self.assertNotIn("material update metrics", package.variables_name_change)
                self.assertEqual(package.variables_name_change["heating"], "FE - final energy - heating")
                self.assertEqual(package.variables_name_change["PoR output"], "EXT - external_0 - PoR output")
                self.assertIs(package.datapackage.scenarios, scenarios)
                self.assertEqual(mappings, original)
                for scenario, mapping, sector, decisions in zip(scenarios, mappings, metals, diagnostics):
                    self.assertIs(scenario["mapping"], mapping)
                    self.assertIs(mapping["metals"], sector)
                    self.assertIs(sector["material decisions"], decisions)
                self.assertIs(mappings[0]["fuels"]["fuel variable"][0], fuel_a)

    def test_lazy_activity_metadata_remains_exportable(self):
        provider = LazyActivity(activity("lazy fuel", "fuel", lhv=42.0))
        package = self.package([{"mapping": {"metals": self.diagnostics(),
                                              "fuels": {"fuel": [provider]}}}])
        exported = self.export_mapping(package)
        self.assertEqual(exported["SE - fuels - fuel"]["dataset"], [provider.payload])

    def test_malformed_genuine_activity_fails_and_restores_all_mapping_objects(self):
        for sector, variable in (("metals", "transformed activities"),
                                 ("external_0", "material decisions")):
            with self.subTest(sector=sector, variable=variable):
                mapping = {"metals": self.diagnostics(),
                           sector: {variable: [{"reference product": "steel", "unit": "kilogram"}]}}
                if sector == "metals":
                    mapping["metals"].update(self.diagnostics())
                scenarios = [{"mapping": mapping}]
                original_sector = mapping["metals"]
                original = deepcopy(mapping)
                package = self.package(scenarios)
                with self.assertRaises((KeyError, TypeError, ValueError)):
                    self.export_mapping(package)
                self.assertIs(package.datapackage.scenarios, scenarios)
                self.assertIs(scenarios[0]["mapping"], mapping)
                self.assertIs(mapping["metals"], original_sector)
                self.assertEqual(mapping, original)

    def test_mapping_is_restored_when_writing_the_output_fails(self):
        diagnostics = self.diagnostics_255()
        mapping = {"metals": {"transformed activities": [activity()], **diagnostics}}
        package = self.package([{"mapping": mapping}])
        with patch.object(Path, "mkdir", side_effect=OSError("synthetic output failure")):
            with self.assertRaisesRegex(OSError, "synthetic output failure"):
                package._add_variables_mapping()
        self.assertIs(package.datapackage.scenarios[0]["mapping"], mapping)
        for key, value in diagnostics.items():
            self.assertIs(mapping["metals"][key], value)

    def test_no_diagnostics_retains_legacy_export_behavior(self):
        mappings = {"electricity": {"power": [activity("electricity", "electricity", lhv=0.0)]},
                    "metals": {"steel": [activity()]}, "fuels": {"empty": []}}
        native = self.package([{"mapping": deepcopy(mappings)}], fixed=False)
        fixed = self.package([{"mapping": mappings}])
        self.assertEqual(self.export_mapping(fixed), self.export_mapping(native))
        self.assertEqual(fixed.variables_name_change, native.variables_name_change)
        self.assertIs(fixed.datapackage.scenarios[0]["mapping"], mappings)

    def test_installation_is_idempotent(self):
        cls = type("Package", (), {"_add_variables_mapping": installed_mapper()})
        self.assertTrue(install_premise_pathways_mapping_fix(cls, verbose=False))
        patched = cls._add_variables_mapping
        self.assertFalse(install_premise_pathways_mapping_fix(cls, verbose=False))
        self.assertIs(cls._add_variables_mapping, patched)

    def test_previous_versioned_guard_is_upgraded(self):
        from functools import wraps
        native = installed_mapper()

        @wraps(native)
        def old_guard(self, *args, **kwargs):
            raise AssertionError("The previous mapping guard must be unwrapped")

        old_guard.__por_pathways_mapping_fix__ = True
        old_guard.__por_pathways_mapping_guard_version__ = 2
        cls = type("Package", (), {"_add_variables_mapping": old_guard})
        self.assertTrue(install_premise_pathways_mapping_fix(cls, verbose=False))
        self.assertEqual(cls._add_variables_mapping.__por_pathways_mapping_guard_version__,
                         PATHWAYS_MAPPING_GUARD_VERSION)
        package = cls()
        package.datapackage = SimpleNamespace(scenarios=[{
            "mapping": {"metals": {"transformed activities": [activity()], **self.diagnostics_255()}}
        }])
        package.variables_name_change = {}
        self.assertEqual(self.export_mapping(package), {
            "SE - metals - transformed activities": {"dataset": [activity()]},
        })

    def test_old_boolean_marker_is_upgraded_and_no_diagnostic_errors_get_context(self):
        from functools import wraps
        native = installed_mapper()
        @wraps(native)
        def old_guard(self, *args, **kwargs):
            return native(self, *args, **kwargs)
        old_guard.__por_pathways_mapping_fix__ = True
        cls = type("Package", (), {"_add_variables_mapping": old_guard})
        self.assertTrue(install_premise_pathways_mapping_fix(cls, verbose=False))
        package = cls()
        package.datapackage = SimpleNamespace(scenarios=[{
            "model": "remind", "pathway": "CL", "year": 2030,
            "mapping": {"external_0": {"FE_polymer": [{"unit": "kilogram"}]}}}])
        package.variables_name_change = {}
        with self.assertRaisesRegex(ValueError, "2030.*external_0.*FE_polymer.*name"):
            self.export_mapping(package)


if __name__ == "__main__":
    unittest.main()
