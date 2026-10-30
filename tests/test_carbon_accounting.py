"""Physical carbon accounting checks; no premise or Brightway calculation."""
from __future__ import annotations

from copy import deepcopy
import ast
import hashlib
import json
from pathlib import Path
import shutil
from types import SimpleNamespace
import unittest
from unittest import mock
from uuid import uuid4

import numpy as np
import pandas as pd
import yaml
import xarray as xr

import carbon_accounting as carbon


ROOT = Path(__file__).resolve().parents[1]


def external(rows):
    return pd.DataFrame([
        dict(scenario="CL", year=2025, process_id=process, origin=origin, amount_kgC=amount)
        for process, origin, amount in rows
    ], columns=["scenario", "year", "process_id", "origin", "amount_kgC"])


def transfers(rows):
    return pd.DataFrame([
        dict(scenario="CL", year=2025, supplier_id=supplier, consumer_id=consumer, amount_kgC=amount)
        for supplier, consumer, amount in rows
    ], columns=["scenario", "year", "supplier_id", "consumer_id", "amount_kgC"])


def gate_fixture(*, origin="dac", amount=10., year=2025):
    production = pd.DataFrame([dict(scenario="CL", year=year, product="PET", route="r1", production_kg=amount)])
    content = pd.DataFrame([dict(product="PET", carbon_fraction_kgC_per_kg=.6,
                                 source="Synthetic known composition", status="test")])
    origins = pd.DataFrame([dict(scenario="CL", year=year, product="PET", route="r1", origin=origin, share=1.)])
    return carbon.build_factory_gate_carbon(production, content, origins)


def applications(*, lifetime=1., lifetime_type="deterministic", share=1.):
    return pd.DataFrame([dict(product="PET", application="packaging", share=share,
        lifetime_type=lifetime_type, lifetime_years=lifetime, weibull_shape=3.)])


def fates(*, fate="incineration", share=1., recycling_yield=0., oxidation=1., methane=0.):
    return pd.DataFrame([dict(product="PET", application="packaging", fate=fate, share=share,
        recycling_yield=recycling_yield, oxidation_fraction=oxidation, methane_carbon_fraction=methane,
        reject_fate="incineration", reject_oxidation_fraction=1., recovery_application="packaging")])


class OriginPropagationTests(unittest.TestCase):
    def test_carbon_weighted_origin_mixing_and_inputs_are_preserved(self):
        sources = external([("fossil_supply", "fossil", 30.), ("biomass_supply", "biogenic", 70.)])
        network = transfers([("fossil_supply", "mix", 30.), ("biomass_supply", "mix", 70.),
                             ("mix", "polymer", 100.)])
        before = (sources.copy(deep=True), network.copy(deep=True))
        output = carbon.propagate_carbon_origins(sources, network)
        origins = output["origins"]
        shares = origins.loc[origins.process_id.eq("polymer")].set_index("origin").share
        self.assertAlmostEqual(shares["fossil"], .3)
        self.assertAlmostEqual(shares["biogenic"], .7)
        np.testing.assert_allclose(origins.groupby(["scenario", "year", "process_id"]).share.sum(), 1.)
        pd.testing.assert_frame_equal(sources, before[0])
        pd.testing.assert_frame_equal(network, before[1])

    def test_closed_loop_without_an_external_carbon_origin_remains_unknown(self):
        output = carbon.propagate_carbon_origins(external([]), transfers([("A", "B", 10.), ("B", "A", 10.)]))
        origins = output["origins"]
        unknown = origins.loc[origins.origin.eq("unknown")].set_index("process_id").share
        self.assertAlmostEqual(unknown["A"], 1.)
        self.assertAlmostEqual(unknown["B"], 1.)

    def test_negative_or_unrecognized_sources_cannot_enter_the_network(self):
        for sources in (external([("A", "fossil", -1.)]), external([("A", "renewable", 1.)]),
                        external([("A", "fossil", np.nan)])):
            with self.assertRaises(ValueError):
                carbon.propagate_carbon_origins(sources, transfers([("A", "B", 1.)]))


