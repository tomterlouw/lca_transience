"""Shared model settings and repository-relative input/output locations."""
from pathlib import Path
from datetime import datetime as _datetime, timezone as _timezone
import hashlib as _hashlib
import json as _json
import os as _os
import shutil as _shutil
import tempfile as _tempfile
import uuid as _uuid
import xml.etree.ElementTree as _ET
import zipfile


ROOT = Path(__file__).resolve().parent
DATAPACKAGE_DIR = ROOT / "datapackages"
PACKAGE_OUTPUT_DIR = ROOT / "results" / "datapackages"
IMPACT_RESULTS_DIR = ROOT / "results" / "impacts"
EDGES_RESULTS_DIR = ROOT / "results" / "edges"
INPUT_SNAPSHOT_DIR = ROOT / "results" / "input_snapshots"
ITOM_INVENTORY_PATH = ROOT / "inventories" / "lci-itom_por.xlsx"
INVENTORY_SOURCES_LOCAL = ROOT / "inventory_sources.local.json"

EI_VERSION = "3.12"
DB_NAME_INIT = 'ecoinvent-{}-cutoff'.format(EI_VERSION)
PROJECT_NAME = "por"
NAME_REF_DB = "ecoinvent_{}_reference".format(EI_VERSION).replace(".","")
BIOSPHERE_DB = f'ecoinvent-{EI_VERSION}-biosphere'


def _resolve_artifact_path(name, default_directory):
    path = Path(name).expanduser()
    if path.is_absolute():
        return path.resolve()
    # A basename selects the standard output folder. An explicit relative path
    # remains relative to the repository, independently of the kernel's cwd.
    base = default_directory if path.parent == Path(".") else ROOT
    return (base / path).resolve()


def resolve_package_path(name):
    """Resolve a generated scenario archive, without creating directories."""
    return _resolve_artifact_path(name, PACKAGE_OUTPUT_DIR)


def resolve_result_path(name, *, edges=False):
    """Resolve an ordinary or EDGES result, keeping the two streams separate."""
    return _resolve_artifact_path(name, EDGES_RESULTS_DIR if edges else IMPACT_RESULTS_DIR)


def store_generated_package(name, *, source_dir=None):
    """Move a completed native ZIP to the shared package output directory.

    premise 2.3.8/2.5.2 require a plain package name and write into cwd. Move
    only after successful creation, without changing cwd or descriptor names.
    A rerun atomically replaces this selected generated target; other archives
    are untouched. Importing config or resolving a path never moves any files.
    """
    name = str(name)
    if name in ("", ".", "..", ".zip") or any(separator in name for separator in ("/", "\\")):
        raise ValueError("Use a plain package basename, not an output path.")
    filename = name if name.endswith(".zip") else name + ".zip"
    source = (Path.cwd() if source_dir is None else Path(source_dir)).resolve() / filename
    target = PACKAGE_OUTPUT_DIR / filename
    if not source.is_file():
        raise FileNotFoundError(f"Native package export did not produce {source}")
    if not zipfile.is_zipfile(source):
        raise ValueError(f"Native package export is not a valid ZIP: {source}")
    if source.resolve() == target.resolve():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    source.replace(target)
    return target


def _inventory_sha256(path):
    digest = _hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_inventory_xlsx(path):
    """Read-only container validation; premise still validates the LCI schema."""
    try:
        with zipfile.ZipFile(path) as workbook:
            names = set(workbook.namelist())
            required = {"[Content_Types].xml", "_rels/.rels", "xl/workbook.xml",
                        "xl/_rels/workbook.xml.rels"}
            if not required.issubset(names) or not any(
                    name.startswith("xl/worksheets/") and name.endswith(".xml") for name in names):
                raise ValueError("Required XLSX workbook/worksheet parts are missing")
            if workbook.testzip() is not None:
                raise ValueError("XLSX ZIP checksum validation failed")
            # Parse every XML part, without re-saving, recalculating or changing it.
            for name in names:
                if name.endswith((".xml", ".rels")):
                    _ET.fromstring(workbook.read(name))
    except (zipfile.BadZipFile, _ET.ParseError, KeyError, RuntimeError) as error:
        raise ValueError(f"Invalid inventory XLSX {path}: {error}") from error


def _copy_stable_xlsx(source, destination, *, validate=True):
    """Reject saves/sync replacements during copying instead of taking a mix."""
    before = source.stat()
    _shutil.copyfile(source, destination)
    digest = _inventory_sha256(destination)
    current_digest = _inventory_sha256(source)
    after = source.stat()
    identity = lambda stat: (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns,
                             stat.st_ctime_ns)
    if identity(before) != identity(after) or digest != current_digest:
        raise RuntimeError("Inventory source changed while copying; save it, wait for sync, and retry.")
    if validate:
        _validate_inventory_xlsx(destination)
    return digest


