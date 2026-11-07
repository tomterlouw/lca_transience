"""Carbon adapter conservation, origin coverage and export-order regressions."""
from copy import deepcopy
import json
import math
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import pandas as pd
import yaml

import por_carbon_emissions as adapter
from carbon_accounting import _itom_formula_fraction as fraction

ROOT = Path(__file__).resolve().parents[1]
CODES = {("Carbon dioxide, non-fossil", "air", "unspecified", "kilogram"): "nonfossil-code"}
CODES[("Carbon dioxide, non-fossil", "air", "urban air close to ground", "kilogram")] = "urban-nonfossil-code"


def activity(name, product, exchanges=()):
    return dict(name=name, **{"reference product": product}, unit="kilogram", location="NL",
                database="test", code=name, exchanges=list(exchanges))


def feed(ds, amount):
    return dict(name=ds["name"], product=ds["reference product"], unit=ds["unit"],
                location=ds["location"], type="technosphere", amount=amount,
                input=(ds["database"], ds["code"]))


def fixture(dac=.7, biomass=0.):
    policy = yaml.safe_load(adapter.POLICY.read_text(encoding="utf-8"))
    policy["processes"] = {"MTO_test": dict(name_fragment="MTO test", yields={"ethylene": .35})}
    solar = activity("methanol, DAC-based, imported outside EU (por)", "methanol")
    unknown = activity("methanol unknown source", "methanol")
    bio = activity("methanol verified biomass", "methanol")
    policy["source_origins"].append(dict(name=bio["name"], product="methanol", origin="biogenic"))
    producers = [(solar, dac), (bio, biomass), (unknown, 1-dac-biomass)]
    market = activity("market for methanol (por)", "methanol", [feed(ds, q) for ds,q in producers if q])
    emitted = (fraction("CH4O") - .35*fraction("C2H4")) / fraction("CO2") / .35
    co2 = dict(name="Carbon dioxide, fossil", amount=emitted, type="biosphere",
               categories=("air",), unit="kilogram", input=("custom-biosphere", "fossil-code"))
    process = activity("ethylene production, MTO test", "ethylene", [feed(market, 1/.35), co2])
    spec = dict(policy=policy, active={process["name"]}, declared={process["name"]}, case="CL", year=2050,
                expected_methanol={ds["name"]:q for ds,q in producers if q}, signature="test", source_sha256={})
    return [solar, bio, unknown, market, process], spec


def plan(db, spec, codes=CODES):
    return adapter.plan_carbon_emissions(db, lambda d:d, spec, codes)


def oxide_activity(ethylene):
    """Actual carbon-bearing coefficients from the saved NL ecoinvent recipe."""
    emissions=[('Carbon dioxide, fossil',.49954596161842346),('Carbon monoxide, fossil',.00011000000085914508),
               ('Ethylene',.0002300000051036477),('Ethylene oxide',.000019999999494757503),
               ('Methane, fossil',.0006120000034570694),('NMVOC, non-methane volatile organic compounds',.00023999999393709004),
               ('DOC, Dissolved Organic Carbon',.00019999999494757503),('TOC, Total Organic Carbon',.000022000000171829015)]
    exchanges=[feed(ethylene,.7959704995155334)]
    for name,amount in emissions:
        exchanges.append(dict(name=name,amount=amount,type='biosphere',unit='kilogram',
                              categories=('water','surface water') if name.startswith(('DOC,','TOC,')) else ('air','urban air close to ground'),
                              input=('custom-biosphere','original-'+name)))
    return activity('ethylene oxide production, ethylene oxidation','ethylene oxide',exchanges)


def oxide_fixture(dac=.7,biomass=0.):
    db,spec=fixture(dac,biomass)
    spec['policy']['processes']['ethylene_oxide']=yaml.safe_load(adapter.POLICY.read_text())['processes']['ethylene_oxide']
    ethylene=activity('market for ethylene (por)','ethylene',[feed(db[-1],1)])
    oxide=oxide_activity(ethylene)
    db.extend([ethylene,oxide])
    spec['active'].add(oxide['name']);spec['declared'].add(oxide['name'])
    spec['required_targets']=[adapter._key(oxide)]
    spec['expected_carbon_markets']={'market for methanol (por)':dict(product='methanol',providers=spec['expected_methanol']),
        'market for ethylene (por)':dict(product='ethylene',providers={db[-3]['name']:1.})}
    return db,spec


