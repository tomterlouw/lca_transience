"""Artifact paths and notebook hand-offs without running production LCA work."""
from __future__ import annotations

import ast
from datetime import datetime
from importlib.util import find_spec
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import zipfile

import config


ROOT = Path(__file__).resolve().parents[1]
DESCRIPTORS = (
    "datapackage_open_prom.json", "datapackage_edm_i.json",
    "datapackage_forecast.json", "datapackage_itom_por.json",
)
NOTEBOOKS = (
    "1_export_packages.ipynb", "2_calc_impacts.ipynb",
    "3_regionalization_impacts.ipynb", "4_edges_regionalized_impacts.ipynb",
)


def code_cells(filename):
    notebook = json.loads((ROOT / filename).read_text(encoding="utf-8"))
    return ["".join(cell["source"]) for cell in notebook["cells"]
            if cell["cell_type"] == "code"]


class ArtifactPathTests(unittest.TestCase):
    def test_shared_directories_are_absolute_and_repo_owned(self):
        expected = {
            "ROOT": ROOT, "DATAPACKAGE_DIR": ROOT / "datapackages",
            "PACKAGE_OUTPUT_DIR": ROOT / "results/datapackages",
            "IMPACT_RESULTS_DIR": ROOT / "results/impacts",
            "EDGES_RESULTS_DIR": ROOT / "results/edges",
        }
        for name, path in expected.items():
            with self.subTest(name=name):
                self.assertEqual(getattr(config, name), path)
                self.assertTrue(getattr(config, name).is_absolute())

    def test_package_basename_and_explicit_paths_remain_distinct(self):
        self.assertEqual(config.resolve_package_path("remind-transience_por.zip"),
                         config.PACKAGE_OUTPUT_DIR / "remind-transience_por.zip")
        self.assertEqual(config.resolve_package_path("archive/old.zip"), ROOT / "archive/old.zip")
        explicit = ROOT / "archive/outside-default.zip"
        self.assertEqual(config.resolve_package_path(explicit), explicit)

    def test_result_basenames_use_separate_ordinary_and_edges_directories(self):
        name = "results_example.gzip"
        self.assertEqual(config.resolve_result_path(name), config.IMPACT_RESULTS_DIR / name)
        self.assertEqual(config.resolve_result_path(name, edges=True), config.EDGES_RESULTS_DIR / name)
        explicit = ROOT / "archive" / name
        self.assertEqual(config.resolve_result_path(explicit, edges=True), explicit)
        self.assertEqual(config.resolve_result_path("archive/" + name), explicit)

    def test_paths_do_not_depend_on_the_kernel_working_directory(self):
        previous = Path.cwd()
        with tempfile.TemporaryDirectory(prefix="artifact_cwd_") as directory:
            try:
                os.chdir(directory)
                self.assertEqual(config.resolve_package_path("model.zip"),
                                 ROOT / "results/datapackages/model.zip")
                self.assertEqual(config.resolve_result_path("results.gzip"),
                                 ROOT / "results/impacts/results.gzip")
                self.assertEqual(config.resolve_result_path("results.gzip", edges=True),
                                 ROOT / "results/edges/results.gzip")
            finally:
                os.chdir(previous)

    def test_descriptors_moved_and_resource_paths_still_resolve_from_repo_root(self):
        count = 0
        for name in DESCRIPTORS:
            with self.subTest(descriptor=name):
                self.assertFalse((ROOT / name).exists())
                descriptor = config.DATAPACKAGE_DIR / name
                self.assertTrue(descriptor.is_file())
                data = json.loads(descriptor.read_text(encoding="utf-8"))
                for resource in data["resources"]:
                    relative = Path(resource["path"])
                    self.assertFalse(relative.is_absolute())
                    self.assertIn(relative.parts[0],
                                  {"scenario_data", "configuration_file", "inventories"})
                    self.assertTrue((ROOT / relative).resolve().is_relative_to(ROOT))
                    count += 1
        self.assertEqual(count, 12)

    def test_moved_descriptors_load_real_resources_with_explicit_base_path(self):
        if find_spec("datapackage") is None:
            self.skipTest("datapackage library is not installed")
        from datapackage import Package
        missing = []
        for name in DESCRIPTORS:
            package = Package(str(config.DATAPACKAGE_DIR / name), base_path=str(ROOT))
            self.assertTrue(package.valid, package.errors)
            for resource in package.resources:
                expected = (ROOT / resource.descriptor["path"]).resolve()
                self.assertEqual(Path(resource.source).resolve(), expected)
                if expected.is_file():
                    self.assertGreater(len(resource.raw_read()), 0)
                else:
                    missing.append(str(expected))
        if missing:
            self.skipTest("Local ignored inventory/scenario inputs unavailable: " + ", ".join(missing))

    def test_root_contains_no_generated_packages_or_compressed_results(self):
        self.assertEqual(list(ROOT.glob("remind-transience*.zip")), [])
        self.assertEqual(list(ROOT.glob("*.gzip")), [])


