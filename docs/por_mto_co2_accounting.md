# MTO mode-2 CO2 accounting

Updated 7 October 2026. The active inventory is `inventories/lci-itom_por.xlsx` and both MTO production pathways are selected in `configuration_file/config_itom_por.yaml`.

## Physical interpretation and source

The ITOM team's clarification supplied by the user states that `MTO_with_loop` mode 2 combines MTO with gasification of its heavy-fuel-oil by-product. `flue_gas_CO2` is the amount actually emitted to air by MTO, primarily from its catalyst regenerator and heaters. The integrated capture systems separate CO2 from synthesis gas; they do not capture the reported MTO flue stream. Additional post-combustion capture of that flue stream is outside the mode-2 definition.

In the supplied model version, `stored_CO2` means **captured and compressed CO2 ready for pipeline transport**. Capture and compression energy are included in ITOM. Transport and storage energy are outside ITOM's boundary. The name of the variable alone does not establish permanent storage.

Source coefficients were checked in both:

- `TRANSIENCE-WP8_ITOM-petchem_data_CL-scenario_2026-04-15.xlsx`;
- `TRANSIENCE-WP8_ITOM-petchem_data_OCE-scenario_2026-08-19.xlsx`.

Use sheets `input_factors` and `output_factors`, site `West`, technology `MTO_with_loop`, operating mode `2`. These factors are identical across CL/OCE and the supplied 2020-2050 years. Mode 2 is the only active MTO mode in the supplied production tables. Coefficients are not five-year totals and are not divided by five.

## Allocation and exchanges

The existing propylene allocation pool is retained and applied consistently to the new ethylene dataset. It consists of the four reported hydrocarbon outputs below. CO2, heat and steam are outside that mass-allocation pool. The LCIs do not automatically credit exported heat or steam. The syngas/HFO loop is part of the integrated technology boundary.

| ITOM factor | Amount per kg methanol | Treatment |
|---|---:|---|
| Methanol input | 1 | Technosphere feed |
| Electricity input | 0.461282965997242 MJ | Includes integrated capture/compression |
| Ethylene output | 0.184885989812598 kg | Allocated product |
| Propylene output | 0.186592997848474 kg | Allocated product |
| Ethane output | 0.00314082636580021 kg | Included in allocation pool |
| Propane output | 0.00161589181602507 kg | Included in allocation pool |
| `flue_gas_CO2` output | 0.136861682579402 kg | Actual atmospheric emission |
| `stored_CO2` output | 0.0418749819145093 kg | Captured/compressed stream |

The pool is `S = 0.3762357058428973 kg/kg methanol`. The mass shares are approximately 49.141% ethylene, 49.595% propylene, 0.835% ethane and 0.429% propane. For product yield `y`, allocating an exchange `q` by `y/S` and normalizing by `y` gives `q/S` per kg product. Ethylene and propylene therefore have identical exchange amounts per kilogram under this convention.

| Exchange in each active MTO LCI | Amount per kg product | Unit | Supplier/location |
|---|---:|---|---|
| Methanol | 2.657908285870041 | kg | `market for methanol`, `RER w/o RU`; PoR market replacement during scenario construction |
| Electricity | 0.340568838181883 | kWh | `market for electricity, medium voltage`, `NL` |
| Direct flue CO2 | 0.363765800145908 | kg | `Carbon dioxide, fossil`, air |
| Captured CO2 sent to storage service | 0.111299861401232 | kg | `carbon dioxide compression, transport and storage`, product `carbon dioxide, stored`, `RER` |

Active dataset names:

- `ethylene production, MTO with loop, mode 2, allocated (por)`;
- `propylene production, MTO with loop, mode 2, allocated (por)`.

The older `ethylene, via methanol-to-olefins, mass allocation` activity remains in the workbook for compatibility and is not selected for `ethylene_mto_with_loop`. Retrofit MTA and other MTO-named proxies have different technology boundaries and have not been converted to mode 2 by this change.

## Storage assumption and credit convention

The positive service exchange assumes **100% of the captured/compressed stream is transported and durably stored**. This is the requested provisional LCA destination assumption. ITOM confirms the condition of the stream at the plant gate, but does not confirm its downstream destination in these files.

No additional negative CO2 exchange is applied: the source inventory already contains the CO2 actually emitted. Subtracting the captured stream again would underestimate atmospheric emissions. For one kg methanol, atmospheric MTO CO2 remains 0.136861682579402 kg, before the additional supply-chain burdens of storage. A separate no-capture counterfactual would need its own process and energy definition.

The historical activity `carbon dioxide compression, transport and storage, with credit, MTO with loop` is retained to preserve its name, but its former -1 kg fossil CO2 exchange is set to **zero** and its metadata identifies it as legacy. `FE_CC_MTO_with_loop` stays commented out in the config. Demanding this service separately would duplicate the embedded storage burden. The gasification and pyrolysis legacy inventories are outside this MTO revision.

The fossil biosphere label is retained for compatibility. It remains provisional where the PoR methanol mix contains fossil, biogenic or DAC-derived carbon. This revision does not resolve carbon-origin tracking or add a second atmospheric uptake credit.

## Compression overlap in the requested supplier

The exact requested supplier was found in the local **premise reference database** `ecoinvent_312_reference`, location `RER`, product `carbon dioxide, stored`, unit kg. It is an additional reference inventory rather than an activity found in the original `ecoinvent-3.12-cutoff` database. Its direct exchanges contain no negative CO2 biosphere credit.

The supplier documentation describes initial compression to 11 MPa, a 50 km pipeline and further compression/injection at 15 MPa into a 3 km reservoir, following Koornneef et al. (2008), *Life cycle assessment of a pulverized coal power plant with post-combustion capture, transport and storage of CO2*, International Journal of Greenhouse Gas Control 2, 448-467. These are generic proxy assumptions, not measured PoR transport distances or storage conditions.

The service includes 0.118 kWh/kg CO2 of operational electricity plus 0.000001 kWh/kg for compression-facility infrastructure. Its operational amount does not split initial compression from transport/injection. ITOM already includes capture/compression in the MTO electricity coefficient. Using the full requested service therefore introduces potential overlap. The full service is retained as a conservative proxy, with the overlap stated in both active LCI comments. Its operational electricity contributes 0.013133384 kWh/kg allocated product; the duplicated portion is not identifiable from these inputs. Do not deduct the entire amount from MTO electricity because some of it provides the downstream injection service that ITOM excludes. A supplier for already compressed CO2 or a documented energy split would resolve this limitation.

## Checks and rebuilding

Regression checks cover matching CL/OCE factors, active mode 2, equal allocation conventions, positive storage-service demand, actual flue emissions without a second credit, and disabled standalone MTO CCS demand. The final workbook also preserves unrelated cells, existing formulas, worksheet names, formatting and annotations.

Only ethylene and propylene are mapped here. Their combined allocated demand represents about 98.736% of the four-product pool; the remainder belongs to ethane and propane. Do not force their combined storage demand to equal the full physical MTO capture total unless the other co-products are also included or the allocation boundary is explicitly changed. Allocation of storage follows the joint-process product shares and is not an independent PoR carbon-flow inventory.

Run the tests from the repository root:

```powershell
python -m unittest discover -s tests -p test_por_mode_aware.py -v
```

Rebuild the PoR datapackage and prospective inventories with `1_export_packages.ipynb`, then recalculate `2_calc_impacts.ipynb` and any regional results. Existing ZIPs, Brightway databases and saved figures retain their previous inventories until rebuilt. No new ITOM scenario variables are needed for this exchange: its demand scales with the mapped MTO product supply.
