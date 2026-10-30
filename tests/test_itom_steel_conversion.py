"""ITOM steel route conversion without loading premise or a Brightway project."""
from __future__ import annotations

import hashlib
import ast
import importlib.util
import json
import math
import pickle
from pathlib import Path
import shutil
import sys
import unittest
from unittest import mock
from uuid import uuid4
from copy import deepcopy
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pycountry
import yaml
import por_scenario_inputs as scenario_inputs


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "0_convert_ITOM_steel_to_IAMC.py"
RAW_WORKBOOK = ROOT / "inventories" / "Copy of ITOM_Steel_Test-scenario_IAMC_v2.xlsx"
SPEC = importlib.util.spec_from_file_location("itom_steel_converter_under_test", MODULE_PATH)
steel = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = steel
SPEC.loader.exec_module(steel)

STEEL_PREFIX = "Production|Industry|Iron and Steel|Steel|"
IRON_PREFIX = "Production|Industry|Iron and Steel|Iron|"
MATERIAL_PREFIX = "Material Consumption|Industry|Iron and Steel|"
LEAVES = (
    "BF/BOF", "Secondary steel", "DRI_NG", "DRI_H2", "DRI_NG_H2",
    "DRI_unresolved", "HBI_NG", "HBI_H2", "HBI_NG_H2", "HBI_unresolved",
)
COUNTRY_SUPPLY_PREFIX = "Production|Steel|Supply from|"
STEEL_MARKETS = [
    {"name": "market for steel, low-alloyed (ITOM)", "reference product": "steel, low-alloyed", "unit": "kilogram"},
    {"name": "market for steel, unalloyed (ITOM)", "reference product": "steel, unalloyed", "unit": "kilogram"},
]


def country_market_iamc():
    """Different total-output growth makes volume/share interpolation diverge."""
    rows = []
    for country, first, last in [("DE", 20., 800.), ("AT", 80., 200.), ("FI", 0., 0.)]:
        for region, variable in [(country, "Production|Steel"), ("RER", COUNTRY_SUPPLY_PREFIX + country)]:
            rows.append(dict(model="itom-steel", scenario="test", region=region,
                             variables=variable, unit="Mt/yr", **{"2025": first, "2050": last}))
    rows.append(dict(model="itom-steel", scenario="test", region="RER", variables="Production|Steel",
                     unit="Mt/yr", **{"2025": 100., "2050": 1000.}))
    # Technology diagnostics are alternative views, not extra national supply.
    rows.append(dict(model="itom-steel", scenario="test", region="RER", variables="Production|Steel|DRI_H2",
                     unit="Mt/yr", **{"2025": 100., "2050": 1000.}))
    return pd.DataFrame(rows)


def country_mix_scenario(frame=None, *, year=2025, declaration=True):
    frame = country_market_iamc() if frame is None else frame
    payload = frame.to_csv(index=False).encode("utf-8-sig")

    class Resource:
        descriptor = {"path": "memory-country-steel.csv"}
        source = None

        def raw_read(self):
            return payload

    class Package:
        descriptor = ({"itom_steel_country_market_mix": {
            "version": 1, "region": "RER", "variable_prefix": COUNTRY_SUPPLY_PREFIX,
            "total_variable": "Production|Steel", "markets": deepcopy(STEEL_MARKETS),
        }} if declaration else {})

        def get_resource(self, name):
            if name == "scenario_data":
                return Resource()
            return None

    return {"model": "remind", "pathway": "background_scenario", "year": year,
            "external scenarios": [{"scenario": "test", "data": Package()}]}


def country_market_database(volumes=None, *, total=None):
    volumes = {"DE": 20., "AT": 80.} if volumes is None else volumes
    total = sum(volumes.values()) if total is None else total
    database = []
    for market in STEEL_MARKETS:
        name, product = market["name"], market["reference product"]
        production = {"name": name, "product": product, "amount": 1., "type": "production",
                      "unit": "kilogram", "location": "RER", "production volume": total}
        database.append({**market, "location": "RER", "code": name + "_RER", "marker": "keep RER metadata",
                         "exchanges": [production,
                            {"name": "steel production, hydrogen-based direct reduction iron-electric arc furnace, low-alloyed",
                             "product": product, "unit": "kilogram", "location": "RER", "amount": 1., "type": "technosphere"},
                            {"name": "market for transport, freight, lorry", "product": "transport, freight, lorry",
                             "unit": "ton kilometer", "location": "RER", "amount": .01, "type": "technosphere"},
                            {"name": "Carbon dioxide, fossil", "categories": ("air",), "unit": "kilogram",
                             "amount": .001, "type": "biosphere"}]})
        for country, volume in volumes.items():
            database.append({**market, "location": country, "code": name + "_" + country,
                             "marker": "preserve national recipe", "exchanges": [
                                {"name": name, "product": product, "amount": 1., "type": "production",
                                 "unit": "kilogram", "location": country, "production volume": volume},
                                {"name": "Carbon dioxide, fossil", "categories": ("air",), "unit": "kilogram",
                                 "amount": 2. if country == "DE" else 10., "type": "biosphere"}]})
    database.append({"name": "unrelated process", "reference product": "unrelated product", "unit": "kilogram",
                     "location": "RER", "exchanges": [{"name": "keep me", "type": "biosphere", "amount": 1.}]})
    return database


def raw_fixture():
    # Iron totals are intentionally different from final steel output: using
    # iron production as steel volumes would violate the independent total.
    values = {
        STEEL_PREFIX + "BOF": 50., STEEL_PREFIX + "EAF_DRI": 40.,
        STEEL_PREFIX + "EAF_HBI": 10., STEEL_PREFIX + "EAF_Scrap": 20.,
        IRON_PREFIX + "DRI_NG": 10., IRON_PREFIX + "DRI_H2": 30.,
        IRON_PREFIX + "DRI_NG_H2": 10.,
        "Final Energy|Industry|Iron and Steel|Hydrogen": 987.,
    }
    return pd.DataFrame([
        dict(model="itom-steel", scenario="test", region="AT", variables=name,
             unit="PJ/yr" if name.startswith("Final Energy") else "Mt/yr",
             **{"2025": amount, "2050": amount})
        for name, amount in values.items()
    ])


def value(frame, route, year="2050", region="AT"):
    name = "Production|Steel" + ("|" + route if route else "")
    selected = frame.loc[frame.variables.eq(name) & frame.region.eq(region)]
    if len(selected) != 1:
        raise AssertionError(f"Expected one {region}/{name}, found {len(selected)}")
    return float(selected.iloc[0][str(year)])


def override(**changes):
    row = dict(scenario="test", region="AT", year=2050, route="EAF_HBI",
               ng_share=.25, h2_share=.75, mixed_share=0., unresolved_share=0.)
    row.update(changes)
    return row


def hbi_material_rows(**amounts):
    return pd.DataFrame([
        dict(model="itom-steel", scenario="test", region="AT",
             variables=MATERIAL_PREFIX + name, unit="Mt/yr",
             **{"2025": float(amount), "2050": float(amount)})
        for name, amount in amounts.items()
    ])


def european_hbi_fixture():
    """EU27 pool is 10 NG / 80 H2 / 10 mixed, unlike Austria's own mix.

    The large GB NG quantity deliberately tests that geographic and context
    boundaries are applied before the upstream pool weights are calculated.
    """
    austria = raw_fixture()
    finland = raw_fixture().assign(region="FI")
    finland.loc[finland.variables.str.startswith(STEEL_PREFIX), ["2025", "2050"]] = 0.
    for route, amount in [("DRI_NG", 0.), ("DRI_H2", 50.), ("DRI_NG_H2", 0.)]:
        finland.loc[finland.variables.eq(IRON_PREFIX + route), ["2025", "2050"]] = amount
    britain = raw_fixture().assign(region="GB")
    britain.loc[britain.variables.str.startswith(STEEL_PREFIX), ["2025", "2050"]] = 0.
    for route, amount in [("DRI_NG", 9000.), ("DRI_H2", 0.), ("DRI_NG_H2", 0.)]:
        britain.loc[britain.variables.eq(IRON_PREFIX + route), ["2025", "2050"]] = amount
    return pd.concat([austria, finland, britain], ignore_index=True)


def european_market_fixture():
    """A small country and large country have very different route shares."""
    rows = []
    for region, bof_2025, scrap_2025, bof_2050, scrap_2050 in [
        ("AT", 80., 20., 10., 90.), ("DE", 90., 810., 180., 720.),
    ]:
        country = raw_fixture().assign(region=region)
        country.loc[country.variables.str.startswith(STEEL_PREFIX) | country.variables.str.startswith(IRON_PREFIX), ["2025", "2050"]] = 0.
        country.loc[country.variables.eq(STEEL_PREFIX + "BOF"), ["2025", "2050"]] = [bof_2025, bof_2050]
        country.loc[country.variables.eq(STEEL_PREFIX + "EAF_Scrap"), ["2025", "2050"]] = [scrap_2025, scrap_2050]
        rows.append(country)
    return steel.derive_steel_routes(pd.concat(rows, ignore_index=True))[0]


def european_value(frame, route, *, year="2050", model="itom-steel", scenario="test"):
    variable = "Production|Steel" + ("|" + route if route else "")
    selected = frame.loc[frame.model.eq(model) & frame.scenario.eq(scenario)
                         & frame.region.eq("RER") & frame.variables.eq(variable)]
    if len(selected) != 1:
        raise AssertionError(f"Expected one European {model}/{scenario}/{variable}, found {len(selected)}")
    return float(selected.iloc[0][str(year)])


