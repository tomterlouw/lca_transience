# Post-update PoR supplier linking

The repo now owns the additional supplier-linking step in `por_supplier_linking.py`. It runs after premise has created and replaced its external markets, and before export. `1_export_packages.ipynb` uses the same implementation for Brightway databases and the Pathways ZIP.

## Shared geography and reference-inventory preparation

`supplier_resolution.py` is production code shared by the exporter and the post-update linker. Tests exercise it; neither workflow imports test code. It owns supplier identity matching, duplicate detection, 1:1 identity substitution and active-IAM geographic definitions.

`0_export_act_to_excel.py` no longer imports `regionalization.py`, premise or Wurst. Its Brightway access is deferred until explicit execution. It prepares the existing 26 reference activities, including the three Morocco copies, before creating a workbook. The configured reference database is read, not modified. The output remains a candidate/reference workbook, not the final `inventories/lci-itom_por.xlsx`.

```powershell
python 0_export_act_to_excel.py --model remind
python 0_export_act_to_excel.py --model image --output export/reference_candidates_image.xlsx
```

The default output is unchanged. Supplying `--output` avoids overwriting the existing candidate workbook. The worksheet still uses Activity metadata followed by Exchanges, and now also retains available uncertainty fields. A required `Database` header names the candidate workbook after its output stem, allowing Brightway's Excel importer to parse it directly without conflating it with the reference database or final PoR inventory. The exporter does not write a Brightway database. `regionalize_to: None` preserves the original activity and suppliers. Target source matching remains exact-first with the existing contains/fuzzy fallbacks, but every stage is constrained to the requested reference product. Missing suppliers fail before writing a partial workbook. Missing targets are warned, skipped and listed in `missing_targets` in the JSON report; `--strict-targets` makes them fatal.

Live reference checks exposed an existing fuzzy-match hazard: the unavailable PBS target could select isobutyl acetate. Wrong-product fallbacks are now prohibited. The PET/PLA/PTA/polyol/PP target names and products were corrected to verified ecoinvent identities; PET amorphous and long-chain polyols match the existing PoR configuration. The unavailable PBS and bulk-polymerised PVC targets are kept as explicit missing items, not swapped to another technology. The suspension-PVC target is retained separately. In the inspected local reference database, this yields 24 available inventories from the 26 target slots.

`tests/check_reference_export_readonly.py` is an optional live-data test adapter for a locked Brightway project. It opens the existing SQLite file with `mode=ro`, reads the selected source activities, exercises the **same** preparation and workbook writer for both IAMs, and checks repeatability. It does not run Brightway project setup or alter the source database. For example:

```powershell
python tests/check_reference_export_readonly.py --sqlite "<Brightway project>/lci/databases.db" --output-dir export/reference_validation_new
```

Use a new output directory; this check refuses to overwrite previous validation workbooks.

### Topologies

The shared loader reads `data/iam_variables_mapping/topologies/<model>-topology.json` with an absolute repository-relative path. It supports the supplied REMIND and IMAGE files (and other models with an explicit matching definition file). Geographic definitions are added to constructive-geometries under `(model, region)` keys, rather than merging both IAMs into a global bare-label dictionary. The selected model determines which IAM region labels in datasets are interpreted. Countries and ecoinvent regions use the base geographic definitions. NL maps to REMIND EUR / IMAGE WEU; MA maps to REMIND MEA / IMAGE NAF. The World aggregate is not preferred over specific regional providers. A missing/invalid topology raises an error. Topology path and SHA-256 are recorded in both workflows' JSON reports.

Additional ecoinvent 3.12 regions are read from the repo-owned `ei312-topology.json`, mirrored from premise 2.3.8's supplied country definitions, and registered under the separate `ecoinvent` namespace. This includes `IAI Area, Western and Central Europe`; it is not guessed from a generic European label. `RER w/o RU` is explicitly resolved as RER excluding Russia for covering-region checks. The additional topology file/hash are also recorded in both workflows' provenance. The export guard rejects geographic definitions changed after validation.

Topologies are geographic definitions only: they do not import inventories, alter technology efficiencies or decarbonize a supplier. Select the same IAM as the subsequent premise scenarios. The post-update adapter automatically takes each scenario's `model`; the standalone exporter uses `IAM_MODEL = "remind"` unless overridden on the command line.

### Reference supplier rules

