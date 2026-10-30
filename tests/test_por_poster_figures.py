"""Poster regeneration checks without importing premise or calculating LCA."""
from __future__ import annotations

import ast
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
from uuid import uuid4
from types import SimpleNamespace
import unittest
from unittest import mock

import numpy as np
import pandas as pd
import xarray as xr

from scripts import por_poster_figures as figures

ROOT = Path(__file__).resolve().parents[1]


def fixture(metals=figures.DEFAULT_METALS, metal_indices=None):
    rows, production = [], []
    methods = [figures.CLIMATE_METHOD] + ["RELICS - metals extraction - " + m for m in metals]
    for case in ("CL", "OCE"):
        for year in (2025, 2050):
            for product, base in (("PET", 1000.), ("PVC", 600.)):
                factor = 1. if year == 2025 else (.75 if case == "CL" else 1.3125)
                variable = "EXT - 1 - FE_" + product
                production.append(dict(model="remind", scenario="Study - " + case,
                    region="NL", year=year, variable=variable, production_quantity=1., unit="kt/yr"))
                for method in methods:
                    impact_factor = factor
                    if year == 2050 and method != methods[0] and metal_indices is not None:
                        metal = method.removeprefix("RELICS - metals extraction - ")
                        impact_factor = metal_indices.get(case, {}).get(metal, factor * 100.) / 100.
                    for location, share in (("NL", .25), ("GLO", .75)):
                        rows.append(dict(model="remind", scenario="Study - " + case,
                            region="NL", year=year, variable=variable, act_category="chemicals",
                            location=location, impact_category=method,
                            value=base * impact_factor * share * (1. if method == methods[0] else .001)))
    return pd.DataFrame(rows), pd.DataFrame(production)


class PosterDataTests(unittest.TestCase):
    def setUp(self):
        self.results, self.production = fixture()

    def test_scenario_scopes_and_explicit_mass_conversion(self):
        cl = figures.prepare_poster_data(self.results, self.production, scenario="CL")["summary"]
        oce = figures.prepare_poster_data(self.results, self.production, scenario="OCE")["summary"]
        self.assertAlmostEqual(cl["climate_base_Mt"], 1600./1e9)
        self.assertAlmostEqual(cl["climate_target_Mt"], 1200./1e9)
        self.assertAlmostEqual(cl["climate_change_percent"], -25.)
        self.assertAlmostEqual(oce["climate_change_percent"], 31.25)
        self.assertAlmostEqual(cl["output_base_Mt"], .002)
        self.assertAlmostEqual(cl["other_location_labels_signed_percent"], 75.)
        self.assertEqual(cl["years"], [2025, 2050])

    def test_signed_credits_are_retained(self):
        self.results.loc[(self.results.year == 2050) & self.results.scenario.eq("Study - CL")
            & self.results.location.eq("GLO") & self.results.impact_category.eq(figures.CLIMATE_METHOD), "value"] *= -1
        cl = figures.prepare_poster_data(self.results, self.production)["summary"]
        self.assertAlmostEqual(cl["climate_target_Mt"], -600./1e9)
        self.assertLess(cl["geography"]["Other location labels"], 0)

    def test_missing_positive_output_requires_assessed_zero_proof(self):
        results = self.results.loc[~(self.results.year.eq(2050) & self.results.variable.eq("EXT - 1 - FE_PET")
                                    & self.results.impact_category.eq(figures.CLIMATE_METHOD))]
        with self.assertRaisesRegex(ValueError, "Positive-output cells"):
            figures.prepare_poster_data(results, self.production)
        coords = {key: self.results[key].unique().tolist() for key in
                  ["model", "scenario", "region", "year", "variable", "impact_category"]}
        data = figures.prepare_poster_data(results, self.production, assessed_coords=coords)
        pet = data["product_impacts"].loc[lambda x: x.year.eq(2050) & x.variable.eq("EXT - 1 - FE_PET")
            & x.impact_category.eq(figures.CLIMATE_METHOD)]
        self.assertEqual(pet.value.iloc[0], 0.)

    def test_duplicate_contributions_rejected(self):
        duplicated = pd.concat([self.results, self.results.iloc[:1]], ignore_index=True)
        with self.assertRaisesRegex(ValueError, "Duplicate full-coordinate"):
            figures.prepare_poster_data(duplicated, self.production)

    def test_quantiles_are_not_summed(self):
        with self.assertRaisesRegex(ValueError, "contribution-level quantiles"):
            figures.prepare_poster_data(self.results.assign(quantile=.5), self.production)

    def test_missing_years_and_unsupported_units_rejected(self):
        with self.assertRaisesRegex(ValueError, "actually calculated years"):
            figures.prepare_poster_data(self.results, self.production, years=[2025, 2030, 2050])
        with self.assertRaisesRegex(ValueError, "Unsupported output mass unit"):
            figures.prepare_poster_data(self.results, self.production.assign(unit="megajoule"))

    def test_selected_unknown_sparse_product_is_not_dropped(self):
        with self.assertRaisesRegex(ValueError, "selected foreground variables"):
            figures.prepare_poster_data(self.results, self.production,
                selected_variables=["EXT - 1 - FE_NEW_PRODUCT"])