def workbook_activity_blocks(path):
    """Read the native Brightway workbook without rewriting or recalculating it."""
    import openpyxl
    book = openpyxl.load_workbook(path, read_only=True, data_only=True)
    activities, current, headers = [], None, None
    try:
        for sheet in book:
            for row in sheet.iter_rows(values_only=True):
                if not row or row[0] is None or not str(row[0]).strip():
                    continue
                key = str(row[0]).strip().lower()
                if key == "activity":
                    current = {"activity": row[1], "exchanges": [], "sheet": sheet.title}
                    activities.append(current)
                    headers = None
                elif key == "exchanges":
                    continue
                elif current is not None and key == "name" and len(row) > 1 and str(row[1]).lower() == "amount":
                    headers = [str(cell).strip().lower() if cell is not None else "" for cell in row]
                elif current is not None and headers is None and len(row) > 1:
                    current[key] = row[1]
                elif current is not None and headers is not None:
                    current["exchanges"].append({key: cell for key, cell in zip(headers, row) if key})
    finally:
        book.close()
    return activities


class SteelNormalizationTests(unittest.TestCase):
    def test_import_does_not_read_a_workbook_or_publish_outputs(self):
        spec = importlib.util.spec_from_file_location("itom_steel_import_isolation", MODULE_PATH)
        module = importlib.util.module_from_spec(spec)
        with mock.patch("pandas.ExcelFile", side_effect=AssertionError("Import must not read data")), \
                mock.patch("pandas.DataFrame.to_csv", side_effect=AssertionError("Import must not export data")), \
                mock.patch.object(Path, "write_text", side_effect=AssertionError("Import must not write")), \
                mock.patch.object(Path, "write_bytes", side_effect=AssertionError("Import must not write")):
            spec.loader.exec_module(module)
        self.assertTrue(callable(module.transform_itom_steel))

    def test_country_names_and_explicit_aliases_use_iso2(self):
        for country, code in [("Austria", "AT"), ("Netherlands", "NL"),
                              ("Slovokia", "SK"), ("United Kingdom", "GB"),
                              ("Greece", "GR"), ("Czechia", "CZ"), ("AT", "AT")]:
            self.assertEqual(steel.normalize_region(country), code)
        with self.assertRaises(ValueError):
            steel.normalize_region("Atlantis")

    def test_variable_cleanup_preserves_the_edm_i_hierarchy_case(self):
        self.assertEqual(steel.normalize_variable("  Production | Industry | Iron and Steel | Steel | EAF_DRI  "),
                         STEEL_PREFIX + "EAF_DRI")

    def test_vscode_kernel_connection_argument_is_not_a_user_option(self):
        result = dict(output="converted.csv", audit="audit.csv", manifest="manifest.json",
                      raw_rows=1, derived_rows=1, output_rows=2, country_count=1)
        with mock.patch.object(steel, "transform_itom_steel", return_value=result) as convert, \
                mock.patch.object(sys, "argv", ["ipykernel_launcher.py", "--f=connection.json"]):
            self.assertEqual(steel.main(), result)
        self.assertEqual(convert.call_count, 1)


class SteelRouteTests(unittest.TestCase):
    def setUp(self):
        self.raw = raw_fixture()

    def test_domestic_iron_mix_splits_eaf_steel_without_changing_steel_total(self):
        before = self.raw.copy(deep=True)
        derived, audit = steel.derive_steel_routes(self.raw)
        # 40 Mt EAF-DRI steel x iron shares 20%,60%,20%.
        self.assertAlmostEqual(value(derived, "DRI_NG"), 8.)
        self.assertAlmostEqual(value(derived, "DRI_H2"), 24.)
        self.assertAlmostEqual(value(derived, "DRI_NG_H2"), 8.)
        self.assertAlmostEqual(value(derived, "HBI_unresolved"), 10.)
        self.assertAlmostEqual(value(derived, "BF/BOF"), 50.)
        self.assertAlmostEqual(value(derived, "Secondary steel"), 20.)
        self.assertAlmostEqual(value(derived, ""), 120.)
        self.assertAlmostEqual(sum(value(derived, route) for route in LEAVES), 120.)
        self.assertFalse(audit.empty)
        pd.testing.assert_frame_equal(self.raw, before)

    def test_country_aggregate_hydrogen_energy_does_not_determine_steel_share(self):
        alternative = self.raw.copy(deep=True)
        alternative.loc[alternative.variables.str.startswith("Final Energy"), ["2025", "2050"]] = 0.
        first, _ = steel.derive_steel_routes(self.raw)
        second, _ = steel.derive_steel_routes(alternative)
        pd.testing.assert_frame_equal(first, second)

    def test_positive_eaf_dri_without_iron_route_data_stays_unresolved(self):
        raw = self.raw.loc[~self.raw.variables.str.startswith(IRON_PREFIX)]
        derived, _ = steel.derive_steel_routes(raw)
        self.assertAlmostEqual(value(derived, "DRI_unresolved"), 40.)
        self.assertEqual(value(derived, "DRI_H2"), 0.)
        self.assertAlmostEqual(sum(value(derived, route) for route in LEAVES), 120.)

    def test_hbi_origin_is_not_assumed_to_match_domestic_dri(self):
        derived, _ = steel.derive_steel_routes(self.raw)
        self.assertEqual(value(derived, "HBI_NG"), 0.)
        self.assertEqual(value(derived, "HBI_H2"), 0.)
        self.assertEqual(value(derived, "HBI_NG_H2"), 0.)
        self.assertEqual(value(derived, "HBI_unresolved"), 10.)

    def test_explicit_hbi_override_changes_only_the_selected_year(self):
        derived, _ = steel.derive_steel_routes(self.raw, pd.DataFrame([override()]))
        self.assertAlmostEqual(value(derived, "HBI_NG"), 2.5)
        self.assertAlmostEqual(value(derived, "HBI_H2"), 7.5)
        self.assertEqual(value(derived, "HBI_unresolved"), 0.)
        self.assertEqual(value(derived, "HBI_unresolved", year="2025"), 10.)
        self.assertEqual(value(derived, "DRI_H2"), 24.)
        self.assertAlmostEqual(sum(value(derived, route) for route in LEAVES), 120.)

    def test_specific_override_takes_precedence_over_a_wildcard(self):
        rules = pd.DataFrame([
            override(scenario="*", region="*", year="*", ng_share=1., h2_share=0.),
            override(ng_share=0., h2_share=1.),
        ])
        derived, _ = steel.derive_steel_routes(self.raw, rules)
        self.assertEqual(value(derived, "HBI_H2"), 10.)
        self.assertEqual(value(derived, "HBI_NG", year="2025"), 10.)

    def test_override_share_errors_and_ambiguities_are_rejected(self):
        for rules in [pd.DataFrame([override(ng_share=.8, h2_share=.8)]),
                      pd.DataFrame([override(ng_share=-.1, h2_share=1.1)]),
                      pd.DataFrame([override(h2_share=np.nan)]),
                      pd.DataFrame([override(), override()])]:
            with self.subTest(rules=rules.to_dict("records")):
                with self.assertRaises(ValueError):
                    steel.derive_steel_routes(self.raw, rules)

    def test_invalid_values_or_duplicate_source_rows_cannot_silently_become_zero(self):
        for bad in [np.nan, np.inf, -1.]:
            frame = self.raw.copy(deep=True)
            frame.loc[frame.variables.eq(STEEL_PREFIX + "EAF_DRI"), "2050"] = bad
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    steel.derive_steel_routes(frame)
        duplicate = pd.concat([self.raw, self.raw.iloc[:1]], ignore_index=True)
        with self.assertRaises(ValueError):
            steel.derive_steel_routes(duplicate)
        alias_duplicate = self.raw.iloc[:1].copy(deep=True)
        alias_duplicate.loc[:, "region"] = "Austria"
        with self.assertRaises(ValueError):
            steel.derive_steel_routes(pd.concat([self.raw, alias_duplicate], ignore_index=True))

    def test_missing_whole_route_is_audited_and_can_be_strict(self):
        raw = self.raw.loc[~self.raw.variables.eq(STEEL_PREFIX + "EAF_HBI")]
        derived, audit = steel.derive_steel_routes(raw)
        self.assertEqual(value(derived, "HBI_unresolved"), 0.)
        self.assertAlmostEqual(value(derived, ""), 110.)
        self.assertIn("missing", audit.to_string().lower())
        with self.assertRaises(ValueError):
            steel.derive_steel_routes(raw, strict_missing_routes=True)

    def test_routes_not_reported_by_itom_are_not_invented(self):
        derived, _ = steel.derive_steel_routes(self.raw)
        names = " ".join(derived.variables.tolist())
        self.assertNotIn("CCS", names)
        self.assertNotIn("Electrolysis", names)

    def test_unknown_routes_and_wrong_production_units_fail_explicitly(self):
        bad_unit = self.raw.copy(deep=True)
        bad_unit.loc[bad_unit.variables.eq(STEEL_PREFIX + "EAF_DRI"), "unit"] = "PJ/yr"
        with self.assertRaises(ValueError):
            steel.derive_steel_routes(bad_unit)
        for prefix in [STEEL_PREFIX, IRON_PREFIX]:
            unknown = self.raw.iloc[:1].copy(deep=True)
            unknown.loc[:, "variables"] = prefix + "new_unmapped_route"
            with self.subTest(prefix=prefix):
                with self.assertRaises(ValueError):
                    steel.derive_steel_routes(pd.concat([self.raw, unknown], ignore_index=True))


