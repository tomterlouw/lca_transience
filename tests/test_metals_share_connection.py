"""File-based numerical policy connection tests, with isolated share inputs."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest

import yaml

from metals_scenario_policy import (
    MetalPolicyError, MetalSteelPolicy, StaleMetalPolicyError, apply_database,
    classify_supply, plan_database,
)
from tests.test_metals_scenario_policy import configuration, exchange, inventory, market


class ShareConnectionTests(unittest.TestCase):
    def setUp(self):
        self.directory_context = tempfile.TemporaryDirectory()
        self.directory = Path(self.directory_context.name)
        self.addCleanup(self.directory_context.cleanup)
        self.policy_path = self.directory / "policy.yaml"
        self.input_path = self.directory / "source.yaml"
        self.selected_path = self.directory / "scenario.yaml"
        self.steel_path = self.directory / "steel.yaml"
        self.config = configuration()
        self.config["share_data"] = {
            "directory": ".",
            "nonferrous": {"reference": {"file": "source.yaml", "year": 2020},
                           "high_recycling": {"file": "scenario.yaml"}},
        }
        self.records = {"copper, cathode": {"name": "market for copper, cathode", "reference product": "copper, cathode",
                                            "shares": {"primary": {2020: 0.8, 2050: 0.75}, "secondary": {2020: 0.2, 2050: 0.25}}}}
        self.write(self.input_path, self.records)
        self.write(self.selected_path, self.records)
        self.write(self.steel_path, {"schema_version": 1, "products": {"steel": {"high_recycling": {2025: 0, 2050: 0.8}}}})
        self.write(self.policy_path, self.config)

    def write(self, path, value):
        path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")

    def selected(self, **kwargs):
        return MetalSteelPolicy.from_file(self.policy_path, **kwargs)

    def test_selected_file_replaces_inline_numbers_and_drives_plan(self):
        selected = self.selected(nonferrous_case="high_recycling")
        plan = plan_database(inventory(market()), selected, scenario={"year": 2050})
        self.assertAlmostEqual(plan.audit[0]["applied_secondary_share"], 0.25)
        self.assertEqual(plan.audit[0]["share_data_path"], str(self.selected_path.resolve()))
        self.assertEqual(plan.audit[0]["share_data_targets"], {2020: 0.2, 2050: 0.25})
        self.assertEqual(selected.describe()["share_data_sources"][0]["source_product"], "copper, cathode")
        self.assertNotIn(str(self.steel_path.resolve()), selected.source_hashes)

    def test_direct_selected_file_edit_changes_new_plan_and_invalidates_old_policy(self):
        old = self.selected(nonferrous_case="high_recycling")
        revised = deepcopy(self.records)
        revised["copper, cathode"]["shares"]["secondary"][2050] = 0.55
        revised["copper, cathode"]["shares"]["primary"][2050] = 0.45
        self.write(self.selected_path, revised)
        with self.assertRaises(StaleMetalPolicyError):
            old.verify_sources()
        new = self.selected(nonferrous_case="high_recycling")
        self.assertNotEqual(old.fingerprint, new.fingerprint)
        self.assertAlmostEqual(plan_database(inventory(market()), new, scenario={"year": 2050}).audit[0]["applied_secondary_share"], 0.55)

    def test_native_baseline_never_applies_generated_baseline_numbers(self):
        self.selected_path.unlink()
        self.input_path.unlink()
        self.steel_path.unlink()
        selected = self.selected()
        self.assertTrue(selected.baseline)
        self.assertEqual(set(selected.source_hashes), {str(self.policy_path.resolve())})
        self.assertEqual(plan_database(inventory(market()), selected, scenario={"year": 2050}).changes, ())

    def test_reference_reads_original_year_and_not_generated_future(self):
        revised = deepcopy(self.records)
        revised["copper, cathode"]["shares"]["secondary"][2020] = 0.17
        revised["copper, cathode"]["shares"]["primary"][2020] = 0.83
        self.write(self.input_path, revised)
        selected = self.selected(nonferrous_case="reference")
        plan = plan_database(inventory(market()), selected, scenario={"year": 2050})
        self.assertAlmostEqual(plan.audit[0]["applied_secondary_share"], 0.17)
        self.assertEqual(selected.describe()["share_data_sources"][0]["reference_year"], 2020)

    def test_missing_empty_wrong_grade_or_unbalanced_source_fails(self):
        variants = ({}, {"copper, cathode": {**self.records["copper, cathode"], "reference product": "copper, anode"}},
                    {"copper, cathode": {**self.records["copper, cathode"], "unit": "tonne"}},
                    {"copper, cathode": {**self.records["copper, cathode"], "shares": {"primary": {2020: 0.8}, "secondary": {2020: 0.3}}}},
                    {"copper, cathode": {**self.records["copper, cathode"], "shares": {"primary": {2020: 0.8}, "secondary": {2050: 0.2}}}})
        for document in variants:
            with self.subTest(document=document):
                self.write(self.selected_path, document)
                with self.assertRaises(MetalPolicyError):
                    self.selected(nonferrous_case="high_recycling")
        self.selected_path.unlink()
        with self.assertRaisesRegex(MetalPolicyError, "ensure_share_outputs"):
            self.selected(nonferrous_case="high_recycling")

    def test_legacy_steel_share_data_rejected_before_reading_any_graph(self):
        self.config["share_data"]["steel"] = {"file": "does-not-exist.yaml", "key": "steel"}
        self.write(self.policy_path, self.config)
        with self.assertRaisesRegex(MetalPolicyError, "endogenous to IAM/EDM-I"):
            self.selected()
        with self.assertRaisesRegex(MetalPolicyError, "endogenous to IAM/EDM-I"):
            self.selected(steel_case="high_recycling")

    def test_manifest_and_upstream_inputs_are_freshness_dependencies(self):
        digest = lambda path: sha256(path.read_bytes()).hexdigest()
        manifest = {"schema_version": 1, "generator_version": 1,
                    "input_sha256": {str(self.input_path.resolve()): digest(self.input_path)},
                    "output_sha256": {self.selected_path.name: digest(self.selected_path)}}
        manifest_path = self.directory / "share_generation_manifest.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        selected = self.selected(nonferrous_case="high_recycling")
        self.assertIn(str(manifest_path.resolve()), selected.source_hashes)
        self.assertIn(str(self.input_path.resolve()), selected.source_hashes)
        self.assertTrue(selected.describe()["share_data_sources"][0]["matches_generated_manifest"])
        self.input_path.write_text(self.input_path.read_text() + "\n# revised\n", encoding="utf-8")
        with self.assertRaises(StaleMetalPolicyError):
            selected.verify_sources()
        with self.assertRaisesRegex(StaleMetalPolicyError, "ensure_share_outputs"):
            self.selected(nonferrous_case="high_recycling")

    def test_manual_selected_output_can_be_read_with_manifest_receipt(self):
        manifest = {"schema_version": 1, "input_sha256": {}, "output_sha256": {"scenario.yaml": "earlier-generation"}}
        (self.directory / "share_generation_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        selected = self.selected(nonferrous_case="high_recycling")
        self.assertFalse(selected.describe()["share_data_sources"][0]["matches_generated_manifest"])
        self.assertEqual(selected.rules[0]["targets"]["high_recycling"], self.records["copper, cathode"]["shares"]["secondary"])

    def test_manifest_input_paths_are_resolved_portably(self):
        manifest = {"schema_version": 1, "input_sha256": {"source.yaml": sha256(self.input_path.read_bytes()).hexdigest()},
                    "output_sha256": {"scenario.yaml": sha256(self.selected_path.read_bytes()).hexdigest()}}
        (self.directory / "share_generation_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        selected = self.selected(nonferrous_case="high_recycling")
        self.assertIn(str(self.input_path.resolve()), selected.source_hashes)

    def test_only_supported_configured_material_subset_loaded(self):
        self.records["copper ore"] = {"name": "market for copper ore", "reference product": "copper ore", "shares": "not interpreted"}
        self.write(self.selected_path, self.records)
        selected = self.selected(nonferrous_case="high_recycling")
        self.assertEqual(len(selected.describe()["share_data_sources"]), 1)

    def test_duplicate_source_keys_never_silently_overwrite_assumptions(self):
        documents = (
            "copper, cathode: {}\ncopper, cathode: {}\n",
            "copper, cathode:\n  name: market for copper, cathode\n  reference product: copper, cathode\n  shares:\n    primary: {2020: 0.8, 2050: 0.75}\n    secondary: {2020: 0.2, 2050: 0.25, 2050: 0.7}\n",
        )
        for document in documents:
            with self.subTest(document=document):
                self.selected_path.write_text(document, encoding="utf-8")
                with self.assertRaisesRegex(MetalPolicyError, "duplicate key"):
                    self.selected(nonferrous_case="high_recycling")
        self.policy_path.write_text("schema_version: 1\nschema_version: 2\n", encoding="utf-8")
        with self.assertRaisesRegex(MetalPolicyError, "duplicate key"):
            self.selected()

    def test_source_rounding_tolerance_matches_generator(self):
        revised = deepcopy(self.records)
        revised["copper, cathode"]["shares"]["primary"][2050] = 0.7499995
        self.write(self.selected_path, revised)
        self.assertAlmostEqual(self.selected(nonferrous_case="high_recycling").rules[0]["targets"]["high_recycling"][2050], 0.25)


class PinnedSecondarySupplyTests(unittest.TestCase):
    """Pinned provider identities do not import vendor behaviour or share weights."""

    def setUp(self):
        self.directory_context = tempfile.TemporaryDirectory()
        self.directory = Path(self.directory_context.name)
        self.addCleanup(self.directory_context.cleanup)
        self.policy_path = self.directory / "policy.yaml"
        self.source_path = self.directory / "upstream" / "secondary_supply_activities.yaml"
        self.source_path.parent.mkdir()
        self.provenance_path = self.source_path.with_name("provenance.json")
        self.config = configuration()
        self.rule = self.config["markets"][0]
        self.rule["secondary_source_product"] = "copper, cathode"
        self.config["secondary_supply_source"] = {
            "file": "upstream/secondary_supply_activities.yaml", "provenance": "upstream/provenance.json",
        }
        self.source = {"copper, cathode": [{
            "name": {"all": [{"startswith": "treatment of copper scrap"}, {"contains": "refining"}]},
            "reference product": {"equals": "copper, cathode"},
            "location": {"any": [{"equals": "World"}, {"equals": "RER"}]},
            "shares": {2020: 1.0},
        }]}
        self.write_source()

    def write_source(self):
        self.source_path.write_text(yaml.safe_dump(self.source, sort_keys=False), encoding="utf-8")
        self.provenance = {"schema_version": 1, "commit": "d" * 40, "files": {
            "secondary_supply_activities.yaml": {"local_file": self.source_path.name,
                                                  "sha256": sha256(self.source_path.read_bytes()).hexdigest()},
        }}
        self.provenance_path.write_text(json.dumps(self.provenance), encoding="utf-8")
        self.policy_path.write_text(yaml.safe_dump(self.config, sort_keys=False), encoding="utf-8")

    def selected(self, case="high_recycling"):
        self.policy_path.write_text(yaml.safe_dump(self.config, sort_keys=False), encoding="utf-8")
        return MetalSteelPolicy.from_file(self.policy_path, nonferrous_case=case)

    def test_nonbaseline_loads_pinned_hash_dependencies_and_exports_receipt(self):
        selected = self.selected()
        self.assertEqual(set(selected.source_hashes), {
            str(self.policy_path.resolve()), str(self.source_path.resolve()), str(self.provenance_path.resolve()),
        })
        receipt = selected.describe()["secondary_supply_sources"][0]
        self.assertEqual(receipt["commit"], "d" * 40)
        self.assertEqual(receipt["source_product"], "copper, cathode")
        self.assertFalse(receipt["source_weights_applied"])
        self.assertEqual(receipt["selector_count"], 1)
        plan = plan_database(inventory(market()), selected, scenario={"year": 2050})
        self.assertEqual(plan.audit[0]["secondary_supply_source_sha256"], receipt["sha256"])

    def test_native_baseline_does_not_open_optional_upstream_sources(self):
        self.source_path.unlink()
        self.provenance_path.unlink()
        selected = self.selected("baseline")
        self.assertEqual(set(selected.source_hashes), {str(self.policy_path.resolve())})
        self.assertEqual(plan_database(inventory(market()), selected, scenario={"year": 2050}).changes, ())

    def test_all_selector_fields_must_match_and_unlisted_recycling_is_unknown(self):
        selected = self.selected()
        rule = selected.rules[0]
        supplied = exchange("treatment of copper scrap, refining", 0.2)
        self.assertEqual(classify_supply(supplied, rule), "secondary")
        supplied["location"] = "RoW"
        self.assertEqual(classify_supply(supplied, rule), "unknown")
        supplied["name"] = "treatment of other copper waste"
        self.assertEqual(classify_supply(supplied, rule), "unknown")
        self.assertEqual(classify_supply(exchange("copper production, primary", 0.8), rule), "primary")
        supplied["product"] = "copper, anode"
        self.assertIsNone(classify_supply(supplied, rule))

    def test_local_predicate_narrows_but_never_bypasses_upstream_selectors(self):
        self.rule["secondary"] = {"names": ["treatment of copper scrap, refining, approved"]}
        selected = self.selected()
        rule = selected.rules[0]
        self.assertEqual(classify_supply(exchange("treatment of copper scrap, refining, approved", 0.2), rule), "secondary")
        self.assertEqual(classify_supply(exchange("treatment of copper scrap, refining, unreviewed", 0.2), rule), "unknown")
        self.assertEqual(classify_supply(exchange("treatment of copper scrap, refining, approved", 0.2,
                                                  location="RoW"), rule), "unknown")

    def test_all_pinned_manifest_files_are_hash_checked_not_only_selected_map(self):
        auxiliary = self.source_path.with_name("primary_secondary_split.yaml")
        auxiliary.write_text("fixture: primary reference source\n", encoding="utf-8")
        self.provenance["files"][auxiliary.name] = {
            "local_file": auxiliary.name, "sha256": sha256(auxiliary.read_bytes()).hexdigest(),
        }
        self.provenance_path.write_text(json.dumps(self.provenance), encoding="utf-8")
        selected = self.selected()
        self.assertIn(str(auxiliary.resolve()), selected.source_hashes)
        auxiliary.write_text("fixture: drift\n", encoding="utf-8")
        with self.assertRaises(StaleMetalPolicyError):
            selected.verify_sources()
        with self.assertRaisesRegex(StaleMetalPolicyError, "provenance SHA256"):
            self.selected()

    def test_source_regional_weights_are_not_used_to_rebuild_recipe(self):
        self.source["copper, cathode"] = [
            {"name": {"equals": "treatment of copper scrap, refining"},
             "reference product": {"equals": "copper, cathode"},
             "location": {"equals": location}, "shares": {2020: weight}}
            for location, weight in (("World", 0.999), ("RER", 0.001))
        ]
        self.write_source()
        ds = market([exchange("copper production, primary", 0.8),
                     exchange("treatment of copper scrap, refining", 0.1),
                     exchange("treatment of copper scrap, refining", 0.1, location="RER")])
        selected = self.selected()
        plan = plan_database(inventory(ds), selected, scenario={"year": 2050})
        out = apply_database(inventory(ds), plan)
        self.assertAlmostEqual(out[0]["exchanges"][2]["amount"], 0.3)
        self.assertAlmostEqual(out[0]["exchanges"][3]["amount"], 0.3)
        self.assertAlmostEqual(plan.audit[0]["applied_supply_total"], 1.0)

    def test_pinned_snapshot_and_provenance_cannot_drift(self):
        selected = self.selected()
        self.source_path.write_text(self.source_path.read_text() + "\n# drift\n", encoding="utf-8")
        with self.assertRaises(StaleMetalPolicyError):
            selected.verify_sources()
        with self.assertRaisesRegex(StaleMetalPolicyError, "provenance SHA256"):
            self.selected()
        self.write_source()
        self.provenance["commit"] = "moving-master"
        self.provenance_path.write_text(json.dumps(self.provenance), encoding="utf-8")
        with self.assertRaisesRegex(MetalPolicyError, "pinned 40-character commit"):
            self.selected()

    def test_empty_missing_unsupported_and_ambiguous_criteria_fail(self):
        original = deepcopy(self.source)
        variants = (
            [], [{"name": {"equals": "treatment of copper scrap, refining"}}],
            [{**original["copper, cathode"][0], "name": {"regex": ".*"}}],
            [{**original["copper, cathode"][0], "name": {"contains": "scrap", "equals": "scrap"}}],
            [{**original["copper, cathode"][0], "name": {"any": []}}],
            original["copper, cathode"] * 2,
        )
        for records in variants:
            with self.subTest(records=records):
                self.source = {"copper, cathode": records}
                self.write_source()
                with self.assertRaises(MetalPolicyError):
                    self.selected()

    def test_overlapping_criteria_fail_on_actual_provider_instead_of_double_match(self):
        self.source["copper, cathode"].append({
            "name": {"startswith": "treatment of"}, "reference product": {"equals": "copper, cathode"},
            "location": {"equals": "World"}, "shares": {2020: 0.0},
        })
        self.write_source()
        selected = self.selected()
        with self.assertRaisesRegex(MetalPolicyError, "ambiguous secondary-provider match"):
            plan_database(inventory(market()), selected, scenario={"year": 2050})

    def test_explicit_grade_whitelist_and_documented_interface_are_required(self):
        self.source["copper, cathode"][0]["reference product"] = {"equals": "copper, anode"}
        self.write_source()
        with self.assertRaisesRegex(MetalPolicyError, "supply_products whitelist"):
            self.selected()
        self.rule["supply_products"] = ["copper, cathode", "copper, anode"]
        with self.assertRaisesRegex(MetalPolicyError, "supply_product_note"):
            self.selected()
        self.rule["supply_product_note"] = "Fixture: explicitly declared upstream grade interface, not a conversion inventory."
        selected = self.selected()
        self.assertEqual(classify_supply(exchange("treatment of copper scrap, refining", 0.2,
                                                  product="copper, anode"), selected.rules[0]), "secondary")

    def test_opted_in_mapping_cannot_silently_bypass_source_loader(self):
        with self.assertRaisesRegex(MetalPolicyError, "from_file"):
            MetalSteelPolicy.from_mapping(self.config, nonferrous_case="high_recycling")
        self.assertTrue(MetalSteelPolicy.from_mapping(self.config).baseline)

    def test_primary_only_still_validates_provider_identities_but_never_reads_weights_as_target(self):
        selected = self.selected("primary_only")
        plan = plan_database(inventory(market()), selected, scenario={"year": 2050})
        self.assertAlmostEqual(plan.audit[0]["applied_secondary_share"], 0.0)
        self.assertIn(str(self.source_path.resolve()), selected.source_hashes)

    def test_evidence_year_gate_prevents_unreviewed_minimum_interpolation(self):
        self.rule["minimum_target_years"] = [2050]
        selected = self.selected()
        for year in (2025, 2030, 2040, 2045, 2060):
            with self.subTest(year=year):
                plan = plan_database(inventory(market()), selected, scenario={"year": year})
                self.assertEqual(plan.changes, ())
                self.assertEqual(plan.audit[0]["status"], "evidence_year_not_covered")
                self.assertAlmostEqual(plan.audit[0]["applied_secondary_share"], 0.2)
        covered = plan_database(inventory(market()), selected, scenario={"year": 2050})
        self.assertEqual(len(covered.changes), 1)
        self.assertAlmostEqual(covered.audit[0]["applied_secondary_share"], 0.6)

    def test_evidence_year_gate_does_not_change_reference_primary_only_or_baseline(self):
        self.rule["minimum_target_years"] = [2050]
        for case, expected in (("reference", 0.2), ("primary_only", 0.0), ("baseline", 0.2)):
            with self.subTest(case=case):
                selected = self.selected(case)
                plan = plan_database(inventory(market()), selected, scenario={"year": 2030})
                self.assertNotEqual(plan.audit[0]["status"], "evidence_year_not_covered")
                self.assertAlmostEqual(plan.audit[0]["applied_secondary_share"], expected)

    def test_evidence_year_gate_rejects_empty_invalid_duplicate_or_nonfinite_years(self):
        for years in ([], "2050", [2050, "2050"], [True], [float("nan")], [float("inf")], ["future"]):
            with self.subTest(years=years):
                self.rule["minimum_target_years"] = years
                with self.assertRaises(MetalPolicyError):
                    self.selected()


class RealRepositoryLithiumExtensionTests(unittest.TestCase):
    """Real reviewed policy/targets, synthetic inventories and no Brightway I/O."""

    root = Path(__file__).resolve().parents[1]
    policy_path = root / "configuration_file" / "metals_scenario_policy.yaml"

    @staticmethod
    def lithium_market(*, secondary_share=0.000840414604538239, location="World"):
        product = "lithium carbonate, battery grade"
        name = "market for " + product
        return {"name": name, "reference product": product, "unit": "kilogram", "location": location,
                "exchanges": [
                    {"name": name, "amount": 1, "product": product, "unit": "kilogram",
                     "location": location, "type": "production"},
                    exchange("lithium carbonate production, from brine", 1 - secondary_share,
                             product=product, location="CL"),
                    exchange("treatment of used Li-ion battery, hydrometallurgical treatment", secondary_share,
                             product="lithium carbonate", location="GLO"),
                    exchange("transport, freight", 0.01, product="transport", unit="ton kilometer"),
                    {"name": "Carbon dioxide, fossil", "type": "biosphere", "amount": 0.03},
                ]}

    def policy(self, case="baseline"):
        return MetalSteelPolicy.from_file(self.policy_path, nonferrous_case=case)

    def test_real_baseline_remains_eleven_rule_audit_only_with_no_upstream_dependency(self):
        selected = self.policy()
        self.assertEqual(len(selected.rules), 11)
        self.assertEqual(set(selected.source_hashes), {str(self.policy_path.resolve())})
        ds = self.lithium_market()
        db = inventory(ds)
        before = deepcopy(db)
        plan = plan_database(db, selected, scenario={"year": 2050})
        self.assertEqual(plan.changes, ())
        self.assertEqual(apply_database(db, plan, policy=selected), before)

    def test_real_2050_literature_endpoints_drive_both_supply_groups(self):
        for case, expected in (("conservative", 250 / 1122), ("moderate_recycling", 250 / 1122),
                               ("high_recycling", 560 / 1501)):
            with self.subTest(case=case):
                selected = self.policy(case)
                ds = self.lithium_market()
                db = inventory(ds)
                plan = plan_database(db, selected, scenario={"year": 2050})
                self.assertEqual(len(plan.changes), 1)
                self.assertAlmostEqual(plan.audit[0]["applied_secondary_share"], expected)
                revised = apply_database(db, plan, policy=selected)[0]
                self.assertAlmostEqual(revised["exchanges"][1]["amount"], 1 - expected)
                self.assertAlmostEqual(revised["exchanges"][2]["amount"], expected)
                self.assertAlmostEqual(sum(exc["amount"] for exc in revised["exchanges"][1:3]), 1)
                self.assertEqual(revised["exchanges"][3:], ds["exchanges"][3:])

    def test_real_minimum_cases_interpolate_linearly_in_intermediate_years(self):
        reference = 0.000840414604538239
        for case, endpoint in (("conservative", 250 / 1122), ("moderate_recycling", 250 / 1122),
                               ("high_recycling", 560 / 1501)):
            selected = self.policy(case)
            rule = next(rule for rule in selected.rules if rule["id"] == "lithium_carbonate_battery_grade")
            self.assertNotIn("minimum_target_years", rule)
            self.assertEqual(set(rule["targets"][case]), {2020, 2050})
            self.assertAlmostEqual(rule["targets"][case][2020], reference)
            self.assertAlmostEqual(rule["targets"][case][2050], endpoint)
            for year in (2025, 2030, 2035, 2040, 2045, 2050):
                with self.subTest(case=case, year=year):
                    ds = self.lithium_market()
                    # Preserve the market's gross material demand, including
                    # distribution losses, rather than normalising it to output.
                    for exchange_row in ds["exchanges"][1:3]:
                        exchange_row["amount"] *= 1.01
                    db = inventory(ds)
                    expected = reference + (endpoint - reference) * (year - 2020) / 30
                    plan = plan_database(db, selected, scenario={"year": year})
                    self.assertEqual(len(plan.changes), 1)
                    self.assertNotEqual(plan.audit[0]["status"], "evidence_year_not_covered")
                    self.assertAlmostEqual(plan.audit[0]["target_secondary_share"], expected)
                    self.assertAlmostEqual(plan.audit[0]["applied_secondary_share"], expected)
                    revised = apply_database(db, plan, policy=selected)[0]
                    self.assertAlmostEqual(revised["exchanges"][1]["amount"], 1.01 * (1 - expected))
                    self.assertAlmostEqual(revised["exchanges"][2]["amount"], 1.01 * expected)
                    self.assertAlmostEqual(sum(exc["amount"] for exc in revised["exchanges"][1:3]), 1.01)
                    self.assertEqual(revised["exchanges"][3:], ds["exchanges"][3:])

    def test_real_minimum_curves_clamp_to_2020_and_2050_endpoints(self):
        reference = 0.000840414604538239
        for case, endpoint in (("conservative", 250 / 1122), ("moderate_recycling", 250 / 1122),
                               ("high_recycling", 560 / 1501)):
            selected = self.policy(case)
            for year, expected in ((2015, reference), (2020, reference), (2050, endpoint), (2055, endpoint)):
                with self.subTest(case=case, year=year):
                    plan = plan_database(inventory(self.lithium_market()), selected, scenario={"year": year})
                    self.assertAlmostEqual(plan.audit[0]["target_secondary_share"], expected)
                    self.assertAlmostEqual(plan.audit[0]["applied_secondary_share"], expected)

    def test_real_minimum_curves_preserve_higher_native_intermediate_shares(self):
        for case in ("conservative", "moderate_recycling", "high_recycling"):
            selected = self.policy(case)
            for year in (2025, 2030, 2035, 2040, 2045):
                with self.subTest(case=case, year=year):
                    db = inventory(self.lithium_market(secondary_share=0.8))
                    before = deepcopy(db)
                    plan = plan_database(db, selected, scenario={"year": year})
                    self.assertEqual(plan.changes, ())
                    self.assertEqual(plan.audit[0]["status"], "already_satisfied")
                    self.assertAlmostEqual(plan.audit[0]["applied_secondary_share"], 0.8)
                    self.assertEqual(apply_database(db, plan, policy=selected), before)

    def test_real_reference_and_primary_only_counterfactuals_are_explicit(self):
        for case, expected in (("reference", 0.000840414604538239), ("primary_only", 0.0)):
            with self.subTest(case=case):
                selected = self.policy(case)
                ds = self.lithium_market(secondary_share=0.1)
                plan = plan_database(inventory(ds), selected, scenario={"year": 2030})
                self.assertAlmostEqual(plan.audit[0]["applied_secondary_share"], expected)

    def test_real_policy_keeps_steel_unrelated_products_and_higher_native_shares(self):
        selected = self.policy("high_recycling")
        lithium = self.lithium_market(secondary_share=0.8)
        steel = market(steel=True)
        unreviewed = {"name": "market for rhodium", "reference product": "rhodium", "unit": "kilogram",
                      "location": "World", "exchanges": []}
        db = inventory(lithium) + [steel, unreviewed]
        before = deepcopy(db)
        plan = plan_database(db, selected, scenario={"year": 2050})
        self.assertEqual(plan.changes, ())
        self.assertEqual(plan.stats["matched_markets"], 1)
        self.assertEqual(plan.audit[0]["status"], "already_satisfied")
        self.assertEqual(apply_database(db, plan, policy=selected), before)

    def test_original_five_high_targets_remain_unchanged(self):
        selected = self.policy("high_recycling")
        expected = {"aluminium, cast alloy": 0.70, "copper, cathode": 0.45, "nickel, class 1": 0.30,
                    "cobalt": 0.40, "lead": 0.85}
        observed = {rule["reference product"]: rule["targets"]["high_recycling"][2050]
                    for rule in selected.rules if rule["reference product"] in expected}
        self.assertEqual(observed, expected)

    def test_pinned_source_and_provenance_hashes_are_verified_dependencies(self):
        selected = self.policy("high_recycling")
        directory = self.root / "configuration_file" / "upstream" / "premise_metals"
        provenance_path = directory / "provenance.json"
        provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
        self.assertEqual(provenance["commit"], "d5362d8ce1df66c20c7e71c91051a1b88e46c583")
        expected = {
            "secondary_supply_activities.yaml": "7fa070cf59aae49260d8464e6e7315bed521edfe919ccb591e2eddef5147ed65",
            "primary_secondary_split.yaml": "cb56e74eb4ce0fa2d24cc96f6eacc681df2a6989c4edb7e1cbe346ebea54c8e1",
        }
        for name, fingerprint in expected.items():
            path = directory / name
            self.assertEqual(sha256(path.read_bytes()).hexdigest(), fingerprint)
            self.assertEqual(provenance["files"][name]["sha256"], fingerprint)
            self.assertEqual(selected.source_hashes[str(path.resolve())], fingerprint)
        self.assertEqual(selected.source_hashes[str(provenance_path.resolve())],
                         sha256(provenance_path.read_bytes()).hexdigest())
        selected.verify_sources()

    def test_real_exact_li_recovery_refines_broad_upstream_treatment_pattern(self):
        selected = self.policy("high_recycling")
        rule = next(rule for rule in selected.rules if rule["reference product"] == "lithium carbonate, battery grade")
        known = self.lithium_market()["exchanges"][2]
        self.assertEqual(classify_supply(known, rule), "secondary")
        unreviewed = dict(known, name="treatment of unrelated lithium-bearing waste")
        self.assertEqual(classify_supply(unreviewed, rule), "unknown")
        foreign = dict(known, location="RER")
        self.assertEqual(classify_supply(foreign, rule), "unknown")


class GenericLithiumCarbonateConnectionTests(unittest.TestCase):
    """Generic GLO carbonate is distinct from the World battery-grade proxy.

    Synthetic inventories exercise the live policy files without overwriting
    exports or claiming a complete elemental-lithium characterization method.
    """

    policy_path = Path(__file__).resolve().parents[1] / "configuration_file/metals_scenario_policy.yaml"
    product = "lithium carbonate"
    recovery = "treatment of used Li-ion battery, hydrometallurgical treatment"
    reference = 0.000840414604538239
    endpoints = {"conservative": 250 / 1122, "moderate_recycling": 250 / 1122,
                 "high_recycling": 560 / 1501}

    def policy(self, case="baseline"):
        return MetalSteelPolicy.from_file(self.policy_path, nonferrous_case=case)

    def carbonate(self, *, secondary_share=None, location="GLO", gross=1.0):
        share = self.reference if secondary_share is None else secondary_share
        name = "market for " + self.product
        return {"name": name, "reference product": self.product, "unit": "kilogram", "location": location,
                "exchanges": [
                    {"name": name, "product": self.product, "unit": "kilogram", "location": location,
                     "amount": 1, "type": "production"},
                    exchange("lithium carbonate production, from concentrated brine", gross * (1 - share) * 0.34,
                             product=self.product, location="GLO"),
                    exchange("lithium carbonate production, from spodumene", gross * (1 - share) * 0.525,
                             product=self.product, location="CN"),
                    exchange("lithium carbonate production, from spodumene", gross * (1 - share) * 0.135,
                             product=self.product, location="RoW"),
                    exchange(self.recovery, gross * share, product=self.product, location="GLO",
                             **{"comment": "Existing hydro-recovery recipe, not burden-free scrap"}),
                    exchange("transport, freight, lorry", 0.027, product="transport, freight, lorry",
                             unit="ton kilometer", location="GLO"),
                    {"name": "Carbon dioxide, fossil", "amount": 0.007, "type": "biosphere"},
                ]}

    def database(self, market=None):
        db = inventory(self.carbonate() if market is None else market)
        # The policy changes demand for this supplier, not its allocated recipe.
        provider = next(ds for ds in db if ds["name"] == self.recovery)
        provider["exchanges"] = [
            {"name": self.recovery, "product": self.product, "unit": "kilogram", "location": "GLO",
             "type": "production", "amount": 1},
            exchange("market for electricity, medium voltage", 0.4,
                     product="electricity, medium voltage", unit="kilowatt hour", location="GLO"),
            exchange("treatment of used Li-ion battery", -2.2,
                     product="used Li-ion battery", unit="kilogram", location="GLO"),
            {"name": "Carbon dioxide, fossil", "amount": 0.25, "type": "biosphere"},
        ]
        return db

    def audit(self, plan):
        return next(row for row in plan.audit if row["reference product"] == self.product)

    def test_same_product_glo_rule_and_source_identity_are_explicit(self):
        selected = self.policy("high_recycling")
        rule = next(rule for rule in selected.rules if rule["id"] == "lithium_carbonate_generic")
        self.assertEqual(rule["name"], "market for lithium carbonate")
        self.assertEqual(rule["reference product"], self.product)
        self.assertEqual(rule["source_product"], self.product)
        self.assertEqual(rule["supply_products"], [self.product])
        self.assertEqual(rule["locations"], ["GLO"])
        self.assertTrue(rule["strict_secondary_matching"])
        self.assertNotIn("secondary_source_product", rule)
        self.assertNotIn("_secondary_supply_selectors", rule)
        self.assertEqual(rule["secondary"]["names"], [self.recovery])
        self.assertTrue(rule["target_note"])

    def test_native_baseline_is_exact_noop_and_uses_no_optional_share_files(self):
        selected = self.policy()
        db = self.database()
        before = deepcopy(db)
        plan = plan_database(db, selected, scenario={"year": 2050})
        self.assertEqual(plan.changes, ())
        self.assertEqual(apply_database(db, plan, policy=selected), before)
        self.assertEqual(set(selected.source_hashes), {str(self.policy_path.resolve())})
        self.assertTrue(plan.stats["native_baseline"])

    def test_live_floors_interpolate_clamp_and_preserve_gross_supply_and_recipes(self):
        for case, endpoint in self.endpoints.items():
            selected = self.policy(case)
            for year in (2015, 2020, 2025, 2030, 2035, 2040, 2045, 2050, 2060):
                with self.subTest(case=case, year=year):
                    expected = self.reference + (endpoint - self.reference) * min(30, max(0, year - 2020)) / 30
                    db = self.database(self.carbonate(gross=1.01))
                    before = deepcopy(db)
                    plan = plan_database(db, selected, scenario={"year": year})
                    audit = self.audit(plan)
                    self.assertAlmostEqual(audit["target_secondary_share"], expected)
                    self.assertAlmostEqual(audit["applied_secondary_share"], expected)
                    result = apply_database(db, plan, policy=selected)
                    inputs = result[0]["exchanges"][1:5]
                    self.assertAlmostEqual(sum(exc["amount"] for exc in inputs), 1.01)
                    self.assertAlmostEqual(inputs[-1]["amount"], 1.01 * expected)
                    for revised, weight in zip(inputs[:3], (0.34, 0.525, 0.135)):
                        self.assertAlmostEqual(revised["amount"], 1.01 * (1 - expected) * weight)
                    self.assertEqual(result[0]["exchanges"][5:], before[0]["exchanges"][5:])
                    self.assertEqual(result[1:], before[1:])  # Full provider LCIs retained.
                    self.assertEqual(db, before)  # The input itself remains unchanged.

    def test_higher_native_recycled_share_is_not_reduced_by_a_floor(self):
        for case in self.endpoints:
            with self.subTest(case=case):
                selected = self.policy(case)
                db = self.database(self.carbonate(secondary_share=0.8))
                before = deepcopy(db)
                plan = plan_database(db, selected, scenario={"year": 2050})
                self.assertEqual(plan.changes, ())
                self.assertAlmostEqual(self.audit(plan)["applied_secondary_share"], 0.8)
                self.assertEqual(apply_database(db, plan, policy=selected), before)

    def test_reference_and_primary_only_adjust_demand_not_recycling_burdens(self):
        for case, expected in (("reference", self.reference), ("primary_only", 0.0)):
            with self.subTest(case=case):
                selected = self.policy(case)
                db = self.database(self.carbonate(secondary_share=0.1))
                before = deepcopy(db)
                plan = plan_database(db, selected, scenario={"year": 2050})
                result = apply_database(db, plan, policy=selected)
                self.assertAlmostEqual(self.audit(plan)["applied_secondary_share"], expected)
                self.assertAlmostEqual(result[0]["exchanges"][4]["amount"], expected)
                self.assertEqual(result[1:], before[1:])

    def test_ordinary_hydroxide_benefits_via_unchanged_carbonate_input_only(self):
        selected = self.policy("high_recycling")
        db = self.database()
        hydroxide = {"name": "lithium hydroxide production", "reference product": "lithium hydroxide",
                     "unit": "kilogram", "location": "GLO", "exchanges": [
                         {"name": "lithium hydroxide production", "product": "lithium hydroxide",
                          "unit": "kilogram", "location": "GLO", "type": "production", "amount": 1},
                         exchange("market for lithium carbonate", 1.6227970123291016,
                                  product=self.product, location="GLO"),
                         {"name": "Carbon dioxide, fossil", "amount": 0.05, "type": "biosphere"},
                     ]}
        db.append(hydroxide)
        before = deepcopy(db)
        plan = plan_database(db, selected, scenario={"year": 2050})
        result = apply_database(db, plan, policy=selected)
        self.assertEqual(plan.changed_indices, (0,))
        self.assertEqual(result[-1], hydroxide)
        self.assertFalse(any("hydroxide" in rule["reference product"] for rule in selected.rules))
        carbonate_required = result[-1]["exchanges"][1]["amount"]
        recycled_per_kg_hydroxide = carbonate_required * result[0]["exchanges"][4]["amount"]
        self.assertAlmostEqual(recycled_per_kg_hydroxide, carbonate_required * 560 / 1501)
        self.assertGreater(recycled_per_kg_hydroxide,
                           before[-1]["exchanges"][1]["amount"] * before[0]["exchanges"][4]["amount"])
        self.assertEqual(result[1:], before[1:])  # No extra conversion or credit added.

    def test_glo_battery_grade_and_unapproved_regions_are_not_silently_overridden(self):
        selected = self.policy("high_recycling")
        battery = RealRepositoryLithiumExtensionTests.lithium_market(location="GLO")
        world_generic = self.carbonate(location="World")
        db = self.database() + [battery, world_generic]
        before = deepcopy(db)
        plan = plan_database(db, selected, scenario={"year": 2050})
        result = apply_database(db, plan, policy=selected)
        self.assertEqual(plan.changed_indices, (0,))
        self.assertEqual(result[-2:], before[-2:])
        self.assertEqual(plan.stats["matched_markets"], 1)

    def test_unreviewed_recycling_route_or_wrong_product_cannot_become_secondary(self):
        selected = self.policy("high_recycling")
        rule = next(rule for rule in selected.rules if rule["id"] == "lithium_carbonate_generic")
        known = self.carbonate()["exchanges"][4]
        self.assertEqual(classify_supply(known, rule), "secondary")
        wrong_grade = dict(known, product="lithium carbonate, battery grade")
        self.assertIsNone(classify_supply(wrong_grade, rule))
        unknown = dict(known, name="treatment of unreviewed lithium-bearing waste")
        self.assertEqual(classify_supply(unknown, rule), "unknown")
        ds = self.carbonate()
        ds["exchanges"][4] = unknown
        with self.assertRaisesRegex(MetalPolicyError, "Unrecognised"):
            plan_database(inventory(ds), selected, scenario={"year": 2050})

    def test_audit_reports_generic_market_target_source_and_supplier_recipe(self):
        selected = self.policy("high_recycling")
        plan = plan_database(self.database(), selected, scenario={"year": 2050})
        row = self.audit(plan)
        self.assertEqual(row["share_data_source_product"], self.product)
        self.assertEqual(set(row["share_data_targets"]), {2020, 2050})
        self.assertAlmostEqual(row["share_data_targets"][2020], self.reference)
        self.assertAlmostEqual(row["share_data_targets"][2050], 560 / 1501)
        self.assertTrue(row["evidence_details"])
        self.assertTrue(row["target_note"])
        self.assertTrue(row["applied_secondary_suppliers"])
        self.assertEqual(len(row["share_data_sha256"]), 64)
        selected.verify_sources()


if __name__ == "__main__":
    unittest.main()