class EthyleneOxideTests(unittest.TestCase):
    def test_native_unlinked_biosphere_uses_configured_database(self):
        db,spec=oxide_fixture()
        for e in db[-1]['exchanges']:
            if e.get('type')=='biosphere':e.pop('input',None)
        with self.assertRaisesRegex(ValueError,'biosphere identity'):
            plan(db,spec)
        changes,_=adapter.plan_carbon_emissions(db,lambda d:d,spec,CODES,biosphere_name='ecoinvent-3.12-biosphere')
        e=next(e for e in changes[adapter._key(db[-1])]['exchanges'] if e.get('name')=='Carbon dioxide, non-fossil')
        self.assertEqual(e['input'],('ecoinvent-3.12-biosphere','urban-nonfossil-code'))

    def test_inherits_reviewed_mto_origins_and_changes_only_co2(self):
        db,spec=oxide_fixture(.6,.2);original=deepcopy(db[-1])
        changes,report=plan(db,spec)
        row=next(r for r in report['activities'] if r.get('process')=='ethylene_oxide')
        self.assertAlmostEqual(row['origin_fractions']['dac'],.6)
        self.assertAlmostEqual(row['origin_fractions']['biogenic'],.2)
        self.assertAlmostEqual(row['origin_fractions']['unknown'],.2)
        self.assertAlmostEqual(row['nonfossil_CO2'],.8*.49954596161842346)
        result=changes[adapter._key(original)]
        for exchange in original['exchanges']:
            if exchange.get('name')!='Carbon dioxide, fossil':
                self.assertIn(exchange,result['exchanges'])
        nonfossil=next(e for e in result['exchanges'] if e.get('name')=='Carbon dioxide, non-fossil')
        self.assertEqual(nonfossil['categories'],('air','urban air close to ground'))
        self.assertEqual(nonfossil['input'],('custom-biosphere','urban-nonfossil-code'))
        self.assertAlmostEqual(row['total_CO2'],row['fossil_label_CO2']+row['nonfossil_CO2'])
        self.assertEqual(db[-1],original)
        self.assertEqual(row['process_basis'],'kg ethylene oxide output')
        self.assertNotIn('incoming_kgC_per_kg_methanol',row)

    def test_all_carbon_releases_checked_without_double_counting_doc_toc(self):
        db,spec=oxide_fixture();_,report=plan(db,spec)
        row=next(r for r in report['activities'] if r.get('process')=='ethylene_oxide')
        self.assertAlmostEqual(row['residual_fraction'],-.0014324993296014707)
        self.assertAlmostEqual(row['uncertain_emitted_carbon_upper_kgC_per_process_basis'],.00023999999393709004+.000022000000171829015)
        self.assertGreater(row['residual_min_kgC_per_process_basis']/row['incoming_kgC_per_process_basis'],-.005)
        self.assertEqual(row['balance_status'],'balanced_within_tolerance')

    def test_missing_regionalized_activity_and_new_fuel_fail(self):
        db,spec=oxide_fixture()
        with self.assertRaisesRegex(ValueError,'Missing regionalized'):
            plan(db[:-1],spec)
        db[-1]['exchanges'].append(dict(type='technosphere',name='market for natural gas, high pressure',amount=.1))
        with self.assertRaisesRegex(ValueError,'auxiliary fuel'):
            plan(db,spec)

    def test_original_mto_balance_is_checked_during_ethylene_tracing(self):
        db,spec=oxide_fixture()
        # Select only EO; its ethylene provider still needs a reviewed balance.
        spec['declared'].remove(db[-3]['name'])
        db[-3]['exchanges'][-1]['amount']*=10
        with self.assertRaisesRegex(ValueError,'Carbon output exceeds input'):
            plan(db,spec)

    def test_repeat_run_and_activity_order_are_safe(self):
        db,spec=oxide_fixture();changes,_=plan(db,spec)
        reordered,_=plan(list(reversed(db)),spec)
        self.assertEqual(changes,reordered)
        adapted=[changes.get(adapter._key(d),d) for d in db]
        self.assertFalse(plan(adapted,spec)[0])

    def test_unknown_ethylene_remains_fossil_labelled(self):
        db,spec=oxide_fixture(0)
        changes,report=plan(db,spec)
        self.assertFalse(changes)
        row=next(r for r in report['activities'] if r.get('process')=='ethylene_oxide')
        self.assertEqual(row['nonfossil_CO2'],0)


def syngas_fixture(*, unresolved_balance=False, dac=.6):
    db, spec = fixture(dac)
    real_policy = yaml.safe_load(adapter.POLICY.read_text(encoding='utf-8'))
    spec['policy']['processes']['methanol_syngas'] = real_policy['processes']['methanol_syngas']
    co=activity('CO source, known atmospheric fraction','syngas, CO component (por)')
    co2=activity('CO2 source, known biogenic fraction','syngas, CO2 component (por)')
    for ds,origin in [(co,'dac'),(co2,'biogenic')]:
        spec['policy']['source_origins'].append(dict(name=ds['name'],product=ds['reference product'],origin=origin))
    qco,qco2=.979299637743661,.263811330984007
    incoming=qco*fraction('CO')+qco2*fraction('CO2')
    emitted=.283009708737864 if unresolved_balance else (incoming-fraction('CH4O'))/fraction('CO2')
    emission=deepcopy(db[-1]['exchanges'][-1]);emission['amount']=emitted
    methanol=activity('methanol production, from syngas, stage-based (por)','methanol',
                       [feed(co,qco),feed(co2,qco2),emission])
    spec['active'].add(methanol['name']);spec['declared'].add(methanol['name'])
    # Replace the unreviewed remainder of the same methanol market.
    market=db[-2];market['exchanges']=[feed(db[0],dac),feed(methanol,1-dac)]
    spec['expected_methanol']={db[0]['name']:dac,methanol['name']:1-dac}
    return db+[co,co2,methanol],spec


