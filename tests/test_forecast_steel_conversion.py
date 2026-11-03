"""FORECAST steel input, market construction and notebook integration."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import unittest

import numpy as np
import pandas as pd
import yaml

import por_scenario_inputs as inputs
from config import ASSESSMENT_SCOPES

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("forecast_steel_converter", ROOT / "0_convert_FORECAST_steel_to_IAMC.py")
steel = importlib.util.module_from_spec(spec)
spec.loader.exec_module(steel)


class ForecastSteelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = pd.read_excel(steel.DEFAULT_INPUT)
        cls.iamc, cls.shares = steel.convert_frame(cls.raw)
        cls.descriptor = json.loads((ROOT / "datapackages/datapackage_forecast_steel.json").read_text())

    def scenario(self, year=2050, case="2"):
        payload = self.iamc.to_csv(index=False).encode("utf-8-sig")
        package = SimpleNamespace(descriptor=self.descriptor,
                                  get_resource=lambda name: SimpleNamespace(raw_read=lambda: payload))
        selected_case = self.descriptor.get("source_scenarios", {}).get(case, case)
        return {"year": year, "external scenarios": [{"scenario": selected_case, "data": package}]}

    def test_native_premise_reader_selects_every_case_and_production_variable(self):
        from datapackage import Package
        from premise.data_collection import IAMDataCollection
        from premise.external_data_validation import flag_activities_to_adjust
        package = Package(str(ROOT / "datapackages/datapackage_forecast_steel.json"), base_path=str(ROOT))
        collector = object.__new__(IAMDataCollection)
        aliases = {r["alias"] for r in steel.route_definitions()}
        for source_id in ("1", "2"):
            case = package.descriptor["source_scenarios"][source_id]
            self.assertIn(case, package.descriptor["scenarios"])
            native = collector.get_external_data([{"scenario": case, "data": package}])[0]
            array = native["production volume"]
            self.assertEqual(set(array.variables.values), aliases)
            self.assertEqual(array.sizes["region"], 25)
            self.assertEqual(array.sizes["year"], 6)
            self.assertTrue(np.isfinite(array.values).all())
            # This is the exact native function/selection that previously failed.
            flagged = flag_activities_to_adjust({}, native, 2050, {
                "production volume variable": "steel_eaf_scrap_ccs",
                "replaces": [], "replaces in": [], "replacement ratio": 1., "regionalize": True,
            })
            self.assertEqual(set(flagged["regions"]), set(self.iamc.region))

    def test_source_totals_and_country_weights_are_conserved(self):
        self.assertEqual(set(self.iamc.scenario), {"forecast_steel_1", "forecast_steel_2"})
        self.assertEqual(self.iamc.region.nunique(), 25)
        self.assertEqual(len(self.iamc), 398)
        for case, raw in self.raw.groupby("scenario"):
            rer = self.iamc.loc[self.iamc.scenario.eq("forecast_steel_" + str(case)) & self.iamc.region.eq("RER")]
            total = rer.loc[rer.variables.eq("Production|Steel")].iloc[0]
            for year in range(2025, 2051, 5):
                self.assertAlmostEqual(total[str(year)], raw[year].sum())
                country_sum = rer.loc[rer.variables.str.startswith(steel.SUPPLY_PREFIX), str(year)].sum()
                self.assertAlmostEqual(total[str(year)], country_sum)
        np.testing.assert_allclose(self.shares.groupby(["scenario", "year"]).country_share.sum(), 1.)

    def test_malformed_inputs_fail_and_sparse_blanks_are_explicit(self):
        with self.assertRaisesRegex(ValueError, "blank cells are not zero"):
            steel.convert_frame(self.raw, blank_policy="error")
        variants = [pd.concat([self.raw, self.raw.iloc[[0]]]), self.raw.assign(variable="unknown"),
                    self.raw.assign(region="US"), self.raw.assign(unit="tonne/year")]
        for frame in variants:
            with self.subTest(), self.assertRaises(ValueError):
                steel.convert_frame(frame)
        for value in (-1, np.inf, "bad data"):
            frame = self.raw.astype({2025: object}).copy()
            frame.loc[0, 2025] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                steel.convert_frame(frame)

    def test_generic_country_contract_interpolates_volumes(self):
        recipe = inputs.load_itom_steel_country_mix(self.scenario(year=2032))
        self.assertEqual(recipe["model"], "forecast")
        a = self.shares.loc[self.shares.scenario.eq("forecast_steel_2")].pivot(index="source_country", columns="year", values="country_production_Mt_per_year")
        amounts = a[2030] + .4 * (a[2035] - a[2030])
        for country, amount in amounts.items():
            self.assertAlmostEqual(recipe["weights"][country], amount / amounts.sum())

    def test_rer_buys_national_markets_and_keeps_national_recipes(self):
        recipe = inputs.load_itom_steel_country_mix(self.scenario())
        database = []
        for market in recipe["markets"]:
            for country in ["RER", *recipe["weights"]]:
                volume = recipe["total"] if country == "RER" else recipe["country_volumes"][country]
                database.append({**market, "location": country, "code": market["name"] + country,
                    "exchanges": [dict(name=market["name"], product=market["reference product"],
                        unit="kilogram", type="production", amount=1., location=country, **{"production volume": volume}),
                        dict(name="native steel route", product=market["reference product"],
                             unit="kilogram", type="technosphere", amount=1., location=country)]})
        linked, receipt, changed = inputs.plan_itom_steel_country_markets(database, recipe)
        self.assertEqual(len(changed), 2)
        for original, result in zip(database, linked):
            if result["location"] != "RER":
                self.assertIs(original, result)
            else:
                supplies = [e for e in result["exchanges"] if e["type"] == "technosphere"]
                self.assertEqual(len(supplies), 24)
                self.assertAlmostEqual(sum(e["amount"] for e in supplies), 1.)
                self.assertTrue(all(e["name"] == result["name"] and e["location"] in recipe["weights"] for e in supplies))

    def test_route_yaml_config_inventory_and_scope_agree(self):
        config = yaml.safe_load((ROOT / "configuration_file/config_forecast_steel.yaml").read_text())
        definitions = steel.route_definitions()
        self.assertEqual(set(config["production pathways"]), set(ASSESSMENT_SCOPES["forecast_steel"]["aliases"]))
        self.assertEqual(set(config["production pathways"]), {r["alias"] for r in definitions})
        activities = {r["activity"] for r in definitions}
        self.assertEqual(len(activities), 6)
        for r in definitions:
            pathway = config["production pathways"][r["alias"]]
            self.assertEqual(pathway["production volume"]["variable"], r["variable"])
            self.assertEqual(pathway["ecoinvent alias"]["name"], r["activity"])
            self.assertEqual(pathway["ecoinvent alias"]["exists in original database"],
                             r["exists_in_original_database"])
        from bw2io.extractors.excel import ExcelExtractor
        extracted = ExcelExtractor.extract(str(ROOT / "inventories/lci-forecast_steel.xlsx"))
        rows = extracted[0][1]
        required_wrappers = {r["activity"] for r in definitions if not r["exists_in_original_database"]}
        self.assertEqual(required_wrappers, {"steel production, low-alloyed (FORECAST scrap EAF CCS unresolved proxy)"})
        self.assertEqual(required_wrappers, {r[1] for r in rows if r and r[0] == "Activity"})
        exchanges = [r for r in rows if len(r) > 5 and r[5] in ("production", "technosphere")]
        self.assertEqual(len(exchanges), 2 * len(required_wrappers))
        self.assertTrue(all(isinstance(r[1], (float, int)) and r[1] == 1 for r in exchanges))

    def test_native_ccs_routes_keep_capture_in_the_selected_inventory(self):
        routes = {r["alias"]: r for r in steel.route_definitions()}
        for alias in ("steel_bof_ccs", "steel_eaf_dri_ng_ccs"):
            route = routes[alias]
            self.assertTrue(route["exists_in_original_database"])
            self.assertIn("with carbon capture and storage", route["activity"])
            self.assertEqual(route["ccs_inventory"], "native")
        self.assertEqual(routes["steel_eaf_scrap_ccs"]["ccs_inventory"], "unresolved_proxy")

    def test_notebooks_offer_the_separate_source_with_visible_native_calls(self):
        one = json.loads((ROOT / "1_export_packages.ipynb").read_text(encoding="utf-8"))
        two = json.loads((ROOT / "2_calc_impacts.ipynb").read_text(encoding="utf-8"))
        for notebook in [one, two]:
            for cell in notebook["cells"]:
                if cell["cell_type"] == "code":
                    compile("".join(cell["source"]), "notebook", "exec")
        source = "\n".join("".join(c["source"]) for c in one["cells"])
        self.assertIn('"data": sps_forecast_steel', source)
        self.assertIn('forecast_steel_converter["transform_forecast_steel"]()', source)
        demand = next("".join(c["source"]) for c in two["cells"] if c.get("id") == "steel-market-demand")
        self.assertIn('scope.get("market"', demand)
        self.assertIn('"forecast_steel"', demand)


if __name__ == "__main__":
    unittest.main()
