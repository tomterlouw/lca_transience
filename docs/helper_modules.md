# Consolidated Python helpers

The top-level framework helpers were reduced from eleven files to seven.
The two `0_*.py` entry scripts, project settings and private credentials are
not helpers and are unchanged. Test modules remain separate so regressions can
be checked by concern; no forwarding modules were added under the old names.

## Responsibilities

| Module | Responsibility |
| --- | --- |
| `por_scenario_inputs.py` | Fresh scenario definitions, input isolation, ITOM p-xylene yield/balance checks and terminal-purity reconciliation. |
| `metals_scenario_policy.py` | Non-ferrous case selection, validated supplier shares, post-update hooks, audit reports and package provenance. Steel stays endogenous. |
| `por_classifications.py` | Classification assignment and export snapshots, complete Pathways grouping and Windows worker support. |
| `premise_compat.py` | Scoped premise compatibility fixes, mapping guards, certified native inventory access and checkpoint/capture optimizations. |
| `supplier_resolution.py` | Shared supplier identities and IAM/reference geography, used by reference extraction and post-update linking. |
| `inventory_supplier_compat.py` | Declared compatibility rules and explicit fallback suppliers before inventory import. |
| `por_supplier_linking.py` | Post-update PoR supplier selection, protected imports, validation, atomic checkpoint publication and concrete export hooks. |

The last three stay separate deliberately: reference supplier resolution is
shared, import-time compatibility chooses available provider identities, and
post-update linking applies scenario-specific PoR policy. Combining them would
couple stages with different inputs and lifecycle guarantees.

## Import migration

| Removed module | Import from instead |
| --- | --- |
| `por_px_terminal_purity` | `por_scenario_inputs` |
| `metals_policy_workflow` | `metals_scenario_policy` |
| `pathways_classification_compat` | `por_classifications` |
| `native_inventory_access` | `premise_compat` |

Public function names are retained. Repo scripts, notebook code cells and test
mock targets use the new locations. Notebook 1 reloads each consolidated module
only once before making a fresh builder, rather than reloading old/new aliases
of the same module separately. Notebook 2 installs classification safeguards
from `por_classifications`.

If you keep personal scripts outside this repo, update their imports using this
table. Restart notebook kernels after pulling the refactor so old functions and
worker entry points are not retained in memory. There is no additional notebook
or standalone setup script to run.

## Behaviour and checks

This is a code-organization change, not a scenario change. YAML/LCI content,
annualization, p-xylene purity allocation, renewable power protection, supplier
quantities/uncertainty, native metals baseline and endogenous steel are retained.
No production database, archive, scenario CSV or result workbook is regenerated.

`tests/test_helper_consolidation.py` checks removed-module imports and the public
destinations. Existing concern-specific suites exercise fresh inputs, purity,
native/list inventory paths, linking, classification aggregation, policy reports,
notebook call sites and concrete package export. Run them in the environment you
use for the notebook; old/new premise compatibility must remain covered.