class NotebookIntegrationTests(unittest.TestCase):
    def test_appended_cells_use_fresh_current_results_for_both_scenarios(self):
        notebook = json.loads((ROOT / "2_calc_impacts.ipynb").read_text(encoding="utf-8"))
        cells = {c.get("id"): "".join(c.get("source", [])) for c in notebook["cells"]}
        definitions = [cells[key] for key in ("poster-figures-style", "poster-plot-climate-output", "poster-plot-geography-metals")]
        source = cells["poster-figures-regenerate"]
        preview = cells["poster-figures-display"]
        for code in definitions + [source, preview]: ast.parse(code)
        selected_metals = next(ast.literal_eval(node.value) for node in ast.parse(source).body
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "POSTER_METALS" for target in node.targets))
        rows, production = fixture(metals=selected_metals)
        coordinates = ["act_category", "variable", "year", "region", "location", "model", "scenario", "impact_category"]
        lca = rows.set_index(coordinates).value.to_xarray()
        scenarios = production.set_index(figures.KEYS).production_quantity.to_xarray().rename(
            {"scenario": "pathway", "variable": "variables"})
        scenarios.attrs["units"] = {v: "kt/yr" for v in production.variable.unique()}
        exports = []

        def export_results(filename):
            path = str(filename) + ".gzip"
            rows.to_parquet(path, compression="gzip", index=False)
            exports.append(path)
            return path

        p = SimpleNamespace(scenarios=scenarios, lca_results=lca, ei_version="3.12", export_results=export_results)
        before = Path.cwd()
        # Python's secure Windows tempfile ACL can exclude the sandbox token.
        # These synthetic, non-sensitive files use an ordinary workspace folder.
        parent = ROOT / "tmp" / "poster_figure_tests"
        parent.mkdir(parents=True, exist_ok=True)
        folder = parent / ("run_" + uuid4().hex)
        folder.mkdir()
        try:
            os.chdir(folder)
            namespace = {"p": p, "fp": "old_result_must_not_be_used.gzip",
                         "df": pd.DataFrame(), "IMPACT_RESULTS_DIR": folder / "results"}
            with mock.patch("config.FIGURES_DIR", folder / "figs"), \
                    mock.patch("IPython.display.display") as display:
                for index, code in enumerate(definitions):
                    exec(compile(code, f"poster-plot-definition-{index}", "exec"), namespace)
                exec(compile(source, "poster-regenerate-cell", "exec"), namespace)
                exec(compile(preview, "poster-preview-cell", "exec"), namespace)
            self.assertEqual(len(exports), 1)
            self.assertEqual(set(namespace["poster_runs"]), {"CL", "OCE"})
            self.assertEqual(display.call_count, 5)  # Summary plus two PNGs per case.
            for case, run in namespace["poster_runs"].items():
                self.assertEqual(len(run["figures"]), 6)
                self.assertTrue(all(Path(f).is_file() for f in run["figures"]))
                self.assertFalse(any("workflow" in Path(f).name for f in run["figures"]))
                for image in run["figures"]:
                    if Path(image).suffix == ".svg":
                        self.assertNotIn(" | ", Path(image).read_text(encoding="utf-8"))
                manifest = json.loads(Path(run["manifest"]).read_text(encoding="utf-8"))
                self.assertEqual(manifest["result_file"], str(Path(exports[0]).resolve()))
                self.assertEqual(manifest["lcia_version"], "3.12")
                self.assertEqual(run["summary"]["years"], [2025, 2050])
        finally:
            os.chdir(before)
            # Check the final absolute target before deleting this test's files.
            folder.resolve().relative_to(parent.resolve())
            parent.resolve().relative_to(ROOT.resolve())
            if folder.name.startswith("run_"):
                shutil.rmtree(folder)