class HbiTradeOriginTests(unittest.TestCase):
    def test_intra_eu_uses_eu27_production_mix_not_domestic_mix_or_gb(self):
        raw = pd.concat([european_hbi_fixture(), hbi_material_rows(HBI_intra_EU=50.)], ignore_index=True)
        before = raw.copy(deep=True)
        derived, _ = steel.derive_steel_routes(raw)
        self.assertAlmostEqual(value(derived, "HBI_NG"), 1.)
        self.assertAlmostEqual(value(derived, "HBI_H2"), 8.)
        self.assertAlmostEqual(value(derived, "HBI_NG_H2"), 1.)
        self.assertEqual(value(derived, "HBI_unresolved"), 0.)
        # 50 Mt of feedstock is a mixing weight, not 50 Mt of steel output.
        self.assertAlmostEqual(sum(value(derived, route) for route in LEAVES), 120.)
        pd.testing.assert_frame_equal(raw, before)

    def test_explicit_extra_eu_imports_weight_actual_hbi_steel_volume(self):
        raw = pd.concat([european_hbi_fixture(), hbi_material_rows(
            HBI_intra_EU=20., HBI_import_NG=30., HBI_import_H2=50.)], ignore_index=True)
        derived, _ = steel.derive_steel_routes(raw)
        # 10 steel x [20% EU-pool + 30% known NG + 50% known H2].
        self.assertAlmostEqual(value(derived, "HBI_NG"), 3.2)
        self.assertAlmostEqual(value(derived, "HBI_H2"), 6.6)
        self.assertAlmostEqual(value(derived, "HBI_NG_H2"), .2)
        self.assertEqual(value(derived, "HBI_unresolved"), 0.)
        self.assertAlmostEqual(sum(value(derived, route) for route in LEAVES), 120.)

    def test_missing_eu_pool_leaves_only_intra_eu_portion_unresolved(self):
        raw = european_hbi_fixture()
        raw.loc[raw.region.isin(["AT", "FI"]) & raw.variables.str.startswith(IRON_PREFIX), ["2025", "2050"]] = 0.
        raw = pd.concat([raw, hbi_material_rows(HBI_intra_EU=20., HBI_import_NG=30., HBI_import_H2=50.)], ignore_index=True)
        derived, _ = steel.derive_steel_routes(raw)
        self.assertAlmostEqual(value(derived, "HBI_NG"), 3.)
        self.assertAlmostEqual(value(derived, "HBI_H2"), 5.)
        self.assertEqual(value(derived, "HBI_NG_H2"), 0.)
        self.assertAlmostEqual(value(derived, "HBI_unresolved"), 2.)
        self.assertAlmostEqual(sum(value(derived, route) for route in LEAVES), 120.)

    def test_eu_pools_do_not_mix_models_or_scenarios(self):
        raw = pd.concat([european_hbi_fixture(), hbi_material_rows(HBI_intra_EU=50.)], ignore_index=True)
        other = raw_fixture().assign(region="FR")
        other.loc[other.variables.str.startswith(STEEL_PREFIX), ["2025", "2050"]] = 0.
        for route, amount in [("DRI_NG", 10000.), ("DRI_H2", 0.), ("DRI_NG_H2", 0.)]:
            other.loc[other.variables.eq(IRON_PREFIX + route), ["2025", "2050"]] = amount
        raw = pd.concat([raw, other.assign(scenario="other_scenario"), other.assign(model="other_model")], ignore_index=True)
        derived, _ = steel.derive_steel_routes(raw)
        self.assertAlmostEqual(value(derived, "HBI_NG"), 1.)
        self.assertAlmostEqual(value(derived, "HBI_H2"), 8.)
        self.assertAlmostEqual(value(derived, "HBI_NG_H2"), 1.)

    def test_explicit_override_has_priority_over_material_and_pool_inference(self):
        raw = pd.concat([european_hbi_fixture(), hbi_material_rows(
            HBI_intra_EU=20., HBI_import_NG=30., HBI_import_H2=50.)], ignore_index=True)
        derived, _ = steel.derive_steel_routes(raw, pd.DataFrame([override(ng_share=1., h2_share=0.)]))
        self.assertEqual(value(derived, "HBI_NG"), 10.)
        self.assertEqual(value(derived, "HBI_H2"), 0.)
        self.assertEqual(value(derived, "HBI_NG_H2"), 0.)
        self.assertAlmostEqual(value(derived, "HBI_H2", year="2025"), 6.6)
        self.assertAlmostEqual(sum(value(derived, route) for route in LEAVES), 120.)

    def test_positive_unknown_hbi_route_and_invalid_hbi_mass_units_fail(self):
        for suffix in ["HBI_import_unknown", "HBI_import_NG_CCS"]:
            raw = pd.concat([european_hbi_fixture(), hbi_material_rows(**{suffix: 1.})], ignore_index=True)
            with self.subTest(suffix=suffix):
                with self.assertRaises(ValueError):
                    steel.derive_steel_routes(raw)
        wrong_unit = hbi_material_rows(HBI_intra_EU=50.)
        wrong_unit.loc[:, "unit"] = "PJ/yr"
        with self.assertRaises(ValueError):
            steel.derive_steel_routes(pd.concat([european_hbi_fixture(), wrong_unit], ignore_index=True))

    def test_unreported_hbi_materials_preserve_the_unresolved_fallback(self):
        derived, _ = steel.derive_steel_routes(european_hbi_fixture())
        self.assertEqual(value(derived, "HBI_unresolved"), 10.)
        self.assertEqual(value(derived, "HBI_NG"), 0.)
        self.assertEqual(value(derived, "HBI_H2"), 0.)