class FactoryGateCarbonTests(unittest.TestCase):
    def test_content_is_physical_carbon_and_origin_is_preserved(self):
        gate = gate_fixture(origin="dac", amount=10.)
        self.assertAlmostEqual(gate.total_carbon_kgC.sum(), 6.)
        self.assertAlmostEqual(gate.carbon_dac_kgC.sum(), 6.)
        self.assertAlmostEqual(gate.carbon_fossil_kgC.sum(), 0.)
        # Carbon at the gate is a material quantity, not a negative emission.
        self.assertTrue((gate.filter(regex=r"^carbon_.*_kgC$").fillna(0) >= 0).all().all())

    def test_unknown_origin_is_retained_and_missing_composition_is_not_zero(self):
        production = pd.DataFrame([dict(scenario="CL", year=2025, product="PET", route="r1", production_kg=10.)])
        content = pd.DataFrame([dict(product="PET", carbon_fraction_kgC_per_kg=.6, source="test")])
        origins = pd.DataFrame(columns=["scenario", "year", "product", "route", "origin", "share"])
        output = carbon.build_factory_gate_carbon(production, content, origins)
        self.assertAlmostEqual(output.carbon_unknown_kgC.sum(), 6.)
        unresolved = carbon.build_factory_gate_carbon(production, content.assign(carbon_fraction_kgC_per_kg=np.nan), origins)
        self.assertTrue(unresolved.total_carbon_kgC.isna().all())

    def test_origin_shares_are_not_silently_renormalized(self):
        production = pd.DataFrame([dict(scenario="CL", year=2025, product="PET", route="r1", production_kg=10.)])
        content = pd.DataFrame([dict(product="PET", carbon_fraction_kgC_per_kg=.6, source="test")])
        origins = pd.DataFrame([dict(scenario="CL", year=2025, product="PET", route="r1", origin="fossil", share=1.1)])
        with self.assertRaises(ValueError):
            carbon.build_factory_gate_carbon(production, content, origins)


class PhysicalBalanceTests(unittest.TestCase):
    def test_captured_carbon_is_one_outgoing_stream_and_imbalance_is_visible(self):
        rows = []
        for direction, stream, amount in [("in", "feedstock", 100.), ("out", "product", 80.),
                                           ("out", "air", 10.), ("out", "captured", 10.)]:
            rows.append(dict(scenario="CL", year=2025, process_id="MTO", origin="fossil",
                             direction=direction, stream_type=stream, amount_kgC=amount))
        flows = pd.DataFrame(rows)
        original = flows.copy(deep=True)
        balanced = carbon.audit_carbon_balance(flows).iloc[0]
        self.assertAlmostEqual(balanced.incoming_kgC, 100.)
        self.assertAlmostEqual(balanced.outgoing_kgC, 100.)
        self.assertAlmostEqual(balanced.residual_kgC, 0.)
        self.assertEqual(balanced.balance_status, "balanced")
        # Counting both captured transfer and its later destination as outputs
        # of the same source process must reveal the duplicated carbon.
        duplicated_storage = pd.concat([flows, flows.iloc[[-1]].assign(stream_type="geological_storage")])
        unbalanced = carbon.audit_carbon_balance(duplicated_storage).iloc[0]
        self.assertAlmostEqual(unbalanced.residual_kgC, -10.)
        self.assertEqual(unbalanced.balance_status, "unbalanced")
        pd.testing.assert_frame_equal(flows, original)

    def test_equal_total_mass_cannot_hide_a_changed_carbon_origin(self):
        flows = pd.DataFrame([
            dict(scenario="CL", year=2025, process_id="P", direction="in", stream_type="feedstock",
                 origin="fossil", amount_kgC=10.),
            dict(scenario="CL", year=2025, process_id="P", direction="out", stream_type="product",
                 origin="biogenic", amount_kgC=10.),
        ])
        result = carbon.audit_carbon_balance(flows).iloc[0]
        self.assertAlmostEqual(result.residual_kgC, 0.)
        self.assertEqual(result.origin_balance_status, "origin_mismatch")
        self.assertEqual(result.balance_status, "origin_mismatch")


