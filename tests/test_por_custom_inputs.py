"""Synthetic tests for custom PoR mappings and FORECAST name compatibility.

No ITOM workbooks, licensed inventories, Brightway project or premise key needed.
"""
from contextlib import redirect_stdout
from importlib.util import module_from_spec, spec_from_file_location
from io import StringIO
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
SPEC = spec_from_file_location("por_custom_converter", ROOT / "0_convert_ITOM_por_to_iamc.py")
converter = module_from_spec(SPEC)
SPEC.loader.exec_module(converter)
ETHYLENE = "Intermediate Product|ethylene|retrofit_MTA_with_loop"
SYNGAS = [f"Intermediate Product_by_product|syngas_{gas}|gasification_of_cracker_HFO"
          for gas in ("CO", "H2", "CO2")]
ABSENT = "Intermediate Product_by_product|syngas_CO|synthetic_gasifier"
FINAL = "Final Product|polypropylene|synthetic_polymer_route"


def configuration(bases, absent=False):
    variables = [f"{base}|mode{mode}" for base in bases for mode in (1, 2)]
    pathways = {f"route_{i}": {"production volume": {"variable": value}}
                for i, value in enumerate(variables)}
    pathways["FE_PPP"] = {"production volume": {"variable": FINAL}}
    if absent:
        pathways["synthetic_optional"] = {"production volume": {"variable": ABSENT}}
    return {"production pathways": pathways}


def sheets(scenario):
    production = []
    for year in (2045, 2050):
        mode = 2 if (scenario, year) == ("OCE", 2045) else 1
        for base in [ETHYLENE, *SYNGAS, FINAL]:
            _, product, technology = base.split("|")
            production.append(dict(LOCATION="Rotterdam", TECHNOLOGY=technology,
                PRODUCT=product, MODE_OF_OPERATION=mode, YEAR=year, LocalProductionByMode=50.))
        if scenario == "OCE":
            production.append(dict(LOCATION="Rotterdam", TECHNOLOGY="synthetic_gasifier",
                PRODUCT="syngas_CO", MODE_OF_OPERATION=1, YEAR=year, LocalProductionByMode=20.))
    trade = pd.DataFrame(dict(PRODUCT=["ethylene", "ethylene"], YEAR=[2045, 2050], Transport=[0., 0.]))
    common = dict(TECHNOLOGY=["para_xylene_extraction"]*2, REGION=["NL"]*2,
                  MODE_OF_OPERATION=[1, 1], YEAR=[2045, 2050])
    return {"products": pd.DataFrame(dict(PRODUCT=["ethylene", "syngas_CO", "syngas_H2", "syngas_CO2", "polypropylene"],
                type=["HVC", "intermediates, by-products", "intermediates, by-products", "intermediates, by-products", "outputs"])),
            "production_volume": pd.DataFrame(production),
            "transport_to_PoR": trade, "transport_from_PoR": trade,
            "use_volume": pd.DataFrame(columns=["LOCATION", "TECHNOLOGY", "PRODUCT", "YEAR", "LocalUseByMode"]),
            "input_factors": pd.DataFrame(dict(common, PRODUCT=["raw_para_xylene"]*2, InputActivityRatio=[1., 1.])),
            "output_factors": pd.DataFrame(dict(common, PRODUCT=["para_xylene"]*2, OutputActivityRatio=[1., 1.]))}


class CustomInputsTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        (self.root / "configuration_file").mkdir()
        (self.root / "scenario_data").mkdir()
        self.default = self.root / "configuration_file/config_itom_por.yaml"
        self.default.write_text(yaml.safe_dump(configuration([ETHYLENE])))
        self.custom = self.root / "selected.yaml"
        self.custom.write_text(yaml.safe_dump(configuration([ETHYLENE, *SYNGAS], absent=True)))
        for filename in converter.SCENARIO_INPUTS.values():
            (self.root / "scenario_data" / filename).touch()

    def run_conversion(self, **kwargs):
        scenarios = {Path(filename).name: scenario for scenario, filename in converter.SCENARIO_INPUTS.items()}
        def source(path, **options):
            return sheets(scenarios[Path(path).name])
        with patch.object(converter, "__file__", str(self.root / "converter.py")), \
             patch.object(converter.pd, "read_excel", side_effect=source), redirect_stdout(StringIO()):
            converter.main(**kwargs)

    def test_selected_yaml_exports_source_modes_and_audited_zeros_without_overwriting_defaults(self):
        sentinel = self.root / "scenario_data/scenario_data_itom_por.csv"
        sentinel.write_text("keep existing output\n")
        self.run_conversion(config_path="selected.yaml", output_tag="synthetic", complete_absent=True)
        data = pd.read_csv(self.root / "scenario_data/scenario_data_itom_por_synthetic.csv")
        for scenario in ("CL", "OCE"):
            case = data.loc[data.scenario.eq(scenario)].set_index("variables")
            for base in [ETHYLENE, *SYNGAS]:
                for year in (2045, 2050):
                    self.assertEqual(case.loc[base+"|mode1", str(year)] + case.loc[base+"|mode2", str(year)], case.loc[base, str(year)])
                    active = 2 if (scenario, year) == ("OCE", 2045) else 1
                    self.assertEqual(case.loc[base+f"|mode{active}", str(year)], 10.)
                    self.assertEqual(case.loc[base+f"|mode{3-active}", str(year)], 0.)
            self.assertEqual(case.loc[ABSENT, "2050"], 0. if scenario=="CL" else 4.)
        audit=json.loads((self.root / "scenario_data/p_xylene_terminal_purity_audit_synthetic.json").read_text())
        self.assertEqual([(r["scenario"], r["variable"]) for r in audit["explicit_zero_pathways"]], [("CL", ABSENT)])
        self.assertEqual(sentinel.read_text(), "keep existing output\n")
        self.assertTrue((self.root / "scenario_data/scenario_data_itom_por_CL_synthetic.csv").is_file())
        self.assertTrue((self.root / "scenario_data/scenario_data_itom_por_OCE_synthetic.csv").is_file())

    def test_no_arguments_keep_default_mapping_filenames_and_audit_schema(self):
        self.run_conversion()
        data=pd.read_csv(self.root / "scenario_data/scenario_data_itom_por.csv")
        self.assertIn(ETHYLENE+"|mode1", set(data.variables))
        self.assertNotIn(SYNGAS[0]+"|mode1", set(data.variables))
        audit=json.loads((self.root / "scenario_data/p_xylene_terminal_purity_audit.json").read_text())
        self.assertEqual(set(audit), {"schema_version", "source_sha256", "records"})
        self.assertEqual(set(audit["source_sha256"]), set(converter.SCENARIO_INPUTS.values()))

    def test_invalid_tags_and_untagged_completion_fail_before_reading_sources(self):
        for options in [dict(complete_absent=True), dict(output_tag="../unsafe")]:
            with self.subTest(options=options), patch.object(converter, "__file__", str(self.root / "converter.py")), \
                 patch.object(converter.pd, "read_excel") as reader:
                with self.assertRaises(ValueError):converter.main(**options)
                reader.assert_not_called()

    def test_zero_completion_preserves_rows_and_rejects_missing_or_misclassified_evidence(self):
        data=pd.DataFrame([dict(model="test", scenario=s, region="NL", variables=v, unit="kt/yr", **{"2050": q})
            for s,v,q in [("CL", "Intermediate Product|other|route", 2.), ("OCE", ABSENT, 3.)]])
        path=self.root / "zeros.yaml"
        path.write_text(yaml.safe_dump({"production pathways": {"optional": {"production volume": {"variable": ABSENT}}}}))
        result,audit=converter.complete_absent_pathways(data,path)
        pd.testing.assert_frame_equal(result.iloc[:len(data)].reset_index(drop=True),data)
        self.assertEqual(len(audit),1)
        for variable,region in [("Intermediate Product|syngas_CO|synthetic_gasifier","NL"),
                                ("Intermediate Product_by_product|unknown|route","NL"),
                                (ABSENT,"RER")]:
            invalid=data.copy();invalid.loc[1,["variables","region"]]=[variable,region]
            with self.subTest(variable=variable,region=region),self.assertRaises(ValueError):
                converter.complete_absent_pathways(invalid,path)


class ForecastNamesTest(unittest.TestCase):
    def test_old_and_current_forecast_names_are_replaced_only_inside_NL(self):
        config=yaml.safe_load((ROOT / "configuration_file/config_itom_por.yaml").read_text())
        market=next(m for m in config["markets"] if m["name"]=="market for methanol (por)")
        names={item["name"] for item in market["replaces"] if item["product"]=="methanol"}
        self.assertTrue({"market for methanol (SPS)","market for methanol (Forecast)"} <= names)
        self.assertEqual(market["replaces in"],[{"location":"NL"}])


if __name__ == "__main__":
    unittest.main()