For copied reference inventories, the resolver prefers a valid same-product/unit local supplier, then the active IAM region, then the smallest wholly covering reference geography. It retains a valid original reference proxy when no covering equivalent exists; a global provider is a final fallback for an otherwise missing link. Explicit foreign country-level process inputs are kept where declared. Electricity market/group names are interchangeable only within the same electricity product/voltage and unit, and only when a real supplier is present. No other names are guessed or substituted through fuzzy matching.

Unlike the legacy blanket geographic relinker, this preparation does not split an exchange using arbitrary production-volume or equal weights. Quantities, units, uncertainty and biosphere flows remain unchanged. The production exchange receives the copied activity's destination location. Obsolete supplier identifiers are removed on a changed link; nonzero missing, ambiguous and direct self-supply links fail. Zero technosphere inputs are retained without attempting an unnecessary supplier repair. No supplier recipe in the reference database is edited.

The companion `<output-stem>_supplier_audit.csv` lists checked exchanges and their identities/amounts/reasons. `retained_reference_proxy` is a review item, not evidence that the supplier is physically local. Its JSON companion records the reference database, model, topology hash and totals. Review these files before copying candidates into the final foreground workbook, especially geographic proxies for imported methanol, hydrogen, heat and capital goods. Future renewable supply assumptions remain explicit foreground-inventory decisions, not geography-derived defaults.

### Legacy file

Keep `regionalization.py` for compatibility **only while the locally modified installed premise still has `from regionalization import *`**. This repo's exporter and post-update workflow do not use its relinking function. The legacy constructor hook is disabled by the runtime compatibility setup, but that does not remove an old import statement from site-packages. With a clean premise installation, neither of these new workflows needs that file. No installed package files are edited by this refactor.

## Notebook execution

The setup cell calls `configure_por_export_support()`. This retains the null uncertainty-bound workaround in `premise_compat.py`, disables the old user-added `fully_regionalize_created_inventories` method in memory if that specific legacy method exists, and installs a geographic export guard. A clean premise installation without the legacy method does not need that disabling operation. No site-packages file is edited.

For Brightway output, the notebook attaches the helper to the new builder before updating:

```python
attach_por_supplier_linking(ndb, stage="brightway")
ndb.update()
ndb.write_db_to_brightway(name=scenario_names)
```

The attached update method runs premise's normal update first, then the repo's validated post-step. Existing databases are replaced only after both steps succeed. For the Pathways ZIP, the notebook attaches it to the contained builder:

```python
attach_por_supplier_linking(ndb.datapackage, stage="pathways")
ndb.create_datapackage(...)
```

The public package method calls its internal `NewDatabase.update()` and therefore executes the post-step before matrix export. This avoids copying the private package-export implementation or applying corrections after the ZIP has already been written. The hook is idempotent on a builder instance. It must be attached to each new builder.

Reload the notebook from disk and run its setup before creating new builders. Recreate any builder whose earlier update failed. The already generated ZIPs, Brightway databases and impact figures are not retroactively changed.

## Scope and selection

The policy reads `configuration_file/config_itom_por.yaml` and `inventories/lci-itom_por.xlsx` at attachment time, records their hashes and checks them again during processing. Editing either source while an update runs causes an explicit failure rather than mixing source versions.

Domestic targets are named activity/reference-product pairs under `production pathways`, `regionalize/datasets`, and declared market `add` suppliers. Their actual unit and location remain part of the runtime supplier identity. Domestic supplier substitutions are limited to NL by default. Selecting a pair does not rename or move the consuming activity itself.

The helper distinguishes:

- **Domestic processes:** apply explicit PoR replacement rules first. Other eligible generic inputs use NL, then the active IAM region, then an available wholly covering reference region, even when the original RoW/GLO/World supplier exists. Exact product/unit matching is required. The electricity market/group alternative must retain voltage and unit. Generic capital/service proxies are eligible too; a European construction proxy need not first be added to `regionalize/datasets`.
- **Constructed markets/upgrading activities:** preserve the modeled supply-route exchanges and quantities. Only their declared auxiliary `add` inputs are eligible for local linking.
- **Regionalized background markets:** retain the amount/share of each technology. A product-supply exchange from a generic region (e.g. RoW or RER) is linked to the unique same-name/product/unit NL producer only when that producer is explicitly declared in the PoR policy. Ancillary service/transport **identities** can use a covering regional proxy under version 3, but their quantities and distances remain unchanged. Intentional country-specific material/process supplies, import pathways and unconfigured product-supply technologies are retained. No equal-share or production-volume rebuilding is performed.
- **Imports:** identify Trade/terminal/import pathways and preserve their foreign supply. The original source-workbook recipe can repair a native replacement that inadvertently points the import wrapper at a PoR market. Regionalized copies use the unique original recipe for their activity/product/unit. Legitimate foreign future suppliers are retained.
- **Fixed renewable supply:** preserve the dedicated 50% solar/50% wind recipe and its supplier identities. A changed share fails validation rather than being silently renormalized.