class CohortAndEndOfLifeTests(unittest.TestCase):
    def simulate(self, *, gate=None, apps=None, fate_rules=None, end_year=2028, **kwargs):
        return carbon.simulate_product_cohorts(
            gate_fixture() if gate is None else gate,
            applications() if apps is None else apps,
            fates() if fate_rules is None else fate_rules,
            start_year=2025, end_year=end_year, assume_zero_initial_stock=True, **kwargs)

    def test_one_year_lifetime_emits_next_year_with_correct_stoichiometry(self):
        result = self.simulate(end_year=2026)
        retirement = result["retirements"]
        self.assertEqual(retirement.year.tolist(), [2026])
        self.assertEqual(retirement.origin.tolist(), ["dac"])
        self.assertAlmostEqual(retirement.amount_kgC.sum(), 6.)
        self.assertAlmostEqual(result["treatments"].CO2_emitted_kg.sum(), 6. * 44. / 12.)
        self.assertAlmostEqual(result["treatments"].atmospheric_uptake_added_kg.sum(), 0.)
        checks = result["checks"].set_index("year")
        self.assertAlmostEqual(checks.loc[2025, "closing_stock_kgC"], 6.)
        self.assertAlmostEqual(checks.loc[2026, "closing_stock_kgC"], 0.)
        np.testing.assert_allclose(checks.whole_system_residual_kgC, 0., atol=1e-10)

    def test_recycling_preserves_origin_and_does_not_create_another_uptake(self):
        result = self.simulate(fate_rules=fates(fate="mechanical_recycling", recycling_yield=1.),
                               recycling_mode="loop", allow_additive_recycling=True)
        checks = result["checks"].set_index("year")
        self.assertAlmostEqual(checks.loc[2028, "cumulative_new_kgC"], 6.)
        self.assertAlmostEqual(checks.loc[2028, "pending_recycling_kgC"], 6.)
        self.assertAlmostEqual(checks.loc[2027, "closing_stock_kgC"], 6.)
        self.assertAlmostEqual(checks.loc[2028, "cumulative_emitted_kgC"], 0.)
        self.assertEqual(set(result["recycling_transfers"].origin), {"dac"})
        self.assertAlmostEqual(result["recycling_transfers"].amount_kgC.sum(), 12.)
        self.assertEqual(len(result["cohort_summary"]), 1)
        self.assertAlmostEqual(result["cohort_summary"].atmospheric_uptake_added_kg.sum(), 0.)
        np.testing.assert_allclose(checks.whole_system_residual_kgC, 0., atol=1e-10)

    def test_recycling_rejects_are_treated_and_never_disappear(self):
        result = self.simulate(end_year=2027,
            fate_rules=fates(fate="mechanical_recycling", recycling_yield=.8),
            recycling_mode="loop", allow_additive_recycling=True)
        checks = result["checks"].set_index("year")
        self.assertAlmostEqual(checks.loc[2027, "closing_stock_kgC"], 4.8)
        self.assertAlmostEqual(checks.loc[2027, "cumulative_emitted_kgC"], 1.2)
        self.assertAlmostEqual(result["treatments"].CO2_emitted_kg.sum(), 1.2 * 44. / 12.)
        np.testing.assert_allclose(checks.whole_system_residual_kgC, 0., atol=1e-10)

    def test_default_secondary_pool_does_not_add_to_gross_production(self):
        result = self.simulate(fate_rules=fates(fate="mechanical_recycling", recycling_yield=.8), end_year=2027)
        checks = result["checks"].set_index("year")
        self.assertAlmostEqual(checks.loc[2027, "closing_stock_kgC"], 0.)
        self.assertAlmostEqual(checks.loc[2027, "cumulative_secondary_pool_kgC"], 4.8)
        self.assertAlmostEqual(checks.loc[2027, "cumulative_emitted_kgC"], 1.2)
        self.assertAlmostEqual(checks.loc[2027, "recycled_reentry_kgC"], 0.)
        self.assertFalse(checks.demand_cleared.any())
        np.testing.assert_allclose(checks.whole_system_residual_kgC, 0., atol=1e-10)
        with self.assertRaisesRegex(ValueError, "acknowledge"):
            self.simulate(fate_rules=fates(fate="mechanical_recycling", recycling_yield=1.), recycling_mode="loop")

    def test_methane_and_residual_carbon_have_distinct_destinations(self):
        result = self.simulate(end_year=2026, fate_rules=fates(fate="landfill", oxidation=.5, methane=.2))
        table = result["treatments"]
        self.assertAlmostEqual(table.CO2_emitted_kg.sum(), 3. * 44. / 12.)
        self.assertAlmostEqual(table.CH4_emitted_kg.sum(), 1.2 * 16. / 12.)
        self.assertAlmostEqual(table.retained_residue_kgC.sum(), 1.8)
        np.testing.assert_allclose(result["checks"].whole_system_residual_kgC, 0., atol=1e-10)

    def test_horizon_stock_is_reported_without_inventing_permanent_storage(self):
        result = self.simulate(apps=applications(lifetime=25.), end_year=2040)
        self.assertTrue(result["treatments"].empty)
        self.assertAlmostEqual(result["cohort_summary"].in_use_kgC.sum(), 6.)
        self.assertAlmostEqual(result["cohort_summary"].atmospheric_uptake_added_kg.sum(), 0.)
        self.assertAlmostEqual(result["checks"].iloc[-1].cumulative_new_kgC, 6.)

    def test_weibull_lifetime_releases_progressively_and_preserves_carbon(self):
        result = self.simulate(apps=applications(lifetime=3., lifetime_type="weibull"), end_year=2040)
        checks = result["checks"].set_index("year")
        self.assertGreater(checks.loc[2026, "closing_stock_kgC"], 0.)
        self.assertLess(checks.loc[2026, "closing_stock_kgC"], 6.)
        self.assertLess(checks.loc[2040, "closing_stock_kgC"], 1e-7)
        self.assertTrue((checks.closing_stock_kgC.diff().dropna() <= 1e-10).all())
        np.testing.assert_allclose(checks.whole_system_residual_kgC, 0., atol=1e-10)

    def test_year_specific_treatment_overrides_are_not_cached_into_other_years(self):
        gate = pd.concat([gate_fixture(year=2025), gate_fixture(year=2026)], ignore_index=True)
        default = fates().assign(year="*")
        override = fates(fate="landfill", oxidation=0.).assign(year=2027)
        result = self.simulate(gate=gate, fate_rules=pd.concat([default, override], ignore_index=True), end_year=2027)
        table = result["treatments"].set_index("year")
        self.assertAlmostEqual(table.loc[2026, "emitted_CO2_kgC"], 6.)
        self.assertAlmostEqual(table.loc[2027, "retained_residue_kgC"], 6.)
        self.assertAlmostEqual(table.loc[2027, "emitted_CO2_kgC"], 0.)
        np.testing.assert_allclose(result["checks"].whole_system_residual_kgC, 0., atol=1e-10)

    def test_year_specific_lifetimes_remain_attached_to_their_entry_cohort(self):
        gate = pd.concat([gate_fixture(year=2025), gate_fixture(year=2026)], ignore_index=True)
        default = applications(lifetime=1.).assign(year="*")
        override = applications(lifetime=20.).assign(year=2026)
        result = self.simulate(gate=gate, apps=pd.concat([default, override], ignore_index=True), end_year=2027)
        self.assertAlmostEqual(result["retirements"].amount_kgC.sum(), 6.)
        self.assertEqual(result["retirements"].entry_year.tolist(), [2025])
        self.assertAlmostEqual(result["checks"].iloc[-1].closing_stock_kgC, 6.)

    def test_initial_stock_requires_an_explicit_choice_and_retains_its_own_origin(self):
        with self.assertRaisesRegex(ValueError, "initial_stock"):
            carbon.simulate_product_cohorts(gate_fixture(), applications(), fates(), start_year=2025, end_year=2026)
        initial = pd.DataFrame([dict(scenario="CL", product="PET", application="packaging",
                                     origin="fossil", amount_kgC=3., age_years=1)])
        result = carbon.simulate_product_cohorts(gate_fixture(), applications(lifetime=2.), fates(),
            start_year=2025, end_year=2025, initial_stock=initial)
        self.assertAlmostEqual(result["checks"].iloc[0].initial_stock_kgC, 3.)
        self.assertAlmostEqual(result["checks"].iloc[0].cumulative_new_kgC, 6.)
        self.assertEqual(set(result["treatments"].origin), {"fossil"})
        self.assertAlmostEqual(result["checks"].iloc[0].whole_system_residual_kgC, 0.)

    def test_missing_content_is_an_explicit_coverage_gap(self):
        gate = gate_fixture()
        gate.loc[:, "total_carbon_kgC"] = np.nan
        for origin in carbon.ORIGINS:
            gate.loc[:, f"carbon_{origin}_kgC"] = np.nan
        with self.assertRaisesRegex(ValueError, "Unresolved carbon content"):
            self.simulate(gate=gate)
        result = self.simulate(gate=gate, strict_content=False)
        self.assertEqual(len(result["coverage"]), 1)
        self.assertTrue(result["cohort_summary"].empty)

    def test_invalid_application_and_treatment_fractions_fail(self):
        with self.assertRaises(ValueError):
            self.simulate(apps=applications(share=.9))
        with self.assertRaises(ValueError):
            self.simulate(apps=applications(lifetime=0.))
        with self.assertRaises(ValueError):
            self.simulate(fate_rules=fates(share=.8))
        with self.assertRaises(ValueError):
            self.simulate(fate_rules=fates(fate="landfill", oxidation=.9, methane=.2))

    def test_intermediate_years_are_linear_without_extrapolating_new_production(self):
        gate = pd.concat([gate_fixture(year=2025, amount=10.), gate_fixture(year=2030, amount=20.)], ignore_index=True)
        annual = carbon.annualize_factory_gate(gate)
        self.assertEqual(annual.year.tolist(), list(range(2025, 2031)))
        np.testing.assert_allclose(annual.production_kg, [10., 12., 14., 16., 18., 20.])
        self.assertEqual(annual.production_interpolated.tolist(), [False, True, True, True, True, False])
        with self.assertRaises(ValueError):
            carbon.annualize_factory_gate(gate, interpolation="none")