class GeneratedPackageStorageTests(unittest.TestCase):
    @staticmethod
    def write_package(path, label):
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("datapackage.json", json.dumps({"name": label, "resources": []}))

    def test_successful_package_is_moved_to_canonical_directory(self):
        with tempfile.TemporaryDirectory(prefix="package_storage_") as directory:
            source = Path(directory) / "native-output"
            destination = Path(directory) / "results/datapackages"
            source.mkdir()
            archive = source / "model.zip"
            self.write_package(archive, "fixture")
            original_bytes = archive.read_bytes()
            with patch.object(config, "PACKAGE_OUTPUT_DIR", destination):
                result = config.store_generated_package("model", source_dir=source)
            self.assertEqual(result, destination / "model.zip")
            self.assertEqual(result.read_bytes(), original_bytes)
            self.assertFalse(archive.exists())

    def test_selected_target_can_be_replaced_without_touching_other_packages(self):
        with tempfile.TemporaryDirectory(prefix="package_replacement_") as directory:
            source = Path(directory) / "source"
            destination = Path(directory) / "results/datapackages"
            source.mkdir()
            destination.mkdir(parents=True)
            selected = destination / "model.zip"
            unrelated = destination / "other.zip"
            self.write_package(selected, "old")
            self.write_package(unrelated, "untouched")
            self.write_package(source / "model.zip", "new")
            unrelated_bytes = unrelated.read_bytes()
            with patch.object(config, "PACKAGE_OUTPUT_DIR", destination):
                result = config.store_generated_package("model.zip", source_dir=source)
            with zipfile.ZipFile(result) as archive:
                self.assertEqual(json.loads(archive.read("datapackage.json"))["name"], "new")
            self.assertEqual(unrelated.read_bytes(), unrelated_bytes)

    def test_missing_or_invalid_native_output_does_not_damage_existing_target(self):
        with tempfile.TemporaryDirectory(prefix="package_invalid_") as directory:
            source = Path(directory) / "source"
            destination = Path(directory) / "results/datapackages"
            source.mkdir()
            destination.mkdir(parents=True)
            selected = destination / "model.zip"
            self.write_package(selected, "existing")
            original = selected.read_bytes()
            with patch.object(config, "PACKAGE_OUTPUT_DIR", destination):
                with self.assertRaises(FileNotFoundError):
                    config.store_generated_package("model", source_dir=source)
                self.assertEqual(selected.read_bytes(), original)
                invalid = source / "model.zip"
                invalid.write_bytes(b"not a ZIP")
                with self.assertRaises((ValueError, zipfile.BadZipFile)):
                    config.store_generated_package("model", source_dir=source)
                self.assertEqual(selected.read_bytes(), original)
                self.assertTrue(invalid.exists())

    def test_package_names_reject_paths_instead_of_hiding_them_in_slug(self):
        for name in ("", ".", "..", "../escape", "folder/model", r"folder\model"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                config.store_generated_package(name)


class NotebookArtifactHandOffTests(unittest.TestCase):
    def test_notebook_one_preserves_repo_resource_base_for_all_moved_descriptors(self):
        source = next(source for source in code_cells("1_export_packages.ipynb")
                      if "sps_itom_por = Package(" in source)
        calls = [node for node in ast.parse(source).body if isinstance(node, ast.Assign)
                 and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name)
                 and node.value.func.id == "Package"]
        self.assertEqual(len(calls), 4)
        package = Mock()
        namespace = {"Package": package, "ROOT": ROOT, "Path": Path,
                     "DATAPACKAGE_DIR": config.DATAPACKAGE_DIR}
        exec(compile(ast.Module(body=calls, type_ignores=[]), "descriptor_loading", "exec"), namespace)
        self.assertEqual({Path(call.args[0]).name for call in package.call_args_list}, set(DESCRIPTORS))
        for call in package.call_args_list:
            self.assertEqual(Path(call.args[0]).parent, config.DATAPACKAGE_DIR)
            self.assertEqual(Path(call.kwargs["base_path"]), ROOT)

    def test_all_workflow_notebook_cells_compile_without_execution(self):
        for filename in NOTEBOOKS:
            for index, source in enumerate(code_cells(filename)):
                with self.subTest(notebook=filename, cell=index):
                    try:
                        tree = ast.parse(source)
                    except SyntaxError:
                        from IPython.core.inputtransformer2 import TransformerManager
                        tree = ast.parse(TransformerManager().transform_cell(source))
                    compile(tree, filename, "exec", flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT,
                            dont_inherit=True)

    def test_notebook_two_exports_both_calculations_to_ordinary_results_directory(self):
        exports = [source for source in code_cells("2_calc_impacts.ipynb")
                   if "p.export_results(" in source]
        self.assertEqual(len(exports), 2)
        with tempfile.TemporaryDirectory(prefix="ordinary_export_") as directory:
            output = Path(directory) / "impacts"
            for source in exports:
                tree = ast.parse(source)
                nodes = []
                for node in tree.body:
                    nodes.append(node)
                    if isinstance(node, ast.Assign) and any(
                            isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute)
                            and child.func.attr == "export_results" for child in ast.walk(node)):
                        break
                export = Mock(return_value=str(output / "fixture.gzip"))
                namespace = {"IMPACT_RESULTS_DIR": output, "datetime": datetime,
                             "p": SimpleNamespace(export_results=export)}
                exec(compile(ast.Module(body=nodes, type_ignores=[]), "ordinary_export", "exec"), namespace)
                self.assertTrue(output.is_dir())
                selected = Path(export.call_args.kwargs["filename"])
                self.assertEqual(selected.parent, output)
                self.assertTrue(selected.name.startswith("results_"))
                self.assertEqual(namespace["fp"], str(output / "fixture.gzip"))

    def test_notebook_three_loads_latest_ordinary_result_and_respects_explicit_selection(self):
        source = next(source for source in code_cells("3_regionalization_impacts.ipynb")
                      if "if RESULTS_FILE is None:" in source)
        tree = ast.parse(source)
        nodes = []
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name)
                                                    and target.id == "required_columns" for target in node.targets):
                break
            nodes.append(node)
        code = compile(ast.Module(body=nodes, type_ignores=[]), "ordinary_load", "exec")
        with tempfile.TemporaryDirectory(prefix="ordinary_load_") as directory:
            output = Path(directory) / "impacts"
            output.mkdir()
            first, latest = output / "results_first.gzip", output / "results_latest.gzip"
            for path, timestamp in ((first, 100), (latest, 200)):
                path.write_bytes(b"fixture: no LCA content")
                os.utime(path, (timestamp, timestamp))
            resolver = lambda name: output / name
            namespace = {"IMPACT_RESULTS_DIR": output, "RESULTS_FILE": None,
                         "resolve_result_path": resolver}
            exec(code, namespace)
            self.assertEqual(namespace["results_path"], latest)
            namespace["RESULTS_FILE"] = first.name
            exec(code, namespace)
            self.assertEqual(namespace["results_path"], first)

    def test_notebook_four_loads_only_edges_directory(self):
        source = next(source for source in code_cells("4_edges_regionalized_impacts.ipynb")
                      if "p_edges.export_results(" in source)
        branch = next(node for node in ast.parse(source).body
                      if isinstance(node, ast.If) and isinstance(node.test, ast.Name)
                      and node.test.id == "RUN_CALCULATION")
        code = compile(ast.Module(body=branch.orelse, type_ignores=[]), "edges_load", "exec")
        with tempfile.TemporaryDirectory(prefix="edges_load_") as directory:
            output = Path(directory) / "edges"
            output.mkdir()
            first, latest = output / "results_edges_por_first.gzip", output / "results_edges_por_latest.gzip"
            for path, timestamp in ((first, 100), (latest, 200)):
                path.write_bytes(b"fixture: no LCA content")
                os.utime(path, (timestamp, timestamp))
            resolver = Mock(side_effect=lambda name, **kwargs: output / name)
            namespace = {"EDGES_RESULTS_DIR": output, "RESULTS_FILE": None,
                         "resolve_result_path": resolver, "results_path": None}
            exec(code, namespace)
            self.assertEqual(namespace["results_path"], latest)
            resolver.assert_not_called()
            namespace["RESULTS_FILE"] = first.name
            exec(code, namespace)
            self.assertEqual(namespace["results_path"], first)
            resolver.assert_called_once_with(first.name, edges=True)

    def test_notebook_four_writes_results_and_provenance_to_edges_directory(self):
        source = next(source for source in code_cells("4_edges_regionalized_impacts.ipynb")
                      if "p_edges.export_results(" in source)
        branch = next(node for node in ast.parse(source).body
                      if isinstance(node, ast.If) and isinstance(node.test, ast.Name)
                      and node.test.id == "RUN_CALCULATION")
        nodes = []
        for node in branch.body:
            names = {child.id for child in ast.walk(node) if isinstance(child, ast.Name)}
            is_export = any(isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute)
                            and child.func.attr == "export_results" for child in ast.walk(node))
            is_destination = isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id in {"timestamp", "export_base"}
                for target in node.targets)
            is_mkdir = isinstance(node, ast.Expr) and "EDGES_RESULTS_DIR" in names
            if is_export or is_destination or is_mkdir:
                nodes.append(node)
        self.assertEqual(len(nodes), 4)
        with tempfile.TemporaryDirectory(prefix="edges_export_") as directory:
            output = Path(directory) / "edges"
            export = Mock(return_value=str(output / "results_edges_por_fixture.gzip"))
            namespace = {"EDGES_RESULTS_DIR": output, "Path": Path, "datetime": datetime,
                         "p_edges": SimpleNamespace(export_results=export)}
            exec(compile(ast.Module(body=nodes, type_ignores=[]), "edges_export", "exec"), namespace)
            self.assertTrue(output.is_dir())
            self.assertEqual(Path(export.call_args.kwargs["filename"]).parent, output)
            self.assertEqual(namespace["results_path"].with_suffix(".json").parent, output)


if __name__ == "__main__":
    unittest.main()
