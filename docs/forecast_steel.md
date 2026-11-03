# FORECAST-sites steel production

This is a separate alternative to the ITOM steel test. It replaces selected
national steel market mixes using FORECAST-sites production volumes, then builds
an RER market from the national markets. It does not combine FORECAST steel and
ITOM steel transformations in the same scenario.

## Run it

1. Keep the raw workbook at
   `scenario_data/scenario_data_forecast_steel_production.xlsx`. The converter
   reads it without editing it.
2. Set `ASSESSMENT_SCOPE = "forecast_steel"` in **both** main notebooks.
3. In notebook 1, leave `FORECAST_STEEL_SCENARIOS = None` to export both supplied
   scenarios, `1` and `2`. To select one, use `["1"]` or `["2"]`. The converter
   exports explicit text IDs, `forecast_steel_1` and `forecast_steel_2`, and the
   notebook accepts either source IDs or exported IDs. These are identifiers,
   not policy names. Text IDs prevent premise's pandas reader from inferring
   integers that fail to match its required string scenario selection.
4. Run the normal notebook 1 workflow. It prepares the FORECAST IAMC resource
   before loading the package. The visible premise calls, export hooks and
   non-ferrous policy remain in place. The shared PoR workbook is not staged.
5. In notebook 2, use `STEEL_VIEW = "market"` and select `ANALYSIS_REGIONS =
   ["RER"]` or a supplied country. Set `PACKAGE_FILE = None` to use the new
   scope's package name. Existing explicit package, year and region overrides
   still take precedence. `STEEL_VIEW = "routes"` exposes the six route outputs.

The package basename is `remind-transience_forecast_steel`. Native IAM steel
updates run first. This external module then changes the selected market mixes.
The separate ITOM and PoR workflows retain their own inputs and mappings.

Input preparation can also run independently:

```powershell
python 0_convert_FORECAST_steel_to_IAMC.py
```

It writes:

- `scenario_data/scenario_data_forecast_steel_iamc.csv`: premise IAMC input.
- `scenario_data/scenario_data_forecast_steel_iamc_country_shares.csv`: national
  production and RER weights for each scenario and year.
- `scenario_data/scenario_data_forecast_steel_iamc.json`: source hash, coverage
  and assumptions.
- `configuration_file/config_forecast_steel.yaml`: generated premise mapping.
- `datapackages/datapackage_forecast_steel.json`: generated import descriptor.

Edit source-route definitions in `configuration_file/forecast_steel_routes.yaml`,
then rerun the converter. Its generated mapping and descriptor are overwritten.
Five routes link directly to existing native inventories, including BOF and
NG-DRI with CCS. Only scrap-EAF CCS uses the unresolved custom wrapper in
`inventories/lci-forecast_steel.xlsx`. A custom wrapper purchases one kilogram of its
native producer rather than copying all exchanges. Route definitions declare
`exists_in_original_database` so regenerated configuration preserves the choice
between a native provider and a custom wrapper.

## Production and geography

The supplied workbook has 108 route rows, 24 countries, two scenarios and six
years, 2025–2050. Conversion yields 398 IAMC rows. Country names/codes resolve
to ISO2, the source model becomes `forecast`, units become `Mt/yr`, and scenario
IDs become `forecast_steel_1` and `forecast_steel_2`. The descriptor and audit
preserve the source-to-export ID mapping. Unknown routes, duplicate identities, negative values,
invalid units and countries outside the reviewed European boundary fail.

The RER market consumes the same-grade national FORECAST markets:

```text
country weight = country steel output / total supplied European steel output

RER steel market
    national DE steel market, weighted by DE production
    national FR steel market, weighted by FR production
    other supplied national steel markets, weighted in the same way
```

This retains each country's route mix and native regional background. The
existing post-update country-market hook applies the generic
`steel_country_market_mix` descriptor, before either Brightway or Pathways
export. Historical function names still mention ITOM, but their recipe engine
supports both contracts. Legacy ITOM descriptors remain supported.

RER covers the countries **present in this workbook**, including GB, CH and NO.
It is not EU27-only or complete native ecoinvent RER coverage. Missing countries
are not estimated. RER and national totals are alternative views of the same
production: do not add them together as separate demands. World market creation
is excluded. Global consumers are excluded from replacements, and consumers in
unmodelled countries retain their existing links. Between source years, the
country-market hook interpolates production volumes first and normalises them
afterwards. Native premise handles the pathway time series.

## Route inventories and limitations

| FORECAST route | Current inventory |
| --- | --- |
| BF-BOF | Native blast furnace/basic oxygen furnace steel |
| Secondary EAF | Native European scrap-EAF steel |
| DRI-NG-EAF | Native natural-gas DRI/EAF steel |
| BF-BOF-CCS | Native BF/BOF with carbon capture and storage |
| DRI-NG-EAF-CCS | Native NG-DRI/EAF with carbon capture and storage |
| Secondary EAF-CCS | Separate wrapper using the uncaptured scrap-EAF proxy |

**BOF and NG-DRI CCS now inherit the native CCS inventory assumptions.** They
therefore represent capture using the existing inventories, rather than the
uncaptured route. FORECAST supplies production volumes but no route-specific
captured/residual CO2 coefficients, capture-energy requirements or storage
details. Native CCS inventories are therefore technology proxies, pending
comparison with FORECAST's own coefficients. No additional CO2 subtraction or
storage credit is added on top of those recipes.

**Scrap-EAF CCS remains unresolved.** Its custom wrapper retains the uncaptured
European scrap-EAF recipe, without an inferred capture rate or energy penalty.
This remaining gap must be disclosed when comparing scenarios.
No hydrogen DRI route is invented because none appears in this workbook.

The current European scrap inventory represents a scrap-only ferrous charge,
with native yield losses, alloys, emissions and upstream burdens retained. A
route-output share is not a finished-product recycled-content measurement.
Low-alloyed inventories also supply unalloyed demand as an explicit grade proxy.
The workbook supplies production volumes, not route-specific energy/material
coefficients, plant utilisation or trade matrices.

**Sparse blanks are an explicit source interpretation.** The source contains
160 blank numeric cells around route start-up/retirement and CCS switching.
`source_blank_policy: zero_inactive_route` interprets these as zero production.
Absent route rows within a supplied country also mean zero. This convention
needs confirmation from FORECAST-sites. It never fills a missing country.
To require complete numeric input instead, use `--strict-blanks` or change the
YAML policy to `error`. The converter then rejects the supplied sparse workbook
until the missing cells are resolved. Arbitrary text and infinity always fail.

Before interpreting CCS scenario differences as FORECAST-specific findings, request capture rates and
energy by route/year, residual and gross process emissions, CO2 transport and
permanent storage assumptions, and confirmation of the blank-cell convention.
For a stronger regional assessment, request production/consumption trade flows
and steel grade information alongside the country production data.