class CapturedCarbonAndOutputTests(unittest.TestCase):
    def test_inventory_review_reports_existing_uptake_and_fixed_labels_without_changes(self):
        inventory = [dict(name="MTO test", **{"reference product": "propylene"}, location="NL", unit="kilogram",
            exchanges=[
                dict(name="Carbon dioxide, fossil", amount=.36, type="biosphere", unit="kilogram", categories=("air",)),
                dict(name="Carbon dioxide, in air", amount=1., type="biosphere", unit="kilogram", categories=("natural resource", "in air")),
                dict(name="carbon dioxide compression, transport and storage", amount=.1,
                     type="technosphere", unit="kilogram"),
            ])]
        original = deepcopy(inventory)
        audit = carbon.inventory_carbon_emission_audit(inventory)
        self.assertEqual(set(audit.review_issue), {
            "fixed_fossil_label_check_feedstock_vs_fuel_origin",
            "uptake_already_in_inventory_do_not_add_again",
            "check_capture_vs_compression_transport_storage_boundary"})
        self.assertFalse(audit.action_applied.any())
        self.assertEqual(inventory, original)

    def test_captured_destinations_and_leakage_reconcile_without_an_lcia_credit(self):
        captured = pd.DataFrame([dict(scenario="CL", year=2025, process_id="MTO", origin="dac",
            captured_CO2_kg=100., geological_fraction=.7, reuse_fraction=.2, vent_fraction=.05,
            unknown_fraction=.05, storage_leakage_fraction=.1)])
        original = captured.copy(deep=True)
        result = carbon.audit_co2_destinations(captured).iloc[0]
        self.assertAlmostEqual(result.geological_delivered_CO2_kg, 70.)
        self.assertAlmostEqual(result.geological_retained_CO2_kg, 63.)
        self.assertAlmostEqual(result.storage_leakage_CO2_kg, 7.)
        self.assertAlmostEqual(sum(result[field] for field in ["geological_retained_CO2_kg",
            "storage_leakage_CO2_kg", "reused_CO2_kg", "vented_CO2_kg", "unresolved_CO2_kg"]), 100.)
        self.assertEqual(result.additional_LCIA_credit_kg, 0.)
        pd.testing.assert_frame_equal(captured, original)

    def test_output_runs_are_separate_and_sources_are_never_overwritten(self):
        parent = ROOT / "tmp" / "carbon_accounting_tests"
        parent.mkdir(parents=True, exist_ok=True)
        folder = parent / ("run_" + uuid4().hex)
        folder.mkdir()
        try:
            source = folder / "baseline_source.csv"
            source.write_text("unchanged baseline\n", encoding="utf-8")
            tables = {"gate": gate_fixture()}
            run1 = carbon.write_carbon_outputs(tables, folder / "outputs", source_paths=[source])
            run2 = carbon.write_carbon_outputs(tables, folder / "outputs", source_paths=[source])
            self.assertNotEqual(run1["output_dir"], run2["output_dir"])
            self.assertEqual(source.read_text(encoding="utf-8"), "unchanged baseline\n")
            for run in [run1, run2]:
                manifest = json.loads(Path(run["manifest"]).read_text(encoding="utf-8"))
                self.assertFalse(manifest["corrections_applied"])
                self.assertFalse(manifest["LCIA_recalculated"])
                self.assertEqual(len(manifest["sources"][0]["sha256"]), 64)
                self.assertTrue(Path(run["tables"]["gate"]).is_file())
        finally:
            folder.resolve().relative_to(parent.resolve())
            parent.resolve().relative_to(ROOT.resolve())
            if folder.name.startswith("run_"):
                shutil.rmtree(folder)


