# Dependency files and environment setup

The default modelling profile is **premise 2.5.2**, Python 3.11 and Brightway 2.5.
Files were regenerated from direct source/notebook imports and installed package
metadata on 2026-10-14. The older premise 2.3.8 stack remains an explicit
comparison profile, not an automatic fallback or the default installation.

## Which file does what?

| File | Purpose |
| --- | --- |
| `requirements-common.txt` | Shared direct/runtime packages and reviewed numerical versions. Included by a complete profile, not installed alone. |
| `requirements.txt` | Current pip installation: premise 2.5.2 and the newer Pathways commit. |
| `requirements-legacy.txt` | Optional separate comparison installation: premise 2.3.8 and the older Pathways commit. |
| `lca_transience.yml` | Portable Conda environment named `premise_pathways_new`; installs the current requirements profile. |
| `lca_transience_legacy.yml` | Optional Conda environment named `premise_pathways_legacy`; installs the legacy requirements profile. |

A pip requirements file lists Python package specifiers, one per line. For
example, `numpy==1.26.4` pins a version; `-r requirements-common.txt` includes a
shared file. A VCS requirement can pin an exact source commit rather than a
moving branch. These are standard [pip requirements-file formats](https://pip.pypa.io/en/stable/reference/requirements-file-format/).

A Conda environment YAML also specifies the interpreter, environment name and
channels. Here, Conda installs Python/Git/tooling and its `pip` section installs
the same pip profile. The files contain no user-specific `prefix`, Windows
runtime build strings or unused Gurobi channel. See the [Conda environment documentation](https://docs.conda.io/projects/conda/en/stable/user-guide/tasks/manage-environments.html).

## Current Conda installation

Run from the repository root, with Git and Conda/Mamba available:

```bash
conda env create -f lca_transience.yml
conda activate premise_pathways_new
python -m pip check
```

Mamba can use the same file:

```bash
mamba env create -f lca_transience.yml
```

If the named environment already exists, do not replace a working environment
just to test these files. Use another name:

```bash
conda env create -f lca_transience.yml -n lca_transience_252
conda activate lca_transience_252
```

Requirements are included using relative paths; keep all three requirements
files beside the YAMLs and run the commands from this repo. Internet access to
the package channels/PyPI and the public Pathways Git repository is needed.

## Pip-only installation

Start with a fresh Python 3.11 virtual environment. On Windows PowerShell:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip check
```

On Linux/macOS:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip check
```

The pinned Pathways source needs Git on PATH, including for pip-only installs.
An activated Python environment must be 3.11; requirements files cannot install
the Python interpreter. Scientific/geography libraries include compiled code,
so platform-specific wheel/channel availability must be checked on a new host.

## Notebook kernel

Register the active environment and select it in VS Code/Jupyter:

```bash
python -m ipykernel install --user --name premise_pathways_new --display-name "TRANSIENCE (premise 2.5.2)"
```

If you used another environment name, give its kernel a distinct name. Confirm
the running interpreter and distribution versions without loading a Brightway
project:

```python
import sys
import importlib.metadata as metadata

print(sys.executable)
for package in ("premise", "pathways", "bw2data", "bw2calc", "edges"):
    print(package, metadata.version(package))
print(metadata.distribution("pathways").read_text("direct_url.json"))
```

The requirements include `ipykernel`/IPython, sufficient for a VS Code kernel;
they do not add a browser notebook server. If you need JupyterLab, install it
separately with `python -m pip install jupyterlab`, then run `python -m pip check`
and record the installed server version with the analysis provenance.

## Shared inventory source configuration

The optional shared-master workflow needs no extra Python dependencies or cloud
credentials. OneDrive handles authentication and synchronization; this repo
stages a local file, not a SharePoint browser URL. The same staging step is used
with both premise profiles.

1. Share the master `lci-itom_por.xlsx` or its folder with the other editors, and
   use OneDrive to sync the SharePoint/shared folder locally. Each collaborator
   may have a different absolute path. Microsoft explains [SharePoint synchronization](https://support.microsoft.com/en-us/sharepoint/sync/sync-sharepoint-and-teams-files-with-your-computer).
2. In File Explorer select **Always keep on this device** for the workbook. Save
   changes and wait for OneDrive synchronization to complete before building.
   A local path alone does not prove that the workbook is shared or up to date
   on the server; see [Microsoft's file-availability guidance](https://support.microsoft.com/en-us/sharepoint/sync/work-with-synced-files-in-file-explorer).
3. Create `inventory_sources.local.json` in the repository root. This file is
   ignored by Git; use your own absolute local synced path, not this placeholder:

   ```json
   {
     "itom_por": "C:/Users/YOUR_USER/OneDrive - YOUR_ORGANISATION/Shared inventories/lci-itom_por.xlsx"
   }
   ```

4. Restart an existing notebook kernel after pulling the new workflow, then run
   `1_export_packages.ipynb` from the beginning. When either `create_dbs` or
   `create_db_package` is true, its input setup stages the inventory once, before
   `Package(...)`, `NewDatabase(...)` or `PathwaysDataPackage(...)` construction.
   Keep the datapackage resource path as `inventories/lci-itom_por.xlsx`.

For a session-specific override, set the environment variable in PowerShell
**before launching the notebook process/kernel from that shell**:

```powershell
$env:ITOM_POR_SHARED_INVENTORY = 'C:\Users\YOUR_USER\OneDrive - YOUR_ORGANISATION\Shared inventories\lci-itom_por.xlsx'
```

A kernel already running in VS Code does not inherit later changes to another
terminal's environment. The local JSON configuration avoids that ambiguity and
does not need a `.env` parser.

The source selection order is:

1. An explicit `stage_itom_inventory(source=...)` argument.
2. `ITOM_POR_SHARED_INVENTORY` in the current Python process environment.
3. The `itom_por` entry in `inventory_sources.local.json`.
4. With no shared source configured, the canonical local
   `inventories/lci-itom_por.xlsx`.

For an intentional one-off preflight outside notebook 1:

```python
from config import stage_itom_inventory

inventory_stage = stage_itom_inventory()  # before creating any builders/policies
```

The stage operation validates the XLSX ZIP structure and CRC checksums, verifies
source stability while copying, and copies the workbook without changing its
bytes or exchanges. Only a validated snapshot replaces the canonical workbook,
using an atomic replacement. Old/new snapshots are retained by content hash at
`results/input_snapshots/<sha256>/lci-itom_por.xlsx`, alongside timestamped
staging manifests recording the chosen source, shared/local mode and SHA-256.
Keep this provenance with the YAML/Git revision and generated package name.
It records the exact local bytes used, not a SharePoint server-version identifier.

The shared master is never overwritten, and no changes are copied back to the
cloud. A missing configured source or an invalid/unstable workbook fails clearly;
it does not silently fall back to the last local snapshot. Correct the local
path/sync status or intentionally remove the shared-source setting and use an
approved canonical local workbook. Do not disable validation merely to continue
with an unknown inventory version.

Do not restage or edit the canonical workbook during a model run: supplier-linking
guards deliberately reject changed inputs after policy creation. A restage
requires new builders and policies. Shared edits made during a run are picked up
on the next build; save and finish syncing first. Staging a newer inventory also
makes an older package potentially stale, so regenerate rather than mixing new
input hashes with old scenario matrices. Inventory snapshots and source paths
remain local research records and may contain licensed or sensitive information.

## Optional legacy comparison

Use a separate environment; do not combine two conflicting profiles:

```bash
conda env create -f lca_transience_legacy.yml
conda activate premise_pathways_legacy
python -m pip check
python -m ipykernel install --user --name premise_pathways_legacy --display-name "TRANSIENCE (premise 2.3.8 legacy)"
```

In a separate Python 3.11 venv, the equivalent is
`python -m pip install -r requirements-legacy.txt`.
The original older environment may still be named `premise_pathways`; the new
legacy YAML deliberately uses a distinct name and does not rename it.

## Why these pins matter

Both inspected Pathways builds identify themselves as **1.0.4**, but their
`direct_url.json` records different source commits. Pinning only that version
label would not reproduce either inspected source tree:

| Profile | premise | Pathways source commit | bw-processing | matrix-utils | lxml |
| --- | --- | --- | --- | --- | --- |
| Current | 2.5.2 | `82e736438d13c7b465361914534229cf3860bf46` | 1.5 | 0.8 | 5.4.0 |
| Legacy | 2.3.8 | `f9187faeab92be4dd7a9fe5e90256870b33bc618` | 1.0 | 0.7.0 | 6.0.2 |

The compatible shared numerical basis is NumPy 1.26.4, SciPy 1.13.1 and xarray
2024.2.0. Installed premise metadata requires NumPy below 2, SciPy below 1.14
and xarray no later than 2024.2.0. The current Windows premise metadata also
requires lxml below 6; the current profile therefore pins 5.4.0. The legacy
profile records its inspected 6.0.2 without forcing it into the current profile.

These profiles preserve native premise behaviour; different releases can still
produce different future market shares and inventories. Cross-version
comparison also needs matching ecoinvent version, methods, classifications,
scenario files and repo hooks. A clean dependency installation is not proof
that impact results are numerically equivalent between profiles.

## Maintaining the files

When a new direct import is introduced, add its actual distribution name to
`requirements-common.txt` if it belongs to both profiles. Update only the
relevant profile for a version-specific package. Keep source-dependent Pathways
changes pinned to a reviewed full commit, and keep YAMLs referencing the
requirements files rather than duplicating the package list.

Use installed metadata to inspect a candidate pin:

```bash
python -m pip show premise pathways
python -m pip check
python -m unittest tests.test_dependency_manifests -v
```

Check the repo-owned linking/classification/metals suites in both profiles after
an upgrade. For full archival reproducibility, additionally capture a resolved
platform-specific environment export and `python -m pip freeze --all` with the
result provenance. Such snapshots include transitive packages and may include
local paths; review them before sharing. They are analysis records, not a
replacement for these portable, curated installation files.

The existing YAML was a UTF-16 machine export; the regenerated text files are
UTF-8. Preserve UTF-8 when editing them, especially when generating text from
Windows PowerShell. Python standard-library modules, repo-owned helpers,
credentials and licensed ecoinvent/ITOM data are not pip dependencies.

## Verification scope

The manifest tests check syntax, include paths, duplicate distributions,
direct-import coverage, source commit pins and YAML/profile alignment. Both
existing working environments pass `python -m pip check`; their repo regression
suites were exercised before regenerating the manifests. No active environment,
Brightway database, scenario archive, LCI workbook or impact result was changed.

These are direct/core version pins, not a complete hash-locked transitive
environment. A new environment has not been created from them during this
request; clean installation and platform availability still need a fresh-host
check. Credentials, ecoinvent licenses/data, the premise IAM key and local
scenario/LCI files remain required as described in the main README.
