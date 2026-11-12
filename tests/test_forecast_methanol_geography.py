import copy
from types import SimpleNamespace
import unittest
from forecast_methanol_geography import install_forecast_methanol_geography_fix

class GeographyTests(unittest.TestCase):
    def test_actual_relink_scopes_consumers_and_preserves_original_market(self):
        from premise.external import ExternalScenario
        original = ExternalScenario.relink_to_new_datasets
        def consumer(location):
            return {'name':'consumer', 'reference product':'product','unit':'kilogram','location':location,
                    'exchanges':[{'name':'market for methanol','product':'methanol','unit':'kilogram','location':'GLO','amount':2.,'type':'technosphere'}]}
        source={'name':'market for methanol','reference product':'methanol','unit':'kilogram','location':'GLO','exchanges':[{'name':'original producer','type':'technosphere','amount':1}]}
        locations=['DE','NL','RER','UN-EASIA','US','RoW','World']
        fake=SimpleNamespace(database=[consumer(l) for l in locations]+[source],geo=SimpleNamespace(geo=SimpleNamespace(contained=lambda key:{'DE','NL',('ecoinvent','RER')})))
        before=copy.deepcopy(fake.database)
        try:
            install_forecast_methanol_geography_fix(verbose=False)
            ExternalScenario.relink_to_new_datasets(fake,[{'name':'market for methanol','product':'methanol'}],[{'location':'RER','operator':'within'}],'market for methanol (Forecast)','methanol',1.,['DE'])
            for i,l in enumerate(locations):
                e=fake.database[i]['exchanges'][0]
                self.assertEqual(e['name'],'market for methanol (Forecast)' if l in ['DE','NL','RER'] else 'market for methanol')
                self.assertEqual(e['amount'],2.)
            self.assertEqual(fake.database[-1],before[-1])
            # Scalar native RER retains exact-match semantics.
            fake.database=copy.deepcopy(before)
            ExternalScenario.relink_to_new_datasets(fake,[{'name':'market for methanol','product':'methanol'}],[{'location':'RER'}],'market for methanol (Forecast)','methanol',1.,['DE'])
            self.assertEqual(fake.database[0],before[0])
            self.assertEqual(fake.database[2]['exchanges'][0]['name'],'market for methanol (Forecast)')
        finally:
            ExternalScenario.relink_to_new_datasets=original
