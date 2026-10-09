# Renewable power for imported DAC methanol

Updated 7 October 2026 following the user's request. The active inventories remain in `inventories/lci-itom_por.xlsx` and the existing `methanol_dac_terminal_row` YAML pathway still selects `methanol, DAC-based, imported outside EU (por)`.

## Supply assumption

The route retains Morocco (`MA`) as a provisional production origin. Its operating electricity now uses **50% solar photovoltaic and 50% onshore wind**, measured as kWh shares. These are fixed user-selected modeling assumptions for CL and OCE and all modeled years, not observed Moroccan grid shares or sourcing confirmed by ITOM.

Two auxiliary electricity-supply activities in the same workbook provide the existing low- and medium-voltage interfaces:

- `electricity supply, 50% solar and 50% wind, low voltage, for DAC methanol (por)`;
- `electricity supply, 50% solar and 50% wind, medium voltage, for DAC methanol (por)`.

Each consumes exactly 0.5 kWh from `electricity production, photovoltaic, commercial`, `MA`, product `electricity, low voltage`, and 0.5 kWh from `electricity production, wind, >3MW turbine, onshore`, `RoW`, product `electricity, high voltage`. Both provider identities were found in the local premise reference inventory. The Moroccan PV inventory incorporates its country-specific yield. The RoW wind inventory is a provisional proxy for local onshore generation; it does not model importing electricity from another country.

The generator inventories retain upstream construction, materials and maintenance burdens. Renewable operating electricity therefore still has life-cycle impacts. The 50/50 assumption applies to this route's operating power, rather than replacing every electricity mix in upstream equipment and material manufacturing.

For this screening implementation, the supply activities assume lossless voltage conversion between the generators' native voltage products and the consuming process interfaces. Additional transformers, transmission, storage, backup and balancing are not modeled. The annual energy shares do not demonstrate that the synthesis plant can run continuously on variable renewables; hourly supply and utilization would need a separate assessment. The fixed foreground supply shares remain 50/50 even where premise changes their upstream background inventories.

## Linked process changes

| Process | Exchange changed | Amount retained | New supplier |
|---|---|---:|---|
| Methanol synthesis in MA | Direct low-voltage electricity | 0.3028953229398664 kWh/kg methanol | Dedicated low-voltage renewable supply |
| DAC in MA | Capture operation electricity | 0.5 kWh/kg CO2 | Dedicated medium-voltage renewable supply |
| DAC in MA | Heat-pump electricity | 0.5172413793103449 kWh/kg CO2 | Dedicated medium-voltage renewable supply |
| Steam production in MA | Auxiliary electricity | 0.008080000057816505 kWh/MJ steam | Dedicated medium-voltage renewable supply |
| Methanol synthesis in MA | Hydrogen | 0.1389755011135858 kg/kg methanol | New dedicated renewable PEM electrolysis supplier |

The generic hydrogen-market exchange has been replaced because changing only direct process electricity would leave its hydrogen supply unconstrained. The new activity is `hydrogen production, PEM electrolysis, 50% solar and 50% wind, for DAC methanol (por)`, location `MA`, reference product `hydrogen, gaseous, 30 bar`.

This activity copies the local premise template `hydrogen production, gaseous, 30 bar, from PEM electrolysis, from grid electricity`, `RER`, and replaces its electricity supplier with the dedicated renewable mix. It retains the template's **54 kWh/kg H2**, electrolyzer stack and balance of plant, 14 kg deionized water/kg H2, land occupation/transformation, oxygen and end-of-life exchanges. Its original source is Gerloff (2021), as recorded in that template: https://doi.org/10.1016/j.est.2021.102759. The PEM technology, 30 bar pressure and RER equipment/water inputs remain documented proxies. No additional pressure adjustment or oxygen credit is added.

For each kilogram of imported methanol, the explicit operating-electricity demand is approximately 9.55 kWh: direct synthesis electricity, 54 kWh/kg H2 times the hydrogen input, both DAC electricity exchanges times the CO2 input, and steam auxiliaries times the steam input. This excludes electricity within upstream materials and infrastructure. Existing process amounts are unchanged when their power supplier is replaced.

## Retained assumptions and outstanding checks

The 3.509803027737619 MJ/kg methanol steam input and its natural-gas heat supplier remain unchanged. This revision provides renewable power, not a fully renewable heat-and-power system. The old DAC activity name containing `grid electricity` is retained for compatibility; its comment now describes the renewable operating supply.

Carbon uptake, the 0.32 kg non-fossil CO2 emission in synthesis, other material quantities, wastewater and construction coefficients are preserved. The existing methanol construction amounts (12.4568278943279 and 12.89 units/kg in the purification/synthesis chain) appear unusually large and still require an independent unit/scaling check. They were not changed as part of the requested electricity revision. Existing duplicate water/material/wastewater rows also remain for a separate inventory review. The power substitution alone does not validate the total methanol footprint.

Moroccan origin, import transport distance/mode, process utilization, hydrogen pressure needs and operational power matching remain provisional. No transport distance or new steam technology has been inferred from this change.

## Validation and use

The new auxiliary inventories link by their activity name, reference product, location and unit. No ITOM variables or additional `production pathways` entries are needed for them; their demand follows the imported methanol activity.

Regression checks confirm equal solar/wind shares, preservation of electricity quantities and carbon exchanges, explicit renewable electrolytic hydrogen, and retention of the existing steam heat boundary. The edit preserves unrelated worksheet cells, formulas and annotations, including the previous MTO revision.

Run:

```powershell
python -m unittest discover -s tests -p "test_por_*.py" -v
```

Rebuild the PoR package and prospective inventories using `1_export_packages.ipynb`, then recalculate the impact and regionalization notebooks. Existing exported databases and figures keep their previous supplier links until rebuilt. Inspect the generated prospective inventory to confirm that the two custom supply activities retain their 0.5/0.5 provider exchanges.
