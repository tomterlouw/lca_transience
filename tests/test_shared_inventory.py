"""Shared inventory staging, using tiny OOXML fixtures rather than model LCIs."""
from __future__ import annotations

import ast
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import config


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ENV = "ITOM_POR_SHARED_INVENTORY"


def write_xlsx(path, label="fixture"):
    """Write the minimal linked parts of an OOXML spreadsheet in a temp folder."""
    path.parent.mkdir(parents=True, exist_ok=True)
    content_types = '''<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
</Types>'''
    relationships = '''<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>'''
    workbook = '''<?xml version="1.0" encoding="UTF-8"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
<sheets><sheet name="Inventory" sheetId="1" r:id="rId1"/></sheets></workbook>'''
    workbook_rels = '''<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>'''
    worksheet = '''<?xml version="1.0" encoding="UTF-8"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>
<row r="1"><c r="A1" t="inlineStr"><is><t>Activity</t></is></c><c r="B1" t="inlineStr"><is><t>''' + label + '''</t></is></c></row>
<row r="2"><c r="A2" t="inlineStr"><is><t>Exchanges</t></is></c></row>
<row r="3"><c r="A3" t="inlineStr"><is><t>name</t></is></c><c r="B3" t="inlineStr"><is><t>amount</t></is></c></row>
</sheetData></worksheet>'''
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, value in (
            ("[Content_Types].xml", content_types), ("_rels/.rels", relationships),
            ("xl/workbook.xml", workbook), ("xl/_rels/workbook.xml.rels", workbook_rels),
            ("xl/worksheets/sheet1.xml", worksheet),
        ):
            archive.writestr(name, value)
    return path


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class SharedInventoryStagingTests(unittest.TestCase):
    def setUp(self):
        temporary_root = ROOT / "tmp"
        temporary_root.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="shared_inventory_", dir=temporary_root)
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.source = self.directory / "shared" / "lci-itom_por.xlsx"
        self.target = self.directory / "repo" / "inventories" / "lci-itom_por.xlsx"
        self.archive = self.directory / "repo" / "results" / "input_snapshots"
        self.local_config = self.directory / "repo" / "inventory_sources.local.json"
        environment = patch.dict(os.environ, {SOURCE_ENV: ""})
        environment.start()
        self.addCleanup(environment.stop)

    def stage(self, source=None):
        return config.stage_itom_inventory(
            source, target=self.target, archive_dir=self.archive, local_config=self.local_config,
        )

    def test_shared_source_is_frozen_byte_for_byte_with_manifest(self):
        write_xlsx(self.source)
        original = self.source.read_bytes()
        result = self.stage(self.source)
        self.assertEqual(self.target.read_bytes(), original)
        self.assertEqual(self.source.read_bytes(), original)
        expected_hash = hashlib.sha256(original).hexdigest()
        self.assertEqual(result["sha256"], expected_hash)
        self.assertEqual(result["size_bytes"], len(original))
        self.assertEqual(result["source_kind"], "shared")
        self.assertEqual(Path(result["source_path"]), self.source.resolve())
        self.assertEqual(Path(result["staged_path"]), self.target.resolve())
        snapshot = self.archive / expected_hash / "lci-itom_por.xlsx"
        self.assertEqual(Path(result["snapshot_path"]), snapshot.resolve())
        self.assertEqual(snapshot.read_bytes(), original)
        self.assertTrue(result["staged_at_utc"])
        manifests = [json.loads(path.read_text(encoding="utf-8"))
                     for path in snapshot.parent.glob("*.json")]
        self.assertTrue(any(all(manifest.get(key) == result[key]
                                for key in ("source_path", "staged_path", "sha256", "source_kind",
                                            "size_bytes", "snapshot_path", "staged_at_utc"))
                            for manifest in manifests))

    def test_existing_valid_local_target_is_archived_before_replacement(self):
        write_xlsx(self.target, "previously approved inventory")
        old_bytes, old_hash = self.target.read_bytes(), digest(self.target)
        write_xlsx(self.source, "new shared inventory")
        result = self.stage(self.source)
        self.assertEqual((self.archive / old_hash / "lci-itom_por.xlsx").read_bytes(), old_bytes)
        self.assertEqual(self.target.read_bytes(), self.source.read_bytes())
        self.assertNotEqual(result["sha256"], old_hash)

    def test_source_changes_after_staging_do_not_change_local_or_archived_snapshot(self):
        write_xlsx(self.source, "first version")
        result = self.stage(self.source)
        staged = self.target.read_bytes()
        write_xlsx(self.source, "edited during model run")
        self.assertNotEqual(staged, self.source.read_bytes())
        self.assertEqual(self.target.read_bytes(), staged)
        self.assertEqual(Path(result["snapshot_path"]).read_bytes(), staged)

    def test_source_save_during_copy_is_rejected_without_replacing_target(self):
        write_xlsx(self.target, "old valid model input")
        write_xlsx(self.source, "first shared version")
        before = self.target.read_bytes()
        original_copy = config._shutil.copyfile

        def copy_then_edit(source, destination, *args, **kwargs):
            result = original_copy(source, destination, *args, **kwargs)
            if Path(source) == self.source:
                write_xlsx(self.source, "second shared version saved during copying")
            return result

        with patch.object(config._shutil, "copyfile", side_effect=copy_then_edit):
            with self.assertRaisesRegex(RuntimeError, "changed|copy|sync"):
                self.stage(self.source)
        self.assertEqual(self.target.read_bytes(), before)
        self.assertEqual(list(self.target.parent.glob("*.xlsx")), [self.target])

    def test_explicit_source_has_priority_over_environment_and_local_setting(self):
        write_xlsx(self.source, "explicit")
        self.local_config.parent.mkdir(parents=True)
        self.local_config.write_text(json.dumps({"itom_por": str(self.directory / "missing-local.xlsx")}),
                                     encoding="utf-8")
        with patch.dict(os.environ, {SOURCE_ENV: str(self.directory / "missing-env.xlsx")}):
            result = self.stage(self.source)
        self.assertEqual(Path(result["source_path"]), self.source.resolve())

    def test_environment_source_has_priority_over_personal_json(self):
        write_xlsx(self.source, "environment")
        self.local_config.parent.mkdir(parents=True)
        self.local_config.write_text(json.dumps({"itom_por": str(self.directory / "missing-local.xlsx")}),
                                     encoding="utf-8")
        with patch.dict(os.environ, {SOURCE_ENV: str(self.source)}):
            result = self.stage()
        self.assertEqual(Path(result["source_path"]), self.source.resolve())
        self.assertEqual(result["source_kind"], "shared")

    def test_ignored_personal_json_selects_shared_master(self):
        write_xlsx(self.source)
        self.local_config.parent.mkdir(parents=True)
        self.local_config.write_text(json.dumps({"itom_por": str(self.source)}), encoding="utf-8")
        result = self.stage()
        self.assertEqual(Path(result["source_path"]), self.source.resolve())
        self.assertEqual(result["source_kind"], "shared")

    def test_no_shared_source_keeps_and_records_existing_local_inventory(self):
        write_xlsx(self.target, "local inventory")
        before, modified = self.target.read_bytes(), self.target.stat().st_mtime_ns
        result = self.stage()
        self.assertEqual(result["source_kind"], "local")
        self.assertEqual(Path(result["source_path"]), self.target.resolve())
        self.assertEqual(self.target.read_bytes(), before)
        self.assertEqual(self.target.stat().st_mtime_ns, modified)
        self.assertEqual(Path(result["snapshot_path"]).read_bytes(), before)

    def test_explicit_null_in_personal_setting_selects_local_mode(self):
        write_xlsx(self.target, "deliberate local inventory")
        self.local_config.write_text(json.dumps({"itom_por": None}), encoding="utf-8")
        result = self.stage()
        self.assertEqual(result["source_kind"], "local")
        self.assertEqual(Path(result["source_path"]), self.target.resolve())

    def test_blank_configured_path_is_rejected_instead_of_using_local_target(self):
        write_xlsx(self.target)
        before = self.target.read_bytes()
        for source in ("", "   "):
            with self.subTest(source=source), self.assertRaises(ValueError):
                self.stage(source)
            self.assertEqual(self.target.read_bytes(), before)
        self.local_config.write_text(json.dumps({"itom_por": " "}), encoding="utf-8")
        with self.assertRaises(ValueError):
            self.stage()
        self.assertEqual(self.target.read_bytes(), before)

    def test_identical_shared_content_does_not_touch_canonical_mtime(self):
        write_xlsx(self.source)
        self.target.parent.mkdir(parents=True)
        self.target.write_bytes(self.source.read_bytes())
        os.utime(self.target, ns=(1_500_000_000_000_000_000, 1_500_000_000_000_000_000))
        modified = self.target.stat().st_mtime_ns
        result = self.stage(self.source)
        self.assertEqual(self.target.stat().st_mtime_ns, modified)
        self.assertEqual(result["source_kind"], "shared")
        self.assertEqual(Path(result["snapshot_path"]).read_bytes(), self.target.read_bytes())

    def test_configured_missing_source_never_falls_back_to_valid_target(self):
        write_xlsx(self.target)
        before = self.target.read_bytes()
        with self.assertRaises((FileNotFoundError, ValueError, RuntimeError)):
            self.stage(self.source)
        self.assertEqual(self.target.read_bytes(), before)

    def test_missing_local_inventory_is_an_actionable_error(self):
        with self.assertRaises((FileNotFoundError, ValueError, RuntimeError)):
            self.stage()
        self.assertFalse(self.target.exists())

    def test_invalid_xlsx_never_replaces_existing_target(self):
        write_xlsx(self.target)
        before = self.target.read_bytes()
        self.source.parent.mkdir()
        self.source.write_bytes(b"SharePoint login page, not a workbook")
        with self.assertRaises((ValueError, RuntimeError, zipfile.BadZipFile)):
            self.stage(self.source)
        self.assertEqual(self.target.read_bytes(), before)

    def test_arbitrary_zip_is_not_accepted_as_a_workbook(self):
        write_xlsx(self.target)
        before = self.target.read_bytes()
        self.source.parent.mkdir()
        with zipfile.ZipFile(self.source, "w") as archive:
            archive.writestr("not-an-ooxml-workbook.txt", "fixture")
        with self.assertRaises((ValueError, RuntimeError, zipfile.BadZipFile)):
            self.stage(self.source)
        self.assertEqual(self.target.read_bytes(), before)

    def test_zip_crc_corruption_is_detected_before_replacing_target(self):
        write_xlsx(self.target)
        before = self.target.read_bytes()
        write_xlsx(self.source, "unique CRC fixture marker")
        with zipfile.ZipFile(self.source, "r") as archive:
            parts = {name: archive.read(name) for name in archive.namelist()}
        with zipfile.ZipFile(self.source, "w", zipfile.ZIP_STORED) as archive:
            for name, content in parts.items():
                archive.writestr(name, content)
        damaged = bytearray(self.source.read_bytes())
        position = damaged.index(b"unique CRC fixture marker")
        damaged[position] = ord("X")
        self.source.write_bytes(damaged)
        with self.assertRaises((ValueError, RuntimeError, zipfile.BadZipFile)):
            self.stage(self.source)
        self.assertEqual(self.target.read_bytes(), before)

    def test_broken_xml_is_not_accepted_as_a_workbook(self):
        write_xlsx(self.target)
        before = self.target.read_bytes()
        write_xlsx(self.source)
        with zipfile.ZipFile(self.source, "r") as archive:
            parts = {name: archive.read(name) for name in archive.namelist()}
        parts["xl/worksheets/sheet1.xml"] = b"<not-closed>"
        with zipfile.ZipFile(self.source, "w") as archive:
            for name, content in parts.items():
                archive.writestr(name, content)
        with self.assertRaises((ValueError, RuntimeError, zipfile.BadZipFile)):
            self.stage(self.source)
        self.assertEqual(self.target.read_bytes(), before)

    def test_explicit_same_file_is_not_a_frozen_shared_snapshot(self):
        write_xlsx(self.target)
        before = self.target.read_bytes()
        with self.assertRaises((ValueError, RuntimeError)):
            self.stage(self.target)
        self.assertEqual(self.target.read_bytes(), before)

    def test_hardlinked_shared_source_is_rejected(self):
        write_xlsx(self.source)
        self.target.parent.mkdir(parents=True)
        try:
            os.link(self.source, self.target)
        except OSError as error:
            self.skipTest(f"Hard links unavailable in test filesystem: {error}")
        before = self.source.read_bytes()
        with self.assertRaises((ValueError, RuntimeError)):
            self.stage(self.source)
        self.assertEqual(self.source.read_bytes(), before)
        self.assertEqual(self.target.read_bytes(), before)

    def test_symlinked_shared_target_is_rejected(self):
        write_xlsx(self.source)
        self.target.parent.mkdir(parents=True)
        try:
            self.target.symlink_to(self.source)
        except OSError as error:
            self.skipTest(f"Symlinks unavailable in test filesystem: {error}")
        with self.assertRaises((ValueError, RuntimeError)):
            self.stage(self.source)
        self.assertTrue(self.target.is_symlink())

    def test_malformed_personal_json_does_not_silently_use_local_target(self):
        write_xlsx(self.target)
        before = self.target.read_bytes()
        self.local_config.write_text("{not valid json", encoding="utf-8")
        with self.assertRaises((ValueError, RuntimeError)):
            self.stage()
        self.assertEqual(self.target.read_bytes(), before)

    def test_archive_failure_preserves_previous_working_inventory(self):
        write_xlsx(self.target, "previous valid inventory")
        write_xlsx(self.source, "new valid inventory")
        before = self.target.read_bytes()
        self.archive.parent.mkdir(parents=True)
        self.archive.write_bytes(b"a file prevents creating the archive directory")
        with self.assertRaises((OSError, ValueError, RuntimeError)):
            self.stage(self.source)
        self.assertEqual(self.target.read_bytes(), before)

    def test_manifest_write_failure_preserves_previous_working_inventory(self):
        write_xlsx(self.target, "previous valid inventory")
        write_xlsx(self.source, "new valid inventory")
        before = self.target.read_bytes()
        with patch.object(config._json, "dump", side_effect=OSError("manifest disk full")):
            with self.assertRaisesRegex(OSError, "manifest disk full"):
                self.stage(self.source)
        self.assertEqual(self.target.read_bytes(), before)
        self.assertEqual(list(self.archive.glob("*/*.json")), [])

    def test_failed_atomic_replacement_keeps_old_file_and_removes_manifest(self):
        write_xlsx(self.target, "previous valid inventory")
        write_xlsx(self.source, "new valid inventory")
        before = self.target.read_bytes()
        original_replace = Path.replace

        def replace(path, destination):
            if Path(destination) == self.target:
                raise PermissionError("canonical workbook locked")
            return original_replace(path, destination)

        with patch.object(Path, "replace", new=replace):
            with self.assertRaisesRegex(PermissionError, "canonical workbook locked"):
                self.stage(self.source)
        self.assertEqual(self.target.read_bytes(), before)
        self.assertEqual(list(self.archive.glob("*/*.json")), [])

    def test_snapshot_verifier_accepts_unmodified_staged_and_archived_files(self):
        write_xlsx(self.source)
        result = self.stage(self.source)
        self.assertEqual(config.verify_itom_inventory_snapshot(result), result)

    def test_snapshot_verifier_detects_changed_canonical_file(self):
        write_xlsx(self.source)
        result = self.stage(self.source)
        write_xlsx(self.target, "changed after setup")
        with self.assertRaisesRegex(RuntimeError, "changed|snapshot"):
            config.verify_itom_inventory_snapshot(result)

    def test_snapshot_verifier_detects_changed_archived_file(self):
        write_xlsx(self.source)
        result = self.stage(self.source)
        write_xlsx(Path(result["snapshot_path"]), "changed archive")
        with self.assertRaisesRegex(RuntimeError, "changed|snapshot"):
            config.verify_itom_inventory_snapshot(result)

    def test_snapshot_verifier_requires_successful_setup(self):
        with self.assertRaisesRegex(RuntimeError, "setup|snapshot"):
            config.verify_itom_inventory_snapshot(None)


