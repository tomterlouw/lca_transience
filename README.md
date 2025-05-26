# Environmental life cycle assessment (LCA) module – TRANSIENCE

This repository contains the code, data structures, and documentation for the **Environmental Life Cycle Assessment (LCA) module** developed during the **[TRANSIENCE](https://www.transience.eu/)** project, related to project Deliverable 4.7. 

This repository is a key component of the **MIC3** framework (Model for European Industry Circularity and Climate Change Mitigation), enabling prospective environmental assessment of products, industrial decarbonization pathways, and circular economy pathways.

It builds upon the existing **prospective LCA frameworks** `premise` and `pathways` to calculate environmental burdens of energy transition pathways:
- [`premise`](https://github.com/polca/premise) *(Sacchi et al., 2022)* – which enables the modification of background LCA databases.
- [`pathways`](https://github.com/polca/pathways) *(Sacchi & Hahn-Menacho, 2024)* – which supports quantifying the environmental burdens of entire energy transformation scenarios using modified LCA databases.

The full documentation of those Python packages can be found in the above links.

##### DISCLAIMER:
>  The current results and the coupling between the energy system module variables and life cycle inventories are still at an early stage. The integration between MIC3 modules will be further harmonized in future iterations. The results presented here are therefore illustrative and serve only to demonstrate that the integration is functional.

---

## 🔍 Overview

The LCA module is designed to:

- Integrate **prospective LCA** with modified background LCA databases.
- Full linking with **energy system scenarios** from Task 4.6.
- Assess products (e.g., buildings, packaging, transport – Task 4.2) and **industrial transformations** (Task 4.4).
- Quantify **life cycle environmental impacts** throughout various environmental impact categories, for example:
  - 🟢 Climate change (life cycle GHG emissions).
  - ❤️ Human health.
  - 🪨 Resource depletion (including critical raw materials).
- Enable **spatially explicit** and regionalised environmental assessments.

---

## 🚀 Features

- 🔁 Modular and flexible design.  
- 🧩 Compatible with other MIC3 modules.  
- 📊 Supports product-, process-, and system-level analysis.  

---

## 📁 Repository structure

```bash
code
├── configuration_file/               # Configuration files for scenario setup
├── inventories/                      # Life cycle inventory datasets
├── pathways/                         # Energy and material transition pathway outputs
├── 1_export_package_Transience.ipynb # Notebook to export scenario packages
├── 2_calc_impacts_ALL.ipynb          # Notebook to calculate all LCA impacts
├── config.py                         # Configuration parameters for setting up the module
├── datapackage_ce.json               # Circular economy scenario datapackage
├── datapackage_edm_i.json            # EDM industrial scenario datapackage
├── datapackage_forecast.json         # Forecast scenario datapackage
├── datapackage_open_prom.json        # Open PROM scenario 
├── lca_transience.yml                # Python packages needed to run this repository
└── README.md                         # readme file
```

---

## 🔧 Dependencies

To run this repository or use the code, the following credentials and tools are required:

- **`KEY_PREMISE`**: A key for [`premise`](https://github.com/polca/premise) to generate prospective LCA databases, including additional life cycle inventories.
- **`USER_PW`**: An account and user password to access the background LCA database **ecoinvent**.
- All required Python (v3.11) packages are listed in the environment file: `lca_transience.yml`.

---

## 📄 License, citing, and scientific references

If you use this repository, the data, or any of the included code, please cite the following (citation coming soon):

> *[citation]*

For detailed licensing information, please refer to the [`LICENSE`](./LICENSE) file.

---

## 🤝 Contributing

Your contribution is welcome! For major suggestions, collaborations, or structural changes, please contact:

**Tom Terlouw**  
[tom.terlouw@psi.ch](mailto:tom.terlouw@psi.ch)

Other contributors:
* **Romain Sacchi (PSI)**

---

## 🙏 Acknowledgements

This repository builds upon several scientific contributions (see *License, citing, and scientific references*) and was developed as part of the **TRANSIENCE** project.

Supported by:
- **Horizon Europe TRANSIENCE** (Project No. 101137606), funded by:
    - European Health and Digital Executive Agency (HADEA)  
    - Swiss State Secretariat for Education, Research and Innovation (SERI)  
    - UK Research and Innovation (UKRI) Horizon Europe Guarantee  

> The views expressed are those of the authors and do not necessarily reflect the position of the European Commission or other funding agencies.