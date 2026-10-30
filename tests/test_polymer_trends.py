"""Regression checks for the appended polymer-specific trend section."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.figure import Figure
import numpy as np
import pandas as pd
import xarray as xr

ROOT = Path(__file__).resolve().parents[1]
NOTEBOOK = ROOT / "2_calc_impacts.ipynb"


def impact(variable, value, year=2025, scenario="CL", method="climate", model="remind"):
    return {"model": model, "scenario": scenario, "region": "NL", "year": year,
            "variable": "EXT - 1 - " + variable, "impact_category": method, "value": value}


def production(variable, value, year=2025, scenario="CL", model="remind"):
    return {"model": model, "scenario": scenario, "region": "NL", "year": year,
            "variable": "EXT - 1 - " + variable, "production_quantity": value}


class PolymerTrendsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        notebook = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
        cls.namespace = {}
        helper = next(cell for cell in notebook["cells"] if cell.get("id") == "polymer-trends-helpers")
        exec(compile("".join(helper["source"]), str(NOTEBOOK), "exec"), cls.namespace)
        cls.prepare = staticmethod(cls.namespace["prepare_polymer_trends"])
        cls.plot = staticmethod(cls.namespace["plot_polymer_trends"])

    def test_polymer_specific_denominator_and_signed_net_impacts(self):
        results = pd.DataFrame([impact("FE_PET", 8e6), impact("FE_PET", -2e6), impact("FE_MDI", 90e6)])
        volumes = pd.DataFrame([production("FE_PET", 2), production("FE_MDI", 100)])
        units = {variable: "kt/yr" for variable in volumes["variable"]}
        before = deepcopy(results)
        table = self.prepare(results, volumes, units)
        pet = table.loc[table["polymer"].eq("PET")].iloc[0]
        self.assertEqual(pet["annual_impact"], 6e6)
        self.assertEqual(pet["production_kg"], 2e6)
        self.assertEqual(pet["impact_per_kg"], 3)
        pd.testing.assert_frame_equal(results, before)

    def test_polyol_routes_aggregate_with_production_weighted_intensity(self):
        results = pd.DataFrame([impact("FE_polyols", 2e6), impact("FE_polyols_retrofit", 9e6), impact("FE_polyols_lignin", -3e6)])
        volumes = pd.DataFrame([production("FE_polyols", 10), production("FE_polyols_retrofit", 30), production("FE_polyols_lignin", 10)])
        table = self.prepare(results, volumes, {v: "kt/yr" for v in volumes["variable"]})
        self.assertEqual(len(table), 1)
        self.assertEqual(table.iloc[0]["polymer"], "Polyols")
        self.assertEqual(table.iloc[0]["annual_impact"], 8e6)
        self.assertEqual(table.iloc[0]["production_kg"], 50e6)
        self.assertEqual(table.iloc[0]["impact_per_kg"], 0.16)

    def test_zero_output_has_zero_annual_impact_and_undefined_intensity(self):
        results = pd.DataFrame([impact("FE_PET", 10), impact("FE_PET", 20, 2030)])
        volumes = pd.DataFrame([production("FE_PET", 1), production("FE_PET", 2, 2030),
                                production("FE_PBS", 0), production("FE_PBS", 1, 2030)])
        coords = {"model": ["remind"], "scenario": ["CL"], "region": ["NL"], "year": [2025, 2030],
                  "variable": volumes["variable"].unique().tolist(), "impact_category": ["climate"]}
        table = self.prepare(results, volumes, {v: "kt/yr" for v in volumes["variable"]}, assessment_coords=coords)
        pbs = table.loc[table["polymer"].eq("PBS")].set_index("year")
        self.assertEqual(pbs.loc[2025, "annual_impact"], 0)
        self.assertTrue(np.isnan(pbs.loc[2025, "impact_per_kg"]))
        self.assertEqual(pbs.loc[2030, "annual_impact"], 0)  # Known sparse zero.
        self.assertEqual(pbs.loc[2030, "impact_per_kg"], 0)

    def test_missing_results_are_not_silently_zero_or_partial_polyol_totals(self):
        results = pd.DataFrame([impact("FE_polyols", 10), impact("FE_PET", 1)])
        volumes = pd.DataFrame([production("FE_polyols", 1), production("FE_polyols_lignin", 1), production("FE_PET", 1)])
        table = self.prepare(results, volumes, {v: "kt/yr" for v in volumes["variable"]})
        polyols = table.loc[table["polymer"].eq("Polyols")].iloc[0]
        self.assertTrue(np.isnan(polyols["annual_impact"]))
        self.assertEqual(polyols["missing_result_count"], 1)

    def test_models_and_scenarios_never_merge(self):
        results = pd.DataFrame([impact("FE_PET", 1, scenario="CL"), impact("FE_PET", 2, scenario="OCE"),
                                impact("FE_PET", 3, scenario="CL", model="image")])
        volumes = pd.DataFrame([production("FE_PET", 1, scenario="CL"), production("FE_PET", 1, scenario="OCE"),
                                production("FE_PET", 1, scenario="CL", model="image")])
        table = self.prepare(results, volumes, {"EXT - 1 - FE_PET": "kg/yr"})
        self.assertEqual(len(table), 3)
        self.assertEqual(sorted(table["annual_impact"]), [1, 2, 3])

    def test_pbr_selection_and_nonpolymer_cc_flows_are_excluded(self):
        results = pd.DataFrame([impact("FE_PET", 10), impact("FE_PBR", 20), impact("FE_CC_MTO_with_loop", -3)])
        volumes = pd.DataFrame([production("FE_PET", 1), production("FE_PBR", 1), production("FE_CC_MTO_with_loop", 1)])
        table = self.prepare(results, volumes, {v: "kt/yr" for v in volumes["variable"]},
                             selected_variables=["EXT - 1 - FE_PET"])
        self.assertEqual(table["polymer"].unique().tolist(), ["PET"])

    def test_units_duplicates_and_stochastic_replicates_are_checked(self):
        results = pd.DataFrame([impact("FE_PET", 10)])
        volumes = pd.DataFrame([production("FE_PET", 1)])
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            self.prepare(results, volumes, {"EXT - 1 - FE_PET": "TJ/yr"})
        with self.assertRaisesRegex(KeyError, "unit"):
            self.prepare(results, volumes, {})
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            self.prepare(results, pd.concat([volumes, volumes]), {"EXT - 1 - FE_PET": "kt/yr"})
        stochastic = pd.concat([results.assign(iteration=0), results.assign(iteration=1)])
        with self.assertRaisesRegex(ValueError, "stochastic"):
            self.prepare(stochastic, volumes, {"EXT - 1 - FE_PET": "kt/yr"})

    def test_plotting_one_and_multiple_scenarios_exports_both_formats(self):
        for scenarios in (["CL"], ["CL", "OCE", "third"]):
            with self.subTest(scenarios=scenarios):
                results = pd.DataFrame([impact("FE_PET", -10 + i, year, scenario)
                                        for i, scenario in enumerate(scenarios) for year in (2025, 2030)])
                volumes = pd.DataFrame([production("FE_PET", 1, year, scenario)
                                        for scenario in scenarios for year in (2025, 2030)])
                table = self.prepare(results, volumes, {"EXT - 1 - FE_PET": "kt/yr"})
                figures = []
                native_subplots = plt.subplots
                def capture(*args, **kwargs):
                    figure, axes = native_subplots(*args, **kwargs)
                    figures.append(figure)
                    return figure, axes
                # A locked target can make the shared exporter return a sibling
                # filename. The plot must return that actual path to its caller.
                save = Mock(side_effect=lambda figure, target, **kwargs:
                    Path(target).with_name(Path(target).stem + "_alternate" + Path(target).suffix))
                with patch.object(Path, "mkdir"), patch.dict(self.namespace, {"save_figure": save}), patch.object(plt, "subplots", capture):
                    paths = self.plot(table, {"climate": "kg CO2-eq"}, output_dir=Path("not_written_trends"), show=False)
                self.assertEqual([path.suffix for path in paths], [".png", ".pdf"])
                self.assertTrue(all(path.stem.endswith("_alternate") for path in paths))
                self.assertEqual(save.call_count, 2)
                self.assertEqual(len(figures[0].legends[0].get_texts()), len(scenarios))
                # The additional horizontal zero-reference line is not a scenario.
                self.assertEqual(sum(line.get_marker() != "None" for line in figures[0].axes[0].lines), len(scenarios))
                for line in figures[0].axes[0].lines[:len(scenarios)]:
                    np.testing.assert_array_equal(line.get_xdata(), [2025, 2030])
                    self.assertTrue((line.get_ydata() < 0).all())

    def test_appended_execution_cell_uses_scenario_units_and_current_results(self):
        results = pd.DataFrame([impact("FE_PET", 10, year, scenario)
                                for scenario in ("CL", "OCE") for year in (2025, 2030)])
        volumes = pd.DataFrame([production("FE_PET", 2, year, scenario)
                                for scenario in ("CL", "OCE") for year in (2025, 2030)])
        scenario_array = volumes.set_index(["model", "scenario", "region", "year", "variable"])["production_quantity"].to_xarray()
        scenario_array = scenario_array.rename({"scenario": "pathway", "variable": "variables"})
        scenario_array.attrs["units"] = {"EXT - 1 - FE_PET": "kt/yr"}
        coords = {name: results[name].unique() for name in ("model", "scenario", "region", "year", "variable", "impact_category")}
        lca_array = xr.DataArray(np.zeros(tuple(len(v) for v in coords.values())), coords=coords, dims=list(coords))
        plot = Mock(return_value=[Path("preview.png"), Path("preview.pdf")])
        namespace = {**self.namespace, "df": results, "p": SimpleNamespace(scenarios=scenario_array, lca_results=lca_array),
                     "selected_variables": ["EXT - 1 - FE_PET"], "dict_units": {"climate": "kg CO2-eq"},
                     "methods_sel": ["unused first method", "climate"],
                     "plot_polymer_trends": plot, "display": lambda value: None}
        notebook = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
        run_cell = next(cell for cell in notebook["cells"] if cell.get("id") == "polymer-trends-run")
        with patch.object(Path, "mkdir"), patch.object(pd.DataFrame, "to_csv"):
            exec(compile("".join(run_cell["source"]), str(NOTEBOOK), "exec"), namespace)
        table = namespace["polymer_trend_data"]
        self.assertEqual(len(table), 4)
        self.assertTrue(table["production_kg"].eq(2e6).all())
        self.assertTrue(table["impact_per_kg"].eq(5e-6).all())
        self.assertEqual(plot.call_args.kwargs["methods"], ["climate"])


if __name__ == "__main__":
    unittest.main()
