"""Read-only dependency coverage checks; never initialize LCA packages/projects."""
from __future__ import annotations

import ast
import json
from pathlib import Path
import re
import sys
import unittest

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
import yaml


ROOT = Path(__file__).resolve().parents[1]
PROFILES = {
    "requirements.txt": (
        "lca_transience.yml", "lca_transience_255", "2.5.5",
        "a7d510d45192b5f95c76360e78a89224abea97e4", "1.5", "0.8", "5.4.0",
    ),
    "requirements-legacy.txt": (
        "lca_transience_legacy.yml", "premise_pathways_legacy", "2.3.8",
        "f9187faeab92be4dd7a9fe5e90256870b33bc618", "1.0", "0.7.0", "6.0.2",
    ),
}
IMPORT_PACKAGES = {
    "bw2data": "bw2data", "bw2io": "bw2io", "premise": "premise",
    "pathways": "pathways", "edges": "edges", "datapackage": "datapackage",
    "constructive_geometries": "constructive-geometries", "pycountry": "pycountry",
    "pandas": "pandas", "numpy": "numpy", "matplotlib": "matplotlib",
    "seaborn": "seaborn", "openpyxl": "openpyxl", "yaml": "PyYAML",
    "IPython": "ipython",
    "xarray": "xarray",
}


