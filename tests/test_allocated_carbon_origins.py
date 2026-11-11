"""Synthetic allocation-independent carbon origins; no private inputs."""
from copy import deepcopy
import unittest
import yaml
import por_carbon_emissions as a


def fixture(technology="MTA", mode=1, allocation="energy"):
    policy=yaml.safe_load(a.POLICY.read_text())
    def ds(name, product, unit="kilogram", exchanges=()):
        return dict(name=name, **{"reference product":product}, unit=unit, location="NL", exchanges=list(exchanges))
    def feed(d, q):
        return dict(name=d['name'],product=d['reference product'],unit=d['unit'],location='NL',type='technosphere',amount=q)
    dac=ds('methanol, DAC-based, imported outside EU (por)','methanol')
    unknown=ds('unreviewed methanol','methanol')
    market=ds('market for methanol (por)','methanol',exchanges=[feed(dac,.7),feed(unknown,.3)])
    core=('MTO with loop' if technology=='MTO' else 'MTA retrofit')+f', mode {mode}, stage-based, {allocation} allocation (por)'
    outputs=[]
    for name,product,unit,scale in [('ethylene production','ethylene','kilogram',2.),('steam, co-product of','heat, from steam','megajoule',.03),('electricity, co-product of','electricity','kilowatt hour',.2),('hydrogen in cracker off-gas','hydrogen','kilogram',.8),('methane','methane','kilogram',1.2)]:
        sep=' ' if 'co-product of' in name else ', '
        output=ds(name+sep+core,product,unit,[feed(market,scale),dict(name='Carbon dioxide, fossil',amount=.1*scale,type='biosphere',unit='kilogram',categories=('air',),input=('bio','fossil'))])
        outputs.append(output)
    spec=dict(policy=policy,declared={outputs[0]['name']},active={outputs[0]['name']},case='CL',year=2050,signature='synthetic',source_sha256={},expected_methanol={dac['name']:.7,unknown['name']:.3})
    return [dac,unknown,market,*outputs],spec


def plan(db,spec):
    return a.plan_carbon_emissions(db,lambda d:d,spec,{('Carbon dioxide, non-fossil','air','unspecified','kilogram'):'nonfossil'})


class AllocatedOrigins(unittest.TestCase):
    def test_nonfinite_feed_is_not_mistaken_for_zero_allocation(self):
        db,spec=fixture();db[-1]['exchanges'][0]['amount']=float('nan');db[-1]['exchanges'][1]['amount']=0
        with self.assertRaisesRegex(ValueError,'Non-finite allocated carbon'):plan(db,spec)

    def test_all_families_units_outputs_and_downstream_origins(self):
        for tech in ('MTO','MTA'):
            for mode in (1,2):
                for allocation in ('energy','economic'):
                    with self.subTest(tech=tech,mode=mode,allocation=allocation):
                        db,spec=fixture(tech,mode,allocation);before=deepcopy(db)
                        changes,report=plan(db,spec)
                        self.assertEqual(len(changes),5)
                        self.assertEqual(db,before)
                        for row in report['activities']:
                            self.assertAlmostEqual(row['origin_fractions']['dac'],.7)
                            self.assertAlmostEqual(row['origin_fractions']['unknown'],.3)
                            self.assertAlmostEqual(row['total_CO2'],row['fossil_label_CO2']+row['nonfossil_CO2'])
                            self.assertEqual(row['balance_status'],'documented_externally_not_reconstructed')
                        supply=a.CarbonSupply(db,lambda d:d,spec['policy'])
                        self.assertAlmostEqual(supply.origins(db[3])[2],.7)

    def test_same_fraction_as_synthetic_mass_recipe(self):
        db,spec=fixture('MTO');spec['policy']['processes']['synthetic_mass']=dict(name_fragment='synthetic mass',yields={'ethylene':.35})
        mass=deepcopy(db[3]);mass['name']='ethylene production, synthetic mass'
        mass['exchanges'][0]['amount']=1/.35
        from carbon_accounting import _itom_formula_fraction as c
        mass['exchanges'][1]['amount']=(c('CH4O')-.35*c('C2H4'))/c('CO2')/.35
        db.append(mass);spec['active'].add(mass['name']);spec['declared'].add(mass['name'])
        _,report=plan(db,spec)
        self.assertTrue(all(abs(r['origin_fractions']['dac']-.7)<1e-12 for r in report['activities']))

    def test_inconsistent_carbon_proportions_fail(self):
        db,spec=fixture();db[-1]['exchanges'].append(dict(name=db[1]['name'],product='methanol',unit='kilogram',location='NL',type='technosphere',amount=1.))
        with self.assertRaisesRegex(ValueError,'Inconsistent input carbon origins'):plan(db,spec)

    def test_zero_allocation_and_zero_emissions_are_noops(self):
        db,spec=fixture();db[-1]['exchanges']=[];db[-2]['exchanges']=db[-2]['exchanges'][:1]
        changes,report=plan(db,spec)
        self.assertEqual(len(changes),3)
        self.assertEqual(report['activities'][-1]['status'],'zero_allocated_carbon')
        self.assertEqual(report['activities'][-2]['status'],'no_direct_CO2')

    def test_missing_inputs_and_auxiliary_fuel_fail(self):
        db,spec=fixture();db[-1]['exchanges']=db[-1]['exchanges'][1:]
        with self.assertRaisesRegex(ValueError,'without allocated carbon'):plan(db,spec)
        db,spec=fixture();db[-1]['exchanges'].append(dict(name='market for natural gas',type='technosphere',amount=1,unit='cubic meter'))
        with self.assertRaisesRegex(ValueError,'auxiliary fuel'):plan(db,spec)

    def test_storage_credits_idempotence_and_inactive_family(self):
        db,spec=fixture();extra=[dict(name='carbon dioxide compression, transport and storage',amount=.2,type='technosphere',product='storage',unit='kilogram'),dict(name='heat credit',amount=-2,type='technosphere',product='heat',unit='megajoule')]
        for d in db[3:]:d['exchanges'].extend(deepcopy(extra))
        changes,_=plan(db,spec)
        for d in changes.values():
            for e in extra:self.assertIn(e,d['exchanges'])
        updated=[changes.get(a._key(d),d) for d in db];again,report=plan(updated,spec)
        self.assertFalse(again);self.assertTrue(all(r['status']=='already_applied' for r in report['activities']))
        spec['active']=set();again,report=plan(db,spec);self.assertFalse(again)
        self.assertTrue(all(r['status']=='inactive_pathway' for r in report['activities']))

    def test_foreign_and_unreviewed_names_not_broadened(self):
        db,spec=fixture();other=deepcopy(db[-1]);other['location']='DE';db.append(other)
        old=deepcopy(db[-1]);old['location']='NL';old['name']=old['name'].replace('stage-based, energy','legacy, energy');db.append(old)
        changes,_=plan(db,spec);self.assertNotIn(a._key(other),changes);self.assertNotIn(a._key(old),changes)
