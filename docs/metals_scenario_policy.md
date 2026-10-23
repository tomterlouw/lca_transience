# Non-ferrous metals scenarios and endogenous steel

The export workflow now has a repo-owned non-ferrous metals policy, selected in
`1_export_packages.ipynb`. Both Brightway databases and the Pathways package
use the same policy after the normal `premise.update()` transformations.
There is no additional notebook to run and no need to copy scenario YAML into
`site-packages`.

The default is **native premise baseline**, as requested. It records supply
details but does not reweight any exchange. This means the baseline intentionally
preserves each installed premise version's behaviour, rather than making the two
versions mathematically identical.

**Steel remains endogenous to the selected IAM or EDM-I model.** This policy
never reweights steel supply, sets a steel recycling floor, or imposes an
exogenous steel primary/secondary scenario.

## Selecting a case

1. Start with a fresh kernel/builder. Select `NONFERROUS_METALS_SCENARIO` in
   the settings section of notebook 1. Keep it at `"baseline"` for native results.
2. Run the ordinary input/package preparation and the desired export block.
   `load_selected_metal_steel_policy()` prepares the live share files automatically,
   then loads the policy. Its post-update step is already attached to both export
   routes, before final supplier linking and export checks. No standalone
   metal-share generation or workbook-conversion step is required.
3. Review the printed summary and per-scenario reports in `export/metals_policy/`.
   Confirm the case, premise version, affected markets, effective recycled shares,
   and any skipped/unsupported recipes before accepting the export.
4. The baseline keeps the usual output names. Alternative cases receive an output
   `__metals-<case>` suffix identifying the selected non-ferrous policy, so they do not silently
   overwrite the baseline package/databases. Use the printed archive name when
   selecting a package in notebook 2; its calculation workflow is otherwise unchanged.
5. To compare policies, create a fresh builder for each case. Do not change the
   selectors on an already updated object, or apply a different policy to an
   already modified inventory. The guards deliberately reject that workflow.

Apply the same policy to CL and OCE for a clean ITOM scenario comparison. Change
the policy in a separate export run when studying metals/recycling sensitivity.
The policy does not silently make CL or OCE intrinsically more circular.

For example, the ordinary notebook settings are:

```python
NONFERROUS_METALS_SCENARIO = "baseline"
METALS_SHARE_SOURCE = "yaml"
FORCE_METALS_SHARE_REGENERATION = False
```

An increased-recycling sensitivity could instead select `"high_recycling"` for
non-ferrous metals; steel still follows the selected IAM or EDM-I model. The notebook loads
the shared configuration with
`prepare_policy_share_data(...)` followed by
`MetalSteelPolicy.from_file(METALS_POLICY_FILE, nonferrous_case=...)`.
There is no separate scenario-generating script to run.

The existing `MetalSteelPolicy` class and `attach_metal_steel_*` function names
are retained in the consolidated `metals_scenario_policy.py` module; their names
do not enable a steel scenario. The former `metals_policy_workflow.py` imports
must use `metals_scenario_policy` instead.
Legacy steel arguments permit native baseline only, and non-native steel
selections are rejected rather than silently changing model outputs.

## Live share files and automatic preparation

Numeric assumptions are read from `primary_vs_secondary_metals/scenario_data/`,
not duplicated in the policy configuration. The policy YAML specifies case
selection, file/product mappings, supplier matching and safeguards. This separates
the editable supply assumptions from the implementation.

| Notebook selection | Live data source | Interpretation |
|---|---|---|
| Non-ferrous `baseline` | No numerical override | Preserve native premise; ignore the historical generated case named baseline. |
| Non-ferrous `reference` | `metal_primary_secondary_shares.yaml`, year 2020 | Freeze the documented reference fraction. |
| Non-ferrous `conservative` | `primary_secondary_split_conservative.yaml` | Use its curve as an increase-only floor. |
| Non-ferrous `moderate_recycling` | `primary_secondary_split_baseline.yaml` | Use the historical baseline data as an explicit moderate floor, **not** native premise baseline. |
| Non-ferrous `high_recycling` | `primary_secondary_split_optimistic.yaml` | Use the optimistic data curve as a higher floor. |
| Non-ferrous `primary_only` | Synthetic exact 0% target | No secondary supply in supported markets. |