class EuropeanCountryMarketLinkingTests(unittest.TestCase):
    def test_selected_external_scenario_supplies_country_weights_not_background_model(self):
        source = country_market_iamc()
        unrelated = source.assign(scenario="unrelated", model="other_model")
        unrelated.loc[:, ["2025", "2050"]] *= 1000.
        spec = scenario_inputs.load_itom_steel_country_mix(country_mix_scenario(pd.concat([source, unrelated])))
        self.assertEqual(spec["model"], "itom-steel")
        self.assertEqual(spec["scenario"], "test")
        self.assertEqual(spec["region"], "RER")
        self.assertEqual(spec["status"], "ready")
        self.assertAlmostEqual(spec["total"], 100.)
        self.assertEqual(spec["country_volumes"], {"AT": 80., "DE": 20., "FI": 0.})
        self.assertEqual(spec["weights"], {"AT": .8, "DE": .2})
        self.assertEqual(spec["markets"], STEEL_MARKETS)

    def test_intermediate_year_interpolates_national_volumes_before_normalizing(self):
        spec = scenario_inputs.load_itom_steel_country_mix(country_mix_scenario(year=2030))
        self.assertAlmostEqual(spec["country_volumes"]["DE"], 176.)
        self.assertAlmostEqual(spec["country_volumes"]["AT"], 104.)
        self.assertAlmostEqual(spec["total"], 280.)
        self.assertAlmostEqual(spec["weights"]["DE"], 176. / 280.)
        self.assertNotAlmostEqual(spec["weights"]["DE"], .2 + .2 * (.8 - .2))

    def test_outside_year_bounds_use_one_coherent_endpoint(self):
        first = scenario_inputs.load_itom_steel_country_mix(country_mix_scenario(year=2020))
        last = scenario_inputs.load_itom_steel_country_mix(country_mix_scenario(year=2060))
        self.assertEqual(first["weights"], {"AT": .8, "DE": .2})
        self.assertEqual(last["weights"], {"AT": .2, "DE": .8})
        self.assertEqual(first["effective_year"], 2025)
        self.assertEqual(last["effective_year"], 2050)

    def test_undeclared_packages_and_zero_production_are_explicit(self):
        self.assertIsNone(scenario_inputs.load_itom_steel_country_mix(country_mix_scenario(declaration=False)))
        source = country_market_iamc()
        source.loc[:, ["2025", "2050"]] = 0.
        spec = scenario_inputs.load_itom_steel_country_mix(country_mix_scenario(source))
        self.assertEqual(spec["status"], "no_european_production")
        self.assertEqual(spec["weights"], {})
        self.assertEqual(spec["total"], 0.)
        linked, receipt, changed = scenario_inputs.plan_itom_steel_country_markets([], spec)
        self.assertEqual(linked, [])
        self.assertFalse(changed)

    def test_invalid_country_supply_and_inconsistent_total_fail_before_any_market_edit(self):
        source = country_market_iamc()
        country_mask = source.variables.eq(COUNTRY_SUPPLY_PREFIX + "DE")
        for value in (-1., np.nan, np.inf):
            invalid = source.copy()
            invalid.loc[country_mask, "2025"] = value
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    scenario_inputs.load_itom_steel_country_mix(country_mix_scenario(invalid))
        duplicate = pd.concat([source, source.loc[country_mask]], ignore_index=True)
        inconsistent = source.copy()
        inconsistent.loc[inconsistent.region.eq("RER") & inconsistent.variables.eq("Production|Steel"), "2025"] = 200.
        invalid_region = source.copy()
        invalid_region.loc[country_mask, "variables"] = COUNTRY_SUPPLY_PREFIX + "RER"
        wrong_unit = source.copy()
        wrong_unit.loc[country_mask, "unit"] = "kilogram"
        for invalid in (duplicate, inconsistent, invalid_region, wrong_unit):
            with self.subTest(invalid=invalid.to_dict("records")):
                with self.assertRaises(ValueError):
                    scenario_inputs.load_itom_steel_country_mix(country_mix_scenario(invalid))

    def test_rer_buys_same_grade_national_markets_and_retains_national_footprints(self):
        spec = scenario_inputs.load_itom_steel_country_mix(country_mix_scenario())
        database = country_market_database()
        before = deepcopy(database)
        linked, receipt, changed = scenario_inputs.plan_itom_steel_country_markets(database, spec)
        self.assertEqual(database, before, "Planning must not mutate the scenario before validation")
        self.assertEqual(set(changed), {0, 3})
        for position in changed:
            rer = linked[position]
            old = before[position]
            self.assertEqual(rer["code"], old["code"])
            self.assertEqual(rer["marker"], old["marker"])
            steel_inputs = [exchange for exchange in rer["exchanges"]
                            if exchange["type"] == "technosphere" and exchange.get("product") == rer["reference product"]]
            self.assertEqual(len(steel_inputs), 2)
            self.assertEqual({exchange["name"] for exchange in steel_inputs}, {rer["name"]})
            self.assertEqual({exchange["location"] for exchange in steel_inputs}, {"AT", "DE"})
            self.assertAlmostEqual(sum(exchange["amount"] for exchange in steel_inputs), 1.)
            weights = {exchange["location"]: exchange["amount"] for exchange in steel_inputs}
            self.assertAlmostEqual(weights["DE"], .2)
            self.assertAlmostEqual(weights["AT"], .8)
            for exchange in steel_inputs:
                self.assertEqual(exchange["unit"], "kilogram")
                self.assertNotEqual(exchange["location"], "RER")
                self.assertNotIn("production", exchange["name"])
                self.assertNotIn("hydrogen", exchange["name"])
                self.assertFalse(any(key in exchange for key in ("input", "output", "database", "code")))
            # Contrasting national burdens survive the market construction:
            # .2 * 2 kg CO2/kg DE + .8 * 10 kg CO2/kg AT = 8.4 kg CO2/kg.
            footprint = 0.
            for exchange in steel_inputs:
                supplier = next(item for item in linked if item["name"] == exchange["name"]
                                and item["location"] == exchange["location"]
                                and item["reference product"] == exchange["product"])
                footprint += exchange["amount"] * sum(e["amount"] for e in supplier["exchanges"] if e["type"] == "biosphere")
            self.assertAlmostEqual(footprint, 8.4)
            preserved = [e for e in rer["exchanges"] if e not in steel_inputs]
            expected_preserved = [e for e in old["exchanges"]
                                  if not (e["type"] == "technosphere" and e.get("product") == old["reference product"])]
            self.assertEqual(preserved, expected_preserved)
        for position in set(range(len(before))) - set(changed):
            self.assertEqual(linked[position], before[position])

    def test_zero_weight_country_does_not_require_an_unproduced_national_market(self):
        spec = scenario_inputs.load_itom_steel_country_mix(country_mix_scenario())
        self.assertIn("FI", spec["country_volumes"])
        self.assertNotIn("FI", spec["weights"])
        linked, _, _ = scenario_inputs.plan_itom_steel_country_markets(country_market_database(), spec)
        for dataset in linked:
            if dataset["location"] == "RER" and dataset["name"] in {m["name"] for m in STEEL_MARKETS}:
                self.assertFalse(any(e.get("location") == "FI" for e in dataset["exchanges"]))

    def test_positive_missing_duplicate_or_wrong_grade_national_market_fails_transactionally(self):
        spec = scenario_inputs.load_itom_steel_country_mix(country_mix_scenario())
        source = country_market_database()
        missing = source[:1] + source[2:]
        duplicate = source + [deepcopy(source[1])]
        wrong_grade = deepcopy(source)
        wrong_grade[1]["reference product"] = "steel, unalloyed"
        for invalid in (missing, duplicate, wrong_grade):
            before = deepcopy(invalid)
            with self.subTest(kind=len(invalid)):
                with self.assertRaises(ValueError):
                    scenario_inputs.plan_itom_steel_country_markets(invalid, spec)
                self.assertEqual(invalid, before)

    def test_stale_national_production_volume_is_rejected_and_float32_rounding_is_allowed(self):
        spec = scenario_inputs.load_itom_steel_country_mix(country_mix_scenario())
        source = country_market_database()
        source[1]["exchanges"][0]["production volume"] = 200.
        with self.assertRaises(ValueError):
            scenario_inputs.plan_itom_steel_country_markets(source, spec)
        spec = scenario_inputs.load_itom_steel_country_mix(country_mix_scenario(year=2030))
        source = country_market_database({"DE": 176., "AT": 104.})
        for dataset in source:
            for exchange in dataset["exchanges"]:
                if "production volume" in exchange:
                    exchange["production volume"] = float(np.float32(exchange["production volume"]))
        linked, _, changed = scenario_inputs.plan_itom_steel_country_markets(source, spec)
        self.assertEqual(set(changed), {0, 3})

    def test_rer_country_market_replacement_is_idempotent(self):
        spec = scenario_inputs.load_itom_steel_country_mix(country_mix_scenario())
        first, _, _ = scenario_inputs.plan_itom_steel_country_markets(country_market_database(), spec)
        second, _, changed = scenario_inputs.plan_itom_steel_country_markets(first, spec)
        self.assertEqual(second, first)
        self.assertFalse(changed)


class EuropeanCountryMarketExportTests(unittest.TestCase):
    def setUp(self):
        self.directory = ROOT / "tmp" / "itom_steel_conversion_tests" / ("export_" + uuid4().hex)
        self.directory.mkdir(parents=True)

    def tearDown(self):
        self.directory.resolve().relative_to((ROOT / "tmp").resolve())
        shutil.rmtree(self.directory)

    def dump(self, scene):
        path = self.directory / (uuid4().hex + ".pickle")
        path.write_bytes(pickle.dumps(scene.pop("database")))
        scene["database filepath"] = path

    @staticmethod
    def load(scenario, **options):
        assert options["delete"] is False and options["original_database"] == []
        scenario["database"] = pickle.loads(Path(scenario.pop("database filepath")).read_bytes())
        return scenario

    def scene(self):
        return dict(country_mix_scenario(), database=country_market_database(), **{"applied functions": ["external"]})

    def test_existing_update_hook_also_handles_steel_without_por(self):
        from por_supplier_linking import attach_por_supplier_linking
        scene = country_mix_scenario()
        def native_update():
            scene.update(database=country_market_database(), **{"applied functions": ["external"]})
            return "native result"
        builder = SimpleNamespace(scenarios=[scene], update=native_update)
        policy = SimpleNamespace(verify_sources=lambda: None)
        with mock.patch("por_supplier_linking.ROOT", self.directory):
            attach_por_supplier_linking(builder, policy=policy, install_support=False, verbose=False,
                report_dir=self.directory / "por", load_database_func=self.load, dump_database_func=self.dump)
            self.assertEqual(builder.update(), "native result")
        self.assertEqual(builder.por_supplier_linking_reports, [])
        self.assertEqual(builder.itom_steel_country_market_reports[0]["changed_markets"], 2)
        linked = pickle.loads(Path(scene["database filepath"]).read_bytes())
        for position in (0, 3):
            steel_inputs = [e for e in linked[position]["exchanges"]
                            if e.get("type") == "technosphere" and e.get("unit") == "kilogram"]
            self.assertEqual({e["location"]: e["amount"] for e in steel_inputs}, {"AT": .8, "DE": .2})
        fresh = scenario_inputs.fresh_scenario_definitions([scene])[0]
        self.assertNotIn("itom steel market aggregation", fresh)

    def test_cache_roundtrip_and_repeated_application_do_not_lose_country_mix(self):
        from por_supplier_linking import apply_itom_steel_country_market_aggregation as apply
        scene = self.scene()
        self.dump(scene)
        original = scene["database filepath"]
        builder = SimpleNamespace(scenarios=[scene])
        options = dict(report_dir=self.directory / "reports", verbose=False,
                       load_database_func=self.load, dump_database_func=self.dump)
        first = apply(builder, **options)
        linked_path = scene["database filepath"]
        self.assertNotEqual(original, linked_path)
        self.assertTrue(original.is_file())
        self.assertEqual(first[0]["changed_markets"], 2)
        second = apply(builder, **options)
        self.assertEqual(second[0]["changed_markets"], 0)
        self.assertEqual(scene["database filepath"], linked_path)
        self.assertEqual(json.loads(Path(second[0]["report_path"]).read_text())["weights"], {"AT": .8, "DE": .2})

    def test_failure_never_publishes_partial_scenario_replacements(self):
        from por_supplier_linking import apply_itom_steel_country_market_aggregation as apply
        first, second = self.scene(), self.scene()
        second["database"].pop(2)  # Missing the positive AT low-alloyed market.
        old_first, old_second = first["database"], second["database"]
        builder = SimpleNamespace(scenarios=[first, second])
        with self.assertRaisesRegex(ValueError, "national ITOM market"):
            apply(builder, report_dir=self.directory, verbose=False, dump_database_func=self.dump)
        self.assertIs(first["database"], old_first)
        self.assertIs(second["database"], old_second)
        self.assertNotIn("itom steel market aggregation", first)

    def test_installed_legacy_premise_cache_functions_export_the_country_recipe(self):
        from por_supplier_linking import apply_itom_steel_country_market_aggregation as apply
        legacy = Path.home() / ".conda/envs/premise_pathways/Lib/site-packages/premise/utils.py"
        if not legacy.is_file():
            self.skipTest("Optional premise 2.3.8 environment not installed")
        import uuid
        tree = ast.parse(legacy.read_text(encoding="utf-8"))
        functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                     and node.name in ("load_database", "dump_database", "get_uuids")]
        ns = {"Dict": dict, "Any": object, "List": list, "Path": Path,
              "Iterable": list, "uuid": uuid, "pickle": pickle, "DIR_CACHED_FILES": self.directory}
        exec(compile(ast.Module(body=functions, type_ignores=[]), str(legacy), "exec"), ns)
        scene = self.scene()
        ns["dump_database"](scene)
        original_path = scene["database filepath"]
        apply(SimpleNamespace(scenarios=[scene]), report_dir=self.directory / "reports", verbose=False,
              load_database_func=ns["load_database"], dump_database_func=ns["dump_database"])
        self.assertTrue(original_path.is_file())
        loaded = ns["load_database"](dict(scene), [], delete=False, load_metadata=False, warning=False)
        supplies = [e for e in loaded["database"][0]["exchanges"]
                    if e["type"] == "technosphere" and e["unit"] == "kilogram"]
        self.assertEqual({e["location"]: e["amount"] for e in supplies}, {"AT": .8, "DE": .2})

    def test_changed_or_incomplete_sources_fail_before_rewriting_inventory(self):
        from por_supplier_linking import apply_itom_steel_country_market_aggregation as apply
        scene = self.scene()
        before = deepcopy(scene["database"])
        builder = SimpleNamespace(scenarios=[scene])
        with self.assertRaisesRegex(ValueError, "inputs changed"):
            apply(builder, report_dir=self.directory, expected_signatures=["stale hash"], verbose=False)
        scene["applied functions"] = []
        with self.assertRaisesRegex(ValueError, "completed external"):
            apply(builder, report_dir=self.directory, verbose=False)
        self.assertEqual(scene["database"], before)