The source profile reader inspects only import and fixed-power recipes. Missing reference-product fields can be resolved only when the live database supplies one unambiguous product for the original name/unit/location. Missing or ambiguous source identities fail explicitly; they are not guessed.

### Embedded market supply correction (policy version 2)

The earlier post-step validated `existing_market` exchanges without changing their identities. Consequently, MDI could consume NL aniline and NL nitrobenzene **markets**, while the latter still supplied RoW nitrobenzene production despite the configured NL copy. Both market and production entries were already correctly present in `regionalize/datasets`; adding more duplicate inventories or declarations would not fix that missing market-supply branch.

Version 2 links this declared local product supply through existing markets as well as processes. For example, the chain becomes MDI NL → aniline market NL → aniline production NL → nitrobenzene market NL → nitrobenzene production NL. The last process continues to consume the configured PoR benzene market. Each exchange keeps its amount/unit/uncertainty; market transport proxies remain unchanged. This implements the explicit NL regionalization assumption, rather than choosing suppliers by their LCIA score. An NL label alone is not evidence that all upstream inputs or impacts are domestic or low-carbon.

Reason `regionalized_market_product_supply` identifies these corrections in CSV reports. If the declared local counterpart is absent, a valid original supplier is retained and reported with `unavailable_local_supplier`; JSON includes `unavailable_local_suppliers`. Ambiguous local identities fail, rather than choosing arbitrarily. Constructed PoR market routes and protected imports/fixed renewable shares remain outside this rule.

Read-only checks on the existing 2050 CL and OCE Brightway databases reproduced the nitrobenzene RoW link and found 19 qualifying market-product corrections per scenario, with no missing local counterparts. These diagnostics do **not** modify the old databases. `tests/check_por_linking_readonly.py` can repeat that check and write CSV/JSON comparisons for any explicitly named scenario databases.

Restart the notebook kernel and rebuild through `1_export_packages.ipynb` to update Brightway outputs and the Pathways ZIP. The export guard requires the current `linking_policy_version` in its validation marker; an old prepared cache/export tag cannot silently count as validation under the new rules. Already generated matrices/results/figures need regeneration after the rebuilt package is selected.

### Regional input completion (policy version 3)

Version 2 completed the declared market-to-production links, but two gaps remained deeper in those production inventories: it tried European fallback only for a **missing** original supplier, and local lookup required the identical activity name. Thus valid RoW material/water proxies and World heat were retained, while a GLO electricity group missed the real NL electricity market. Unlisted generic construction proxies also remained outside the local-only branch.

Version 3 uses `ReferenceSupplierResolver.regional_candidate()` in both reference preparation and post-update linking. For eligible inputs the priority is explicit PoR replacement → exact local supplier → active IAM region → smallest available wholly covering reference region. A valid original supplier no longer blocks this search. Only the documented electricity market/group family can change names, with exact reference product, voltage and unit. Heat/steam products are not interchanged, transport distances are not invented and exchanges are not split or rescaled.

Explicit foreign country/subnational material and process suppliers remain pinned, as do import wrappers and fixed renewable supply. Operational electricity/heat in a copied domestic plant are instead sourced for that plant's location; a country label inherited from the original reference plant is not treated as a foreign energy import. This exception does not apply to protected imported inventories or fixed electricity recipes. Constructed PoR market supply routes remain protected, and generic background market recipes are not recursively rewritten simply to minimize impacts.

For the existing 2050 nitrobenzene-production inventories the verified corrections are:

| Input | Original proxy | Selected provider geography |
| --- | --- | --- |
| Chemical factory construction | RoW | RER |
| District/industrial heat | World | EUR |
| Nitric acid | RoW | RER w/o RU |
| Soda ash and sulfuric acid | RoW | RER |
| Wastewater and deionised water | RoW | Europe without Switzerland |
| Medium-voltage electricity group | GLO | NL medium-voltage electricity market |
| PoR benzene | NL | NL, unchanged |

These are geography-based modeling choices, **not a guarantee of lower LCIA scores** or evidence of specific real procurement contracts. Amounts (including the negative wastewater quantity), units, uncertainty and biosphere/production exchanges remain unchanged. The read-only scenario checks validate source/target identities and repeated application without editing the existing Brightway databases. Rebuild both export routes and recalculate results to deploy this version.