The default `METALS_SHARE_SOURCE = "yaml"` uses the raw reference YAML together
with `metal_2050_literature_evidence.csv` and `metal_product_family_mapping.csv`.
Notebook 1 calls the repo-owned `ensure_share_outputs()` preparation through
`prepare_policy_share_data()`. Missing/empty named scenario files are generated;
generated files that are stale against changed raw inputs are refreshed. The
three named cases are written separately, fixing the former loop that repeatedly
overwrote one output and left the named files empty.

Preparation also maintains `primary_secondary_split.yaml` as an alias of the
generated **baseline data** case, the combined `metal_shares_2050_scenarios.yaml`,
and `share_generation_manifest.json`. The alias is not whichever case happened
to be generated last, and it does not change the notebook's native baseline
selection. These derived files are for consistency/provenance.
The folder's `.gitattributes` preserves YAML/CSV/JSON line endings so Git checkout
conversion does not invalidate the manifest's exact-byte source hashes.
The policy reads the selected named source. Generation covers the existing product table, whereas
the LCI override deliberately remains restricted to eleven reviewed market rules
for ten metal families: the original five refined non-ferrous metals, two
lithium-carbonate products, and gold, silver, platinum and palladium. The local
104-product reference table includes the added generic carbonate; it is not a
catalogue of 104 constructed recycling inventories or approved market overrides.

The product-to-family CSV also accepts an optional `evidence_scope` column.
`apply` generates that family's candidate curve (and remains the default for
older two-column files). Once the column exists, every row must state `apply`
or `reference_only`; blank or misspelled values stop generation.
`reference_only` retains the original source curve and records the exclusion in
the combined output. This prevents the magnet-REE assumptions from being
assigned to the thirteen non-magnet rare-earth oxides. The four Nd/Pr/Dy/Tb
entries may carry candidate endpoints, but **no rare-earth runtime rule is
enabled**. Scope of evidence and scope of implemented LCI overrides are separate
checks; a new family percentage never constructs a recovery inventory.

Valid edits to the named conservative/baseline/optimistic YAML files are retained
when upstream inputs are unchanged. They are recorded as manual outputs, and
their actual values feed the selected policy. Edit both primary and secondary
values consistently: fractions must be finite, in `[0, 1]`, and sum to one for
each year, with unchanged product/activity identities and complete product
coverage. The combined document and alias are refreshed from those authoritative
named files. Do not edit the derived alias/combined file expecting it to override
the named scenario.

If both upstream inputs and named scenario outputs were edited, preparation stops
with a conflict instead of silently choosing or overwriting assumptions. Review
and back up the edits; only set `FORCE_METALS_SHARE_REGENERATION = True` when you
deliberately want to rebuild scenario outputs from upstream data. Return it to
`False` afterwards. Malformed/incomplete manual files likewise require review,
not a silent fallback to default fractions.

For an Excel-owned reference source, explicitly set `METALS_SHARE_SOURCE = "xlsx"`.
This reads the `Shares` sheet in `metal_primary_secondary_shares.xlsx` through the
repo's read-only converter and prepares the reference YAML and named scenarios
inside notebook 1; it never writes the workbook. In this mode the workbook owns
the reference values. An independently edited extracted reference YAML triggers
a conflict: choose `"yaml"` to use that YAML or explicitly force workbook
re-extraction. The default YAML mode does not read/reimport the workbook and
therefore does not overwrite a deliberately edited YAML reference.

The workbook also contains `Scenario assumptions` and `Evidence` documentation
for the active products, including observed precious-metal supply components,
their denominator, the 2025 anchor and the separately labelled future
sensitivities. These sheets are an explanatory snapshot, not an additional
runtime control: edits there do not change the policy. The original 103-product
`Shares` reference values are retained, with one generic-carbonate record added
to keep the 104-product local source consistent with YAML mode. Default YAML mode consumes
the evidence/mapping CSVs and named YAMLs; XLSX mode reads `Shares` only.

