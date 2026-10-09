# Nylon 6-6 resin inventory

## Integration

On 8 October 2026, the reviewed resin inventory was appended to `inventories/lci-itom_por.xlsx` as:

`nylon 6-6 production, explicit monomers, reconstructed (por)`

Its reference product is `nylon 6-6`, unit kilogram, location NL, production amount 1. `FE_nylon` in `configuration_file/config_itom_por.yaml` now selects this activity with `exists in original database: False`.

The existing IAMC variable `Final Product|nylon_6_6|nylon_6_6_default` and CL/OCE production quantities are unchanged. No second demand variable or extra nylon volume is added.

Only the **resin** activity from the reviewed proposal is integrated. The optional injection-moulded part, Method sheet and Validation sheet are not imported into the main inventory workbook. The older aggregated `nylon 6-6 production` activity is retained unchanged for reference, but `FE_nylon` no longer selects it.

## Boundary and exchanges

The functional output is 1 kg dry, unreinforced PA66 resin at the plant gate. Explicit suppliers account for upstream monomer manufacture, utilities, transport and infrastructure. Forming, fibre reinforcement, use and end of life are excluded.

All eight technosphere exchange amounts are unchanged from the reviewed proposal:

| Supplier | Amount | Unit | Location |
| --- | ---: | --- | --- |
| adipic acid production | 0.6457317073170731 | kilogram | RER |
| hexamethylenediamine production | 0.5134676564156946 | kilogram | RER |
| market for electricity, medium voltage | 0.75 | kilowatt hour | NL |
| market for heat, from steam, in chemical industry | 6.6 | megajoule | RER |
| market for water, deionised | 1.1591993637327678 | kilogram | Europe without Switzerland |
| market for wastewater, average | -0.0013183987274655356 | cubic meter | Europe without Switzerland |
| market for transport, freight, lorry, unspecified | 0.23183987274655357 | ton kilometer | RER |
| chemical factory construction, organics | 4E-10 | unit | RER |

The monomers are equimolar, not equal-mass. In the long-chain repeat-unit limit:

```text
C6H10O4 + C6H16N2 -> C12H22N2O2 + 2 H2O
0.6457317073 kg adipic acid + 0.5134676564 kg HMDA
  -> 1 kg resin + 0.1591993637 kg reaction water
```

The 50 wt% nylon-salt assumption gives 1.1591993637 kg fresh solution water. All solution water plus reaction water is assumed condensed and treated, without recycling. The negative wastewater exchange follows the ecoinvent waste-provider sign convention and creates a treatment burden, not an avoided-emission credit.

The electricity requirement is 2.7 MJ / 3.6 = 0.75 kWh. The 6.6 MJ heat input is delivered process heat. These requirements come from the published generic polymerization model, not from measured ITOM nylon utilities. Monomer delivery assumes 200 km road transport; production suppliers, not transported monomer markets, are used to avoid counting delivery twice. The infrastructure proxy is one 100 kt/yr chemical plant over a 25-year life.

There are **no aggregate nylon biosphere exchanges** in the new activity. Adipic-acid N2O, upstream fossil CO2 and fuel/power emissions are accounted for by the suppliers. Do not copy the old aggregated nylon CO2, methane or N2O alongside these explicit inputs. Retained fossil carbon in the resin does not receive an atmospheric-uptake credit. There is one polymer product and no new co-product allocation at polymerization.

## Sources and provisional assumptions

- [EPiC Nylon 66](https://figshare.unimelb.edu.au/articles/dataset/EPiC_database_-_Nylon_66/9979853?file=25741025), detailed report file 25741025, DOI 10.26188/5da55609d0f6f: hybrid embodied-footprint benchmark, not a polymerization recipe.
- [OEKOBAUDAT PA66 part](https://www.oekobaudat.de/OEKOBAU.DAT/datasetdetail/process.xhtml?lang=en&uuid=7511c948-c441-42fb-9ff1-cfdc54e58d7e): DE 2018 finished-part A1-A3 benchmark including casting. Its impacts/primary energy/water indicators are not direct foreground exchanges.
- [Matthews et al. (2019)](https://www.nature.com/articles/s41598-019-54331-7), Supplementary Tables 7/8: generic nylon polymerization electricity, heat and supplier proxies. Table 13 provides an independent HMDA hydrogen comparison.
- [Assessment of Nylon-66 Depolymerization for Circular Economy (2025)](https://pubs.acs.org/doi/10.1021/acs.iecr.4c04411): equimolar salt chemistry and approximately 50 wt% aqueous salt preparation.
- Named suppliers and their units were verified against the locally licensed ecoinvent 3.12 cutoff reference background.

This is a **screening reconstruction**, not the original EPiC/GaBi unit inventory or measured PoR plant data. The following remain provisional:

1. European monomer production is a geographic proxy. Actual PoR suppliers and feedstock origins are unknown. Direct RER monomer links do not establish an ITOM-specific bio-adipic-acid mix.
2. Net monomer conversion is ideal. Purges, off-spec resin, end groups and minor additives are not quantified. Cooling-water make-up, nitrogen purge, stabilizers and fugitive emissions are missing, not measured zeros.
3. The steam supplier is a generic process-heat proxy. Verify pressure and the high-temperature/thermal-oil duty around 270-280 C before replacing it with low-temperature heat. No renewable power or steam assumption is introduced by this integration.
4. Fresh-water demand, condensate recycling/composition and generic wastewater treatment require plant data.
5. The existing HMDA RER supplier reports 0.0184439346 kg H2/kg HMDA, below the ideal ADN + 4 H2 requirement of approximately 0.0693928129 kg/kg. The background is retained unchanged. The proposal's minimum hydrogen-gap sensitivity adds about 0.294 kg CO2-eq/kg resin under the static reference hydrogen supply; this is not an implemented or validated HMDA correction.

The original proposal calculated about 9.02 kg CO2-eq/kg resin with the static ecoinvent 3.12 EF v3.1 background. This is not a recalculated prospective CL/OCE result. Explicit suppliers allow background changes to propagate, but do not guarantee that premise changes monomer process technology or N2O abatement.

The EPiC landing record and detailed report display different licence labels (CC BY 4.0 versus CC-BY-NC-ND 4.0). Only limited factual benchmarks informed this reconstruction; verify source and ecoinvent permissions before redistributing inventory data.

## Verification and rerun

The integrated activity has one production exchange and eight technosphere exchanges. Supplier identities/units, negative wastewater sign, stoichiometric mass/carbon balance and absence of moulding/aggregate-emission duplication were checked. Brightway Excel parsing was tested in an isolated project; the user's Brightway databases were not rewritten. Original workbook cells, styles, all 277 formulas and their cached values were preserved.

```powershell
python -m unittest discover -s tests -p test_por_nylon66.py -v
python -m unittest discover -s tests -p test_por_mode_aware.py -v
```

Rebuild the datapackage and prospective databases through `1_export_packages.ipynb`, then recalculate `2_calc_impacts.ipynb`. Existing packages, databases and figures still use their previous inventory until rebuilt. No scenario converter change is needed.
