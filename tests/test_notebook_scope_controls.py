"""Lightweight notebook selection controls; no licensed inventories required."""
import ast
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
import shutil
from uuid import uuid4

import numpy as np
import pandas as pd
import re
import xarray as xr
from config import ASSESSMENT_SCOPES, production_mass_factor

ROOT = Path(__file__).resolve().parents[1]


def cells(name):
    return json.loads((ROOT / name).read_text(encoding="utf-8"))["cells"]


def find_source(text, name="2_calc_impacts.ipynb"):
    return next("".join(c["source"]) for c in cells(name) if c["cell_type"] == "code" and text in "".join(c["source"]))


class NotebookScopeTests(unittest.TestCase):
    def fixture(self, scope="por_polymers"):
        preset = ASSESSMENT_SCOPES[scope]
        variables = ["EXT - 7 - " + alias for alias in preset["aliases"]]
        array = xr.DataArray(np.ones((len(variables), 2, 1)), dims=["variables", "region", "year"],
                             coords=dict(variables=variables, region=["DE", "RER"], year=[2050]))
        array.attrs["units"] = dict.fromkeys(variables, "Mt/yr")
        p = SimpleNamespace(scenarios=array, mapping={v: {"dataset": [
            {"name": "activity " + v, "reference product": "test product", "unit": "kilogram"}]} for v in variables})
        return dict(scope=preset, p=p, pd=pd, np=np, re=re, display=Mock(), ASSESSMENT_SCOPE=scope,
                    PRODUCT_ALIASES=None, VARIABLES_OVERRIDE=None, exclude_obsolete_pbr=True,
                    production_mass_factor=production_mass_factor, STEEL_VIEW="market")

    def test_prefix_is_resolved_from_aliases_not_a_fixed_module_position(self):
        ns = self.fixture()
        exec(find_source("# Resolve named pathways"), ns)
        self.assertEqual(len(ns["selected_variables"]), 11)
        self.assertTrue(all(v.startswith("EXT - 7 - ") for v in ns["selected_variables"]))
        self.assertNotIn("FE_PBR", [re.sub(r"^EXT - \d+ - ", "", v) for v in ns["selected_variables"]])

    def test_product_subset_and_ambiguous_aliases_are_explicit(self):
        ns = self.fixture()
        ns["PRODUCT_ALIASES"] = ["FE_PET", "FE_PVC"]
        source = find_source("# Resolve named pathways")
        exec(source, ns)
        self.assertEqual(ns["selected_variables"], ["EXT - 7 - FE_PET", "EXT - 7 - FE_PVC"])
        p = ns["p"]
        extra = p.scenarios.sel(variables="EXT - 7 - FE_PET", drop=True).expand_dims(variables=["EXT - 2 - FE_PET"])
        p.scenarios = xr.concat([p.scenarios, extra], dim="variables")
        p.mapping["EXT - 2 - FE_PET"] = p.mapping["EXT - 7 - FE_PET"]
        with self.assertRaisesRegex(ValueError, "Expected one mapping"):
            exec(source, ns)

    def test_steel_market_preserves_region_dimension_and_uses_existing_market(self):
        ns = self.fixture("european_steel")
        ns["p"].scenarios.loc[dict(region="RER")] = 2.
        exec(find_source("# Resolve named pathways"), ns)
        source = next("".join(c["source"]) for c in cells("2_calc_impacts.ipynb") if c.get("id") == "steel-market-demand")
        exec(source, ns)
        p = ns["p"]
        variable = ns["selected_variables"][0]
        self.assertEqual(float(p.scenarios.sel(variables=variable, region="DE").sum()), 1e10)
        self.assertEqual(float(p.scenarios.sel(variables=variable, region="RER").sum()), 2e10)
        self.assertEqual(p.mapping[variable]["dataset"][0]["name"], "market for steel, low-alloyed (ITOM)")
        self.assertEqual(p.scenarios.attrs["units"][variable], "kilogram")

    def test_steel_routes_remain_available_and_market_cannot_use_partial_volumes(self):
        ns = self.fixture("european_steel")
        ns["PRODUCT_ALIASES"] = ["steel_bof"]
        exec(find_source("# Resolve named pathways"), ns)
        source = next("".join(c["source"]) for c in cells("2_calc_impacts.ipynb") if c.get("id") == "steel-market-demand")
        with self.assertRaisesRegex(ValueError, "all ten route"):
            exec(source, ns)
        ns["STEEL_VIEW"] = "routes"
        exec(source, ns)
        self.assertEqual(ns["selected_variables"], ["EXT - 7 - steel_bof"])

    def test_scope_scenarios_are_visible_and_have_correct_module_order(self):
        source = find_source("# The external modules and their order", "1_export_packages.ipynb")
        tree = ast.parse(source)
        selection = next(n for n in tree.body if isinstance(n, ast.If) and "ASSESSMENT_SCOPE" in ast.unparse(n.test))
        for scope, count, tail in [("por_polymers", 2, ["OCE", "CL"]), ("european_steel", 1, ["ITOM_Steel_Test-scenario"])]:
            ns = {"ASSESSMENT_SCOPE": scope, "bau_iam_scenario": "background",
                  **{name: name for name in ["sps_op", "sps_itom", "sps_itom_por", "sps_forecast"]}}
            exec(compile(ast.Module(body=[selection], type_ignores=[]), "scope-cases", "exec"), ns)
            self.assertEqual(len(ns["scenarios"]), count)
            self.assertEqual([s["external scenarios"][-1]["scenario"] for s in ns["scenarios"]], tail)

    def test_snapshot_is_skipped_for_steel_and_runs_once_for_por(self):
        source = find_source("itom_inventory_snapshot = stage_itom_inventory()", "1_export_packages.ipynb")
        node = next(n for n in ast.parse(source).body if isinstance(n, ast.If))
        for scope, builds, expected in [("por_polymers", True, 1), ("european_steel", True, 0), ("por_polymers", False, 0)]:
            stage = Mock(return_value={"source_path": "test", "sha256": "test", "manifest_path": "test"})
            ns = dict(ASSESSMENT_SCOPE=scope, create_dbs=builds, create_db_package=False,
                      stage_itom_inventory=stage, print=lambda *args: None)
            exec(compile(ast.Module(body=[node], type_ignores=[]), "stage-scope", "exec"), ns)
            self.assertEqual(stage.call_count, expected)

    def test_units_and_native_calls_remain_explicit(self):
        self.assertEqual(production_mass_factor("Mt/yr"), 1e9)
        self.assertEqual(production_mass_factor("kt/year"), 1e6)
        with self.assertRaises(ValueError): production_mass_factor("megajoule")
        text = "\n".join("".join(c["source"]) for c in cells("1_export_packages.ipynb"))
        for call in ["pr.NewDatabase(", "pr.PathwaysDataPackage(", "ndb.update()", "ndb.write_db_to_brightway(", "ndb.create_datapackage("]:
            self.assertIn(call, text)
        calculation = find_source("p.calculate(")
        for argument in ["regions=regions_sel", "scenarios=scenarios_sel", "variables=selected_variables", "years=years_sel"]:
            self.assertIn(argument, calculation)
        for name in ["1_export_packages.ipynb", "2_calc_impacts.ipynb"]:
            for c in cells(name):
                if c["cell_type"] == "code": compile("".join(c["source"]), name, "exec")


