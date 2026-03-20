# Environmental Life Cycle Assessment (LCA) Module – TRANSIENCE

This is the documentation for the **Environmental Life Cycle Assessment (LCA) module** developed as part of the **[TRANSIENCE](https://www.transience.eu/)** project. This module is a key component of the **MIC3** framework, enabling environmental assessment of products and services, entire industrial decarbonization pathways, and circular economy transformations. As such, it links to various MIC3 modules developed within the **MIC3** framework.

Use this documentation to:

- Understand the environmental LCA module and its capabilities.
- Install and run the module (if having access to the ecoinvent database, see next requirements).
- Learn about integration with other MIC3 modules.
- Calculate environmental burdens of products, services, and entire energy transformation scenarios that are consistent with the scenarios from the **MIC3** framework.
- Perform case studies for the **Port of Rotterdam (PoR)**, enabling targeted environmental assessments of industrial systems and transitions in the Rotterdam port area.

Additionally, the module is also (independently) linked to two additional repositories developed within the **TRANSIENCE** project:

- `hydrogen_applications`: Quantifies the climate-effectiveness of planned hydrogen projects and applications using IEA data.  
  https://github.com/tomterlouw/hydrogen_applications

- `Steel_CBAM`: Quantifies the alignment in terms of GHG emission accounting under the EU Carbon Border Adjustment Mechanism (CBAM) for the global steel industry.
   https://github.com/tomterlouw/Steel_CBAM 

---

## First Steps

1. Clone the repository:

```bash
git clone https://github.com/tomterlouw/lca_transience.git
cd lca_transience
```

2. Set up the Python environment using `lca_transience.yml`.

3. Obtain required credentials:
   - `KEY_PREMISE` (for premise)
   - `USER_PW` (for ecoinvent)

4. Run the workflows:

   - `1_export_packages.ipynb` to generate scenario-specific LCA databases.
   - `2_calc_impacts.ipynb` to calculate environmental impacts.

5. Optional preprocessing:

   - `0_export_act_to_excel.py` to extract activity-level data.
   - `0_convert_to_iamc.py` to convert results from ITOM for the Port of Rotterdam to IAMC format.

---

## Installation

Dependencies and setup:

- Python.
- Required packages are listed in `lca_transience.yml`.

Install via:

```bash
conda env create -f lca_transience.yml
conda activate pathw
```

Credentials required:

- `KEY_PREMISE`: Access for premise background database transformations
- `USER_PW`: Credentials for ecoinvent database access

Note: Scenario data from MIC3 modules is not yet fully open-source.

---

## Tutorial

1. Define scenarios in `1_export_packages.ipynb`:
   - Integrate MIC3 outputs (OPEN-PROM, I-TOM, FORECAST)

2. Export LCA packages:
   - Scenario-specific background databases are generated and stored

3. Calculate impacts in `2_calc_impacts.ipynb`:
   - Select year, scenario, and impact categories
   - Supports both system-wide analysis and **PoR-focused case studies**

Impact categories include:

- Climate change (tCO2-eq.)
- Critical raw materials
- Human health
- Water consumption

---

## Model Overview

The LCA module provides:

- Prospective LCA integration using `premise`
- Linking with energy system scenarios
- Assessment of products and industrial transformations using `pathways`
- Support for **Port of Rotterdam (PoR) case studies**, enabling detailed analysis of industrial clusters and transition pathways in the Rotterdam port area
- Spatially explicit assessments (future integration with `edges`)

### Context and Main Features

- Part of the MIC3 framework for industrial decarbonization and circular economy modeling
- Modular and flexible design
- Supports both large-scale system assessments and regional case studies such as PoR

---

## Structure

The repository is structured as follows:

```text
LCA_TRANSIENCE/
├── configuration_file/
├── data/
├── figs/
├── inventories/
├── scenario_data/
├── stats/
├── 0_convert_to_iamc.py
├── 0_export_act_to_excel.py
├── 1_export_packages.ipynb
├── 2_calc_impacts.ipynb
├── config.py
├── datapackage_*.json
├── lca_transience.yml
├── regionalization.py
├── README.md
```

---

## Mathematical Foundation

- Based on standard life cycle assessment using the Brightway 2.5 framework
- Background database transformations are driven by `premise`
- Scenario-dependent modifications reflect future energy and industrial transitions

---

## Code Organisation

- Notebooks serve as the main user interface
- Scripts support preprocessing and data export
- JSON datapackages define scenarios
- Configuration files control model behavior

---

## Parameters

Defined in `config.py`, including:

- Background database settings
- Scenario selection
- Impact categories

---

## Data Inputs

- Ecoinvent database (via Brightway)
- Premise-transformed scenarios
- MIC3 scenario outputs
- Additional inventories

---

## Configuration

- Located in `configuration_file/`
- Allows linking to MIC3 modules and defining **PoR-specific setups**

---

## Constraints

- Requires access to ecoinvent and premise
- Some MIC3 scenario inputs are not publicly available
- PoR case studies require well-defined system boundaries and regional assumptions

---

## Data Outputs

- Environmental impacts per scenario, region, and year
- Indicators for climate change, resources, and health
- Results for single processes, full system analyses, and **Port of Rotterdam case studies**

---

## Integration with Other Models

### Inputs from Other Modules

- OPEN-PROM (electricity mix)
- FORECAST (chemical production mixes)
- I-TOM (steel production, Port of Rotterdam results for one scenario)

### Outputs to Other Modules

- Regionalized environmental indicators
- Scenario comparison results

---

## Example questions the module can address

- What is the environmental impact of a product or service under future scenarios?
- What are the environmental burdens of industrial transformation pathways?
- How does decarbonization affect emissions regionally?
- What are the environmental impacts of transition pathways in the **Port of Rotterdam**?
- How do different industrial configurations in PoR affect emissions and resource use?
- What is the alignemtn of emissions captured in CBAM for the steel industry (see `Steel_CBAM`)?
- What is the climate-effectiveness of hydrogen applications (see `hydrogen_applications`)?

---

## License and Citation

Please refer to the LICENSE file. Citation details will follow with publication.

---

## Contributing

For contributions or questions:

Tom Terlouw  
tom.terlouw@psi.ch  

Other contributors:

- Romain Sacchi (PSI)
- Christian Bauer (PSI)
- Gergő Sütő (Utrecht University)

---

## Acknowledgements

- Developed as part of TRANSIENCE (Horizon Europe Project No. 101137606)
- Supported by HADEA, SERI, and UKRI Horizon Europe Guarantee

Disclaimer: Results are continuously updated; full integration of MIC3 modules is ongoing.