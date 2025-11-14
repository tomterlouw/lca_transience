# Environmental Life Cycle Assessment (LCA) Module – TRANSIENCE

This is the documentation for the **Environmental Life Cycle Assessment (LCA) module** developed as part of the **[TRANSIENCE](https://www.transience.eu/)** project. This module is a key component of the **MIC3** framework, enabling environmental assessment of products and services, entire industrial decarbonization pathways, and circular economy transformations. As such, it links to various MIC3 modules developed within the **MIC3** framework.

Use this documentation to:

* Understand the environmental LCA module and its capabilities.
* Install and run the module (if having access to the ecoinvent database, see next requirements).
* Learn about integration with other MIC3 modules.
* Calculate environmental burdens of products, services, and entire energy transformation scenarios that are consistent with the scenarios from the **MIC3** framework.

Additionally, the module is also (independently) linked to two additional repositories developed with the **[TRANSIENCE](https://www.transience.eu/)** project. The first module is developed to provide more attention to climate-effective applications that may support industrial decarbonization:
- `hydrogen_applications` is a repository that enables the quantification of the climate-effectiveness of planned hydrogen projects and applications. It uses data obtained from the International Energy Agency (IEA), available via: https://github.com/tomterlouw/hydrogen_applications. The results have been reported in the following research paper:
*Terlouw, T., Moretti, C., Harpprecht, C., Sacchi, R., McKenna, R., & Bauer, C. (2025). Global greenhouse gas emissions mitigation potential of existing and planned hydrogen projects. Nature Energy. DOI: 10.1038/s41560-025-01892-9. https://www.nature.com/articles/s41560-025-01892-9.*

Second, another repository is developed to determine the climate effectiveness of the European Union Carbon Border Adjustment Mechanism (CBAM):
- `Steel_CBAM` is a repository designed to quantify the climate-effectiveness of the European Union's Carbon Border Adjustment Mechanism (CBAM) for the global steel industry. It combines (prospective) life cycle assessment (LCA) with regionalized production data.  Available via: https://github.com/tomterlouw/Steel_CBAM. Reported in the following preprint: ... .

---

## First Steps

This section guides you through getting started with the environmental LCA module as part of the **MIC3** framework:

1. **Clone the repository**:

```bash
git clone https://github.com/tomterlouw/lca_transience.git
cd lca_transience
```

2. **Set up the Python environment** using `lca_transience.yml`.
3. **Obtain required credentials**: `KEY_PREMISE` (for `premise`) and `USER_PW` (for ecoinvent).
4. **Run the notebooks**:

   * `1_export_packages.ipynb` to generate new background LCA databases for each user scenario and export as scenario data packages.
   * `2_calc_impacts.ipynb` import data packages (generated in `1_export_packages.ipynb`) and calculate overall environmental burdens of entire energy system model pathways, products, or services.

---

## Installation

Dependencies and setup:

* Python 3.11
* Required packages are listed in `lca_transience.yml`. Install via:

```bash
conda env create -f lca_transience.yml
conda activate pathw
```

* Credentials required:

  * **`KEY_PREMISE`**: Access for premise background database modifications.
  * **`USER_PW`**: Password for the ecoinvent database. Ecoinvent database access with the password and username as used on the **[ecoinvent](https://ecoinvent.org/)** website.

> Note: Scenario data, developed within the **MIC3** framework, to modify background LCA databases is not yet provided open-source but will be included in future releases.

---

## Tutorial

1. **Define scenarios** in `1_export_packages.ipynb`: integrate circular economy measures, and the outputs from **MIC3** modules (currently OPEN-PROM, I-TOM, and FORECAST scenarios) to modify the background LCA database.
2. **Export LCA packages**: transformed databases and scenario assumptions exported to local disk.
3. **Calculate environmental burdens** in `2_calc_impacts.ipynb` by specifying year, scenario, and desired impact categories:

   * Climate change (tCO₂-eq.).
   * Various critical raw materials.
   * Human health.
   * Water consumption.

Detailed instructions and code examples are embedded in the notebooks.

---

## Model Overview

The LCA module provides:

* **Prospective LCA integration** using `premise`.
* **Linking with energy system scenarios** (Task 4.6).
* **Assessment of products and industrial transformations** (Tasks 4.2 & 4.4) using `pathways`.
* **Spatially explicit and regionalized environmental assessments**. This will be integrated later using the novel open-source package `edges`.

### Context and Main Features

* Part of **MIC3 framework** for industrial decarbonization and circular economy modeling.
* Primary outputs: life cycle environmental impacts (climate change, resource use, human health).
* Modular, flexible, and compatible with other MIC3 modules. Further integration is work in progress.

---

## Structure

The module contains the following submodules:

```text
code/
├── configuration_file/       # Scenario setup
├── inventories/              # Additional LCA datasets
├── pathways/                 # Energy/material transition outputs
├── 1_export_packages.ipynb   # Notebook to export LCA packages
├── 2_calc_impacts.ipynb      # Notebook to calculate environmental impacts
├── config.py                 # Module parameters
├── datapackage_*.json        # Scenario definitions
├── lca_transience.yml        # Environment setup
```

---

## Mathematical Foundation

* LCA impacts are computed according to standard life cycle assessment methods using the [brightway 2.5](https://learn.brightway.dev/en/latest/content/chapters/BW25/BW25_introduction.html) framework.
* Modifications to the background database reflect prospective technological, material, and energy transformations. 
* Impact aggregation uses [premise's](https://github.com/polca/premise/tree/master) scenario-driven transformation approaches.

---

## Code Organisation

* **Notebooks**: Main user interface for scenario export and impact calculation.
* **Configuration files**: Parameter settings for linking with MIC3 modules.
* **Data packages**: JSON files containing scenario assumptions.
* **Submodules**: Separate pathways, inventories, and configuration for modularity.

---

## Parameters

Parameters are set in `config.py` and include:

* Key variables related to the background ecoinvent database used.

---

## Data Inputs

* Background LCA databases (ecoinvent, premise-transformed).
* Scenario assumptions (from MIC3 modules).
* Additional inventories in `inventories/`.

---

## Configuration

* Configurations are stored in `configuration_file/` for each linked module.
* Define scenario-specific transformations and parameter overrides.

---

## Constraints

* Model assumes the availability of scenario data from MIC3 modules.
* Results are indicative for early-stage integration and may be refined in future iterations.

---

## Data Outputs

* Environmental burdens (per scenario, region, year).
* Life cycle impact indicators (e.g., climate change, resource use, human health).
* Scenario comparison plots (available via `2_calc_impacts.ipynb`).

---

## Integration with Other Models

### Inputs from Other Modules

* Energy system outputs (Task 4.6) such as energy mix and industrial transformations curently from OPEN-PROM (electricity mix), FORECAST (country-specific methanol and ammonia production mixes), and I-TOM (European steel production mix).
* Circular economy and product-specific scenario data (Tasks 4.2 & 4.4).

### Outputs to Other Modules

* Regionalized life cycle impact data for MIC3 decision support.
* Aggregated impact indicators for scenario comparison.

---

## Example questions the environmental LCA module can address:

* What is the environmental impact of a product or service now and in future considering prospective decarbonization scenarios sourced from the MIC3 framework?
* What are the environmental burdens of entire energy system transformation pathways developed in the MIC3 framework? 
   For example, environmental impacts on entire industrial or energy transformation pathways on (critical) materials, greenhouse gases, human toxicity, and/or biodiversity.
* How does industrial decarbonization affect regional life cycle GHG emissions now and in the future?
* What are the regional environmental impact on local water scarcity and biodiversity (to be added in the future)?
* What is the climate-effectiveness of the CBAM for the steel industry (see `Steel_CBAM`)?
* What is the climate-effectiveness of various hydrogen applications (see `hydrogen_applications`)?

---

## License and Citation

Please refer to the [`LICENSE`](./LICENSE) licensing file. Details on citing this repository will be provided as soon there is an initial publication.

---

## Contributing

For contributions, suggestions, or collaborations, please contact:
**Tom Terlouw** – [tom.terlouw@psi.ch](mailto:tom.terlouw@psi.ch)

Other contributors:

* **Romain Sacchi (PSI)**
* **Christian Bauer (PSI)**

---

## Acknowledgements

* Developed as part of **TRANSIENCE** (Horizon Europe Project No. 101137606).
* Supported by HADEA, SERI, and UKRI Horizon Europe Guarantee.

> Disclaimer: The current results shown are illustrative and continously updated throughout the project; full integration of MIC3 modules is ongoing.