class ScopeFigureTests(unittest.TestCase):
    def setUp(self):
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        self.directory = ROOT / "tmp/notebook_scope_controls" / ("test_" + uuid4().hex)
        self.directory.mkdir(parents=True)
        self.ns = dict(np=np, pd=pd, mpl=matplotlib, plt=plt, re=re, Path=Path,
                       display=Mock(), save_figure=Mock(side_effect=lambda fig, path, **kw: Path(path)))
        source = next("".join(c["source"]) for c in cells("2_calc_impacts.ipynb") if c.get("id") == "scope-impact-plot-functions")
        exec(compile(source, "scope-plot-functions", "exec"), self.ns)

    def tearDown(self):
        self.directory.resolve().relative_to((ROOT / "tmp").resolve())
        shutil.rmtree(self.directory)

    def fixture(self, methods=("climate", "copper")):
        production, results = [], []
        for region, output in [("DE", 100.), ("RER", 200.)]:
            for year in [2025, 2050]:
                key = dict(model="remind", scenario="steel", region=region, year=year, variable="ASSESS - steel market")
                production.append(dict(key, production_kg=output))
                for method in methods:
                    for category, amount in [("Energy", 10.), ("Materials", 4.), ("Capture", -2.)]:
                        results.append(dict(key, impact_category=method, act_category=category, value=amount))
        return pd.DataFrame(results), pd.DataFrame(production)

    def test_normalization_is_regional_and_credits_remain_negative(self):
        raw, production = self.fixture()
        data, output, column = self.ns["scope_plot_data"](raw, production, ["climate", "copper"], top=1)
        self.assertEqual(column, "act_category")
        net = data.groupby(["region", "impact_category", "year"]).impact_per_kg.sum()
        self.assertAlmostEqual(net.loc[("DE", "climate", 2025)], .12)
        self.assertAlmostEqual(net.loc[("RER", "climate", 2025)], .06)
        self.assertTrue(data.loc[data.contributor.eq("Other credits"), "value"].lt(0).all())
        scaled = raw.copy()
        scaled.loc[scaled.impact_category.eq("copper"), "value"] *= 1e9
        alternative, _, _ = self.ns["scope_plot_data"](scaled, production, ["climate", "copper"], top=1)
        self.assertEqual(data.contributor.tolist(), alternative.contributor.tolist())

    def test_all_methods_paginate_for_annual_and_per_kg_without_style_changes(self):
        methods = ["method " + str(i) for i in range(5)]
        raw, production = self.fixture(methods)
        before = dict(self.ns["mpl"].rcParams)
        data, paths = self.ns["plot_scope_impacts"](raw, production, methods, dict.fromkeys(methods, "kg"), self.directory, "Steel")
        self.assertEqual(len(paths), 24)  # 2 regions × 2 metrics × 2 pages × 3 formats
        self.assertEqual(self.ns["display"].call_count, 8)
        self.assertEqual(set(data.impact_category), set(methods))
        self.assertEqual(before, dict(self.ns["mpl"].rcParams))
        self.assertTrue((self.directory / "stacked_contributions.csv").is_file())

    def test_empty_sparse_results_and_zero_output_have_defined_behaviour(self):
        raw, production = self.fixture()
        zero = raw.iloc[:0].copy()
        data, paths = self.ns["plot_scope_impacts"](zero, production, ["climate"], {"climate": "kg"}, self.directory, "Zero")
        self.assertTrue(data.empty)
        self.assertEqual(len(paths), 12)
        production.loc[production.region.eq("DE"), "production_kg"] = 0.
        data, _, _ = self.ns["scope_plot_data"](raw, production, ["climate"])
        self.assertTrue(data.loc[data.region.eq("DE"), "impact_per_kg"].isna().all())

    def test_por_summary_exports_roundtrip_without_inventory_row_bloat(self):
        raw, production = self.fixture()
        # Repeated inventory contributions must collapse into product summaries.
        raw = pd.concat([raw] * 100, ignore_index=True)
        data, paths = self.ns["export_por_plot_data"](
            raw, production, ["climate", "copper"], {"climate": "kg CO2-eq", "copper": "kg"}, self.directory,
        )
        self.assertEqual(len(data), 8)
        self.assertTrue(data.value.eq(1200).all())
        self.assertTrue(data.total.eq(1200).all())
        for path, reader in zip(paths, [pd.read_csv, pd.read_parquet, pd.read_excel]):
            loaded = reader(path)
            pd.testing.assert_frame_equal(loaded, data.reset_index(drop=True), check_dtype=False)
            self.assertLess(path.stat().st_size, 40000)
        with pd.ExcelFile(paths[-1]) as workbook:
            self.assertIn("totals", workbook.sheet_names)
            self.assertEqual(len(workbook.sheet_names), 4)

    def test_por_summary_retains_product_credits_zeroes_and_region_context(self):
        raw, production = self.fixture()
        production["variable"] = "EXT - 1 - FE_PET"
        raw["variable"] = "EXT - 1 - FE_PET"
        other = production.assign(variable="EXT - 1 - FE_PVC")
        production = pd.concat([production, other], ignore_index=True)
        credits = raw.iloc[[0]].assign(variable="EXT - 1 - FE_PVC", value=-3.)
        raw = pd.concat([raw, credits], ignore_index=True)
        data, _ = self.ns["export_por_plot_data"](
            raw, production, ["climate", "copper"], {"climate": "kg CO2-eq", "copper": "kg"}, self.directory,
        )
        pvc = data.loc[data.contributor.eq("FE_PVC")]
        self.assertEqual(len(pvc), 8)
        self.assertEqual(pvc.value.min(), -3.)
        self.assertEqual(pvc.value.eq(0).sum(), 7)
        totals = data.groupby(["model", "scenario", "region", "year", "impact_category"]).value.sum()
        self.assertEqual(totals.loc[("remind", "steel", "DE", 2025, "climate")], 9.)
        self.assertEqual(totals.loc[("remind", "steel", "RER", 2025, "climate")], 12.)


if __name__ == "__main__": unittest.main()
