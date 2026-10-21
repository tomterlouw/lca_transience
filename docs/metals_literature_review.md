# Evidence review for extending non-ferrous recycling scenarios

Review date: **20 October 2026**. This is a scoped, decision-oriented review of
additional metals relevant to the repo's supply-mix policy, not an exhaustive
systematic review of metal circularity. Research was screened for a usable
primary/secondary supply denominator, future year, geography, product grade and
an inventory route that can carry the recycling burdens.

## Decision and scope

The active scope is now **ten product rules**: the original aluminium cast alloy,
copper cathode, nickel class 1, cobalt and lead; World lithium carbonate, battery
grade; and gold, silver, platinum and palladium. The first six numeric targets
remain unchanged. Lithium retains its assumed linear 2020–2050 path. The four
precious metals use documented 2025 supply anchors and separately labelled future
sensitivities, not purported 2050 forecasts. Their curves retain the existing
2020 catalogue point and interpolate piecewise linearly through 2025 to 2050.
Native premise baseline and the fixed-2020 reference meaning are unchanged.
Steel remains endogenous to IAM/EDM-I; no exogenous steel recycling trajectory is
introduced. Zinc, tin and the other candidates below remain deferred, not ruled
out as impossible.

This revises the initial review's overly strict distinction: a validated forecast
is not a prerequisite for a **clearly labelled sensitivity**, provided the source
denominator and inventory boundary are suitable. Analyst assumptions must remain
separate from published results. Conversely, an attractive published percentage
cannot replace a missing recycling inventory or solve scrap availability.

Three distinct objects must not be confused:

1. A reference catalogue can list a primary/secondary fraction for a product.
2. A generated scenario file can contain future numbers for that product.
3. An active policy needs a matching market rule and supported, burden-carrying
   primary/secondary inventories before those numbers can change exchanges.

An entry in a catalogue, or a nonzero generated target, does not by itself
establish either a credible forecast or a usable recycling inventory.

## Latest premise mapping reviewed

The public `master` branch was checked on the review date and pinned to commit
`d5362d8ce1df66c20c7e71c91051a1b88e46c583` (15 October 2026, 15:12:23 UTC).
The repo retains these public source snapshots:

- [Primary/secondary reference catalogue](../configuration_file/upstream/premise_metals/primary_secondary_split.yaml).
- [Secondary supplier mapping](../configuration_file/upstream/premise_metals/secondary_supply_activities.yaml).
- [Source URLs, commit and SHA-256 provenance](../configuration_file/upstream/premise_metals/provenance.json).

The snapshots contain **103 catalogue entries and 12 secondary-supply product
mappings**. Their SHA-256 checksums are respectively
`cb56e74eb4ce0fa2d24cc96f6eacc681df2a6989c4edb7e1cbe346ebea54c8e1` and
`7fa070cf59aae49260d8464e6e7315bed521edfe919ccb591e2eddef5147ed65`.
Both user-supplied YAML attachments matched these snapshots byte for byte.

The mapping's supplier weights describe the composition **within secondary
supply**. They are not the fraction of the entire metal market that is recycled.
Pinned mappings are matching/provenance evidence, not independent validation of
2050 targets. Installed premise files are not overwritten. Native baseline
behaviour therefore remains owned by the installed premise release.