class SharedInventoryNotebookTests(unittest.TestCase):
    def test_importing_configuration_does_not_stage_or_create_files(self):
        source = (ROOT / "config.py").read_text(encoding="utf-8")
        forbidden = AssertionError("Importing configuration must not stage or write files")
        namespace = {"__file__": str(ROOT / "config.py"), "__name__": "_config_import_fixture"}
        with patch.object(Path, "open", side_effect=forbidden), \
                patch.object(Path, "mkdir", side_effect=forbidden), \
                patch.object(Path, "replace", side_effect=forbidden), \
                patch.object(config._shutil, "copyfile", side_effect=forbidden):
            exec(compile(source, "config.py", "exec"), namespace)
        self.assertTrue(callable(namespace["stage_itom_inventory"]))

    def test_staging_runs_only_for_builds_and_precedes_descriptor_loading(self):
        notebook = json.loads((ROOT / "1_export_packages.ipynb").read_text(encoding="utf-8"))
        cells = ["".join(cell["source"]) for cell in notebook["cells"] if cell["cell_type"] == "code"]
        staged = [(index, ast.parse(source)) for index, source in enumerate(cells)
                  if "stage_itom_inventory(" in source]
        self.assertEqual(len(staged), 1, "Stage exactly once before creating builders or Packages")
        stage_index, tree = staged[0]
        guard = next(node for node in ast.walk(tree) if isinstance(node, ast.If)
                     and any(isinstance(child, ast.Call) and isinstance(child.func, ast.Name)
                             and child.func.id == "stage_itom_inventory" for child in ast.walk(node)))
        self.assertEqual({node.id for node in ast.walk(guard.test) if isinstance(node, ast.Name)},
                         {"create_dbs", "create_db_package"})
        self.assertIsInstance(guard.test, ast.BoolOp)
        self.assertIsInstance(guard.test.op, ast.Or)
        for package_index, source in enumerate(cells):
            if "sps_itom_por = Package(" in source:
                self.assertLessEqual(stage_index, package_index)
                if stage_index == package_index:
                    self.assertLess(source.index("stage_itom_inventory("), source.index("sps_itom_por = Package("))
        descriptor = json.loads((ROOT / "datapackages/datapackage_itom_por.json").read_text(encoding="utf-8"))
        inventories = next(resource for resource in descriptor["resources"] if resource["name"] == "inventories")
        self.assertEqual(inventories["path"], "inventories/lci-itom_por.xlsx")

    def test_both_export_builders_verify_the_same_snapshot_before_construction(self):
        notebook = json.loads((ROOT / "1_export_packages.ipynb").read_text(encoding="utf-8"))
        cells = ["".join(cell["source"]) for cell in notebook["cells"] if cell["cell_type"] == "code"]
        for builder_name in ("NewDatabase", "PathwaysDataPackage"):
            with self.subTest(builder=builder_name):
                route = "if create_dbs:" if builder_name == "NewDatabase" else "if create_db_package:"
                source = next(source for source in cells if route in source)
                tree = ast.parse(source)
                calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
                builder = next(node for node in calls if isinstance(node.func, ast.Attribute)
                               and node.func.attr == builder_name)
                verification = next(node for node in calls if isinstance(node.func, ast.Name)
                                    and node.func.id == "verify_itom_inventory_snapshot")
                self.assertLess(verification.lineno, builder.lineno)
                self.assertEqual(len(verification.args), 1)
                self.assertIsInstance(verification.args[0], ast.Name)
                self.assertEqual(verification.args[0].id, "itom_inventory_snapshot")


if __name__ == "__main__":
    unittest.main()