class ITOMWorkbookAdapterTests(unittest.TestCase):
    def setUp(self):
        self.parent = ROOT / "tmp" / "carbon_accounting_tests"
        self.parent.mkdir(parents=True, exist_ok=True)
        self.folder = self.parent / ("run_" + uuid4().hex)
        self.folder.mkdir()

    def tearDown(self):
        self.folder.resolve().relative_to(self.parent.resolve())
        self.parent.resolve().relative_to(ROOT.resolve())
        if self.folder.name.startswith("run_"):
            shutil.rmtree(self.folder)

    def workbook(self, *, methanol_amount=5.):
        path = self.folder / ("itom_" + uuid4().hex + ".xlsx")
        base = dict(LOCATION="Rotterdam", MODE_OF_OPERATION=1, YEAR=2025)
        production = pd.DataFrame([
            dict(base, TECHNOLOGY="DAC_methanol_terminal_BATplus", PRODUCT="methanol", LocalProductionByMode=methanol_amount),
            dict(base, TECHNOLOGY="polyethylene_terephthalate_(PET)_default", PRODUCT="PET", LocalProductionByMode=1.),
            dict(base, TECHNOLOGY="waste_production_PET", PRODUCT="CO2_allowance", LocalProductionByMode=200.),
            dict(base, TECHNOLOGY="waste_production_PET", PRODUCT="avoided_incineration_CO2", LocalProductionByMode=300.),
            dict(base, TECHNOLOGY="waste_production_PET", PRODUCT="electricity", LocalProductionByMode=10.),
        ])
        use = pd.DataFrame([
            dict(base, TECHNOLOGY="MTO_with_loop", PRODUCT="methanol", LocalUseByMode=5.),
            dict(base, TECHNOLOGY="waste_production_PET", PRODUCT="CO2_allowance", LocalUseByMode=200.),
            dict(base, TECHNOLOGY="waste_production_PET", PRODUCT="avoided_incineration_CO2", LocalUseByMode=300.),
            dict(base, TECHNOLOGY="waste_production_PET", PRODUCT="electricity", LocalUseByMode=10.),
        ])
        with pd.ExcelWriter(path, engine="openpyxl") as writer:
            production.to_excel(writer, sheet_name="production_volume", index=False)
            use.to_excel(writer, sheet_name="use_volume", index=False)
        return path

    def test_raw_five_year_materials_are_annualized_and_economic_dummies_excluded(self):
        path = self.workbook()
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        output = carbon.read_itom_carbon_inputs({"CL": path})
        volumes = output["volumes"]
        methanol = volumes.loc[volumes["product"].eq("methanol") & volumes.direction.eq("production")].iloc[0]
        self.assertEqual(methanol.raw_unit, "kt/period")
        self.assertEqual(methanol.period_years, 5.)
        self.assertAlmostEqual(methanol.amount_kg_per_year, 1e6)
        energy = volumes.loc[volumes["product"].eq("electricity")]
        self.assertTrue(energy.amount_kg_per_year.isna().all())
        np.testing.assert_allclose(energy.amount_MJ_per_year, 2e6)
        self.assertAlmostEqual(output["production"].loc[lambda x: x["product"].eq("PET"), "production_kg"].sum(), 200000.)
        dac = output["external"].loc[lambda x: x.origin.eq("dac")]
        self.assertAlmostEqual(dac.amount_kgC.sum(), 1e6 * 12.011 / 32.042)
        self.assertFalse(output["flows"]["product"].isin(["CO2_allowance", "avoided_incineration_CO2", "electricity"]).any())
        self.assertTrue(output["diagnostics"].status.eq("excluded_economic_bookkeeping").any())
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), before)

    def test_negative_source_volumes_fail_instead_of_becoming_a_credit(self):
        with self.assertRaises(ValueError):
            carbon.read_itom_carbon_inputs({"CL": self.workbook(methanol_amount=-1.)})