Public upstream sources:
[reference catalogue](https://github.com/polca/premise/blob/d5362d8ce1df66c20c7e71c91051a1b88e46c583/premise/data/metals/primary_secondary_split.yaml)
and [secondary suppliers](https://github.com/polca/premise/blob/d5362d8ce1df66c20c7e71c91051a1b88e46c583/premise/data/metals/secondary_supply_activities.yaml).

## What the literature supports

| Candidate | Evidence and its scope | Decision for this extension |
| --- | --- | --- |
| Lithium | IEA 2026 provides global 2050 secondary volumes and total elemental-Li demand under identifiable demand pathways. An existing lithium-carbonate recycling input can be traced in the native market and upstream mapping. | Add a qualified 2050 minimum-share sensitivity for the battery-grade market; retain the inherited generic-carbonate grade proxy explicitly. |
| Gold and silver | Global 2025 mine/recycled supply statistics are available from the World Gold Council and Silver Institute. Existing same-product recovery LCIs are identified. | Activate observed-anchor sensitivities with explicit future increments. Gold's recovery mix remains a technology proxy; silver's Parkes-process input is excluded from the claimed secondary fraction. |
| Platinum and palladium | Johnson Matthey 2026 reports 2025 open-loop secondary and primary supply. Gross closed-loop ratios and technology-specific 2050 studies use different denominators. | Activate open-loop observed-anchor sensitivities using existing recovery routes; do not add closed-loop circulation again. |
| Zinc | Refined-metal studies and the newer Rostek et al. global zinc-cycle model provide prospective evidence, but refined metal, direct scrap reuse and recycled concentrate are different boundaries. | Defer activation until a complete appropriate refined-zinc recovery/refining chain and matching scenario denominator are connected. |
| Tin | A 2026 primary study explores 40–60% refined-tin input sensitivities; an older open inventory chain documents detinning and secondary-tin purification. | A credible next candidate. Defer activation until the chain, yields, energy units and current provider links are checked; do not treat a sensitivity as a forecast. |
| Manganese | A supported whole-metal future recycled-input trajectory and matching refined-product recovery boundary were not established here. Battery-material evidence alone is insufficient. | Retain native modelling. |
| Magnesium | Global modelling distinguishes functional recovery of magnesium metal from magnesium retained in aluminium alloys. | Defer until those supply products and burdens are separated; nonfunctional alloy circulation does not supply generic magnesium metal. |
| Graphite and magnet rare earths | Future secondary-supply and recycling LCI studies exist, but product-grade and process interfaces must match the modelled market. | Retain native modelling until the interfaces are established; do not assign magnet recycling to every rare-earth oxide. |
| Rhodium | A 2025 integrated-refinery inventory includes rhodium alongside Au/Ag/Pt/Pd. | Candidate for an additional LCI/coupling; not part of the current ten-rule extension. |
| Ores, concentrates and explicitly primary products | These are different stages or explicitly defined product types, not generic refined-metal supply markets. | No blanket recycled-content override. A separate documented process chain would be needed. |

## Lithium: evidence, arithmetic and limitations

The [IEA's Global Critical Minerals Outlook 2026](https://www.iea.org/reports/global-critical-minerals-outlook-2026),
published 16 July 2026, is the newer source used here. Its
[official PDF](https://iea.blob.core.windows.net/assets/19c60aa0-ee07-4a20-a138-33c967ac5008/GlobalCriticalMineralsOutlook2026.pdf)
gives roughly **250 kt Li** of 2050 secondary supply in the base/STEPS narrative
context and **560 kt Li** in HDS on page 145. Annex page 350 gives total global
2050 Li demand of **1,122 kt** in STEPS and **1,501 kt** in HDS. Both page numbers
are one-based PDF/printed page numbers. The 250 kt figure is associated with
STEPS from the surrounding narrative rather than an explicitly labelled table
cell, so the resulting ratio is an approximate source-context derivation.

| Repo minimum case | Source interpretation | 2050 secondary input floor |
| --- | --- | ---: |
| `conservative` | STEPS-derived ratio | `250 / 1122`, approximately **22.3%** |
| `moderate_recycling` | Same supported STEPS ratio; no invented intermediate case | `250 / 1122`, approximately **22.3%** |
| `high_recycling` | HDS-derived ratio | `560 / 1501`, approximately **37.3%** |

The source denominator is **global total lithium demand across uses**, measured
as elemental Li. It is not the quantity of scrap available, a collection rate,
or a lithium-carbonate production forecast. Mapping the two IEA demand pathways
to the repo's three sensitivity names is a modelling decision; those names do
not make the repo scenarios identical to IEA scenarios.

The earlier [IEA Recycling of Critical Minerals report (2024)](https://www.iea.org/reports/recycling-of-critical-minerals/executive-summary)
supports substantial future battery-metal recycling, reporting 20-30% of demand
in its APS discussion and highlighting collection-rate sensitivity. It also
distinguishes Europe from global results. Those broad ranges are not a licence
to transfer the same percentage between metals, regions, years or chemical
grades. The 2026 element-specific volume/demand calculation is used for the new
lithium endpoint instead of treating the older 20/25/30% placeholders as exact
newer forecasts.

### How this is applied in the model

- The affected market is `market for lithium carbonate, battery grade`, product
  `lithium carbonate, battery grade`, location `World`. The policy does not
  invent separate regional battery-grade supply fractions.
- The native market already includes `treatment of used Li-ion battery,
  hydrometallurgical treatment`, with output `lithium carbonate`, location `GLO`.
  Reusing this inherited generic-carbonate input is a declared grade-interface
  proxy, not proof of battery-grade purity or a newly modelled purification step.
  Its existing recycling burdens are retained; the recovered material is not
  assigned zero environmental burden.
- At the user's request, minimum-share cases use the same linear interpolation
  as the other metals: `floor(y) = s2020 + (s2050 - s2020) * (y - 2020) / 30`,
  clamped to the endpoint fractions outside 2020-2050. The 2020 model reference
  is approximately 0.0840% secondary input. No independent 2030/2040 lithium
  waypoints are asserted: the straight line is an explicit scenario assumption,
  not an IEA recycling trajectory, and can imply earlier uptake than a stock-flow
  model. The former 2050-only gate is not enabled for lithium.
- At the declared target year, the applied secondary share is the larger of the
  native share and the literature-derived floor. Primary supply is the remainder
  of the metal supply mix. Gross material demand and existing distribution
  losses are preserved.
- `baseline` remains a no-op. `reference` is the documented fixed-2020 model
  fraction, not a measured contemporary global lithium share. `primary_only`
  remains an explicit zero-secondary counterfactual, subject to supplier checks.

This is a conditional representative-compound sensitivity. It is not a solved
scrap-stock model, a demonstrated future supply of battery-grade carbonate, or a
prediction that the existing hydrometallurgical technology will provide every
future tonne of recovered lithium. These limitations should be included when
interpreting the resulting impacts.

## Precious metals: implemented anchors and scenario assumptions

The [World Gold Council's full-year 2025 supply statistics](https://www.gold.org/goldhub/research/gold-demand-trends/gold-demand-trends-full-year-2025/supply)
(29 January 2026) report 3,671.6 t mined and 1,404.3 t recycled. The physical
new-supply calculation `recycled / (mined + recycled)` is about **27.67%**.
Using reported total supply instead gives about 28.07%, because that denominator
includes net producer hedging. The physical mine-plus-recycled denominator is
used for the 2025 anchor; it is not a measured 2050 value.

The [Silver Institute's World Silver Survey 2026](https://silverinstitute.org/wp-content/uploads/2026/04/World-Silver-Survey-2026.pdf)
reports 2025 mine supply of 846.6 Moz and recycling of 197.6 Moz: approximately
**18.92%** of their sum. Net hedging and official-sector sales are excluded.
A photovoltaic-only recovery potential has a different denominator.

[Johnson Matthey's May 2026 PGM Market Report](https://matthey.com/documents/161599/509428/pgm-market-report-26.pdf/a2d115af-bf7c-f589-29e9-6beacf8a4452)
gives primary and open-loop secondary supply for 2025 in its tables on printed
pages 27–30. Platinum is 5,557 and 1,547 thousand troy ounces, respectively;
palladium is 6,589 and 3,135 thousand ounces. Secondary divided by combined
primary and secondary supply yields approximately 21.78% Pt and 32.24% Pd.
Industrial demand in the report is net of closed-loop recycling. Those closed
loops are not added to market supply a second time.

| Metal | 2025 observed anchor | Conservative 2050 floor | Moderate 2050 floor | High 2050 floor |
| --- | ---: | ---: | ---: | ---: |
| Gold | 27.6660% | 27.6660% | 37.6660% | 47.6660% |
| Silver | 18.9236% | 18.9236% | 28.9236% | 38.9236% |
| Platinum | 21.7765% | 21.7765% | 31.7765% | 41.7765% |
| Palladium | 32.2398% | 32.2398% | 42.2398% | 52.2398% |

Conservative holds the 2025 fraction through 2050. Moderate and high add
**10 and 20 percentage points**, respectively, as analyst-selected sensitivity
increments. They are not source forecasts, confidence bounds or stock-flow
estimates. All cases use the same observed 2025 anchor, retaining the existing
2020 catalogue point, with piecewise-linear interpolation and endpoint clamping.
The applied fraction is `max(native_share, floor(year))`; a named case may thus
make no change if native supply already exceeds the floor. The original six
products and their curves are not recalibrated by this extension.

The global anchors are applied as representative supply assumptions to the
configured World/GLO markets. The 2020 model reference is not replaced by a
2025 observed fraction, and neither reference nor baseline becomes a historical
validation exercise. High/mid-case uptake is not constrained by actual waste
stocks, refinery capacity, trade or prices.

### Inventory routes and the silver classification exception

Existing LCIs are reweighted, not replaced with published impact factors. Gold
uses `treatment of precious metal from electronics scrap, in anode slime,
precious metal extraction`; this is a declared proxy for the broader recycled
gold mix, including routes not represented by e-waste. An exact local name rule
accepts actual supplier geographies because the pinned GLO selector and the
ecoinvent 3.12 SE/RoW metadata do not fully coincide. Platinum and palladium retain
the pinned name/product/location selector checks and existing recovery burdens.

For silver, approved secondary routes are electronics-scrap precious-metal
extraction and waste-x-ray-film treatment. The pinned upstream file assigns
approximately 70% of its **within-secondary recipe**, not of the entire market,
to `treatment of crust from Parkes process for lead production`. However,
[ecoinvent's primary-lead documentation](https://ecoquery.ecoinvent.org/3.11/cutoff/dataset/3511)
states that primary lead production from concentrate also produces Parkes crust.
Therefore this activity cannot be asserted to be recycled silver solely because
it is called a treatment activity.

The exact Parkes name is assigned to the **primary remainder** for the policy's
accounting, with an explicit reason. This is not a verified statement that all
of its feedstock is primary; unresolved industrial residue origins remain in the
non-secondary fraction. Its exchanges and burdens are retained and scaled with
that remainder. Other unknown treatment routes are not automatically admitted
as primary. The exception is scoped, recorded in audits, and does not modify
the pinned upstream files or the native-baseline inventory.

### A documented future inventory improvement, not an imported dataset

[Cespi et al. (2025), DOI 10.1016/j.cesys.2025.100310](https://doi.org/10.1016/j.cesys.2025.100310)
provide an integrated refinery LCI for Au, Ag, Pt, Pd and Rh based on operating
data from an Italian plant. Waste inputs include production scrap, spent
catalysts, liquid waste and incineration ash. The
[associated inventory/data-quality dataset](https://amsacta.unibo.it/id/eprint/8288/)
is publicly available under CC BY-NC 4.0. It is a promising alternative to narrow
single-feedstock proxies, subject to system-model, allocation, collection,
preprocessing, product-purity, geographic representativeness and licence checks.
It is documented but **not integrated** in this extension. Existing recycling
LCIs are not claimed to have been rebuilt from this paper.

### Evidence deliberately not transferred to market fractions

[Ryan et al. (2026), ACS Omega, DOI 10.1021/acsomega.5c11916](https://doi.org/10.1021/acsomega.5c11916)
estimate contemporary recycled inputs of about **65% for Pt and 45% for Pd**
when closed-loop circulation is included in gross use. Metal retained by its
owner and returned through a closed loop is not necessarily available to buyers
in an open market. Applying these gross-use ratios to a traded-market inventory
could double count recycling already represented within a customer process.
The paper supports explicit sourcing analysis, but these gross-use values are
not the anchors used here. The earlier generic 15/25/35% PGM placeholders are
not activated as literature-validated targets; active Pt/Pd have separate
observed-anchor evidence rows.

Two further primary studies illustrate the scope problem:

- [Ibrahim and Eggert (2026), Resources, DOI 10.3390/resources15080106](https://www.mdpi.com/2079-9276/15/8/106)
  find 2050 secondary Pt covering 75.1% of global PEM fuel-cell Pt demand and
  32.2% of PEM-electrolyzer Pt demand in their central case. Neither percentage
  describes the whole platinum market, and recovery potential is not guaranteed
  market supply.
- [Gu et al. (2026), Earth's Future, DOI 10.1029/2026EF009070](https://agupubs.onlinelibrary.wiley.com/doi/10.1029/2026EF009070)
  provide global PGM metabolism evidence for **2011-2025**, not an auditable 2050
  recycled-input trajectory.

## Other primary sources and remaining inventory gaps

[Van der Voet et al. (2019 issue; first published 2018), DOI 10.1111/jiec.12722](https://doi.org/10.1111/jiec.12722)
study technology-specific global supply scenarios for seven major metals. Its
approximately 13% refined-zinc secondary share in 2050 is relevant evidence, but
does not supply a compatible refined-zinc recycling LCI for this repo. A mapped
secondary concentrate is not an interchangeable refined-metal product.
[Rostek et al. (2023), DOI 10.1016/j.rcradv.2023.200166](https://doi.org/10.1016/j.rcradv.2023.200166)
provide a newer global zinc-cycle assessment with recycling-as-usual and
recycling-improvement cases, yielding roughly 9–14 Mt/year of recycled zinc in
2050. These flows must be extracted at a matching refined-product boundary;
dividing them by mined output, or applying them directly to concentrate, would
be incorrect. A complete recovery/refining chain remains the activation gap.

[Chowdhury et al. (2026), RSC Sustainability, DOI 10.1039/D5SU00956A](https://doi.org/10.1039/D5SU00956A)
distinguish historical total tin recycling input of around 30-35%, substantially
associated with new manufacturing scrap, from lower end-of-life input. Their
40-60% cases are sensitivities, not established annual 2050 market shares.
The absence of a validated forecast is not itself a reason to forbid a labelled
tin sensitivity. The remaining activation gap is the inventory chain.
[Haupt et al. (2018), DOI 10.1016/j.dib.2018.05.067](https://doi.org/10.1016/j.dib.2018.05.067)
document collection, sorting, electrolytic detinning and high-grade secondary-tin
purification. This is a concrete starting point, but its older data, yields,
energy units, collection boundaries and present supplier links must be checked
before integration. No tin process is invented or activated here.

[Golroudbary et al., published online 2022, DOI 10.1080/08827508.2022.2084734](https://doi.org/10.1080/08827508.2022.2084734)
model 2050 functional magnesium recycling around 161 kt and nonfunctional
recycling around 1 Mt. Magnesium retained in aluminium alloys does not supply a
generic magnesium-metal market. Likewise, manganese battery-recovery potentials
must not become whole-metal shares; route-specific recovery may be low, as
discussed in [Maisel et al. (2026), DOI 10.1016/j.wasman.2026.115873](https://doi.org/10.1016/j.wasman.2026.115873).

[Wang et al. (2025), DOI 10.1016/j.resconrec.2024.108106](https://doi.org/10.1016/j.resconrec.2024.108106)
provide LCIs for five rare-earth magnet recycling routes. These support a
product-specific extension, not a uniform recycled fraction for all rare-earth
oxides. Their results also show that recycled routes are not automatically
lower-impact than primary production. These inventory opportunities remain
documented candidates rather than active rules.

## Acceptance criteria for the next extension

Before activating another product, establish all of the following:

1. A primary source identifies the share denominator, geography, product or
   elemental basis, year and scenario. Distinguish annual supply from cumulative
   recovery and potential from actual supply; do not substitute avoided mining
   requirements for recycled input.
2. Metal-grade interfaces are exact or explicitly inherited and qualified. Any
   additional refining, purification or yield losses require a supported LCI.
3. Secondary suppliers actually exist in the scenario database and retain
   collection/recovery/refining burdens under the selected system model.
   A supplier mapping alone is not a constructed inventory.
4. Co-product allocation, closed-loop ownership, scrap trade and existing
   recycling inside downstream processes are checked for double counting.
5. Rules, numeric inputs, audits and tests are connected. Unsupported products
   remain native and visibly reported, rather than becoming zero-impact
   secondary suppliers or silently receiving a family-wide percentage.

## Reproducibility and interpretation of results

Keep the pinned upstream provenance, evidence inputs, generated-share manifest,
policy hash and actual applied-share audit with each exported scenario package.
The catalogue's extent is not the policy's extent. Compare actual audited shares,
not only the named YAML targets, because native shares may already exceed a floor.

The existing metals workbook retains its 103-row `Shares` source and includes
`Scenario assumptions` and `Evidence` documentation of active scope, source
components, derived anchors, analyst increments and deferred candidates. Those
documentation sheets are an explanatory snapshot, not another runtime parameter
store. Default YAML mode uses the evidence/mapping CSVs and named YAML files;
XLSX source mode reads `Shares` only. Reports retain scientific assumptions and
supplier-classification details with the applied fractions.

Rebuild the scenario package/databases in notebook 1 and recalculate impacts in
notebook 2 before interpreting this extension. Old matrices or result files do
not acquire these changes automatically. This review does not establish that a
full prospective database/LCA rerun has been completed or that impacts have been
validated against measured plant data. Unit and supplier checks are necessary
but do not remove the stated scenario, technology and grade assumptions.
