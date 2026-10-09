# LT FT ethylene and propylene inventories

## Implemented change

The two `FT_distillation_LT_default` pathways now select the following NL unit-process inventories in `inventories/lci-itom_por.xlsx`:

- `ethylene production, LT FT distillation, mode 1, natural gas proxy (por)`
- `propylene production, LT FT distillation, mode 1, natural gas proxy (por)`

`configuration_file/config_itom_por.yaml` retains the original pathway keys and IAMC variable names, but sets `exists in original database: False` and points to these custom activities. The two obsolete high-temperature coal-FT regionalization entries have been removed. The new activities already have location NL, like the other imported PoR inventories.

**Natural-gas upstream supply is a provisional modeling assumption explicitly approved by the user on 8 October 2026. It is not identified by ITOM, and "LT" alone does not establish the feedstock.** This is a more appropriate process-family proxy, not a fully ITOM-derived or measured PoR plant inventory.

## What the ITOM files establish

Sources:

- `scenario_data/TRANSIENCE-WP8_ITOM-petchem_data_CL-scenario_2026-04-15.xlsx`
- `scenario_data/TRANSIENCE-WP8_ITOM-petchem_data_OCE-scenario_2026-08-19.xlsx`

Both files have the same `output_factors` for `West`, technology `FT_distillation_LT_default`, mode 1, in every modeled year:

| Output | ITOM coefficient | Share within the C2/C3 group |
| --- | ---: | ---: |
| Ethylene | 0.01000 | 11.101243% |
| Propylene | 0.02400 | 26.642984% |
| Ethane | 0.03600 | 39.964476% |
| Propane | 0.02008 | 22.291297% |
| FT naphtha | 0.12300 | Not part of the C2/C3 group |

The C2/C3 coefficient sum is **0.09008**. The sum of all five reported outputs is 0.21308. Neither sum is a complete FT material balance: fuel/wax products outside the chemical-sector scope are not fully specified, and no physical FT feed is reported. Do not interpret 0.21308 as total saleable-product yield or infer the missing 0.78692 as an observed fuel output.

There are **no records for this technology in `input_factors` or `use_volume`** in either workbook. No FT electricity, steam, flue CO2, captured CO2 or syngas input is supplied. Missing records are not measured zeros.

Only mode 1 is active, in 2040, 2045 and 2050. The five-year production totals are 170 kt ethylene and 408 kt propylene. Dividing production totals, not coefficients, by five gives **34 kt/yr ethylene and 81.6 kt/yr propylene** in each active year, in both CL and OCE. The existing IAMC rows already contain these quantities; no converter or scenario CSV change was needed.

## Background recipe and allocation

The locally verified reference provider is:

`liquefied petroleum gas production, synthetic, Fischer Tropsch process, from natural gas, energy allocation`, reference product `liquefied petroleum gas, synthetic`, location RER, in `ecoinvent_312_reference` (3.12 cutoff/premise reference background), code `3513bc55234f48d9af9b3d4c9d01a7b9`.

Its documentation cites [van den Oever, Costa and Messagie (2023)](https://doi.org/10.1016/j.apenergy.2023.120834), which describes a cobalt FT process with LPG and liquid/wax co-products. This recipe is used as a **shared FT light-gas burden proxy**, not as an assertion that ordinary paraffinic LPG is converted into ethylene without reaction or separation. The source recipe is not a validated match to ITOM's particular low-temperature plant.

The source already uses energy allocation against its other FT product groups (diesel, wax and C5-C10 products). That upstream allocation is retained. Within the recovered C2/C3 group, use mass shares `coefficient / 0.09008`.

For a shared proxy exchange `b` expressed per kg light-gas group, the illustrative group burden on the ITOM activity basis is `0.09008 * b`. A product with coefficient `y` receives share `y / 0.09008`. Its allocated exchange per kg product is:

```text
(0.09008 * b) * (y / 0.09008) / y = b
```

This normalization explains why the ethylene and propylene inventories have identical exchanges per kg under this simplified group-mass split. It does **not** recover actual total-plant inputs from ITOM. Do not divide `b` by 0.09008, 0.01, 0.024 or the source's 11% energy-allocation key; doing so would apply allocation/yield adjustments twice. Do not mass-allocate this LPG-specific recipe again across FT naphtha, fuel or wax.

Each new product inventory has these exchanges per kg product:

| Exchange | Amount | Unit | Source and treatment |
| --- | ---: | --- | --- |
| `syngas production, from natural gas`, RER | 2.28 | kilogram | Reference proxy; upstream syngas manufacture included once |
| `market for electricity, low voltage`, NL | 0.00959662680412371 | kilowatt hour | Proxy demand retained; operating supply changed from RER to NL |
| `gas-to-liquid plant construction`, GLO | 6.7E-12 | unit | Reference infrastructure allocation retained |
| `Carbon dioxide, fossil`, air | 0.13 | kilogram | Proxy operational FT emission, not an ITOM measurement |
| `Water`, water | 0.00011830490434782582 | cubic meter | Proxy water release, not freshwater demand |
| `Heat, waste`, air | 0.4005762550724638 | megajoule | Proxy heat release, not purchased heat |

All six non-production exchanges are copied explicitly from the light-gas recipe, except the electricity-provider regionalization. Neither the full source FT activity nor its high-temperature coal counterpart is added as another exchange. This avoids repeating the FT process alongside its explicit inputs.

The fossil CO2 exchange is consistent with the chosen natural-gas proxy. No captured-CO2 service, negative CO2 flow, fuel-use combustion or CCS credit is added: ITOM reports none for this route. Do not apply the MTO captured-CO2 accounting assumption automatically to FT.

## Limitations and follow-up

Priority clarifications are actual upstream feedstock/origin; total FT input and utility coefficients; and incremental C2/C3 olefin/paraffin separation energy, product purity and recovery losses. The small source electricity demand is not a verified polymer-grade distillation requirement. These missing details can make the screening inventory underestimate recovery burdens, even though it is a better process-family match than the old coal-HTFT alias.

The FT naphtha supplier in the cracker inventories remains the separately documented fossil-naphtha placeholder. This edit changes **only the two specified FT gas pathways**, not the naphtha or ethane/propane markets. An integrated correction across all FT product groups requires a full product slate and consistent upstream allocation, rather than forcing the five reported chemical coefficients to sum to one.

Future background changes depend on premise transformations and supplier linking. An explicit natural-gas syngas link is not a promise of a renewable future FT feed. Electricity keeps the low-voltage unit/interface; there is no unsupported voltage conversion or kWh/MJ adjustment.

## Validation and rerun

Validation covers both source workbooks, constant coefficients, mode 1, annualized scenario quantities, both YAML aliases, market membership, the joint allocation calculation, all six proxy exchanges, supplier identity/units, positive fossil emissions and no duplicate source-process/CCS demand. Brightway Excel parsing was checked in an isolated project without writing the user's LCA databases. All 277 original formulas, their cached values, existing cells/styles and annotation package parts were preserved when appending the two blocks.

Run the regression checks:

```powershell
python -m unittest discover -s tests -p test_por_ft_lt.py -v
python -m unittest discover -s tests -p test_por_mode_aware.py -v
```

Then rebuild the external datapackage and prospective databases with `1_export_packages.ipynb`, and recalculate `2_calc_impacts.ipynb`. Existing ZIP packages, Brightway databases and figures still contain the previous inventories until rebuilt. The full prospective LCA is not rerun by this inventory edit.