class SyngasMethanolTests(unittest.TestCase):
    def test_balanced_syngas_splits_and_propagates_same_origins_to_mto(self):
        db,spec=syngas_fixture()
        original=deepcopy(db)
        changes,report=plan(db,spec)
        rows={r['process']:r for r in report['activities']}
        upstream=rows['methanol_syngas'];downstream=rows['MTO_test']
        expected_dac=.979299637743661*fraction('CO')/upstream['incoming_kgC_per_process_basis']
        self.assertAlmostEqual(upstream['origin_fractions']['dac'],expected_dac)
        self.assertAlmostEqual(upstream['origin_fractions']['biogenic'],1-expected_dac)
        self.assertAlmostEqual(downstream['origin_fractions']['dac'],.6+.4*expected_dac)
        self.assertAlmostEqual(downstream['origin_fractions']['biogenic'],.4*(1-expected_dac))
        for row in rows.values():
            self.assertAlmostEqual(row['total_CO2'],row['fossil_label_CO2']+row['nonfossil_CO2'])
        self.assertEqual(len(changes),2)
        self.assertEqual(db,original)

    def test_current_8_percent_gap_is_not_hidden_or_propagated_as_nonfossil(self):
        db,spec=syngas_fixture(unresolved_balance=True)
        changes,report=plan(db,spec)
        rows={r['process']:r for r in report['activities']}
        upstream=rows['methanol_syngas'];downstream=rows['MTO_test']
        self.assertEqual(upstream['status'],'balance_review_required')
        self.assertAlmostEqual(upstream['residual_fraction'],.08099289215728518)
        self.assertEqual(upstream['nonfossil_CO2'],0)
        self.assertEqual(upstream['emission_origin_fractions']['unknown'],1)
        self.assertNotIn(adapter._key(db[-1]),changes)
        self.assertAlmostEqual(downstream['origin_fractions']['dac'],.6)
        self.assertAlmostEqual(downstream['origin_fractions']['unknown'],.4)
        self.assertTrue(any(r['reason']=='methanol_carbon_balance_review_required' for r in report['unresolved_suppliers']))

    def test_supply_order_does_not_change_results_or_double_split(self):
        db,spec=syngas_fixture()
        changes,report=plan(db,spec)
        reordered,other=plan(list(reversed(db)),spec)
        self.assertEqual(changes,reordered)
        adapted=[changes.get(adapter._key(ds),ds) for ds in db]
        repeated,receipt=plan(adapted,spec)
        self.assertFalse(repeated)
        self.assertTrue(all(r['status']=='already_applied' for r in receipt['activities']))

    def test_bad_methanol_cannot_bypass_balance_via_the_supplier_tracer(self):
        db,spec=syngas_fixture(unresolved_balance=True)
        # Even when the upstream activity is absent from target selection, the
        # downstream origin resolver must enforce the same boundary check.
        spec['declared'].remove(db[-1]['name'])
        _,report=plan(db,spec)
        self.assertAlmostEqual(report['activities'][0]['origin_fractions']['unknown'],.4)
        db[-1]['exchanges'][-1]['amount']=10
        with self.assertRaisesRegex(ValueError,'Carbon output exceeds input'):
            plan(db,spec)