class RepositoryCarbonConfigurationTests(unittest.TestCase):
    def test_active_input_tables_are_present_nonempty_and_keep_corrections_disabled(self):
        config = yaml.safe_load((ROOT / "configuration_file" / "carbon_workflows.yaml").read_text(encoding="utf-8"))
        self.assertIs(config["apply_inventory_corrections"], False)
        self.assertEqual(config["recycling_mode"], "separate_pool")
        self.assertEqual(config["inflow_basis"], "independent_production_cohorts")
        self.assertEqual(config["workbook_period_years"], 5)
        for key in ["carbon_content", "external_origin_rules", "product_mapping", "applications",
                    "end_of_life", "captured_destinations", "treatment_lcia_factors"]:
            path = ROOT / config[key]
            self.assertTrue(path.is_file(), key)
            table = pd.read_csv(path)
            self.assertGreater(len(table), 0, key)
        applications_table = pd.read_csv(ROOT / config["applications"])
        for profile in config["application_profiles"]:
            profile_rows = applications_table.loc[applications_table.profile.eq(profile)]
            self.assertFalse(profile_rows.empty)
            np.testing.assert_allclose(profile_rows.groupby("product").share.sum(), 1.)
        mapping = pd.read_csv(ROOT / config["product_mapping"])
        self.assertFalse(mapping.loc[mapping["product"].isin(["MDI", "polyols"]), "stock_eligible"].any())
        destinations = pd.read_csv(ROOT / config["captured_destinations"])
        np.testing.assert_allclose(destinations[["geological_fraction", "reuse_fraction", "vent_fraction", "unknown_fraction"]].sum(axis=1), 1.)