Changing a consumed named file, policy configuration or raw input
invalidates a loaded builder's source fingerprint. Reload the selection and
construct a fresh builder, rather than exporting an object built under old
assumptions. The resulting ZIP embeds resolved configuration and source snapshots
alongside hashes, so the selected values can be recovered even after live files
are edited.

Generated packages are stored in `results/datapackages/`, including the
`__metals-<case>` suffix for non-baseline cases. Notebook 1 prints the final path
for notebook 2. The archive is moved only after native export completes; its
embedded policy provenance and inventories are unchanged. Policy audit CSV/JSON
files remain under `export/metals_policy/`, not in the results directory.

## Available non-ferrous cases

| Case | Meaning |
|---|---|
| `baseline` | Audit-only. Native premise output is unchanged. |
| `reference` | Fixed documented 2020 recycled-input fractions for the eleven configured market rules, within each rule's geographic scope. Future production/background inventories still apply. |
| `conservative` | Minimum recycled-input trajectory. Keep a higher native share rather than forcing it down. |
| `moderate_recycling` | Minimum trajectory read from the historical generated baseline-data file. This is an explicit override, not native premise baseline. |
| `high_recycling` | Higher minimum trajectory, subject to the same supplier/grade checks. This is a sensitivity, not a validated scrap forecast. |
| `primary_only` | Exact 0% secondary supply counterfactual where a validated primary recipe exists. Not a recommended central forecast. |

The live files, refreshed from the evidence CSV on 23 October 2026, contain these
reference values and 2050 floor inputs.
Later validated file edits are used directly; this table is documentation, not a
second parameter store:

| Refined product | Fixed reference | Conservative file, 2050 | Moderate file, 2050 | High file, 2050 |
|---|---:|---:|---:|---:|
| Aluminium, cast alloy | 68.8245% | 45% | 55% | 65% |
| Copper, cathode | 20% | 25% | 35% | 45% |
| Nickel, class 1 | 0.2028465% | 15% | 22% | 30% |
| Cobalt | 0.5716258% | 20% | 30% | 40% |
| Lead | 54.9162034% | 65% | 77% | 85% |
| Lithium carbonate, generic (GLO only) | 0.08404146% | 15% | approximately 22.28% | approximately 37.31% |
| Lithium carbonate, battery grade (World only) | 0.08404146% | 15% | approximately 22.28% | approximately 37.31% |
| Gold | 0.4426657% | 27.6660% | 37.6660% | 47.6660% |
| Silver | 8.4636523% | 18.9236% | 28.9236% | 38.9236% |
| Platinum | 4.4600386% | 21.7765% | 31.7765% | 41.7765% |
| Palladium | 15.5485524% | 32.2398% | 42.2398% | 52.2398% |

The four precious-metal conservative endpoints hold the reported **2025**
secondary-supply fraction constant to 2050. Moderate/high endpoints add **10/20
percentage points**, respectively, as analyst-selected sensitivities. Those
increments are not forecasts reported by the source organisations. The generated
curves retain the catalogue's 2020 value, pass through a common observed 2025
anchor, then interpolate linearly to the chosen 2050 endpoint. Reference still
selects the fixed 2020 value; native baseline is unchanged. Non-precious products
retain the 2020–2050 linear interpolation, with the updated evidence endpoints
above; both carbonate rules use the same lithium-family endpoints and interval.