class EuropeanMarketAggregationTests(unittest.TestCase):
    def test_european_steel_routes_are_production_weighted_and_preserve_countries(self):
        countries = european_market_fixture()
        before = countries.copy(deep=True)
        europe, shares = steel.aggregate_european_steel(countries)
        self.assertEqual(set(europe.region), {"RER"})
        self.assertEqual(set(europe.variables), {"Production|Steel"} | {"Production|Steel|" + route for route in LEAVES})
        self.assertAlmostEqual(european_value(europe, "", year="2025"), 1000.)
        self.assertAlmostEqual(european_value(europe, "BF/BOF", year="2025"), 170.)
        self.assertAlmostEqual(european_value(europe, "Secondary steel", year="2025"), 830.)
        self.assertAlmostEqual(european_value(europe, "BF/BOF", year="2025") / european_value(europe, "", year="2025"), .17)
        self.assertNotAlmostEqual(.17, (.8 + .1) / 2)
        self.assertAlmostEqual(european_value(europe, "BF/BOF", year="2050"), 190.)
        self.assertAlmostEqual(sum(european_value(europe, route) for route in LEAVES), european_value(europe, ""))
        self.assertFalse(shares.empty)
        pd.testing.assert_frame_equal(countries, before)

    def test_country_contributions_and_route_shares_use_the_european_denominator(self):
        _, shares = steel.aggregate_european_steel(european_market_fixture())
        self.assertEqual(set(shares.aggregate_region), {"RER"})
        self.assertEqual(set(shares.region), {"AT", "DE"})
        self.assertEqual(set(shares.route), set(LEAVES))
        self.assertEqual(set(shares.unit), {"Mt/yr"})
        self.assertFalse(shares.duplicated(["model", "scenario", "region", "year", "route"]).any())
        year = shares.loc[shares.year.astype(str).eq("2025")]
        bof = year.loc[year.route.eq("BF/BOF")].set_index("region")
        self.assertAlmostEqual(bof.loc["AT", "production_Mt_per_year"], 80.)
        self.assertAlmostEqual(bof.loc["DE", "production_Mt_per_year"], 90.)
        self.assertAlmostEqual(bof.loc["AT", "country_share"], .1)
        self.assertAlmostEqual(bof.loc["DE", "country_share"], .9)
        self.assertTrue(np.allclose(bof.route_share, .17))
        self.assertAlmostEqual(bof.loc["AT", "country_route_share"], .08)
        self.assertAlmostEqual(bof.loc["DE", "country_route_share"], .09)
        self.assertAlmostEqual(bof.loc["AT", "country_share_within_route"], 80 / 170)
        self.assertAlmostEqual(bof.loc["DE", "country_share_within_route"], 90 / 170)
        self.assertAlmostEqual(year.country_route_share.sum(), 1.)
        route_shares = year.drop_duplicates("route").set_index("route").route_share
        self.assertAlmostEqual(route_shares.sum(), 1.)
        # Within-route and whole-European contribution shares answer different
        # questions; neither is an unweighted average of national route shares.
        self.assertEqual(set(bof.status), {"defined"})
        zero_route = year.loc[year.route.eq("DRI_NG")]
        self.assertTrue(zero_route.route_share.eq(0.).all())
        self.assertTrue(zero_route.country_route_share.eq(0.).all())
        self.assertTrue(zero_route.country_share_within_route.isna().all())
        self.assertEqual(set(zero_route.status), {"no_route_production"})

    def test_european_market_includes_reported_gb_production(self):
        countries = european_market_fixture()
        britain = countries.loc[countries.region.eq("AT")].copy().assign(region="GB")
        britain.loc[:, ["2025", "2050"]] = 0.
        britain.loc[britain.variables.isin(["Production|Steel", "Production|Steel|BF/BOF"]), ["2025", "2050"]] = 1000.
        europe, _ = steel.aggregate_european_steel(pd.concat([countries, britain], ignore_index=True))
        self.assertEqual(european_value(europe, "", year="2025"), 2000.)
        self.assertEqual(european_value(europe, "BF/BOF", year="2025"), 1170.)

    def test_market_aggregation_separates_models_scenarios_and_years(self):
        countries = european_market_fixture()
        other_scenario = countries.copy().assign(scenario="other_scenario")
        other_model = countries.copy().assign(model="other_model")
        other_scenario.loc[:, ["2025", "2050"]] *= 2
        other_model.loc[:, ["2025", "2050"]] *= 3
        europe, _ = steel.aggregate_european_steel(pd.concat([countries, other_scenario, other_model], ignore_index=True))
        self.assertEqual(european_value(europe, ""), 1000.)
        self.assertEqual(european_value(europe, "", scenario="other_scenario"), 2000.)
        self.assertEqual(european_value(europe, "", model="other_model"), 3000.)
        self.assertEqual(european_value(europe, "BF/BOF", year="2025"), 170.)
        self.assertEqual(european_value(europe, "BF/BOF", year="2050"), 190.)

    def test_preaggregated_non_european_and_missing_country_routes_are_rejected(self):
        countries = european_market_fixture()
        for region in ["RER", "World", "US", "Austria"]:
            invalid = countries.loc[countries.region.eq("AT")].copy().assign(region=region)
            with self.subTest(region=region):
                with self.assertRaises(ValueError):
                    steel.aggregate_european_steel(pd.concat([countries, invalid], ignore_index=True))
        missing = countries.loc[~(countries.region.eq("AT") & countries.variables.eq("Production|Steel|DRI_unresolved"))]
        with self.assertRaises(ValueError):
            steel.aggregate_european_steel(missing)
        duplicate = pd.concat([countries, countries.iloc[:1]], ignore_index=True)
        with self.assertRaises(ValueError):
            steel.aggregate_european_steel(duplicate)

    def test_material_and_iron_volumes_do_not_enter_the_european_steel_total(self):
        countries = european_market_fixture()
        extras = countries.iloc[:2].copy()
        extras.loc[:, "variables"] = [MATERIAL_PREFIX + "HBI_intra_EU", IRON_PREFIX + "DRI_H2"]
        extras.loc[:, ["2025", "2050"]] = 10000.
        with self.assertRaises(ValueError):
            steel.aggregate_european_steel(pd.concat([countries, extras], ignore_index=True))

    def test_zero_output_produces_finite_zero_route_volumes(self):
        countries = european_market_fixture()
        countries.loc[:, ["2025", "2050"]] = 0.
        europe, shares = steel.aggregate_european_steel(countries)
        self.assertTrue(np.isfinite(europe[["2025", "2050"]].to_numpy(dtype=float)).all())
        self.assertTrue(europe[["2025", "2050"]].eq(0.).all().all())
        self.assertTrue(shares[["country_share", "route_share", "country_route_share", "country_share_within_route"]].isna().all().all())
        self.assertEqual(set(shares.status), {"no_european_production"})

    def test_invalid_route_quantities_units_and_totals_are_rejected(self):
        countries = european_market_fixture()
        for amount in [-1., np.nan, np.inf]:
            invalid = countries.copy()
            invalid.loc[invalid.region.eq("AT") & invalid.variables.eq("Production|Steel|BF/BOF"), "2025"] = amount
            with self.subTest(amount=amount):
                with self.assertRaises(ValueError):
                    steel.aggregate_european_steel(invalid)
        invalid = countries.copy()
        invalid.loc[invalid.region.eq("AT"), "unit"] = "kg/yr"
        with self.assertRaises(ValueError):
            steel.aggregate_european_steel(invalid)
        inconsistent = countries.copy()
        inconsistent.loc[inconsistent.region.eq("AT") & inconsistent.variables.eq("Production|Steel"), "2025"] = 999.
        with self.assertRaises(ValueError):
            steel.aggregate_european_steel(inconsistent)

    def test_zero_output_export_keeps_iamc_finite_and_share_denominators_explicitly_undefined(self):
        parent = ROOT / "tmp" / "itom_steel_conversion_tests"
        parent.mkdir(parents=True, exist_ok=True)
        folder = parent / ("run_" + uuid4().hex)
        folder.mkdir()
        source, output = folder / "zero_output.xlsx", folder / "scenario_data_itom.csv"
        raw = pd.concat([raw_fixture(), hbi_material_rows(HBI_intra_EU=0.)], ignore_index=True)
        raw.loc[:, ["2025", "2050"]] = 0.
        try:
            with pd.ExcelWriter(source, engine="openpyxl") as writer:
                for sheet, prefix in [("Production", "Production|"), ("Final Energy", "Final Energy|"),
                                      ("Material Consumption", "Material Consumption|")]:
                    raw.loc[raw.variables.str.startswith(prefix)].rename(columns={"variables": "Name"}).to_excel(
                        writer, sheet_name=sheet, index=False)
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            result = steel.transform_itom_steel(source, output)
            machine = pd.read_csv(result["output"])
            self.assertTrue(np.isfinite(machine[["2025", "2050"]].to_numpy(dtype=float)).all())
            self.assertTrue(machine[["2025", "2050"]].eq(0.).all().all())
            shares = pd.read_csv(result["market_shares"])
            self.assertEqual(set(shares.status), {"no_european_production"})
            self.assertTrue(shares[["country_share", "route_share", "country_route_share", "country_share_within_route"]].isna().all().all())
            self.assertNotIn("NaN", Path(result["market_shares"]).read_text(encoding="utf-8-sig"))
            self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), digest)
        finally:
            folder.resolve().relative_to(parent.resolve())
            parent.resolve().relative_to(ROOT.resolve())
            shutil.rmtree(folder)