def _archive_inventory(path, digest, archive_dir):
    destination = archive_dir / digest / "lci-itom_por.xlsx"
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if _inventory_sha256(destination) != digest:
            raise RuntimeError(f"Inventory snapshot was modified: {destination}")
        return destination
    with _tempfile.NamedTemporaryFile(dir=destination.parent, suffix=".tmp", delete=False) as stream:
        temporary = Path(stream.name)
    try:
        _shutil.copyfile(path, temporary)
        if _inventory_sha256(temporary) != digest:
            raise RuntimeError("Inventory changed while archiving; no staged replacement was made.")
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def stage_itom_inventory(source=None, *, target=None, archive_dir=None, local_config=None):
    """Freeze a saved shared workbook before creating any premise builders.

    Source priority: explicit path, ITOM_POR_SHARED_INVENTORY environment variable,
    ignored inventory_sources.local.json (itom_por), existing local inventory.
    A configured but missing/unreadable source is an error, never a stale fallback.
    No cloud API, background refresh or copy-back is performed. Importing config
    has no I/O side effects. The canonical basename/path remains unchanged for
    both premise versions and independent protected-supplier/freshness checks.
    """
    target = Path(ITOM_INVENTORY_PATH if target is None else target).expanduser().absolute()
    archive_dir = Path(INPUT_SNAPSHOT_DIR if archive_dir is None else archive_dir).expanduser().resolve()
    local_config = Path(INVENTORY_SOURCES_LOCAL if local_config is None else local_config)
    if source is None:
        source = _os.environ.get("ITOM_POR_SHARED_INVENTORY", "").strip() or None
        if source is None and local_config.exists():
            settings = _json.loads(local_config.read_text(encoding="utf-8-sig"))
            if not isinstance(settings, dict) or set(settings) - {"itom_por"}:
                raise ValueError(f"Expected a JSON object with only an itom_por path: {local_config}")
            source = settings.get("itom_por")
    source_kind = "local" if source is None else "shared"
    if source is not None:
        if not isinstance(source, (str, _os.PathLike)) or not str(source).strip() or "://" in str(source):
            raise ValueError("Set itom_por to a non-empty local synced XLSX path, not a sharing URL.")
        source = Path(_os.path.expandvars(str(source))).expanduser()
        if not source.is_absolute():
            raise ValueError("The shared inventory source must be an absolute local synced path.")
        source = source.resolve()
    else:
        source = target
    if target.is_symlink():
        raise ValueError("The staged inventory must be an independent file, not a live symlink.")
    if not source.is_file():
        raise FileNotFoundError(
            f"Inventory source is missing: {source}. Save/sync it and select Always keep on this device; "
            "no previous local inventory was substituted.")
    if source.suffix.lower() != ".xlsx":
        raise ValueError("The inventory source must be an .xlsx workbook.")
    if source_kind == "shared" and (source == target.resolve() or
            (target.exists() and _os.path.samefile(source, target))):
        raise ValueError("Shared source and staged inventory must be different independent files.")

    target.parent.mkdir(parents=True, exist_ok=True)
    with _tempfile.NamedTemporaryFile(dir=target.parent, suffix=".xlsx", delete=False) as stream:
        temporary = Path(stream.name)
    try:
        digest = _copy_stable_xlsx(source, temporary)
        snapshot_path = _archive_inventory(temporary, digest, archive_dir)
        previous_digest = _inventory_sha256(target) if target.exists() else None
        if previous_digest is not None and previous_digest != digest:
            _archive_inventory(target, previous_digest, archive_dir)
        # Do not overwrite a local edit made after its previous version was archived.
        if (target.exists() and _inventory_sha256(target) != previous_digest) or (
                previous_digest is not None and not target.exists()):
            raise RuntimeError("Staged inventory changed during preparation; retry before constructing builders.")
        timestamp = _datetime.now(_timezone.utc)
        manifest = {
            "schema_version": 1, "source_path": str(source), "staged_path": str(target.resolve()),
            "source_kind": source_kind, "sha256": digest, "size_bytes": temporary.stat().st_size,
            "snapshot_path": str(snapshot_path), "staged_at_utc": timestamp.isoformat(),
            "previous_sha256": previous_digest,
        }
        manifest_path = snapshot_path.parent / (timestamp.strftime("%Y%m%dT%H%M%S%fZ") +
                                                 "_" + _uuid.uuid4().hex[:8] + ".json")
        manifest["manifest_path"] = str(manifest_path)
        # Finish provenance I/O before changing the canonical input. On a write
        # failure the original file remains intact; failed swaps remove the record.
        try:
            with manifest_path.open("x", encoding="utf-8") as stream:
                _json.dump(manifest, stream, indent=2)
                stream.write("\n")
            if (target.exists() and _inventory_sha256(target) != previous_digest) or (
                    previous_digest is not None and not target.exists()):
                raise RuntimeError("Staged inventory changed during preparation; recreate builders after retrying.")
            # Identical content keeps the canonical file's mtime/freshness unchanged.
            if source_kind == "shared" and previous_digest != digest:
                temporary.replace(target)
        except BaseException:
            manifest_path.unlink(missing_ok=True)
            raise
        return manifest
    finally:
        temporary.unlink(missing_ok=True)


def verify_itom_inventory_snapshot(snapshot):
    """Refuse a different local workbook if setup cells were rerun mid-session."""
    if not snapshot:
        raise RuntimeError("Run the inventory snapshot setup cell before constructing a builder.")
    for name in ("staged_path", "snapshot_path"):
        path = Path(snapshot[name])
        if not path.is_file() or _inventory_sha256(path) != snapshot["sha256"]:
            raise RuntimeError(f"ITOM inventory snapshot changed: {path}. Rerun setup and recreate all builders.")
    return snapshot
