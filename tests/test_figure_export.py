"""Regression checks for complete, repeatable figure publication on Windows.

Only disposable synthetic figures are written. No model result is overwritten.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import errno
import os
from pathlib import Path
import shutil
import sys
import unittest
from unittest import mock
from uuid import uuid4
import warnings
import xml.etree.ElementTree as ET

from matplotlib.figure import Figure
from matplotlib.backends.backend_agg import FigureCanvasAgg
from PIL import Image

import config


ROOT = Path(__file__).resolve().parents[1]


class FigureExportTests(unittest.TestCase):
    def setUp(self):
        # Ordinary workspace ACLs avoid the Windows secure-temp directory issue.
        self.parent = ROOT / "tmp" / "figure_export_tests"
        self.parent.mkdir(parents=True, exist_ok=True)
        self.folder = self.parent / ("run_" + uuid4().hex)
        self.folder.mkdir()
        self.original_cwd = Path.cwd()
        self.figure = Figure(figsize=(2.2, 1.5))
        FigureCanvasAgg(self.figure)
        ax = self.figure.subplots()
        ax.plot([0, 1, 2], [1, 3, 2], color="#357D99")
        ax.set_title("Synthetic test")

    def tearDown(self):
        os.chdir(self.original_cwd)
        self.figure.clear()
        self.folder.resolve().relative_to(self.parent.resolve())
        self.parent.resolve().relative_to(ROOT.resolve())
        if not self.folder.name.startswith("run_"):
            raise RuntimeError("Unexpected disposable figure-test directory")
        shutil.rmtree(self.folder)

    def png(self, path):
        self.assertEqual(Path(path).read_bytes()[:8], b"\x89PNG\r\n\x1a\n")
        with Image.open(path) as image:
            image.verify()

    def test_real_png_pdf_and_svg_are_complete(self):
        for extension in ("png", "pdf", "svg"):
            with self.subTest(extension=extension):
                target = self.folder / ("figure." + extension)
                actual = config.save_figure(self.figure, target, dpi=70, bbox_inches="tight")
                self.assertEqual(Path(actual), target.resolve())
                payload = target.read_bytes()
                if extension == "png":
                    self.png(target)
                elif extension == "pdf":
                    self.assertTrue(payload.startswith(b"%PDF-"))
                    self.assertIn(b"%%EOF", payload[-1024:])
                else:
                    self.assertTrue(ET.fromstring(payload).tag.endswith("svg"))
        self.assertEqual({p.name for p in self.folder.iterdir()},
                         {"figure.png", "figure.pdf", "figure.svg"})

    def test_unique_closed_sibling_is_renamed_instead_of_truncating_target(self):
        target = self.folder / "staged.png"
        target.write_bytes(b"previous complete figure")
        original_replace = config._os.replace
        staging_sources = []

        def observe(source, destination):
            source, destination = Path(source), Path(destination)
            self.assertEqual(destination, target.resolve())
            self.assertEqual(source.parent, target.parent)
            self.assertNotEqual(source, target)
            self.png(source)
            staging_sources.append(source)
            # On Windows the rename would fail if its ordinary writer were open.
            return original_replace(source, destination)

        with mock.patch.object(config._os, "replace", side_effect=observe):
            first = config.save_figure(self.figure, target, dpi=50)
            second = config.save_figure(self.figure, target, dpi=50)
        self.assertEqual(Path(first), target)
        self.assertEqual(Path(second), target)
        self.assertEqual(len(staging_sources), 2)
        self.assertNotEqual(staging_sources[0], staging_sources[1])
        self.assertTrue(all(not path.exists() for path in staging_sources))
        self.assertEqual(list(self.folder.iterdir()), [target])

    def test_transient_invalid_argument_retries_and_keeps_canonical_name(self):
        target = self.folder / "transient.png"
        target.write_bytes(b"previous complete figure")
        original_replace = config._os.replace
        attempts = []

        def transient(source, destination):
            attempts.append((Path(source), Path(destination)))
            if len(attempts) == 1:
                self.assertEqual(target.read_bytes(), b"previous complete figure")
                raise OSError(errno.EINVAL, "simulated intermittent invalid argument", str(destination))
            return original_replace(source, destination)

        with mock.patch.object(config._os, "replace", side_effect=transient), \
                mock.patch.object(config._time, "sleep") as sleep:
            with warnings.catch_warnings(record=True) as caught:
                actual = config.save_figure(self.figure, target, retry_delays=(.01, .02), dpi=50)
        self.assertEqual(Path(actual), target)
        self.assertEqual(len(attempts), 2)
        self.assertEqual(sleep.call_count, 1)
        self.assertFalse(caught)
        self.png(target)
        self.assertEqual(list(self.folder.iterdir()), [target])

    def test_persistent_target_sharing_error_preserves_old_and_returns_fallback(self):
        target = self.folder / "locked.png"
        old = b"previous complete figure"
        target.write_bytes(old)
        original_replace = config._os.replace
        blocked = []

        def locked(source, destination):
            if Path(destination) == target:
                blocked.append(Path(source))
                error = PermissionError(errno.EACCES, "simulated sharing violation", str(destination))
                error.winerror = 32
                raise error
            return original_replace(source, destination)

        with mock.patch.object(config._os, "replace", side_effect=locked), \
                mock.patch.object(config._time, "sleep") as sleep:
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                actual = Path(config.save_figure(self.figure, target, retry_delays=(.01, .02), dpi=50))
        self.assertNotEqual(actual, target)
        self.assertEqual(actual.parent, target.parent)
        self.assertEqual(actual.suffix, ".png")
        self.assertEqual(target.read_bytes(), old)
        self.assertEqual(len(blocked), 3)
        self.assertEqual(sleep.call_count, 2)
        self.png(actual)
        messages = "\n".join(str(w.message) for w in caught)
        self.assertIn(target.name, messages)
        self.assertIn(actual.name, messages)
        self.assertEqual({p.name for p in self.folder.iterdir()}, {target.name, actual.name})

    def test_strict_mode_raises_without_replacing_old_or_leaving_staging_files(self):
        target = self.folder / "strict.png"
        old = b"previous complete figure"
        target.write_bytes(old)
        with mock.patch.object(config._os, "replace", side_effect=OSError(errno.EINVAL, "blocked")) as replace, \
                mock.patch.object(config._time, "sleep") as sleep:
            with self.assertRaises(OSError):
                config.save_figure(self.figure, target, retry_delays=(.01,), allow_fallback=False, dpi=50)
        self.assertEqual(replace.call_count, 2)
        self.assertEqual(sleep.call_count, 1)
        self.assertEqual(target.read_bytes(), old)
        self.assertEqual(list(self.folder.iterdir()), [target])

    def test_rendering_error_does_not_publish_retry_or_damage_existing_figure(self):
        target = self.folder / "render_failure.png"
        old = b"previous complete figure"
        target.write_bytes(old)
        with mock.patch.object(self.figure, "savefig", side_effect=ValueError("invalid plotting parameter")), \
                mock.patch.object(config._os, "replace") as replace, \
                mock.patch.object(config._time, "sleep") as sleep:
            with self.assertRaisesRegex(ValueError, "invalid plotting parameter"):
                config.save_figure(self.figure, target)
        replace.assert_not_called()
        sleep.assert_not_called()
        self.assertEqual(target.read_bytes(), old)
        self.assertEqual(list(self.folder.iterdir()), [target])

    def test_disk_full_is_not_retried_or_silently_rerouted(self):
        target = self.folder / "disk_full.png"
        old = b"previous complete figure"
        target.write_bytes(old)
        with mock.patch.object(config._os, "replace", side_effect=OSError(errno.ENOSPC, "disk full")) as replace, \
                mock.patch.object(config._time, "sleep") as sleep:
            with self.assertRaises(OSError) as error:
                config.save_figure(self.figure, target, retry_delays=(.01, .02), dpi=50)
        self.assertEqual(error.exception.errno, errno.ENOSPC)
        self.assertEqual(replace.call_count, 1)
        sleep.assert_not_called()
        self.assertEqual(target.read_bytes(), old)
        self.assertEqual(list(self.folder.iterdir()), [target])

    def test_cleanup_problem_does_not_mask_the_initial_write_error(self):
        target = self.folder / "failed_stage.png"
        old = b"previous complete figure"
        target.write_bytes(old)
        with mock.patch.object(Path, "open", side_effect=OSError(errno.ENOSPC, "disk full")), \
                mock.patch.object(Path, "unlink", side_effect=PermissionError(errno.EACCES, "scanner busy")), \
                warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            with self.assertRaises(OSError) as error:
                config.save_figure(self.figure, target, retry_delays=(), dpi=50)
        self.assertEqual(error.exception.errno, errno.ENOSPC)
        self.assertEqual(target.read_bytes(), old)
        self.assertTrue(any("staging file" in str(w.message) for w in caught))

    def test_basename_and_explicit_relative_path_anchor_to_repo_not_cwd(self):
        elsewhere = self.folder / "unrelated_working_directory"
        elsewhere.mkdir()
        output = self.folder / "figs"
        unique = uuid4().hex
        with mock.patch.object(config, "ROOT", self.folder), \
                mock.patch.object(config, "FIGURES_DIR", output):
            os.chdir(elsewhere)
            relative = Path(config.save_figure(self.figure, f"figs/relative_{unique}.png", dpi=50))
            basename = Path(config.save_figure(self.figure, f"basename_{unique}.svg", dpi=50))
        self.assertEqual(relative, output / f"relative_{unique}.png")
        self.assertEqual(basename, output / f"basename_{unique}.svg")
        self.assertTrue(relative.is_absolute() and basename.is_absolute())
        self.png(relative)
        self.assertTrue(ET.parse(basename).getroot().tag.endswith("svg"))
        self.assertFalse(any(elsewhere.iterdir()))

    @unittest.skipUnless(sys.platform == "win32", "Native Windows file-sharing regression")
    def test_real_windows_read_handle_blocks_replace_but_not_fallback_export(self):
        target = self.folder / "native_windows_lock.png"
        old = b"previous complete figure"
        target.write_bytes(old)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
            wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        # Read sharing is permitted; delete sharing is deliberately withheld.
        handle = kernel.CreateFileW(str(target), 0x80000000, 0x1, None, 3, 0x80, None)
        if handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                actual = Path(config.save_figure(self.figure, target, retry_delays=(), dpi=50))
            self.assertNotEqual(actual, target)
            self.assertEqual(target.read_bytes(), old)
            self.png(actual)
            self.assertTrue(caught)
            self.assertEqual({p.name for p in self.folder.iterdir()}, {target.name, actual.name})
        finally:
            kernel.CloseHandle(handle)


if __name__ == "__main__":
    unittest.main()