Reasons `geographic_local_supplier` and `geographic_covering_regional_supplier` identify new changes. Where no matching local/covering provider exists, a valid original broad proxy remains and is recorded with status `retained_geographic_proxy`; JSON includes `retained_geographic_proxies`. Review these residual assumptions rather than expecting every GLO/RoW supplier to disappear. A missing original market can use a documented global fallback; incompatible products/units and ambiguous supplier identities are never guessed.

## Replacement and validation rules

The helper uses `markets/replaces` and `replaces in` for explicit name/product substitutions. This is essential: geographic relinking alone cannot turn `market for methanol (SPS)` into `market for methanol (por)`. Inactive markets absent from a scenario are reported; a valid original supplier is retained in that case.

There is no supplier splitting, equal allocation or production-volume allocation in this step. All replacements are 1:1. Non-unit replacement ratios must be represented through documented inventory changes rather than this linking helper. Every processed nonzero technosphere exchange must resolve to one unique name/product/unit/location identity. Eligible generic markets, materials, services and capital proxies can use a covering regional supplier even when the original link exists. Explicit foreign country-level material/process supplies are not replaced through that fallback. Neither an input's biosphere flow nor its unit is changed to facilitate a supplier match.

Input quantities and units remain unchanged. Uncertainty fields, including numeric zero bounds, are retained. Production and biosphere exchanges are not edited. Changed supplier identities have obsolete `input`/supplier-database addresses removed so the exporter can build correct links. Unintended direct self-supply and direct market/import feedback loops are rejected. This check does not prohibit legitimate process/recycling loops elsewhere in the inventory.

## Scenario cache handling

In the inspected premise version, `update()` stores each database in a pickle and removes its in-memory `database` entry. The adapter therefore reloads the updated scenario with `delete=False` and `load_metadata=False`, links and validates a replacement dataset list, and persists a new cache.

Original scenario cache references are not changed until all selected scenarios and their reports succeed. A later scenario failure leaves the original references intact. Original cache files are retained. Newly staged files from a failed transaction can remain unreferenced until premise's usual cache cleanup. Missing updated data never falls back silently to the unmodified reference inventory.

The cache API is deliberately isolated here. The inspected implementation is premise 2.3.8; an upstream change to the cache API must be reviewed in this adapter. Keeping the code in the repo avoids manual environment edits but does not eliminate dependency-version testing.

## Export preparation guard

The installed `premise.export.check_geographical_linking` can change a foreign supplier location to the consuming dataset's location immediately before export. That could undo validated import or renewable-supply links even after a successful post-update pass.

The runtime guard applies only to scenarios tagged by this repo's validated post-step. It retains the selected supplier identities across that native check, rejects unexpected quantity/unit changes, and appends any prevented location changes to the report. Untagged premise scenarios keep their native behavior. This guard also avoids editing the installed package. Native validation and the actual exporters still run afterward.

## Reports and checks

CSV and JSON reports are written to `export/por_supplier_linking/`. Each report identifies stage, model, pathway, external scenario labels and year. Exchange records contain consumer identity, old/new supplier, original/new amount, unit, reason and status. JSON records the source hashes and summary counts. Status `unavailable_market` flags a market not created for that scenario; `preserved_on_export` records an attempted native location change that was prevented.

JSON also records `linking_policy_version`. Version 2 included the embedded regionalized-market supply correction; version 3 adds appropriate regional inputs within those processes. `unavailable_local_supplier` records a missing declared domestic production counterpart, while `retained_geographic_proxy` records an eligible input without an appropriate regional substitute. Review missing-provider flags and residual geographic assumptions before interpreting impacts.

Review these reports after the first full rebuild, particularly imported methanol, propylene/aromatics, steam auxiliaries and the coupled methanol markets. This module validates coupling, not the physical accuracy of every inventory, carbon-origin classification or trade-distance assumption.

Run:

```powershell
python -m unittest discover -s tests -v
```

Regression cases cover explicit market-name substitution, unchanged uncertainty/amounts, import restoration, renewable shares, missing/ambiguous suppliers, regional fallback, idempotence, transactional cache handling, both export routes and the installed native geographic export check. Reference-export cases also cover REMIND/IMAGE definitions, cwd-independent paths, NL/MA suppliers, preserved foreign processes, electricity market aliases, all 26 existing targets, uncertainty-bearing Excel output and import-time safety. The source workbook and YAML are not modified by this step.