class NotebookMetalAxisTests(unittest.TestCase):
    """Exercise the visible notebook plotter with unusually large baseline ratios."""

    @classmethod
    def setUpClass(cls):
        notebook = json.loads((ROOT / "2_calc_impacts.ipynb").read_text(encoding="utf-8"))
        cells = {cell.get("id"): "".join(cell.get("source", [])) for cell in notebook["cells"]}
        cls.namespace = {}
        for key in ("poster-figures-style", "poster-plot-geography-metals"):
            exec(compile(cells[key], key, "exec"), cls.namespace)

    def _plot_and_check(self, indices, *, scenario="CL", scale="linear", export=False):
        metals = tuple(indices)
        rows, production = fixture(metals=metals, metal_indices={scenario: indices})
        data = figures.prepare_poster_data(rows, production, scenario=scenario, metals=metals)
        data["presentation"] = {
            "label": scenario, "recycling_case": "baseline", "preliminary": True,
            "inventory_version": "3.12", "lcia_version": "3.12", "version_mismatch": False,
        }
        original = deepcopy(data)
        captured = []

        def capture(figure, directory, stem):
            captured.append(figure)
            return []

        # Ordinary workspace folders avoid secure tempfile ACL failures on Windows.
        parent = ROOT / "tmp" / "poster_figure_tests"
        parent.mkdir(parents=True, exist_ok=True)
        folder = parent / ("run_" + uuid4().hex)
        folder.mkdir()
        try:
            with mock.patch.dict(self.namespace, {"poster_export": capture}):
                self.namespace["plot_poster_geography_metals"](data, folder, scenario, "")
            self.assertEqual(len(captured), 1)
            figure = captured[0]
            axis = figure.axes[1]
            self.assertEqual(axis.get_xscale(), scale)
            self.assertLessEqual(len(axis.get_xticks()), 20)
            self.assertTrue(np.isclose(axis.get_xticks(), 100.).any())
            self.assertTrue(np.isfinite(axis.get_xlim()).all())
            self.assertLessEqual(axis.get_xlim()[0], min(0., *indices.values()))
            self.assertGreaterEqual(axis.get_xlim()[1], max(100., *indices.values()))
            self.assertEqual([tick.get_text() for tick in axis.get_yticklabels()], list(metals))
            np.testing.assert_allclose([bar.get_width() for bar in axis.patches], list(indices.values()))
            self.assertTrue(any(np.allclose(line.get_xdata(), 100.) for line in axis.lines))
            self.assertEqual(data["summary"], original["summary"])
            self.assertEqual(data["presentation"], original["presentation"])
            for key, value in original.items():
                if isinstance(value, pd.DataFrame):
                    pd.testing.assert_frame_equal(data[key], value)
                elif isinstance(value, pd.Series):
                    pd.testing.assert_series_equal(data[key], value)
            if export:
                # Every output format used by the notebook must actually render.
                paths = self.namespace["poster_export"](figure, folder, "large_metal_indices")
                self.assertEqual({Path(path).suffix for path in paths}, {".svg", ".pdf", ".png"})
                self.assertTrue(all(Path(path).stat().st_size > 0 for path in paths))
            else:
                figure.clear()
        finally:
            folder.resolve().relative_to(parent.resolve())
            parent.resolve().relative_to(ROOT.resolve())
            if folder.name.startswith("run_"):
                shutil.rmtree(folder)

    def test_large_selected_metal_indices_have_bounded_ticks_and_export(self):
        self._plot_and_check(
            {"Copper": 126.88, "Lithium": 56.6e6, "Dysprosium": 164., "Iridium": 95094.},
            scale="symlog", export=True)
        self._plot_and_check(
            {"Copper": 297.54, "Lithium": 1.25e8, "Dysprosium": 446., "Iridium": 251549.},
            scenario="OCE", scale="symlog")

    def test_ordinary_indices_keep_linear_axis_and_bounded_ticks(self):
        self._plot_and_check({"Copper": 75., "Lithium": 125., "Dysprosium": 164., "Iridium": 220.})

    def test_signed_and_zero_target_indices_are_retained(self):
        self._plot_and_check({"Copper": -20., "Lithium": 0., "Dysprosium": 100., "Iridium": 200.})