class AdditionalWorkflowIntegrationTests(unittest.TestCase):
    setUp = ITOMWorkbookAdapterTests.setUp
    tearDown = ITOMWorkbookAdapterTests.tearDown
    workbook = ITOMWorkbookAdapterTests.workbook

    def test_preflight_cannot_be_used_to_enable_inventory_corrections(self):
        config = self.folder / "correction_request.yaml"
        config.write_text("schema_version: 1\napply_inventory_corrections: true\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "cannot apply inventory corrections"):
            carbon.carbon_workflow_preflight(config)

    def test_technology_aggregate_pairing_weights_modes_but_explicit_modes_stay_distinct(self):
        source = pd.DataFrame([
            dict(scenario="CL", year=2025, product="PET", route="r1|mode1", technology="r1", production_kg=10., process_id="A"),
            dict(scenario="CL", year=2025, product="PET", route="r1|mode2", technology="r1", production_kg=30., process_id="B"),
        ])
        origins = pd.DataFrame([
            dict(scenario="CL", year=2025, process_id="A", origin="fossil", share=1.),
            dict(scenario="CL", year=2025, process_id="B", origin="dac", share=1.),
        ])
        aggregated = pd.DataFrame([dict(scenario="CL", year=2025, product="PET", route="r1", production_kg=40.)])
        rules, audit = carbon.pair_gate_origins(aggregated, source, origins)
        self.assertTrue(audit.volume_matches.all())
        shares = rules.set_index("origin").share
        self.assertAlmostEqual(shares["fossil"], .25)
        self.assertAlmostEqual(shares["dac"], .75)
        explicit_mode = aggregated.assign(route="r1|mode1", production_kg=10.)
        rules, audit = carbon.pair_gate_origins(explicit_mode, source, origins)
        self.assertTrue(audit.volume_matches.all())
        self.assertEqual(rules.origin.tolist(), ["fossil"])
        self.assertAlmostEqual(rules.share.iloc[0], 1.)

    def test_pairing_rejects_origins_from_a_different_source_volume(self):
        source = pd.DataFrame([dict(scenario="CL", year=2025, product="PET", route="r1",
                                   production_kg=100., process_id="PET_process")])
        origins = pd.DataFrame([dict(scenario="CL", year=2025, process_id="PET_process", origin="dac", share=1.)])
        production = source.drop(columns="process_id").assign(production_kg=200.)
        paired, audit = carbon.pair_gate_origins(production, source, origins)
        self.assertEqual(paired.origin.tolist(), ["unknown"])
        self.assertEqual(paired.share.tolist(), [1.])
        self.assertFalse(audit.volume_matches.any())

    def test_missing_treatment_coefficients_stay_unresolved_instead_of_zero(self):
        treatments = carbon.simulate_product_cohorts(gate_fixture(), applications(), fates(),
            start_year=2025, end_year=2026, assume_zero_initial_stock=True)["treatments"]
        content = pd.DataFrame([dict(product="PET", carbon_fraction_kgC_per_kg=.6)])
        factors = pd.DataFrame([dict(fate="incineration", product="PET", impact_category="GWP100",
            value_per_kg_treated=np.nan, direct_carbon_excluded=True)])
        result = carbon.account_treatment_process_burdens(treatments, content, factors)
        self.assertTrue(result.process_burden_kgCO2e.isna().all())
        self.assertTrue(result.total_cradle_to_grave_impact.isna().all())
        self.assertEqual(set(result.coverage_status), {"missing_treatment_inventory_not_zero"})
        # Supplying a residual treatment coefficient adds its explicit process
        # burden, without calculating any new product-carbon uptake.
        factors.loc[:, "value_per_kg_treated"] = .5
        supplied = carbon.account_treatment_process_burdens(treatments, content, factors)
        self.assertAlmostEqual(supplied.process_burden_kgCO2e.sum(), 5.)
        factors.loc[:, "direct_carbon_excluded"] = False
        with self.assertRaises(ValueError):
            carbon.account_treatment_process_burdens(treatments, content, factors)

    def test_live_production_runs_all_connected_additional_accounts_without_baseline_changes(self):
        workbook = self.workbook()
        inventory = self.folder / "lci_read_only.xlsx"
        rows = pd.DataFrame([
            ["Activity", "MTO test", None, None],
            ["reference product", "propylene", None, None],
            ["unit", "kilogram", None, None],
            ["location", "NL", None, None],
            ["Exchanges", None, None, None],
            ["name", "amount", "type", "unit"],
            ["market for methanol", 2.6, "technosphere", "kilogram"],
            ["Carbon dioxide, fossil", .36, "biosphere", "kilogram"],
        ])
        rows.to_excel(inventory, index=False, header=False, engine="openpyxl")
        settings = yaml.safe_load((ROOT / "configuration_file/carbon_workflows.yaml").read_text(encoding="utf-8"))
        for key in ["carbon_content", "external_origin_rules", "product_mapping", "applications",
                    "end_of_life", "captured_destinations", "treatment_lcia_factors"]:
            settings[key] = str((ROOT / settings[key]).resolve())
        settings.update(workbooks={"CL": str(workbook)}, inventory=str(inventory),
            start_year=2025, end_year=2027, application_profiles=["short_lived_screening"],
            end_of_life_profiles=["incineration_screening", "recycling_screening"],
            output_root=str(self.folder / "carbon_outputs"))
        config = self.folder / "workflow.yaml"
        config.write_text(yaml.safe_dump(settings), encoding="utf-8")
        scenarios = xr.DataArray(np.array([[[[[.2]]]]]),
            dims=["model", "pathway", "region", "year", "variables"],
            coords=dict(model=["remind"], pathway=["Study - CL"], region=["NL"],
                        year=[2025], variables=["EXT - 1 - FE_PET"]))
        scenarios.attrs["units"] = {"EXT - 1 - FE_PET": "kt/yr"}
        p = SimpleNamespace(scenarios=scenarios, ei_version="3.12")
        original = scenarios.copy(deep=True)
        hashes = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in [workbook, inventory, config]}
        run = carbon.run_additional_carbon_workflows(p, config_path=config,
            selected_variables=["EXT - 1 - FE_PET"], output_root=self.folder / "outputs")
        gate = run["data"]["factory_gate_carbon"]
        self.assertAlmostEqual(gate.production_kg.sum(), 200000.)
        self.assertTrue(run["data"]["production_pairing_audit"].volume_matches.all())
        self.assertEqual(len(run["cohorts"]), 2)
        self.assertFalse(run["data"]["correction_review_queue"].applied.any())
        for result in run["cohorts"].values():
            np.testing.assert_allclose(result["checks"].whole_system_residual_kgC, 0., atol=1e-7)
            self.assertTrue(result["treatment_lcia_coverage"].process_burden_kgCO2e.isna().all())
        recovery = run["cohorts"]["short_lived_screening__recycling_screening"]["checks"]
        self.assertGreater(recovery.cumulative_secondary_pool_kgC.max(), 0.)
        self.assertAlmostEqual(recovery.recycled_reentry_kgC.sum(), 0.)
        manifest = json.loads(Path(run["manifest"]).read_text(encoding="utf-8"))
        self.assertFalse(manifest["corrections_applied"])
        self.assertFalse(manifest["LCIA_recalculated"])
        self.assertIn("Live Pathways production", manifest["assumptions"]["production_pairing"])
        self.assertTrue(Path(run["correction_review"]).is_file())
        for path, digest in hashes.items():
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest)
        xr.testing.assert_identical(scenarios, original)