@unittest.skipUnless(RAW_WORKBOOK.is_file(), "Private ITOM v2 workbook not present")
class RealWorkbookTests(unittest.TestCase):
    def test_all_sheets_normalize_without_altering_source_or_units(self):
        before = hashlib.sha256(RAW_WORKBOOK.read_bytes()).hexdigest()
        raw, audit = steel.read_itom_steel_workbook(RAW_WORKBOOK)
        self.assertEqual(list(raw.columns[:5]), ["model", "scenario", "region", "variables", "unit"])
        self.assertEqual(set(raw.model), {"itom-steel"})
        self.assertEqual(set(raw.scenario), {"ITOM_Steel_Test-scenario"})
        self.assertIn("SK", set(raw.region))
        self.assertIn("GB", set(raw.region))
        self.assertTrue(all(pycountry.countries.get(alpha_2=code) is not None for code in raw.region.unique()))
        self.assertTrue(raw.variables.str.startswith("Production|").any())
        self.assertTrue(raw.variables.str.startswith("Final Energy|").any())
        self.assertTrue(raw.variables.str.startswith("Material Consumption|").any())
        self.assertFalse(raw.variables.str.contains(r"\s\||\|\s", regex=True).any())
        source = pd.read_excel(RAW_WORKBOOK, sheet_name="Production")
        source_row = source.loc[source.Region.eq("Austria") & source.Name.str.endswith("Steel | BOF")].iloc[0]
        converted = raw.loc[raw.region.eq("AT") & raw.variables.eq(STEEL_PREFIX + "BOF")].iloc[0]
        self.assertAlmostEqual(converted["2025"], source_row[2025])
        self.assertEqual(converted.unit, "Mt/yr")
        self.assertFalse(audit.empty)
        self.assertEqual(hashlib.sha256(RAW_WORKBOOK.read_bytes()).hexdigest(), before)

    def test_every_country_and_year_conserves_the_original_four_steel_routes(self):
        raw, _ = steel.read_itom_steel_workbook(RAW_WORKBOOK)
        derived, _ = steel.derive_steel_routes(raw)
        years = [str(year) for year in range(2020, 2051, 5)]
        original = raw.loc[raw.variables.isin([STEEL_PREFIX + route for route in ["BOF", "EAF_DRI", "EAF_HBI", "EAF_Scrap"]])]
        expected = original.groupby(["model", "scenario", "region"])[years].sum()
        leaves = derived.loc[derived.variables.isin(["Production|Steel|" + route for route in LEAVES])]
        actual = leaves.groupby(["model", "scenario", "region"])[years].sum()
        pd.testing.assert_frame_equal(actual.sort_index(), expected.sort_index(), check_exact=False, rtol=1e-11, atol=1e-10)
        total = derived.loc[derived.variables.eq("Production|Steel")].set_index(["model", "scenario", "region"])[years]
        pd.testing.assert_frame_equal(total.sort_index(), expected.sort_index(), check_exact=False, rtol=1e-11, atol=1e-10)

    def test_reported_intra_eu_hbi_has_an_ng_hydrogen_mix_without_changing_steel(self):
        before = hashlib.sha256(RAW_WORKBOOK.read_bytes()).hexdigest()
        raw, _ = steel.read_itom_steel_workbook(RAW_WORKBOOK)
        derived, _ = steel.derive_steel_routes(raw)
        for route in ["HBI_NG", "HBI_H2"]:
            self.assertGreater(derived.loc[derived.variables.eq("Production|Steel|" + route), "2050"].sum(), 0.)
        hbi = derived.loc[derived.variables.isin(["Production|Steel|HBI_" + suffix for suffix in ["NG", "H2", "NG_H2", "unresolved"]]), "2050"].sum()
        source_hbi = raw.loc[raw.variables.eq(STEEL_PREFIX + "EAF_HBI"), "2050"].sum()
        self.assertAlmostEqual(hbi, source_hbi)
        self.assertEqual(hashlib.sha256(RAW_WORKBOOK.read_bytes()).hexdigest(), before)

    def test_actual_european_aggregate_covers_21_countries_without_adding_iron_or_trade(self):
        inventory = ROOT / "inventories/lci-itom.xlsx"
        before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in [RAW_WORKBOOK, inventory]}
        raw, _ = steel.read_itom_steel_workbook(RAW_WORKBOOK)
        derived, _ = steel.derive_steel_routes(raw)
        europe, shares = steel.aggregate_european_steel(derived)
        self.assertEqual(set(shares.region), set(raw.region))
        self.assertEqual(shares.region.nunique(), 21)
        self.assertEqual(len(europe), 11)
        scenario = raw.scenario.iloc[0]
        self.assertAlmostEqual(european_value(europe, "", scenario=scenario), 140.65, places=7)
        for year in [str(year) for year in range(2020, 2051, 5)]:
            expected = raw.loc[raw.variables.isin([STEEL_PREFIX + route for route in ["BOF", "EAF_DRI", "EAF_HBI", "EAF_Scrap"]]), year].sum()
            self.assertAlmostEqual(european_value(europe, "", year=year, scenario=scenario), expected)
            self.assertAlmostEqual(sum(european_value(europe, route, year=year, scenario=scenario) for route in LEAVES), expected)
        for path, digest in before.items():
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest)

    def test_output_files_keep_raw_workbook_unchanged_and_mapping_is_connected(self):
        parent = ROOT / "tmp" / "itom_steel_conversion_tests"
        parent.mkdir(parents=True, exist_ok=True)
        folder = parent / ("run_" + uuid4().hex)
        folder.mkdir()
        output = folder / "scenario_data_itom.csv"
        before = hashlib.sha256(RAW_WORKBOOK.read_bytes()).hexdigest()
        try:
            result = steel.transform_itom_steel(RAW_WORKBOOK, output)
            self.assertTrue(output.is_file())
            frame = pd.read_csv(output)
            self.assertIn("Production|Steel|DRI_H2", set(frame.variables))
            self.assertIn("Production|Steel|HBI_unresolved", set(frame.variables))
            # The original annual rows remain alongside the new disjoint leaves.
            self.assertIn(STEEL_PREFIX + "EAF_DRI", set(frame.variables))
            raw, _ = steel.read_itom_steel_workbook(RAW_WORKBOOK)
            countries, _ = steel.derive_steel_routes(raw)
            expected_countries = pd.concat([raw, countries], ignore_index=True)
            actual_countries = frame.loc[frame.region.ne("RER")].reset_index(drop=True)
            sort_columns = ["model", "scenario", "region", "variables", "unit"]
            pd.testing.assert_frame_equal(
                actual_countries.sort_values(sort_columns).reset_index(drop=True),
                expected_countries.sort_values(sort_columns).reset_index(drop=True),
                check_dtype=False, check_exact=False, rtol=1e-11, atol=1e-10,
            )
            # Only canonical steel rows are aggregated: neither material trade
            # nor iron production is duplicated as additional European steel.
            european = frame.loc[frame.region.eq("RER")]
            self.assertEqual(len(european), 32)
            self.assertEqual(set(european.variables), {"Production|Steel"} | {"Production|Steel|" + leaf for leaf in LEAVES}
                             | {COUNTRY_SUPPLY_PREFIX + country for country in raw.region.unique()})
            self.assertEqual(result["derived_rows"], len(countries))
            self.assertEqual(result["european_rows"], 32)
            self.assertEqual(result["european_route_rows"], 11)
            self.assertEqual(result["european_country_supply_rows"], 21)
            self.assertEqual(result["country_count"], 21)
            self.assertEqual(result["output_rows"], len(raw) + len(countries) + 32)
            years = [str(year) for year in range(2020, 2051, 5)]
            self.assertTrue(np.isfinite(frame[years].to_numpy(dtype=float)).all())
            self.assertTrue(Path(result["xlsx"]).is_file())
            self.assertEqual(Path(result["xlsx"]), output.with_suffix(".xlsx"))
            excel = pd.ExcelFile(result["xlsx"], engine="openpyxl")
            try:
                self.assertTrue({"IAMC", "European country mix", "European market shares"}.issubset(set(excel.sheet_names)))
                excel_iamc = pd.read_excel(excel, sheet_name="IAMC")
                excel_shares = pd.read_excel(excel, sheet_name="European market shares")
            finally:
                excel.close()
            pd.testing.assert_frame_equal(excel_iamc, frame, check_dtype=False, check_exact=False, rtol=1e-11, atol=1e-10)
            shares = pd.read_csv(result["market_shares"])
            pd.testing.assert_frame_equal(excel_shares, shares, check_dtype=False, check_exact=False, rtol=1e-11, atol=1e-10)
            self.assertEqual(set(shares.region), set(raw.region))
            self.assertEqual(Path(result["market_shares"]), output.with_name("scenario_data_itom_market_shares.csv"))
            self.assertEqual(hashlib.sha256(RAW_WORKBOOK.read_bytes()).hexdigest(), before)
            self.assertTrue(isinstance(result, dict))
            json_files = list(folder.glob("*.json"))
            self.assertTrue(json_files)
            for path in json_files:
                json.loads(path.read_text(encoding="utf-8"))
            manifest = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
            self.assertEqual(manifest["derived_rows"], len(countries))
            self.assertEqual(manifest["output_rows"], len(frame))
            self.assertEqual(manifest["country_count"], 21)
            self.assertEqual(manifest["xlsx_sha256"], hashlib.sha256(Path(result["xlsx"]).read_bytes()).hexdigest())
            self.assertEqual(manifest["market_shares_sha256"], hashlib.sha256(Path(result["market_shares"]).read_bytes()).hexdigest())
            config = yaml.safe_load((ROOT / "configuration_file/config_itom.yaml").read_text(encoding="utf-8"))
            pathways = config["production pathways"]
            includes = {key for market in config["markets"] for key in market["includes"]}
            self.assertTrue(includes.issubset(pathways))
            mapped_variables = {pathways[key]["production volume"]["variable"] for key in includes}
            self.assertTrue(mapped_variables.issubset(set(frame.variables)))
            self.assertIn("Production|Steel|DRI_H2", mapped_variables)
            self.assertNotIn("Production|Steel", mapped_variables)
            descriptor = json.loads((ROOT / "datapackages/datapackage_itom.json").read_text(encoding="utf-8"))
            resource = next(r for r in descriptor["resources"] if r["name"] == "scenario_data")
            self.assertEqual(resource["path"], "scenario_data/scenario_data_itom.csv")
        finally:
            folder.resolve().relative_to(parent.resolve())
            parent.resolve().relative_to(ROOT.resolve())
            shutil.rmtree(folder)

    def test_invalid_override_does_not_replace_an_existing_output(self):
        parent = ROOT / "tmp" / "itom_steel_conversion_tests"
        parent.mkdir(parents=True, exist_ok=True)
        folder = parent / ("run_" + uuid4().hex)
        folder.mkdir()
        output = folder / "scenario_data_itom.csv"
        output.write_bytes(b"existing_output_must_survive")
        overrides = folder / "invalid_overrides.csv"
        pd.DataFrame([override(scenario="*", region="*", year="*", h2_share=10.)]).to_csv(overrides, index=False)
        try:
            with self.assertRaises(ValueError):
                steel.transform_itom_steel(RAW_WORKBOOK, output, overrides_path=overrides)
            self.assertEqual(output.read_bytes(), b"existing_output_must_survive")
            self.assertFalse(list(folder.glob("*manifest*.json")))
        finally:
            folder.resolve().relative_to(parent.resolve())
            parent.resolve().relative_to(ROOT.resolve())
            shutil.rmtree(folder)

    def test_idempotent_rerun_skips_a_protected_identical_canonical_csv(self):
        parent = ROOT / "tmp" / "itom_steel_conversion_tests"
        parent.mkdir(parents=True, exist_ok=True)
        folder = parent / ("run_" + uuid4().hex)
        folder.mkdir()
        output = folder / "scenario_data_itom.csv"
        source_hash = hashlib.sha256(RAW_WORKBOOK.read_bytes()).hexdigest()
        original_replace = Path.replace
        attempted_targets = []
        try:
            first = steel.transform_itom_steel(RAW_WORKBOOK, output)
            payload = output.read_bytes()
            modified = output.stat().st_mtime_ns

            def reject_csv_replacement(stage, destination):
                target = Path(destination).resolve()
                attempted_targets.append(target)
                if target == output.resolve():
                    raise AssertionError("An unchanged CSV open in Excel must not be replaced")
                return original_replace(stage, destination)

            with mock.patch.object(Path, "replace", new=reject_csv_replacement):
                second = steel.transform_itom_steel(RAW_WORKBOOK, output)
            self.assertNotIn(output.resolve(), attempted_targets)
            self.assertEqual(output.read_bytes(), payload)
            self.assertEqual(output.stat().st_mtime_ns, modified)
            self.assertEqual(first["output"], second["output"])
            self.assertEqual(second["output"], str(output.resolve()))
            self.assertEqual(second["audit"], str(output.with_name("scenario_data_itom_route_audit.csv").resolve()))
            self.assertEqual(hashlib.sha256(RAW_WORKBOOK.read_bytes()).hexdigest(), source_hash)
            self.assertFalse(list(folder.glob("*.tmp")))
        finally:
            folder.resolve().relative_to(parent.resolve())
            parent.resolve().relative_to(ROOT.resolve())
            shutil.rmtree(folder)

    def test_transient_permission_error_retries_without_changing_the_canonical_path(self):
        parent = ROOT / "tmp" / "itom_steel_conversion_tests"
        parent.mkdir(parents=True, exist_ok=True)
        folder = parent / ("run_" + uuid4().hex)
        folder.mkdir()
        output = folder / "scenario_data_itom.csv"
        source_hash = hashlib.sha256(RAW_WORKBOOK.read_bytes()).hexdigest()
        original_replace = Path.replace
        csv_attempts = []
        try:
            def transient_lock(stage, destination):
                if Path(destination).resolve() == output.resolve():
                    csv_attempts.append(str(destination))
                    if len(csv_attempts) == 1:
                        raise PermissionError(13, "Temporary viewer lock", str(destination))
                return original_replace(stage, destination)

            with mock.patch.object(Path, "replace", new=transient_lock), \
                    mock.patch.object(steel.time, "sleep") as sleep:
                result = steel.transform_itom_steel(RAW_WORKBOOK, output)
            self.assertEqual(len(csv_attempts), 2)
            sleep.assert_called_once_with(.2)
            self.assertEqual(result["output"], str(output.resolve()))
            self.assertTrue(output.is_file())
            self.assertEqual(set(p.name for p in folder.iterdir()), {
                "scenario_data_itom.csv", "scenario_data_itom_route_audit.csv",
                "scenario_data_itom_conversion_manifest.json", "scenario_data_itom.xlsx",
                "scenario_data_itom_market_shares.csv",
            })
            manifest = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
            self.assertEqual(manifest["output"], str(output.resolve()))
            self.assertEqual(manifest["output_sha256"], hashlib.sha256(output.read_bytes()).hexdigest())
            self.assertEqual(hashlib.sha256(RAW_WORKBOOK.read_bytes()).hexdigest(), source_hash)
        finally:
            folder.resolve().relative_to(parent.resolve())
            parent.resolve().relative_to(ROOT.resolve())
            shutil.rmtree(folder)