The updated evidence distinguishes endpoint provenance, rather than treating
every row as an equally established forecast. Aluminium and copper are
low-confidence analyst sensitivities. Nickel 15/22/30% and cobalt 20/30/40%
use rounded IEA 2024 battery-derived secondary supply divided by global total
metal demand, not measured recycled content for each ecoinvent grade. Their
application to supported class-1 nickel/cobalt markets remains a qualified
product-boundary proxy, using existing recovery burdens. Lithium's 15% is an
analyst lower-collection case; its other two endpoints use source-checked,
approximate elemental-Li ratios but retain low confidence as carbonate-market
proxies. Lead's 77% is a published central
assumption, while 65/85% are analyst brackets. See the
[evidence refresh](metals_literature_review.md#evidence-refresh-23-october-2026)
for denominators, confidence and limits.

### Literature-reviewed extension, 20 October 2026

The initial extension added lithium carbonate; the subsequent review also
activated qualified supply-mix sensitivities for four precious metals.
The existing World market already consumes recovered generic lithium carbonate
from `treatment of used Li-ion battery, hydrometallurgical treatment`, GLO.
The explicit `supply_products` interface retains that inherited grade proxy;
it does not establish battery-grade purity or introduce an unmodelled upgrade.
The **battery-grade GLO** market is not overridden because a positive compatible
secondary recipe was not verified there. The previous five product matching
rules are unchanged; the 23 October evidence refresh updates aluminium/nickel
floor values without broadening their product scope.

The [IEA Global Critical Minerals Outlook 2026](https://www.iea.org/reports/global-critical-minerals-outlook-2026),
pages 145 and 350, supports approximate 2050 ratios of `250 / 1122` in the
STEPS-context base case and `560 / 1501` in HDS. Numerators and denominators are
kt of elemental Li; these are global secondary-supply/total-demand ratios,
not end-of-life collection rates. Applying them to carbonate is an explicit
representative-compound sensitivity. An independent check of the official PDF
on 23 October confirms the approximate source quantities; it does not validate
their compound-grade or scenario interpretation, which remains low-confidence.
Moderate uses the approximate base ratio and high the HDS ratio.
Conservative now uses a separately labelled **15% analyst lower-collection
sensitivity**, not another claimed IEA projection. HDS is not an automatic
mapping to CL/OCE or to an IAM pathway.

No complete 2026 intermediate-year recycling series was verified. At the user's
request, lithium nevertheless uses the same linear minimum-share interpolation
as the other metals, from the documented 2020 reference fraction to the 2050
endpoint, clamped outside that interval. This straight line is an explicit
modelling assumption, not an IEA intermediate-year forecast. At every database
year the applied fraction is `max(native_share, floor(year))`; a higher native
share is never reduced. No `minimum_target_years` gate is enabled for lithium.
`reference` and `primary_only` remain explicitly labelled counterfactuals.

### Generic lithium carbonate and upstream hydroxide, 22 October 2026

An audit of the current premise 2.5.5/ecoinvent 3.12 exported matrices found that
the lithium markets actually used here are GLO, not World. The existing World
battery-grade rule therefore did not change those markets. A separate
`lithium_carbonate_generic` rule now targets exactly `market for lithium
carbonate`, product `lithium carbonate`, kilogram, GLO. Its native positive
recycling input is `treatment of used Li-ion battery, hydrometallurgical
treatment`, product `lithium carbonate`, GLO. This is a same-product interface,
not an extension of the battery-grade equivalence proxy.

The local reference fraction is approximately 0.08404146%, documented from the
existing generic-carbonate recipe. The same elemental-Li evidence and linear
2020–2050 sensitivity are reused for this compound market. Only its existing
primary/secondary supply quantities are reweighted; the recovery inventory,
its energy/material inputs and emissions, within-group provider weights and
distribution exchanges remain intact. No burden-free recycled material, new
recovery technology or avoided-burden credit is added. The audit records this
rule's native, target and effective shares separately from battery-grade supply.

Ordinary `lithium hydroxide production`, GLO, already consumes approximately
**1.622797 kg of generic lithium carbonate per kg hydroxide** in the inspected
matrices. Its burdens therefore change upstream through the carbonate market;
there is no separate hydroxide share target, invented hydroxide recycling
supplier or second credit. This coefficient describes the current inventory,
not a hard-coded policy conversion. Battery-grade hydroxide and battery-grade
GLO carbonate remain native; direct primary-producer links are not silently
rerouted through the generic market.

Re-export with a fresh builder in notebook 1 and recalculate in notebook 2.
Existing archives/results do not change automatically. The selected RELICS
`Lithium` method in the inspected setup characterizes the `Lithium` resource
flow but not `Spodumene`; it is therefore not a complete elemental-lithium
extraction indicator across these routes. Connecting generic carbonate does
not resolve that separate LCIA coverage issue or guarantee a large reduction
in the currently reported lithium result.

Pinned premise supplier selectors and their SHA256 provenance are stored under
`configuration_file/upstream/premise_metals`, from commit
`d5362d8ce1df66c20c7e71c91051a1b88e46c583` (15 October 2026). The two supplied
attachments byte-match that commit's files. A non-baseline opt-in rule uses
`secondary_source_product` to load name/product/location selectors. Their
within-secondary technology/location weights are not applied; the native
secondary recipe is retained and rescaled as a whole. Baseline performs no
optional upstream-source I/O. Installed premise packages are never modified.

The complete scope, rejected denominator conversions, precious-metal benchmarks,
and deferred candidates are recorded in [the literature review](metals_literature_review.md).

### Precious metals: observed anchors and explicit sensitivities

The 2025 gold and silver anchors use `recycled / (mine production + recycled)`.
They exclude hedging, official sales and stock movements. Platinum and palladium
use `open-loop secondary / (primary + open-loop secondary)` from Johnson
Matthey's 2026 report; closed-loop industrial recirculation is not added again.
The global fractions are representative supply assumptions applied to configured
World/GLO markets, not independently measured fractions for each geography.

- Gold: 1,404.3 t recycled and 3,671.6 t mined, from the
  [World Gold Council, full-year 2025 supply](https://www.gold.org/goldhub/research/gold-demand-trends/gold-demand-trends-full-year-2025/supply).
- Silver: 197.6 Moz recycled and 846.6 Moz mined, from the
  [World Silver Survey 2026](https://silverinstitute.org/wp-content/uploads/2026/04/World-Silver-Survey-2026.pdf).
- Platinum: 1,547 thousand oz secondary and 5,557 thousand oz primary;
  palladium: 3,135 thousand oz secondary and 6,589 thousand oz primary, from
  [Johnson Matthey, May 2026, pp. 27–30](https://matthey.com/documents/161599/509428/pgm-market-report-26.pdf/a2d115af-bf7c-f589-29e9-6beacf8a4452).

Existing same-product recovery LCIs are retained, with their process burdens and
within-group supplier weights. Gold's electronics-scrap route is an explicit
proxy for a broader recycled-gold supply mix, not a new jewellery-refining LCI.
Platinum/palladium use the pinned name/product/location selector interfaces.
Gold uses an exact local activity-name rule because the pinned GLO selector does
not match every actual SE/RoW provider geography; no provider location is invented.

For silver, only the explicitly approved electronics-scrap and waste-x-ray-film
recovery routes count as secondary. `treatment of crust from Parkes process for
lead production` is assigned to the **primary remainder** using an exact-name,
documented exception. This is not a claim that all Parkes feedstock is primary:
the stream may include industrial residues of unresolved origin. It is excluded
from the claimed recycled fraction because primary lead production can also
generate Parkes crust. All its existing burdens remain, and it scales with the
non-secondary remainder. Other unrecognised treatment routes still fail the
sensitivity rather than silently becoming primary. The pinned upstream source
files remain unchanged.

The 2025 integrated-refinery LCI of
[Cespi et al.](https://doi.org/10.1016/j.cesys.2025.100310), with its
[public inventory dataset](https://amsacta.unibo.it/id/eprint/8288/), is documented
as a candidate for a later technology-recipe improvement. It is **not imported**
by this extension and no published aggregate impact factor is substituted for
an LCI. Zinc, tin, rhodium and magnet-material additions remain separate work.

The reference fractions come from
`primary_vs_secondary_metals/scenario_data/metal_primary_secondary_shares.yaml`.
They are model-specific/rounded reference inputs, not independently measured
recycled content in every region. For example, the actual exported copper market
has approximately 19.93% secondary supply; `reference` intentionally uses the
documented 20% reference setting. `baseline` retains the actual native recipe.

For the original six products and generic lithium carbonate, linear interpolation
between 2020 and 2050 supplies the floor `f(year)`. Precious-metal curves additionally pass through the 2025
anchor and use piecewise-linear interpolation. Both clamp to endpoint values
outside their covered interval. The effective share is
`max(native_share, f(year))`. Thus selecting a conservative case cannot turn
cast-alloy aluminium from about 68.8% recycled input into the conservative file's
45% endpoint (or the moderate file's 55%), nor reduce an older premise native
copper target that is already above
the conservative floor. The case is a minimum, not a promise of cross-version
identical shares.

These endpoint values are **scenario hypotheses**, not direct reproductions of
the cited literature. Several literature indicators have different denominators:

- Collection/recovery rates describe how much waste is recovered.
- Recycled-input content describes secondary metal in present supply.
- Reduced new-mine requirements compare counterfactual mining trajectories.
- Battery-sector secondary supply is not the same as total refined-metal supply.

The [IEA recycling report](https://www.iea.org/reports/recycling-of-critical-minerals/executive-summary)
distinguishes these indicators and supports higher secondary supply. Its battery
results exclude other recycling channels, including nickel in stainless steel;
they are not an exact class-1 nickel or cobalt market prescription. No
stainless-steel, ferronickel or nickel-sulphate override is added. The
[ICA Europe report](https://internationalcopper.org/wp-content/uploads/2023/03/Copper_The-Pathway-to-Net-Zero_Focus-on-Europe_final.pdf)
describes 0.9–1.0 Mt of end-of-life scrap in 3.4 Mt of EU refined copper in 2050;
the global cathode sensitivity here is not that EU forecast. The
[JRC aluminium report](https://publications.jrc.ec.europa.eu/repository/handle/JRC136525)
supports secondary production as a decarbonisation route, but its 60 Mt and more
than 70 Mt recycled-scrap results are volumes, not percentages. It does not
establish our revised 45/55/65% global cast-alloy endpoints. The
[van der Voet et al. study](https://doi.org/10.1111/jiec.12722)
contains a 77% secondary-lead scenario; 65% and 85% here are sensitivities around
that figure, not confidence limits.

## Steel stays with the IAM or EDM-I model

Steel supply cannot be classified merely by furnace type. Scrap-based EAF is
secondary; hydrogen/natural-gas DRI followed by EAF is primary, because its iron
originates from ore. An EAF label must therefore not overrule a DRI/direct-reduction,
pig-iron or ore-electrowinning/electrolysis label. Electric primary routes remain
in the primary group.

The IAM steel route mix, production inventories, primary/secondary quantities,
future energy inputs and regional market weights remain those produced by the
selected model and premise transformations. If the external EDM-I link is
explicitly enabled, its supply decisions remain authoritative instead. There is
no extra steel selector, share-data input or post-update steel override in this
non-ferrous policy. Changing the non-ferrous case cannot replace a steel market,
set its recycled-content share or alter a steel production exchange.

All steel grades, including low-alloyed, unalloyed and stainless/chromium steel,
are excluded from the exogenous supply sensitivity. An IAM World market that
aggregates regional steel markets retains those weights. Steel scenario choices
belong to the existing IAM/external-model setup, not a second independent
primary/secondary fraction layered on top of it. The usual prospective background
changes and validated PoR supplier-linking workflow still operate normally.

## What is changed, and what is retained?

Alternative cases reweight existing **positive supply exchanges** in the selected
market. The primary and secondary supplier distributions within their groups are
preserved. The total supplied product amount remains constant; it is not assumed
to be exactly one when an existing market includes losses. The supplier identities,
units and declared product grades must remain valid.

The policy does not edit mining, refining, recycling or steel-production LCIs.
Their electricity, fuels, process emissions, recycling treatment burdens and
allocation remain as modelled by premise/ecoinvent. Retained transport and other
non-supply exchanges are not zeroed or treated as free. Rescaling supplies does not
rebuild transport distances or future scrap logistics; that is a limitation of
this supply-mix sensitivity and should be assessed for large changes.

Negative/credit exchanges are not repurposed as supply. Uncertainty parameters on
changed supply exchanges are handled consistently with their deterministic
rescaling; unchanged exchanges are preserved. Production and biosphere exchanges
are not altered. No additional avoided-primary-production credit is added.

Same-product secondary providers are required. A missing recycling technology
does not justify relabelling a primary route, using unprocessed scrap as refined
metal, or silently selecting a different grade/country. In particular, nickel's
existing electronics-scrap recovery recipe and cobalt's battery-treatment recipe
are retained when their quantities are increased. These are prospective process
proxies, not new future technology inventories; scrap availability is not solved.

## Compatibility and native baseline

The integration has two storage paths:

- Legacy premise (including 2.3.8): stage an alternative in a separate updated
  scenario list/cache and publish only after validation succeeds.
- New premise: use the actual updated inventory store/checkpoint, patch only the
  affected markets in a fork, and refresh the inventory's validation certificate
  before final export.

Native validation reports contain immutable payloads. Rollback snapshots retain
the original report objects and restore their registry entries, without trying
to deep-copy live report/store proxies. This preserves validation certificates
and avoids `cannot pickle 'mappingproxy' object` on newer premise releases.

Native baseline is audit-only in either path; it does not fork a native inventory
or rewrite a legacy scenario cache just to record the selected case. The baseline
records the installed premise version and observed splits. Its legacy interpolated
targets versus newer retained ecoinvent fractions are intentional differences.
If an installed premise file was previously edited manually, native baseline
also reflects those existing settings; the repo does not silently restore vendor
files. Use a clean environment if a pristine vendor baseline is required.

Cases are applied after premise has created the relevant suppliers and markets,
and before final export, rather than patching undocumented package internals.
Both export types use the same helper. Classification safeguards and LCIA-factor
selection remain separate: a metal supply policy must not conceal missing
contributions or change the selected characterization method.

## Audits and extending the policy

Every run writes timestamped CSV/JSON reports beneath `export/metals_policy/`.
They record policy/source fingerprints, installed premise/ecoinvent versions,
scenario/year, observed and requested/effective shares, affected identities,
changed supplies and skipped/unsupported markets. Inspect the actual audit rather
than assuming the selector changed every market in a sector.
The scientific metadata and supplier records identify the evidence, target
assumptions, approved secondary routes and any primary-remainder exception, so
an applied percentage can be traced to its source and classification boundary.
For file-based reference/minimum cases, `share_data_evidence_family` and
`share_data_evidence` also carry the selected CSV family's basis, confidence,
source URLs, full qualification notes and scoped exclusions into each market
audit. The combined evidence file is fingerprinted and included in package
provenance. Requested/applied curves still come from the selected named file,
including any validated manual edits. Native baseline remains audit-only and
does not acquire a dependency on the evidence files.

The configuration is `configuration_file/metals_scenario_policy.yaml`; numeric
case inputs live in the share-data folder. To extend it:

1. Add an explicit case under `nonferrous_cases`. Use `minimum`
   for an increase-only floor or `target` for an intentionally exact share.
2. Add the case's named data file mapping under
   `share_data.nonferrous` and provide validated product records with primary and
   secondary year/share curves. State the source, denominator, grade and uncertainty; shares must be
   between 0 and 1. Keep numeric assumptions out of the policy YAML.
3. For a new market, give its exact name, reference product, unit and locations,
   with secondary-route matching and an unambiguous primary recipe. A declared
   `supply_products` whitelist is required for any intentional grade-equivalent
   interface; do not introduce one just to make validation pass.
4. Test the actual recipes in both supported environments, including interpolation,
   mass balance, signs, uncertainty, supplier availability and repeat-application
   protection. Review audits for zero-change/unsupported cases.
5. Export from a fresh builder and compare complete signed impact totals.

Ores, concentrates, explicit-primary products, and products without both
appropriate prospective evidence and validated secondary providers remain outside
this curated policy. Zinc, tin, manganese, magnesium, rhodium and rare-earth
extensions are deferred until their specific inventory and denominator gaps are
resolved; that does not mean no literature or recycling technologies exist.
Their native modelling is retained and the configuration documents why. Existing generated placeholder
targets for those products are not active LCI overrides.
An unsupported nonzero target fails rather than manufacturing a recycling route.
For a scientifically stronger high-recycling case, the next step is to couple
product lifetimes, future waste quantities, collection and recovery yields,
grade constraints and scrap trade to technology-specific recovery inventories.