class CarbonNotebookIntegrationTests(unittest.TestCase):
    def test_additional_cells_parse_and_preflight_is_opt_in(self):
        for filename in ["1_export_packages.ipynb", "2_calc_impacts.ipynb"]:
            notebook = json.loads((ROOT / filename).read_text(encoding="utf-8"))
            ids = [cell["id"] for cell in notebook["cells"]]
            self.assertEqual(len(ids), len(set(ids)))
            additions = [cell for cell in notebook["cells"] if cell.get("id", "").startswith("carbon-workflows-")]
            self.assertGreater(len(additions), 0, filename)
            for cell in additions:
                if cell["cell_type"] == "code":
                    ast.parse("".join(cell["source"]))
            if filename.startswith("1_"):
                preflight = next(cell for cell in additions if cell["id"] == "carbon-workflows-preflight")
                namespace = {}
                exec(compile("".join(preflight["source"]), "carbon-preflight-cell", "exec"), namespace)
                self.assertIs(namespace["RUN_ADDITIONAL_CARBON_PREFLIGHT"], False)
                self.assertNotIn("carbon_preflight", namespace)

    def test_visible_plot_cell_writes_all_formats_and_a_valid_companion_manifest(self):
        notebook = json.loads((ROOT / "2_calc_impacts.ipynb").read_text(encoding="utf-8"))
        cells = {cell.get("id"): "".join(cell.get("source", [])) for cell in notebook["cells"]}
        parent = ROOT / "tmp" / "carbon_accounting_tests"
        parent.mkdir(parents=True, exist_ok=True)
        folder = parent / ("run_" + uuid4().hex)
        folder.mkdir()
        try:
            gate = pd.concat([gate_fixture(year=2025), gate_fixture(year=2030)], ignore_index=True)
            stocks = carbon.simulate_product_cohorts(gate, applications(lifetime=20.),
                fates(fate="mechanical_recycling", recycling_yield=.85), start_year=2025,
                end_year=2055, assume_zero_initial_stock=True)
            manifest = folder / "carbon_run_manifest.json"
            manifest.write_text(json.dumps({"corrections_applied": False, "LCIA_recalculated": False}), encoding="utf-8")
            run = {"output_dir": str(folder), "manifest": str(manifest),
                "cohorts": {"long_lived_screening__recycling_screening": stocks},
                "preflight": {"settings": {"start_year": 2025, "end_year": 2055}}}
            namespace = dict(Path=Path, RUN_ADDITIONAL_CARBON_WORKFLOWS=True,
                             carbon_run=run, carbon_tables={"factory_gate_carbon": gate})
            with mock.patch("IPython.display.display") as display:
                exec(compile(cells["carbon-workflows-figures"], "carbon-figure-cell", "exec"), namespace)
            self.assertEqual(display.call_count, 2)
            paths = namespace["carbon_figure_paths"]
            self.assertEqual(len(paths), 6)
            self.assertEqual({Path(path).suffix for path in paths}, {".png", ".pdf", ".svg"})
            self.assertTrue(all(Path(path).stat().st_size > 0 for path in paths))
            revised = json.loads(manifest.read_text(encoding="utf-8"))
            self.assertEqual(revised["figures"], paths)
            self.assertFalse(revised["corrections_applied"])
            self.assertFalse(revised["LCIA_recalculated"])
            svg = next(Path(path) for path in paths if Path(path).name == "carbon_gate_CL.svg")
            self.assertIn("not a removal credit", svg.read_text(encoding="utf-8"))
            self.assertTrue(manifest.read_text(encoding="utf-8").endswith("\n"))
        finally:
            folder.resolve().relative_to(parent.resolve())
            parent.resolve().relative_to(ROOT.resolve())
            if folder.name.startswith("run_"):
                shutil.rmtree(folder)


if __name__ == "__main__":
    unittest.main()