def requirement_lines(filename):
    return [line.strip() for line in (ROOT / filename).read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")]


def requirements(filename, active=()):
    """Parse declared requirements and relative includes, rejecting cycles/duplicates."""
    if filename in active:
        raise AssertionError(f"Cyclic requirements include: {active + (filename,)}")
    result = {}
    for line in requirement_lines(filename):
        if line.startswith("-r "):
            include = line[3:].strip()
            path = Path(include)
            if path.is_absolute() or re.match(r"^[A-Za-z]:", include) or ".." in path.parts:
                raise AssertionError(f"Non-portable requirement include: {include}")
            if not (ROOT / path).is_file():
                raise AssertionError(f"Missing requirements include: {include}")
            incoming = requirements(include, active + (filename,))
        else:
            requirement = Requirement(line)
            incoming = {canonicalize_name(requirement.name): requirement}
        duplicates = result.keys() & incoming.keys()
        if duplicates:
            raise AssertionError(f"Duplicate normalized requirements in {filename}: {duplicates}")
        result.update(incoming)
    return result


def source_trees():
    files = list(ROOT.glob("*.py"))
    for directory in ("scripts", "primary_vs_secondary_metals"):
        files.extend((ROOT / directory).rglob("*.py"))
    for path in files:
        # Never read credential file contents, even for a static parse.
        if path.name != "private_keys.py":
            yield str(path.relative_to(ROOT)), ast.parse(path.read_text(encoding="utf-8-sig"))
    for path in ROOT.glob("*.ipynb"):
        notebook = json.loads(path.read_text(encoding="utf-8"))
        for index, cell in enumerate(notebook.get("cells", []), 1):
            if cell.get("cell_type") != "code":
                continue
            source = cell.get("source", "")
            if isinstance(source, list):
                source = "".join(source)
            try:
                tree = ast.parse(source)
            except SyntaxError:
                from IPython.core.inputtransformer2 import TransformerManager
                tree = ast.parse(TransformerManager().transform_cell(source))
            yield f"{path.name}:cell {index}", tree


class DependencyManifestTests(unittest.TestCase):
    def test_all_five_manifests_are_utf8_and_portable(self):
        filenames = ["requirements-common.txt", *PROFILES,
                     *(profile[0] for profile in PROFILES.values())]
        self.assertEqual(len(filenames), 5)
        for filename in filenames:
            with self.subTest(filename=filename):
                raw = (ROOT / filename).read_bytes()
                self.assertFalse(raw.startswith((b"\xff\xfe", b"\xfe\xff", b"\xef\xbb\xbf")))
                text = raw.decode("utf-8")
                self.assertNotIn("\x00", text)
                self.assertNotRegex(text, r"(?im)^\s*prefix\s*:")
                self.assertNotRegex(text, r"(?i)(?:C:[\\/]|/Users/|/home/|gurobi|pywin32==)")

    def test_requirements_syntax_includes_and_normalized_uniqueness(self):
        for filename in ("requirements-common.txt", *PROFILES):
            with self.subTest(filename=filename):
                self.assertGreater(len(requirements(filename)), 0)
        for filename in PROFILES:
            self.assertEqual([line for line in requirement_lines(filename) if line.startswith("-")],
                             ["-r requirements-common.txt"])

    def test_profiles_pin_premise_extras_and_full_pathways_source_revision(self):
        for filename, (_, _, premise_version, revision, bw_processing, matrix_utils, lxml) in PROFILES.items():
            with self.subTest(filename=filename):
                values = requirements(filename)
                self.assertEqual(values["premise"].extras, {"bw25"})
                self.assertEqual(str(values["premise"].specifier), "==" + premise_version)
                self.assertRegex(revision, r"^[0-9a-f]{40}$")
                self.assertEqual(values["pathways"].url,
                                 "git+https://github.com/polca/pathways.git@" + revision)
                for name, version in (("bw-processing", bw_processing),
                                      ("matrix-utils", matrix_utils), ("lxml", lxml)):
                    self.assertEqual(str(values[name].specifier), "==" + version)

    def test_common_numerical_and_bw25_pins_are_explicit(self):
        common = requirements("requirements-common.txt")
        for name, version in {"numpy": "1.26.4", "scipy": "1.13.1",
                              "xarray": "2024.2.0", "bw2calc": "2.4.0"}.items():
            self.assertEqual(str(common[name].specifier), "==" + version)
        for name in ("ipykernel", "ipython", "packaging", "setuptools", "wurst",
                     "xlsxwriter", "pyarrow", "scipy", "xarray"):
            self.assertIn(name, common)
        self.assertNotIn("premise", common)
        self.assertNotIn("pathways", common)

    def test_direct_source_imports_and_implicit_io_engines_are_covered(self):
        local = {path.stem for path in ROOT.glob("*.py")}
        local.update(path.stem for path in (ROOT / "primary_vs_secondary_metals").glob("*.py"))
        local.add("primary_vs_secondary_metals")
        local.add("scripts")
        seen, unknown = set(), []
        for label, tree in source_trees():
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imports = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    imports = [node.module] if node.module else []
                else:
                    continue
                for name in imports:
                    namespace = name.split(".", 1)[0]
                    if namespace in sys.stdlib_module_names or namespace in local:
                        continue
                    if namespace not in IMPORT_PACKAGES:
                        unknown.append(f"{label}:{node.lineno}: {namespace}")
                    else:
                        seen.add(canonicalize_name(IMPORT_PACKAGES[namespace]))
        self.assertEqual(unknown, [], "Map newly introduced external imports: " + "; ".join(unknown))
        self.assertEqual(seen, {canonicalize_name(name) for name in IMPORT_PACKAGES.values()})
        for filename in PROFILES:
            declared = requirements(filename)
            self.assertFalse((seen | {"xlsxwriter", "pyarrow"}) - declared.keys())

    def test_conda_profiles_delegate_to_the_correct_requirements_file(self):
        for requirements_file, (yaml_file, environment_name, *_rest) in PROFILES.items():
            with self.subTest(filename=yaml_file):
                specification = yaml.safe_load((ROOT / yaml_file).read_text(encoding="utf-8"))
                self.assertEqual(set(specification), {"name", "channels", "dependencies"})
                self.assertEqual(specification["name"], environment_name)
                self.assertEqual(specification["channels"], ["conda-forge", "nodefaults"])
                conda_dependencies = [entry for entry in specification["dependencies"] if isinstance(entry, str)]
                expected = (["python=3.12", "pip>=24", "setuptools=82.0.0", "wheel", "git",
                             "numpy=1.26.4", "scipy=1.13.1", "pypardiso=0.4.7", "mkl=2026.1.0"]
                            if requirements_file == "requirements.txt" else
                            ["python=3.11", "pip>=24", "setuptools=82.0.0", "wheel", "git"])
                self.assertEqual(conda_dependencies, expected)
                self.assertTrue(all(entry.count("=") <= 1 for entry in conda_dependencies))
                pip_sections = [entry for entry in specification["dependencies"] if isinstance(entry, dict)]
                self.assertEqual(pip_sections, [{"pip": ["-r " + requirements_file]}])


if __name__ == "__main__":
    unittest.main()
