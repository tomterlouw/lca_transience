"""Explicit geographic consumer scope for FORECAST methanol, without global patches."""
from functools import wraps
from types import FunctionType, SimpleNamespace


def install_forecast_methanol_geography_fix(*, verbose=True):
    from premise.external import ExternalScenario
    original = ExternalScenario.relink_to_new_datasets
    if getattr(original, '_forecast_geography', False):
        return

    @wraps(original)
    def scoped(self, replaces, replaces_in, new_name, new_ref, ratio, regions, **kwargs):
        within = [r for r in (replaces_in or []) if r.get('operator') == 'within']
        if not within:
            return original(self, replaces, replaces_in, new_name, new_ref, ratio, regions, **kwargs)
        if new_name not in ('market for methanol (Forecast)', 'market for methanol (SPS)') or new_ref != 'methanol':
            raise ValueError('The within operator is supported only for FORECAST methanol.')
        if len(replaces_in) != 1 or set(within[0]) != {'location', 'operator'}:
            raise ValueError('Use one location-only within selector for FORECAST methanol.')
        region = within[0]['location']
        locations = set()
        for key in self.geo.geo.contained(('ecoinvent', region)):
            if isinstance(key, str):
                locations.add(key)
            elif isinstance(key, tuple) and key[0] == 'ecoinvent':
                locations.add(key[1])
        locations.add(region)
        if not locations or locations & {'GLO', 'RoW', 'World'}:
            raise ValueError('Invalid European consumer scope.')
        namespace = dict(original.__globals__)
        ws = namespace['ws']
        def contains(field, value):
            if field == 'location' and value == region:
                return lambda dataset: dataset.get(field) in locations
            return ws.contains(field, value)
        namespace['ws'] = SimpleNamespace(**{**vars(ws), 'contains': contains})
        local = FunctionType(original.__code__, namespace, original.__name__, original.__defaults__, original.__closure__)
        local.__kwdefaults__ = original.__kwdefaults__
        return local(self, replaces, replaces_in, new_name, new_ref, ratio, regions, **kwargs)

    scoped._forecast_geography = True
    ExternalScenario.relink_to_new_datasets = scoped
    if verbose:
        print('Enabled explicit FORECAST methanol consumer geography.')