class SplitTests(unittest.TestCase):
    def test_full_plan_preserves_declared_numerical_zero_market_exchange(self):
        db,spec=fixture()
        retired=activity('methanol retired source','methanol')
        market=db[-2]
        market['exchanges'].append(feed(retired,1.1866339433827806e-18))
        db.append(retired)
        original=deepcopy(market)
        spec['expected_carbon_markets']={'market for methanol (por)':dict(product='methanol',
            providers=spec['expected_methanol'],declared_providers=[*spec['expected_methanol'],retired['name']])}
        changes,_=plan(db,spec)
        self.assertEqual(len(changes),1)
        self.assertEqual(market,original)
        self.assertEqual(market['exchanges'][-1]['amount'],1.1866339433827806e-18)

    def test_declared_zero_provider_accepts_xarray_interpolation_residue(self):
        actual={'operating':1.,'retired':1.1866339433827806e-18}
        expected=dict(providers={'operating':1.},declared_providers=['operating','retired'])
        original=deepcopy(actual)
        adapter.validate_carbon_market_shares(actual,expected,'syngas CO')
        self.assertEqual(actual,original)

    def test_numerical_zero_is_symmetric_and_does_not_allow_wrong_suppliers(self):
        adapter.validate_carbon_market_shares({'operating':1.},dict(
            providers={'operating':1.,'retired':1e-18},declared_providers=['operating','retired']),'syngas CO')
        with self.assertRaisesRegex(ValueError,'undeclared suppliers'):
            adapter.validate_carbon_market_shares({'operating':1.,'wrong supplier':1e-18},dict(
                providers={'operating':1.},declared_providers=['operating','retired']),'syngas CO')

    def test_meaningful_retired_route_or_wrong_mix_still_fails(self):
        expected=dict(providers={'operating':1.},declared_providers=['operating','retired'])
        with self.assertRaisesRegex(ValueError,'retired: actual='):
            adapter.validate_carbon_market_shares({'operating':1.-1e-8,'retired':1e-8},expected,'syngas CO')
        with self.assertRaisesRegex(ValueError,'operating: actual='):
            adapter.validate_carbon_market_shares({'operating':.9},expected,'syngas CO')

    def test_split_conserves_co2_and_keeps_unknown_fossil_without_editing_inputs(self):
        db,spec = fixture(); original = deepcopy(db)
        changes, report = plan(db, spec)
        result = changes[adapter._key(db[-1])]
        emissions = [e for e in result["exchanges"] if e["type"] == "biosphere"]
        total = db[-1]["exchanges"][-1]["amount"]
        self.assertAlmostEqual(sum(e["amount"] for e in emissions), total)
        self.assertAlmostEqual(emissions[-1]["amount"], .7*total)
        self.assertEqual(emissions[-1]["input"], ("custom-biosphere", "nonfossil-code"))
        self.assertEqual(db, original)
        self.assertEqual(report["activities"][0]["balance_status"], "balanced_within_tolerance")
        self.assertAlmostEqual(report["activities"][0]["origin_fractions"]["unknown"], .3)

    def test_biogenic_and_dac_remain_distinct_in_report(self):
        db,spec = fixture(.5,.2)
        changes,report=plan(db,spec)
        origins=report["activities"][0]["origin_fractions"]
        self.assertAlmostEqual(origins["dac"],.5)
        self.assertAlmostEqual(origins["biogenic"],.2)
        self.assertAlmostEqual(changes[adapter._key(db[-1])]["exchanges"][-1]["amount"],
                               db[-1]["exchanges"][-1]["amount"]*.7)

    def test_idempotence_and_policy_change(self):
        db,spec=fixture(); changes,_=plan(db,spec)
        db[-1]=changes[adapter._key(db[-1])]
        changes,report=plan(db,spec)
        self.assertFalse(changes)
        self.assertEqual(report["activities"][0]["status"],"already_applied")
        spec["signature"]="changed"
        with self.assertRaisesRegex(ValueError,"different carbon-policy"):
            plan(db,spec)

    def test_zero_dac_does_not_manufacture_nonfossil(self):
        db,spec=fixture(0)
        self.assertFalse(plan(db,spec)[0])

    def test_pure_dac_keeps_no_zero_fossil_exchange(self):
        db,spec=fixture(1)
        changes,_=plan(db,spec)
        e=[e for e in changes[adapter._key(db[-1])]["exchanges"] if e["type"]=="biosphere"]
        self.assertEqual(len(e),1)
        self.assertEqual(e[0]["name"],"Carbon dioxide, non-fossil")

    def test_actual_market_must_match_scenario(self):
        db,spec=fixture();spec["expected_methanol"]={"wrong":1}
        with self.assertRaisesRegex(ValueError,"differs"):
            plan(db,spec)

    def test_address_mismatch_and_missing_biosphere_are_errors(self):
        db,spec=fixture();db[-1]["exchanges"][0]["input"]=("test",db[0]["code"])
        with self.assertRaisesRegex(ValueError,"address/identity"):
            plan(db,spec)
        db,spec=fixture()
        with self.assertRaisesRegex(ValueError,"biosphere identity"):
            plan(db,spec,{})

    def test_auxiliary_fuel_is_not_blindly_relabelled(self):
        db,spec=fixture()
        db[-1]["exchanges"].append(dict(name="market for natural gas, high pressure", type="technosphere", amount=.1))
        with self.assertRaisesRegex(ValueError,"auxiliary fuel"):
            plan(db,spec)

    def test_positive_residual_reported_but_excess_output_rejected(self):
        db,spec=fixture();db[-1]["exchanges"][-1]["amount"]*=.95
        _,report=plan(db,spec)
        self.assertEqual(report["activities"][0]["balance_status"],"unresolved_positive_residual")
        db[-1]["exchanges"][-1]["amount"]*=2
        with self.assertRaisesRegex(ValueError,"exceeds input"):
            plan(db,spec)

    def test_recipe_change_or_unreviewed_legacy_not_silently_corrected(self):
        db,spec=fixture();db[-1]["exchanges"][0]["amount"]*=2
        with self.assertRaisesRegex(ValueError,"recipe basis"):
            plan(db,spec)
        db,spec=fixture();spec["policy"]["processes"]={}
        changes,report=plan(db,spec)
        self.assertFalse(changes)
        self.assertEqual(report["activities"][0]["status"],"legacy_recipe_review_required")

    def test_log_normal_uncertainty_and_technosphere_preserved(self):
        db,spec=fixture()
        db[-1]["exchanges"][-1].update({"uncertainty type":2,"loc":math.log(.5),"scale":.2,"minimum":0,"maximum":2})
        original=deepcopy(db[-1]["exchanges"][0])
        changes,_=plan(db,spec); result=changes[adapter._key(db[-1])]
        self.assertEqual(result["exchanges"][0],original)
        new=result["exchanges"][-1]
        self.assertAlmostEqual(new["loc"],math.log(.5)+math.log(.7))
        self.assertEqual(new["scale"],.2)
        self.assertEqual(new["minimum"],0)
        self.assertAlmostEqual(new["maximum"],1.4)

    def test_missing_origin_in_a_loop_stays_unknown(self):
        db,spec=fixture()
        db[2]["exchanges"]=[feed(db[3],1)]
        # This is an unreviewed production recipe, not a licence to assume DAC.
        _,report=plan(db,spec)
        self.assertAlmostEqual(report["activities"][0]["origin_fractions"]["unknown"],.3)

    def test_coproducts_must_share_the_same_joint_process(self):
        db,spec=fixture()
        second=deepcopy(db[-1]);second['name']='propylene production, MTO test';second['reference product']='propylene'
        second['exchanges'][-1]['amount']*=.99
        db.append(second);spec['active'].add(second['name']);spec['declared'].add(second['name'])
        with self.assertRaisesRegex(ValueError,'Inconsistent allocated coproduct'):
            plan(db,spec)