class NotebookBiogenicClimateTests(unittest.TestCase):
    """The companion view uses calculated signed impacts without adding a credit."""

    @classmethod
    def setUpClass(cls):
        notebook = json.loads((ROOT / "2_calc_impacts.ipynb").read_text(encoding="utf-8"))
        cls.cells = {c.get("id"): "".join(c.get("source", [])) for c in notebook["cells"]}

    def setUp(self):
        self.before = Path.cwd()
        self.parent = ROOT / "tmp" / "poster_figure_tests"
        self.parent.mkdir(parents=True, exist_ok=True)
        self.folder = self.parent / ("run_" + uuid4().hex)
        self.folder.mkdir()

    def tearDown(self):
        os.chdir(self.before)
        self.folder.resolve().relative_to(self.parent.resolve())
        self.parent.resolve().relative_to(ROOT.resolve())
        if self.folder.name.startswith("run_"):
            shutil.rmtree(self.folder)

    def _namespace(self, *, version="3.12", include_method=True):
        namespace = {"fp": "stale_results_do_not_read.gzip", "df": pd.DataFrame(),
                     "IMPACT_RESULTS_DIR": self.folder / "results"}
        with mock.patch("config.FIGURES_DIR", self.folder / "figs"):
            for key in ("poster-figures-style", "poster-plot-climate-output", "poster-biogenic-settings"):
                ast.parse(self.cells[key])
                exec(compile(self.cells[key], key, "exec"), namespace)
        rows, production = fixture(metals=())
        if include_method:
            rows["impact_category"] = namespace["BIOGENIC_POSTER_METHOD"]
        # A negative product subtotal in 2050 requires signed stacking and a
        # net marker below the positive stack top. No uptake is added by tests.
        rows.loc[rows.scenario.eq("Study - CL") & rows.year.eq(2050)
                 & rows.variable.eq("EXT - 1 - FE_PET"), "value"] *= -1
        coordinates = ["act_category", "variable", "year", "region", "location", "model", "scenario", "impact_category"]
        calculated = rows.set_index(coordinates).value.to_xarray()
        scenarios = production.set_index(figures.KEYS).production_quantity.to_xarray().rename(
            {"scenario": "pathway", "variable": "variables"})
        scenarios.attrs["units"] = {variable: "kt/yr" for variable in production.variable.unique()}
        exports = []

        def export_results(filename):
            path = str(filename) + ".gzip"
            rows.to_parquet(path, compression="gzip", index=False)
            exports.append(path)
            return path

        namespace["p"] = SimpleNamespace(scenarios=scenarios, lca_results=calculated,
            ei_version=version, export_results=export_results)
        return namespace, exports, rows, production

    def test_companion_uses_live_results_without_metals_and_preserves_signed_totals(self):
        with mock.patch("IPython.display.display") as display:
            namespace, exports, rows, production = self._namespace()
            original = rows.copy(deep=True)
            original_production = production.copy(deep=True)
            self.assertNotIn("poster_runs", namespace)
            exec(compile(self.cells["poster-biogenic-regenerate"], "biogenic-run", "exec"), namespace)
            exec(compile(self.cells["poster-biogenic-display"], "biogenic-preview", "exec"), namespace)
        self.assertEqual(len(exports), 1)
        self.assertEqual(set(namespace["biogenic_poster_runs"]), {"CL", "OCE"})
        self.assertEqual(display.call_count, 3)  # Summary and one PNG for each case.
        for case, run in namespace["biogenic_poster_runs"].items():
            self.assertEqual(len(run["figures"]), 3)
            self.assertTrue(all(Path(path).is_file() for path in run["figures"]))
            self.assertEqual(run["summary"]["metal_indices"], [])
            self.assertEqual(run["summary"]["climate_method"], namespace["BIOGENIC_POSTER_METHOD"])
            expected = rows.loc[rows.scenario.eq("Study - " + case)].groupby("year").value.sum()
            self.assertAlmostEqual(run["summary"]["climate_target_Mt"], expected.loc[2050] / 1e9)
            self.assertAlmostEqual(run["summary"]["output_target_Mt"], .002)
            manifest_path = Path(run["manifest"])
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["result_file"], str(Path(exports[0]).resolve()))
            self.assertEqual(manifest["lcia_version"], "3.12")
            self.assertEqual(manifest["inventory_version"], "3.12")
            table = pd.read_csv(manifest_path.parent / "product_impacts_and_intensities.csv")
            np.testing.assert_allclose(table.groupby("year").value.sum(), expected)
            paired = pd.read_csv(manifest_path.parent / "paired_production.csv")
            self.assertEqual(paired.production_kg.sum(), 4e6)
            svg = next(Path(path) for path in run["figures"] if Path(path).suffix == ".svg")
            content = svg.read_text(encoding="utf-8")
            self.assertIn("including biogenic CO", content)
            self.assertIn("including SLCFs", content)
            self.assertIn("Cradle-to-gate", content)
        self.assertLess(namespace["biogenic_poster_runs"]["CL"]["summary"]["climate_target_Mt"], 0)
        pd.testing.assert_frame_equal(rows, original)
        pd.testing.assert_frame_equal(production, original_production)

    def test_missing_method_fails_before_export_instead_of_relabelling_ef(self):
        namespace, exports, _, _ = self._namespace(include_method=False)
        with self.assertRaisesRegex(RuntimeError, "Relabelling EF results cannot add uptake"):
            exec(compile(self.cells["poster-biogenic-regenerate"], "biogenic-run", "exec"), namespace)
        self.assertEqual(exports, [])
        self.assertFalse(namespace["BIOGENIC_POSTER_OUTPUT_DIR"].exists())

    def test_version_mismatch_warns_and_net_markers_follow_signed_total_without_mutation(self):
        namespace, _, rows, production = self._namespace(version="3.11")
        data = figures.prepare_poster_data(rows, production, scenario="CL", metals=(),
            climate_method=namespace["BIOGENIC_POSTER_METHOD"])
        data["presentation"] = dict(label="Carbon Looping", recycling_case="baseline", preliminary=True,
            inventory_version="3.12", lcia_version="3.11", version_mismatch=True)
        original = deepcopy(data)
        captured = []

        def capture(figure, directory, stem):
            captured.append(figure)
            return []

        with mock.patch.dict(namespace, {"poster_export": capture}):
            namespace["plot_poster_biogenic_climate_output"](data, self.folder, "CL", "")
        figure = captured[0]
        axis = figure.axes[0]
        diamond = next(line for line in axis.lines if line.get_marker() == "D")
        np.testing.assert_allclose(diamond.get_ydata(), data["climate"].sum(axis=1).to_numpy())
        self.assertTrue(any(bar.get_height() < 0 for bar in axis.patches))
        texts = "\n".join(text.get_text() for text in figure.texts)
        self.assertIn("Inventory 3.12; LCIA 3.11 (version mismatch)", texts)
        self.assertIn("including biogenic CO", texts)
        self.assertIn("including SLCFs", texts)
        self.assertNotIn("show_net_total", data["presentation"])
        self.assertEqual(data["summary"], original["summary"])
        self.assertEqual(data["presentation"], original["presentation"])
        for key, value in original.items():
            if isinstance(value, pd.DataFrame):
                pd.testing.assert_frame_equal(data[key], value)
            elif isinstance(value, pd.Series):
                pd.testing.assert_series_equal(data[key], value)
        figure.clear()
        # A pre-export stop keeps this check focused on warning behaviour,
        # without generating another duplicate set of test figures.
        namespace["p"].export_results = mock.Mock(side_effect=RuntimeError("stop after warning"))
        with self.assertWarnsRegex(UserWarning, "Inventory 3.12 and LCIA 3.11 differ"):
            with self.assertRaisesRegex(RuntimeError, "stop after warning"):
                exec(compile(self.cells["poster-biogenic-regenerate"], "biogenic-run", "exec"), namespace)


if __name__ == "__main__":
    unittest.main()
