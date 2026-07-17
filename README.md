# LCA_TRANSIENCE

### Prospective Life Cycle Assessment Framework for Industrial Transition Pathways

[![Python](https://img.shields.io/badge/python-3.11+-blue.svg)]()
[![License:
BSD-3-Clause](https://img.shields.io/badge/License-BSD%203--Clause-blue.svg)](LICENSE)

**LCA_TRANSIENCE** is an open and modular framework for **prospective
Life Cycle Assessment (LCA)** of future industrial systems.

Developed within the **TRANSIENCE (Horizon Europe)** project, the
framework couples **Integrated Assessment Models (IAMs)**, **industrial
energy system models**, and **prospective Life Cycle Inventory (LCI)
databases** to quantify the environmental impacts of future industrial
transition pathways.

Originally developed to evaluate prospective energy system
transformations, the framework has now been extended to support
**regional and industrial cluster case studies**, allowing users to
evaluate environmental implications of detailed industrial
transformation pathways. The current implementation demonstrates this
capability for the **Port of Rotterdam (PoR)**, although the framework
is designed to be transferable to other industrial regions.

------------------------------------------------------------------------

# Workflow

The framework consists of four main steps.

## Step 1 - Prepare industrial scenario data

Industrial energy system model outputs are first converted into the
**IAMC format**, providing a standardized interface between external
scenario models and the LCA framework.

The repository currently includes converters for several TRANSIENCE
modules. For example,

``` text
0_convert_ITOM_por_to_IAMC.py
```

converts **ITOM Port of Rotterdam** outputs into IAMC-formatted scenario
files.

The resulting scenario files are stored in:

``` text
scenario_data/
```

and contain scenario-dependent production volumes, technology
deployment, energy demand, market shares, and other variables required
to modify foreground life cycle inventories.

------------------------------------------------------------------------

## Step 2 - Generate scenario-specific datapackages

Future industrial transition pathways are incorporated into the LCA
framework by **coupling IAMC-formatted scenario outputs to Life Cycle
Inventories (LCIs)**.

This coupling is defined through modular YAML configuration files
located in:

``` text
configuration_file/
├── config_edm_iyaml
├── config_forecast.yaml
├── config_itom_por.yaml
└── config_open_prom.yaml
```

Each YAML file specifies how IAMC variables correspond to existing
ecoinvent activities.

Example:

``` yaml
steel_primary_dri_ng_ccs:

  production volume:
    variable: Production|Steel|DRI/EAF_NG_CCS

  ecoinvent alias:
    name: steel production, natural gas-based direct reduction iron-electric arc furnace, with carbon capture and storage, low-alloyed
    reference product: steel, low-alloyed
    exists in original database: True
```

The YAML files can also define entirely new **scenario-dependent market
activities**, allowing multiple production technologies to be combined
into new foreground markets that replace default ecoinvent markets.

Example:

``` yaml
markets:
  - name: market for steel, low-alloyed (SPS)
    reference product: steel, low-alloyed
    unit: kilogram

    includes:
      - steel_secondary
      - steel_primary
      - steel_primary_ccs
      - steel_primary_dri_h2
      - steel_primary_dri_ng
      - steel_primary_mo_electrolysis
      - steel_primary_dri_ng_ccs

    replaces:
      - name: market for steel, low-alloyed
        product: steel, low-alloyed
        location: EUR

      - name: market for steel, low-alloyed
        product: steel, low-alloyed
        location: RER

      - name: market for steel, low-alloyed
        product: steel, low-alloyed
        location: NEU

      - name: market for steel, low-alloyed
        product: steel, low-alloyed
        location: Europe without Switzerland
```

Run:

``` bash
1_export_packages.ipynb
```

to generate Brightway datapackages containing all inventory
modifications.

------------------------------------------------------------------------

## Step 3 - Build prospective LCA databases

Import the generated datapackages into Brightway to construct
prospective scenario-specific LCA databases by combining the modified
foreground inventories with **premise**-generated prospective background
databases.

------------------------------------------------------------------------

## Step 4 - Calculate impacts

Run:

``` bash
2_calc_impacts.ipynb
```

to calculate environmental impacts for products, industrial processes,
supply chains, industrial clusters, multiple scenarios, future years and
LCIA methods.

------------------------------------------------------------------------

---

# Data Requirements

The framework requires:

- ecoinvent database
- premise
- Brightway
- IAM scenario outputs
- Industrial model outputs (optional)

Some scenario datasets are project-specific and therefore are not included in this repository.

---

# Installation

Clone the repository

```bash
git clone https://github.com/tomterlouw/lca_transience.git

cd lca_transience
```

Create the conda environment

```bash
conda env create -f lca_transience.yml

conda activate pathw
```

Required credentials:

- ecoinvent account
- premise access credentials

---

# Applications

The framework can be used for:

- Industrial decarbonization
- Regional transition pathways
- Chemical industry
- Steel industry
- Circular economy
- Prospective LCA
- Industrial cluster analysis
- Future technology assessment
- Policy support
- Scenario comparison

---

# Related Repositories

### Hydrogen Applications

Assessment of the climate effectiveness of future hydrogen deployment.

https://github.com/tomterlouw/hydrogen_applications

---

### Steel_CBAM

Assessment of future greenhouse gas emissions of the global steel industry and their link to CBAM.

https://github.com/tomterlouw/Steel_CBAM

---

# Future Developments

Planned extensions include:

- Additional industrial clusters
- Improved regionalization
- Dynamic material circularity
- Automated IAM mapping
- Additional industrial sectors
- Expanded LCIA indicators
- Improved visualization tools

---

# Contributors

**Tom Terlouw**

Paul Scherrer Institute (PSI)

Laboratory for Energy Systems Analysis

---

Additional contributors

- Romain Sacchi (PSI)
- Christian Bauer (PSI)
- Gergő Sütő (Utrecht University)

---

# Acknowledgements

This work has been developed within the **TRANSIENCE** Horizon Europe project (Grant Agreement No. 101137606).

The framework builds upon:

- Brightway
- premise
- ecoinvent

Special thanks to all TRANSIENCE project partners for providing scenario data and industrial model outputs.

---

# License

See the LICENSE file.

---

# Citation

Citation information will be added following publication of the accompanying scientific work.
