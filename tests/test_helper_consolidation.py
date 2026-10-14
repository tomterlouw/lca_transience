"""Static consolidation/linkage checks; no notebook or modelling execution."""
from __future__ import annotations

import ast
import importlib
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
RETIRED_MODULES = frozenset({
    "native_inventory_access", "metals_policy_workflow",
    "pathways_classification_compat", "por_px_terminal_purity",
})
PUBLIC_APIS = {
    "premise_compat": (
        "install_premise_null_bounds_fix", "install_premise_pathways_mapping_fix",
        "iter_activity_metadata", "checkpoint_token", "open_inventory_store",
        "capture_updated_stores", "legacy_delta_base_columnar_scope",
    ),
    "metals_scenario_policy": (
        "MetalSteelPolicy", "plan_database", "apply_database",
        "apply_metal_steel_policy", "verify_metal_steel_policy",
        "attach_metal_steel_policy", "attach_metal_steel_package_provenance",
    ),
    "por_classifications": (
        "ClassificationError", "ClassificationSnapshot",
        "capture_scenario_classifications", "write_classification_snapshot",
        "prepare_pathways_classifications", "install_pathways_classification_guard",
    ),
    "por_scenario_inputs": (
        "fresh_scenario_definitions", "verify_px_extraction_yield",
        "validate_px_source_flows", "annual_raw_px_use",
        "reconcile_px_terminal_purity",
    ),
}


def _python_files():
    """Avoid generated inventories, results, caches and environment directories."""
    files = set(ROOT.glob("*.py"))
    for directory in ("scripts", "primary_vs_secondary_metals", "tests"):
        files.update((ROOT / directory).rglob("*.py"))
    return sorted(files)


def _notebook_code_cells():
    # Read only source: saved traceback outputs and prose may intentionally
    # mention retired modules and do not form executable linkages.
    for path in sorted(ROOT.glob("*.ipynb")):
        notebook = json.loads(path.read_text(encoding="utf-8"))
        for index, cell in enumerate(notebook.get("cells", [])):
            if cell.get("cell_type") == "code":
                source = cell.get("source", "")
                if isinstance(source, list):
                    source = "".join(source)
                yield f"{path.name}:cell {index}", source


def _parse_python(source, label):
    """Parse Python without execution; transform IPython syntax only if needed."""
    try:
        return ast.parse(source, filename=label)
    except SyntaxError:
        # Input transformation produces Python text and does not run cells,
        # execute magics/shell commands, or load any LCA project.
        from IPython.core.inputtransformer2 import TransformerManager
        transformed = TransformerManager().transform_cell(source)
        return ast.parse(transformed, filename=label)


def _call_name(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _call_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def _import_targets(tree):
    """Find static imports and literal importlib/loader targets, including aliases."""
    import_calls = {"__import__", "import_module", "find_spec", "spec_from_file_location"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                yield node.lineno, node.module
            if node.module in {"importlib", "importlib.util"}:
                for alias in node.names:
                    if alias.name in import_calls:
                        import_calls.add(alias.asname or alias.name)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node.func)
        if name.rsplit(".", 1)[-1] not in import_calls:
            continue
        for argument in [*node.args, *(keyword.value for keyword in node.keywords)]:
            for value in ast.walk(argument):
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    yield node.lineno, value.value


def _is_retired_target(target):
    normalized = target.replace("\\", "/")
    if normalized.endswith(".py"):
        module = normalized.rsplit("/", 1)[-1][:-3]
    else:
        module = normalized.split(".", 1)[0]
    return module in RETIRED_MODULES


class HelperConsolidationTests(unittest.TestCase):
    def test_retired_helper_files_are_removed(self):
        for module in RETIRED_MODULES:
            with self.subTest(module=module):
                self.assertFalse((ROOT / f"{module}.py").exists())

    def test_project_sources_and_notebook_cells_have_no_retired_imports(self):
        sources = [(str(path.relative_to(ROOT)), path.read_text(encoding="utf-8-sig"))
                   for path in _python_files()]
        sources.extend(_notebook_code_cells())
        failures = []
        for label, source in sources:
            tree = _parse_python(source, label)
            for line, target in _import_targets(tree):
                if _is_retired_target(target):
                    failures.append(f"{label}:{line}: {target}")
        self.assertEqual(failures, [], "Retired executable linkages: " + "; ".join(failures))

    def test_public_apis_import_from_their_new_owner(self):
        for name, exports in PUBLIC_APIS.items():
            module = importlib.import_module(name)
            for export in exports:
                with self.subTest(module=name, export=export):
                    value = getattr(module, export)
                    self.assertTrue(callable(value))
                    self.assertEqual(value.__module__, name)

    def test_all_notebook_code_cells_compile_without_execution(self):
        count = 0
        for label, source in _notebook_code_cells():
            with self.subTest(cell=label):
                tree = _parse_python(source, label)
                # Notebook kernels allow top-level await. Creating a code
                # object here does not execute it or alter any notebook.
                compile(tree, label, "exec", flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT,
                        dont_inherit=True)
            count += 1
        self.assertGreater(count, 0)

    def test_import_target_detector_handles_aliases_and_loader_paths(self):
        samples = (
            "import native_inventory_access as old",
            "from metals_policy_workflow import attach_metal_steel_policy",
            'importlib.import_module("pathways_classification_compat")',
            'from importlib import import_module as load; load("por_px_terminal_purity")',
            'importlib.util.spec_from_file_location("module", ROOT / "native_inventory_access.py")',
        )
        for source in samples:
            with self.subTest(source=source):
                self.assertTrue(any(_is_retired_target(target)
                                    for _, target in _import_targets(ast.parse(source))))
        self.assertFalse(_is_retired_target("tests.test_native_inventory_access"))


if __name__ == "__main__":
    unittest.main()