class SteelMappingContractTests(unittest.TestCase):
    def test_markets_include_disjoint_leaves_without_replacing_production_recipes(self):
        config = yaml.safe_load((ROOT / "configuration_file/config_itom.yaml").read_text(encoding="utf-8"))
        pathways = config["production pathways"]
        expected = {"Production|Steel|" + leaf for leaf in LEAVES}
        self.assertEqual({p["production volume"]["variable"] for p in pathways.values()}, expected)
        by_variable = {pathway["production volume"]["variable"]: pathway for pathway in pathways.values()}
        aliases = [pathway["ecoinvent alias"] for pathway in pathways.values()]
        self.assertEqual(len({alias["name"].casefold() for alias in aliases}), 10)
        regionalized = {item["name"]: item for item in config["regionalize"]["datasets"]}
        originals = {
            "steel production, blast furnace-basic oxygen furnace, low-alloyed",
            "steel production, natural gas-based direct reduction iron-electric arc furnace, low-alloyed",
            "steel production, hydrogen-based direct reduction iron-electric arc furnace, low-alloyed",
            "steel production, electric, low-alloyed",
        }
        self.assertEqual(set(regionalized), originals)
        for original in regionalized.values():
            self.assertIs(original["exists in original database"], True)
            self.assertEqual(original["reference product"], "steel, low-alloyed")
        for alias in aliases:
            self.assertIs(alias["exists in original database"], False)
            self.assertIs(alias["regionalize"], True)
            # Repeating an imported alias in top-level regionalization can
            # overwrite native pathway metadata in premise 2.3.8. Only its
            # original producer belongs there; the alias has its own flag.
            self.assertNotIn(alias["name"], regionalized)
        for route in ("DRI_H2", "HBI_H2"):
            self.assertIn("hydrogen-based", by_variable["Production|Steel|" + route]["ecoinvent alias"]["name"])
        self.assertIn("natural gas-based", by_variable["Production|Steel|DRI_NG"]["ecoinvent alias"]["name"])
        scrap_alias = by_variable["Production|Steel|Secondary steel"]["ecoinvent alias"]
        self.assertEqual(scrap_alias["name"], "steel production, scrap-based electric arc furnace, low-alloyed (ITOM)")
        self.assertIs(scrap_alias["exists in original database"], False)
        self.assertIs(scrap_alias["regionalize"], True)
        self.assertEqual(len(config["markets"]), 2)
        for market in config["markets"]:
            self.assertEqual(set(market["includes"]), set(pathways))
            self.assertTrue(market["name"].startswith("market for steel,"))
            self.assertTrue(all(provider["name"].startswith("market for steel,") for provider in market["replaces"]))

    def test_world_supply_and_global_consumers_are_excluded_without_country_and_filter(self):
        config = yaml.safe_load((ROOT / "configuration_file/config_itom.yaml").read_text(encoding="utf-8"))
        for market in config["markets"]:
            self.assertIn("World", market["except regions"])
            conditions = market["replaces in"]
            # Native premise combines these filters by AND; twenty-one
            # positive location conditions would select no country at all.
            self.assertFalse(any("location" in condition for condition in conditions))
            excluded = {condition["excludes"]["location"] for condition in conditions}
            self.assertTrue({"GLO", "RoW", "World"}.issubset(excluded))

    def test_descriptor_uses_312_and_the_connected_scenario_config_and_steel_workbook(self):
        descriptor = json.loads((ROOT / "datapackages/datapackage_itom.json").read_text(encoding="utf-8"))
        self.assertEqual(descriptor["ecoinvent"]["version"], "3.12")
        self.assertEqual(descriptor["ecoinvent"]["system model"], "cut-off")
        resources = {resource["name"]: resource for resource in descriptor["resources"]}
        self.assertEqual(set(resources), {"scenario_data", "config", "inventories"})
        self.assertEqual(resources["scenario_data"]["path"], "scenario_data/scenario_data_itom.csv")
        self.assertEqual(resources["config"]["path"], "configuration_file/config_itom.yaml")
        self.assertEqual(resources["inventories"]["path"], "inventories/lci-itom.xlsx")
        self.assertEqual(resources["inventories"]["format"], "xlsx")
        self.assertEqual([field["name"] for field in resources["scenario_data"]["schema"]["fields"][:5]],
                         ["model", "scenario", "region", "variables", "unit"])
        if steel.DEFAULT_OUTPUT.is_file():
            actual = pd.read_csv(steel.DEFAULT_OUTPUT)
            self.assertTrue(set(descriptor["scenarios"]).issubset(set(actual.scenario)))
            self.assertTrue({"Production|Steel|" + leaf for leaf in LEAVES}.issubset(set(actual.variables)))

    def test_workbook_contains_ten_unique_one_kg_wrappers_matching_yaml(self):
        path = ROOT / "inventories/lci-itom.xlsx"
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        activities = workbook_activity_blocks(path)
        self.assertEqual(len(activities), 10)
        self.assertEqual(len({activity["activity"].casefold() for activity in activities}), 10)
        config = yaml.safe_load((ROOT / "configuration_file/config_itom.yaml").read_text(encoding="utf-8"))
        aliases = {pathway["ecoinvent alias"]["name"]: pathway["ecoinvent alias"]
                   for pathway in config["production pathways"].values()}
        self.assertEqual(set(aliases), {activity["activity"] for activity in activities})
        for activity in activities:
            self.assertEqual(activity["reference product"], aliases[activity["activity"]]["reference product"])
            self.assertEqual(activity["unit"], "kilogram")
            exchanges = activity["exchanges"]
            self.assertEqual(len(exchanges), 2, "Each wrapper has one output and one existing producer input")
            for exchange in exchanges:
                self.assertTrue(exchange.get("name"))
                self.assertIn(exchange.get("type"), {"production", "technosphere"})
                self.assertIsNotNone(exchange.get("amount"))
                self.assertTrue(math.isfinite(float(exchange["amount"])))
                self.assertEqual(float(exchange["amount"]), 1.)
                self.assertEqual(exchange["unit"], "kilogram")
                self.assertEqual(exchange["reference product"], activity["reference product"])
                self.assertEqual(int(exchange["uncertainty type"]), 0)
                # Source IDs may be retained as provenance text, but active
                # links must resolve by the portable scenario identities.
                for link in ("input", "output", "database", "code"):
                    self.assertIn(exchange.get(link), (None, ""))
            production = [exchange for exchange in exchanges if exchange["type"] == "production"]
            self.assertEqual(len(production), 1)
            self.assertEqual(production[0]["name"], activity["activity"])
            self.assertEqual(float(production[0]["amount"]), 1.)
            self.assertEqual(production[0]["unit"], "kilogram")
            self.assertEqual(production[0]["reference product"], activity["reference product"])
            self.assertEqual(production[0]["location"], activity["location"])
            technosphere = [exchange for exchange in exchanges if exchange["type"] == "technosphere"]
            self.assertEqual(len(technosphere), 1)
            self.assertNotIn(technosphere[0]["name"], aliases,
                             "A wrapper must purchase its original producer rather than another ITOM wrapper")
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), before)

    def test_scrap_wrapper_selects_the_reviewed_european_original_recipe(self):
        activities = workbook_activity_blocks(ROOT / "inventories/lci-itom.xlsx")
        activity = next(item for item in activities if item["activity"] ==
                        "steel production, scrap-based electric arc furnace, low-alloyed (ITOM)")
        technosphere = [exchange for exchange in activity["exchanges"] if exchange.get("type") == "technosphere"]
        self.assertEqual(len(technosphere), 1)
        self.assertEqual(technosphere[0]["name"], "steel production, electric, low-alloyed")
        self.assertEqual(technosphere[0]["location"], "Europe without Switzerland and Austria")
        self.assertEqual(technosphere[0]["reference product"], "steel, low-alloyed")
        self.assertEqual(float(technosphere[0]["amount"]), 1.)
        # The original European provider has a scrap-only ferrous charge. Its
        # burdens remain upstream; no RoW or invented zero-burden recipe is used.

    def test_imported_steel_wrappers_do_not_create_internal_supplier_cycles(self):
        activities = workbook_activity_blocks(ROOT / "inventories/lci-itom.xlsx")
        names = {activity["activity"].casefold() for activity in activities}
        edges = {activity["activity"].casefold(): {
            exchange["name"].casefold() for exchange in activity["exchanges"]
            if exchange.get("type") == "technosphere" and exchange.get("name", "").casefold() in names
            and float(exchange["amount"]) != 0
        } for activity in activities}
        visited, active = set(), set()

        def visit(name):
            self.assertNotIn(name, active, "An imported steel wrapper loops into its own supplier chain")
            if name in visited:
                return
            active.add(name)
            for supplier in edges[name]:
                visit(supplier)
            active.remove(name)
            visited.add(name)

        for name in names:
            visit(name)

    def test_ng_and_hydrogen_wrapper_families_share_their_original_producer(self):
        activities = {item["activity"]: item for item in workbook_activity_blocks(ROOT / "inventories/lci-itom.xlsx")}
        config = yaml.safe_load((ROOT / "configuration_file/config_itom.yaml").read_text(encoding="utf-8"))
        by_variable = {pathway["production volume"]["variable"]: pathway["ecoinvent alias"]["name"]
                       for pathway in config["production pathways"].values()}

        def recipe(name):
            # Only foreground names differ. A shared original supplier is
            # expected and does not create duplicate foreground identities.
            fields = ("type", "name", "reference product", "location", "unit", "categories",
                      "uncertainty type", "loc", "scale", "shape", "minimum", "maximum", "negative")
            return sorted((float(exchange["amount"]), tuple(str(exchange.get(key)) for key in fields))
                          for exchange in activities[name]["exchanges"] if exchange["type"] != "production")

        ng = recipe(by_variable["Production|Steel|DRI_NG"])
        for route in ["DRI_NG_H2", "DRI_unresolved", "HBI_NG", "HBI_NG_H2", "HBI_unresolved"]:
            self.assertEqual(recipe(by_variable["Production|Steel|" + route]), ng)
        self.assertEqual(recipe(by_variable["Production|Steel|HBI_H2"]),
                         recipe(by_variable["Production|Steel|DRI_H2"]))

    def test_four_wrapper_families_purchase_one_kg_of_the_existing_base_producer(self):
        activities = {item["activity"]: item for item in workbook_activity_blocks(ROOT / "inventories/lci-itom.xlsx")}
        config = yaml.safe_load((ROOT / "configuration_file/config_itom.yaml").read_text(encoding="utf-8"))
        by_variable = {pathway["production volume"]["variable"]: pathway["ecoinvent alias"]["name"]
                       for pathway in config["production pathways"].values()}
        expected = {
            "BF/BOF": ("steel production, blast furnace-basic oxygen furnace, low-alloyed", "GLO"),
            "DRI_NG": ("steel production, natural gas-based direct reduction iron-electric arc furnace, low-alloyed", "GLO"),
            "DRI_H2": ("steel production, hydrogen-based direct reduction iron-electric arc furnace, low-alloyed", "GLO"),
            "Secondary steel": ("steel production, electric, low-alloyed", "Europe without Switzerland and Austria"),
        }
        for route, (provider, location) in expected.items():
            with self.subTest(route=route):
                activity = activities[by_variable["Production|Steel|" + route]]
                exchanges = activity["exchanges"]
                self.assertEqual(len(exchanges), 2)
                inputs = [exchange for exchange in exchanges if exchange["type"] == "technosphere"]
                self.assertEqual(len(inputs), 1)
                self.assertEqual(inputs[0]["name"], provider)
                self.assertEqual(inputs[0]["location"], location)
                self.assertEqual(inputs[0]["reference product"], "steel, low-alloyed")
                self.assertEqual(inputs[0]["unit"], "kilogram")
                self.assertEqual(float(inputs[0]["amount"]), 1.)
                self.assertEqual(int(inputs[0]["uncertainty type"]), 0)


if __name__ == "__main__":
    unittest.main()
