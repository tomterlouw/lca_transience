"""Scenario input isolation without importing premise or opening any LCA DB."""
import builtins
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

from por_scenario_inputs import fresh_scenario_definitions


class OpaqueResource:
    def __deepcopy__(self, memo):
        raise AssertionError("Datapackage/store objects must not be deep-copied")

    def __iter__(self):
        raise AssertionError("Runtime stores must not be traversed")


def definition(resource, external="OCE", **extra):
    return {
        "model": "remind", "pathway": "SSP2-PkBudg1000", "year": 2050,
        "external scenarios": [{"scenario": external, "data": resource}],
        **extra,
    }


class FreshScenarioInputsTest(unittest.TestCase):
    def test_module_import_keeps_input_isolation_dependency_light(self):
        original_import = builtins.__import__

        def checked_import(name, *args, **kwargs):
            if name.split(".")[0] in {"pandas", "premise", "bw2data"}:
                raise AssertionError(f"Scenario input import must not load {name}")
            return original_import(name, *args, **kwargs)

        spec = importlib.util.spec_from_file_location(
            "isolated_scenario_inputs", Path(__file__).resolve().parents[1] / "por_scenario_inputs.py"
        )
        module = importlib.util.module_from_spec(spec)
        with patch("builtins.__import__", side_effect=checked_import):
            spec.loader.exec_module(module)
            self.assertEqual(module.fresh_scenario_definitions([]), [])
        self.assertTrue(callable(module.reconcile_px_terminal_purity))

    def test_contaminated_legacy_and_native_definitions_drop_runtime_state(self):
        resource = OpaqueResource()
        runtime = OpaqueResource()
        state = {
            "applied functions": runtime, "database": runtime,
            "database filepath": runtime, "database metadata filepath": runtime,
            "database metadata cache filepath": runtime,
            "inventories metadata cache filepath": runtime,
            "database name": runtime, "iam data": runtime, "external data": runtime,
            "mapping": runtime, "cache": runtime, "index": runtime,
            "configurations": runtime, "heat diagnostics": runtime,
            "unmatched category flows": runtime, "por supplier linking": runtime,
            "_inventory_store": runtime, "_inventory_checkpoint": runtime,
            "_inventory_export_handoff": runtime, "_validation_report": runtime,
            "_future_premise_state": runtime,
        }
        original = definition(resource, **state)
        result = fresh_scenario_definitions([original])
        self.assertEqual(set(result[0]), {"model", "pathway", "year", "external scenarios"})
        self.assertEqual(result[0]["year"], 2050)
        self.assertIs(result[0]["external scenarios"][0]["data"], resource)
        for key in state:
            self.assertIs(original[key], runtime)

    def test_supported_optional_inputs_and_external_options_are_preserved(self):
        resource = OpaqueResource()
        original = definition(resource, filepath=Path("custom_iam"), exclude=["update_steel"])
        original["external scenarios"][0]["options"] = {"regions": ["NL", "RER"]}
        result = fresh_scenario_definitions([original])[0]
        self.assertEqual(result, original)
        self.assertIsNot(result, original)
        self.assertIsNot(result["exclude"], original["exclude"])
        self.assertIsNot(result["external scenarios"], original["external scenarios"])
        self.assertIsNot(result["external scenarios"][0], original["external scenarios"][0])
        self.assertIs(result["external scenarios"][0]["data"], resource)
        result["exclude"].append("update_fuels")
        result["external scenarios"][0]["options"]["regions"].append("RoW")
        self.assertEqual(original["exclude"], ["update_steel"])
        self.assertEqual(original["external scenarios"][0]["options"]["regions"], ["NL", "RER"])

    def test_even_dictionary_resources_are_opaque(self):
        resource = {"resources": [{"path": "scenario_data.csv"}]}
        result = fresh_scenario_definitions([definition(resource)])
        self.assertIs(result[0]["external scenarios"][0]["data"], resource)

    def test_brightway_and_repeated_pathways_inputs_are_independent(self):
        resource = OpaqueResource()
        originals = [definition(resource), definition(resource, external="CL")]
        brightway = fresh_scenario_definitions(originals[:1])
        brightway[0]["applied functions"] = ["external"]
        brightway[0]["_inventory_checkpoint"] = OpaqueResource()
        brightway[0]["external scenarios"][0]["scenario"] = "changed by export"
        first_package = fresh_scenario_definitions(originals)
        first_package[0]["applied functions"] = ["electricity", "external"]
        first_package[0]["external scenarios"][0]["scenario"] = "changed again"
        second_package = fresh_scenario_definitions(originals)
        self.assertEqual([s["external scenarios"][0]["scenario"] for s in second_package], ["OCE", "CL"])
        self.assertEqual([s["year"] for s in second_package], [2050, 2050])
        for scenario in originals + second_package:
            self.assertNotIn("applied functions", scenario)
            self.assertNotIn("_inventory_checkpoint", scenario)

    def test_expanded_years_and_shared_definitions_receive_independent_containers(self):
        resource = OpaqueResource()
        source = definition(resource, exclude=["update_steel"])
        expanded = [dict(source, year=year) for year in (2025, 2030, 2035, 2040, 2045, 2050)]
        result = fresh_scenario_definitions(expanded)
        result[0]["exclude"].append("update_fuels")
        result[0]["external scenarios"][0]["scenario"] = "changed"
        result[0]["applied functions"] = ["external"]
        self.assertEqual([s["year"] for s in result], [2025, 2030, 2035, 2040, 2045, 2050])
        for scenario in result[1:]:
            self.assertEqual(scenario["exclude"], ["update_steel"])
            self.assertEqual(scenario["external scenarios"][0]["scenario"], "OCE")
            self.assertNotIn("applied functions", scenario)
            self.assertIs(scenario["external scenarios"][0]["data"], resource)

    def test_unknown_public_options_fail_instead_of_silently_disappearing(self):
        original = definition(OpaqueResource(), inventory_backend="compact")
        with self.assertRaisesRegex(ValueError, "unsupported public input fields.*inventory_backend"):
            fresh_scenario_definitions([original])
        self.assertEqual(original["inventory_backend"], "compact")

    def test_invalid_input_containers_fail_clearly(self):
        with self.assertRaisesRegex(TypeError, "Scenario 0 must be a mapping"):
            fresh_scenario_definitions([None])
        with self.assertRaisesRegex(TypeError, "external scenarios must be a list"):
            fresh_scenario_definitions([definition(None, **{"external scenarios": None})])
        with self.assertRaisesRegex(TypeError, "external entry 0 must be a mapping"):
            fresh_scenario_definitions([definition(None, **{"external scenarios": [None]})])

    def test_empty_selection_stays_empty_and_optional_fields_stay_absent(self):
        self.assertEqual(fresh_scenario_definitions([]), [])
        source = {"model": "remind", "pathway": "SSP2-NPi", "year": 2025}
        self.assertEqual(fresh_scenario_definitions([source]), [source])


if __name__ == "__main__":
    unittest.main()