class IntegrationTests(unittest.TestCase):
    def test_native_store_certifies_ethylene_oxide_and_keeps_source_immutable(self):
        from premise.inventory_store import create_inventory_store
        db,spec=oxide_fixture()
        for ds in db:
            ds['exchanges'].insert(0,dict(type='production',name=ds['name'],product=ds['reference product'],
                                         location='NL',unit='kilogram',amount=1))
        source=create_inventory_store(db,backend='compact',scenario_identity='carbon-test')
        scene={'_inventory_store':source,'applied functions':['external'],'year':2050}
        builder=SimpleNamespace(scenarios=[scene],version='3.12',system_model='cutoff',
            _validation_reports={},_scenario_identity=lambda s:'carbon-test',
            _validation_source_fingerprint=lambda:'source',_validation_iam_fingerprint=lambda s:'iam')
        with tempfile.TemporaryDirectory() as directory,patch.object(adapter,'load_carbon_spec',return_value=spec):
            receipt=adapter.apply_por_carbon_emissions(builder,report_dir=directory,biosphere_codes=CODES,verbose=False)[0]
            self.assertEqual(receipt['changed_activities'],2)
            self.assertIn('_validation_report',scene)
            original=source.activity(list(source.iter_activity_ids())[-1]).to_dict()
            changed=scene['_inventory_store'].activity(list(source.iter_activity_ids())[-1]).to_dict()
            self.assertFalse(any(e.get('name')=='Carbon dioxide, non-fossil' for e in original['exchanges']))
            self.assertTrue(any(e.get('name')=='Carbon dioxide, non-fossil' for e in changed['exchanges']))

    def test_native_store_certifies_syngas_and_downstream_as_one_plan(self):
        from premise.inventory_store import create_inventory_store
        db,spec=syngas_fixture()
        for ds in db:
            ds['exchanges'].insert(0,dict(type='production',name=ds['name'],product=ds['reference product'],
                                         location='NL',unit='kilogram',amount=1))
        source=create_inventory_store(db,backend='compact',scenario_identity='carbon-test')
        scene={'_inventory_store':source,'applied functions':['external'],'year':2050}
        builder=SimpleNamespace(scenarios=[scene],version='3.12',system_model='cutoff',
            _validation_reports={},_scenario_identity=lambda s:'carbon-test',
            _validation_source_fingerprint=lambda:'source',_validation_iam_fingerprint=lambda s:'iam')
        with tempfile.TemporaryDirectory() as directory,patch.object(adapter,'load_carbon_spec',return_value=spec):
            receipt=adapter.apply_por_carbon_emissions(builder,report_dir=directory,biosphere_codes=CODES,verbose=False)[0]
            self.assertEqual(receipt['changed_activities'],2)
            self.assertEqual(receipt['balance_reviews'],0)
            self.assertIsNot(scene['_inventory_store'],source)
            again=adapter.apply_por_carbon_emissions(builder,report_dir=directory,biosphere_codes=CODES,verbose=False)[0]
            self.assertEqual(again['changed_activities'],0)

    def test_current_workbook_recipes_for_both_cases_all_export_years(self):
        """Dry run actual recipes; synthesize only premise's market construction."""
        import openpyxl
        from datapackage import Package
        package=Package(str(ROOT/'datapackages/datapackage_itom_por.json'),base_path=str(ROOT))
        config=yaml.safe_load((ROOT/'configuration_file/config_itom_por-rebuild.yaml').read_text(encoding='utf-8'))
        table=pd.read_csv(ROOT/'scenario_data/scenario_data_itom_por.csv')
        book=openpyxl.load_workbook(ROOT/'inventories/lci-itom_por.xlsx',read_only=True,data_only=True)
        originals=[]
        try:
            for sheet in book:
                ds=None;headers=None
                for row in sheet.values:
                    if row[0]=='Activity':
                        ds={'name':row[1],'exchanges':[]}; originals.append(ds);headers=None
                    elif ds and row[0] in ('reference product','unit','location','comment'):
                        ds[row[0]]=row[1]
                    elif ds and row[0]=='name' and row[1]=='amount':
                        headers=list(row)
                    elif ds and headers and row[0] is not None:
                        e={k:v for k,v in zip(headers,row) if k is not None and v is not None}
                        if e.get('type') in ('technosphere','biosphere','production'):
                            ds['exchanges'].append(e)
        finally:
            book.close()
        for case in ('CL','OCE'):
            for year in (2025,2030,2035,2040,2045,2050):
                with self.subTest(case=case,year=year):
                    spec=adapter.load_carbon_spec(dict(year=year,**{'external scenarios':[dict(scenario=case,data=package)]}))
                    db=deepcopy(originals)
                    # premise regionalizes the import interface; the protected
                    # upstream EU methanol supplier remains outside the PoR pool.
                    for d in db:
                        if d['name']=='methanol, imported from EU (por)':
                            d['location']='NL'
                    external=activity('market for methanol','methanol')
                    external['location']='RER w/o RU'
                    db.append(external)
                    db.append(oxide_activity(activity('market for ethylene (por)','ethylene')))
                    frame=table.loc[table.scenario.eq(case)&table.region.eq('NL')].set_index('variables')
                    for m in config['markets']:
                        if m['name'] not in ('market for methanol (por)','market for ethylene (por)') and not m['name'].startswith('market for syngas,'):
                            continue
                        supplies=[]
                        for alias in m['includes']:
                            p=config['production pathways'][alias]
                            q=float(frame.loc[p['production volume']['variable'],str(year)])
                            if q>0:
                                a=p['ecoinvent alias']
                                supplies.append(dict(name=a['name'],product=a['reference product'],location='NL',
                                    unit='kilogram',type='technosphere',amount=q))
                                if not any(d['name']==a['name'] and d.get('location')=='NL' and d.get('reference product')==a['reference product'] for d in db):
                                    db.append(activity(a['name'],a['reference product']))
                        total=sum(e['amount'] for e in supplies)
                        for e in supplies:e['amount']/=total
                        db.append(activity(m['name'],m['reference product'],supplies))
                    products={d['name']:d.get('reference product') for d in db}
                    for d in db:
                        for e in d['exchanges']:
                            if e['type']=='biosphere':
                                e['input']=('biosphere','original-code')
                            else:
                                e['product']=e.pop('reference product',None) or products.get(e['name'])
                                if e['name']=='market for methanol' and d['name']!='methanol, imported from EU (por)':
                                    e.update(name='market for methanol (por)',product='methanol',location='NL')
                                if e['name'] in ('syngas, CO component, supply placeholder (por)',
                                                 'syngas, CO2 component, supply placeholder (por)'):
                                    component='CO2' if 'CO2' in e['name'] else 'CO'
                                    e.update(name=f'market for syngas, {component} component (por)',
                                             product=f'syngas, {component} component (por)',location='NL')
                    changes,report=plan(db,spec)
                    if year==2050:
                        self.assertGreater(len(changes),0)
                    for row in report['activities']:
                        if row.get('process')=='methanol_syngas':
                            self.assertEqual(row['status'],'balance_review_required')
                            self.assertAlmostEqual(row['residual_fraction'],.08099289215728518)
                        if row['status']=='split':
                            self.assertGreater(row['origin_fractions']['dac'],0)
                            self.assertAlmostEqual(row['total_CO2'],row['fossil_label_CO2']+row['nonfossil_CO2'])
                            if row['process']=='ethylene_oxide':
                                self.assertGreaterEqual(row['residual_fraction'],-.005)
                            else:
                                self.assertGreaterEqual(row['residual_kgC_per_kg_methanol'],0)
                    if year==2050:
                        # Diagnostic only: review even the inactive modes without
                        # changing their real scenario mapping or production.
                        diagnostic=deepcopy(spec)
                        reviewed={d['name'] for d in db if any(p['name_fragment'] in d['name']
                                  for p in spec['policy']['processes'].values())}
                        diagnostic['active'].update(reviewed)
                        diagnostic['declared'].update(reviewed)
                        _,all_modes=plan(db,diagnostic)
                        residuals={r['process']:r['residual_fraction'] for r in all_modes['activities']
                                   if r.get('process') in ('MTO_1','MTO_2','MTA_1','MTA_2')}
                        self.assertEqual(set(residuals),{'MTO_1','MTO_2','MTA_1','MTA_2'})
                        for group,residual in [('MTO_1',.016098096997303355),('MTO_2',.011112839986722292),
                                               ('MTA_1',.030292938000093774),('MTA_2',.02073289782828384)]:
                            self.assertAlmostEqual(residuals[group],residual,places=8)

    def test_real_native_store_fork_certification_and_repeat_run(self):
        from premise.inventory_store import create_inventory_store
        db,spec=fixture()
        for ds in db:
            ds['exchanges'].insert(0,dict(type='production',name=ds['name'],
                product=ds['reference product'],location='NL',unit='kilogram',amount=1))
        source=create_inventory_store(db,backend='compact',scenario_identity='carbon-test')
        scene={'_inventory_store':source,'applied functions':['external'],'year':2050}
        builder=SimpleNamespace(scenarios=[scene],version='3.12',system_model='cutoff',
            _validation_reports={},_scenario_identity=lambda s:'carbon-test',
            _validation_source_fingerprint=lambda:'source',_validation_iam_fingerprint=lambda s:'iam')
        with tempfile.TemporaryDirectory() as directory,patch.object(adapter,'load_carbon_spec',return_value=spec):
            report=adapter.apply_por_carbon_emissions(builder,report_dir=directory,biosphere_codes=CODES,verbose=False)
            self.assertEqual(report[0]['changed_activities'],1)
            self.assertIsNot(scene['_inventory_store'],source)
            self.assertEqual(len(source.activity(list(source.iter_activity_ids())[-1]).to_dict()['exchanges']),3)
            self.assertEqual(len(scene['_inventory_store'].activity(list(source.iter_activity_ids())[-1]).to_dict()['exchanges']),4)
            self.assertIn('_validation_report',scene)
            repeated=adapter.apply_por_carbon_emissions(builder,report_dir=directory,biosphere_codes=CODES,verbose=False)
            self.assertEqual(repeated[0]['changed_activities'],0)

    def test_native_checkpoint_and_mapping_survive_split(self):
        import gc
        from premise.inventory_store import create_inventory_store
        from premise_compat import open_inventory_store
        import por_supplier_linking as linking
        db,spec=fixture()
        for ds in db:
            ds['exchanges'].insert(0,dict(type='production',name=ds['name'],product=ds['reference product'],
                                         location='NL',unit='kilogram',amount=1))
        with tempfile.TemporaryDirectory() as directory,patch.object(adapter,'load_carbon_spec',return_value=spec):
            source=create_inventory_store(db,backend='compact',scenario_identity='carbon-test')
            checkpoint=source.checkpoint(Path(directory)/'source.inventory-store')
            scene={'_inventory_checkpoint':checkpoint,'applied functions':['external'],'year':2050,
                   'mapping':{'external':{'MTO':[db[-1]]}}}
            builder=SimpleNamespace(scenarios=[scene],version='3.12',system_model='cutoff',
                _validation_reports={},_scenario_identity=lambda s:'carbon-test',
                _validation_source_fingerprint=lambda:'source',_validation_iam_fingerprint=lambda s:'iam')
            report=adapter.apply_por_carbon_emissions(builder,report_dir=directory,biosphere_codes=CODES,verbose=False)
            self.assertEqual(report[0]['changed_activities'],1)
            self.assertNotEqual(scene['_inventory_checkpoint'],checkpoint)
            self.assertNotIn('_inventory_store',scene)
            reopened=open_inventory_store(scene,linking._inventory_store_api())
            ds=reopened.activity(list(reopened.iter_activity_ids())[-1]).to_dict()
            self.assertTrue(ds[adapter.MARKER])
            self.assertEqual(len(ds['exchanges']),4)
            repeat=adapter.apply_por_carbon_emissions(builder,report_dir=directory,biosphere_codes=CODES,verbose=False)
            self.assertEqual(repeat[0]['changed_activities'],0)
            del reopened,ds,builder,scene,source
            gc.collect()

    def test_legacy_cache_roundtrip_uses_a_new_cache(self):
        import pickle
        db,spec=fixture()
        with tempfile.TemporaryDirectory() as directory,patch.object(adapter,'load_carbon_spec',return_value=spec):
            original=Path(directory)/'original.pickle'; original.write_bytes(pickle.dumps(db))
            scene={'database filepath':str(original),'applied functions':['external']}
            def load(**kwargs):
                working=kwargs['scenario'];working['database']=pickle.loads(Path(working['database filepath']).read_bytes());return working
            def dump(working):
                new=Path(directory)/'adapted.pickle';new.write_bytes(pickle.dumps(working.pop('database')))
                working['database filepath']=str(new)
            builder=SimpleNamespace(scenarios=[scene],version='3.12')
            adapter.apply_por_carbon_emissions(builder,report_dir=directory,load_database_func=load,dump_database_func=dump,
                                               biosphere_codes=CODES,verbose=False)
            self.assertNotEqual(scene['database filepath'],str(original))
            self.assertEqual(pickle.loads(original.read_bytes()),db)
            self.assertTrue(pickle.loads(Path(scene['database filepath']).read_bytes())[-1][adapter.MARKER])

    def test_both_export_hooks_run_after_update_and_before_export(self):
        for stage in ("brightway","pathways"):
            db,spec=fixture(); events=[]
            scene={"database":db,"applied functions":["external"],"year":2050}
            builder=SimpleNamespace(scenarios=[scene],update=lambda:events.append("linked"),
                write_db_to_brightway=lambda:events.append("export"),version="3.12")
            with tempfile.TemporaryDirectory() as directory, patch.object(adapter,"load_carbon_spec",return_value=spec):
                self.assertTrue(adapter.attach_por_carbon_emissions(builder,stage=stage,report_dir=directory,biosphere_codes=CODES,verbose=False))
                self.assertFalse(adapter.attach_por_carbon_emissions(builder))
                builder.update()
                self.assertIn(adapter.MARKER,scene)
                self.assertEqual(len(builder.por_carbon_emission_reports),1)
                builder.write_db_to_brightway()
                self.assertEqual(events,["linked","export"])
                report=json.loads(next(Path(directory).glob('*.json')).read_text())
                self.assertEqual(report["changed_activities"],1)

    def test_failure_does_not_publish_earlier_scenario(self):
        db,spec=fixture();bad=deepcopy(db);bad[-1]["exchanges"][-1]["amount"]*=10
        scenes=[dict(database=d,**{"applied functions":["external"]}) for d in (db,bad)]
        originals=deepcopy(scenes)
        builder=SimpleNamespace(scenarios=scenes,version="3.12")
        with tempfile.TemporaryDirectory() as directory,patch.object(adapter,"load_carbon_spec",return_value=spec):
            with self.assertRaisesRegex(ValueError,"exceeds input"):
                adapter.apply_por_carbon_emissions(builder,report_dir=directory,biosphere_codes=CODES,verbose=False)
        self.assertEqual(scenes,originals)

    def test_steel_scope_is_a_noop_without_reading_por_files(self):
        scene={"external scenarios":[]}
        self.assertIsNone(adapter.load_carbon_spec(scene,Path("missing-policy")))
        self.assertEqual(adapter.apply_por_carbon_emissions(SimpleNamespace(scenarios=[scene])),[])

    def test_export_refuses_missing_carbon_preflight(self):
        package=SimpleNamespace(descriptor={'name':'itom_por'})
        builder=SimpleNamespace(scenarios=[{'external scenarios':[{'data':package}]}])
        with self.assertRaisesRegex(ValueError,'preflight did not run'):
            adapter.verify_carbon_reports(builder)

    def test_source_change_during_native_update_is_rejected(self):
        db,spec=fixture()
        builder=SimpleNamespace(scenarios=[dict(database=db)],update=lambda:None)
        altered=dict(spec,signature='changed')
        with patch.object(adapter,'load_carbon_spec',side_effect=[spec,altered]):
            adapter.attach_por_carbon_emissions(builder,biosphere_codes=CODES)
            with self.assertRaisesRegex(ValueError,'changed during premise'):
                builder.update()

    def test_zip_provenance_written_before_archive_builder(self):
        reports=[dict(case='CL',year=2050,changed_activities=1)]
        with tempfile.TemporaryDirectory() as directory:
            package=SimpleNamespace(datapackage=SimpleNamespace(scenarios=[],por_carbon_emission_reports=reports))
            def build():
                payload=json.loads((Path(directory)/'pathways_temp/por_carbon_emissions.json').read_text())
                self.assertEqual(payload['scenario_reports'],reports)
                return 'archive'
            package._build_datapackage=build
            with patch.object(adapter.Path,'cwd',return_value=Path(directory)):
                self.assertTrue(adapter.attach_carbon_package_provenance(package))
                self.assertFalse(adapter.attach_carbon_package_provenance(package))
                self.assertEqual(package._build_datapackage(),'archive')

    def test_real_case_year_bindings_and_intermediate_year(self):
        from datapackage import Package
        p=Package(str(ROOT/'datapackages/datapackage_itom_por.json'),base_path=str(ROOT))
        specs=[adapter.load_carbon_spec(dict(year=y,**{"external scenarios":[dict(scenario=case,data=p)]}))
               for case,y in [("CL",2050),("OCE",2050),("CL",2047)]]
        dac="methanol, DAC-based, imported outside EU (por)"
        self.assertAlmostEqual(specs[0]["expected_methanol"][dac],.71868297494)
        self.assertAlmostEqual(specs[1]["expected_methanol"][dac],.92821,places=4)
        self.assertEqual(len({s["signature"] for s in specs}),3)

    def test_notebook_order_keeps_carbon_step_before_pathways_preflight(self):
        nb=json.loads((ROOT/'1_export_packages.ipynb').read_text(encoding='utf-8'))
        cells=[''.join(c.get('source',[])) for c in nb['cells'] if c['cell_type']=='code']
        pathways=next(s for s in cells if 'if create_db_package:' in s and 'attach_por_pathways_export' in s)
        self.assertLess(pathways.index('attach_por_supplier_linking(ndb.datapackage'),pathways.index('attach_por_carbon_emissions(ndb.datapackage'))
        self.assertLess(pathways.index('attach_por_carbon_emissions(ndb.datapackage'),pathways.index('attach_por_pathways_export(ndb'))
        bw=next(s for s in cells if 'if create_dbs:' in s and 'attach_por_supplier_linking(ndb,' in s)
        self.assertLess(bw.index('attach_por_supplier_linking(ndb,'),bw.index('attach_por_carbon_emissions(ndb,'))
        self.assertLess(bw.index('attach_por_carbon_emissions(ndb,'),bw.index('ndb.update()'))


if __name__ == '__main__':
    unittest.main()
