"""Offline archives with SQLite snapshots, checksums and non-overwriting restore."""

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import time
import zipfile
from contextlib import closing
from pathlib import Path, PurePosixPath

from omnitest.services.lock import WorkerLock

MAX_FILES = 50000
MAX_BYTES = 2_000_000_000
ROOTS = {"evidence", "fixtures", "descriptors"}


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def check_database(path):
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection:
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("Backup database integrity check failed")
        if connection.execute("PRAGMA foreign_key_check").fetchone():
            raise ValueError("Backup contains broken foreign keys")
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if not {"resources", "runs", "case_results", "alembic_version"}.issubset(tables):
            raise ValueError("Archive does not contain an OmniTest database")


def backup(directory, output):
    directory, output = Path(directory).resolve(), Path(output).resolve()
    database = directory / "omnitest.db"
    if not database.is_file():
        raise ValueError("No OmniTest database to back up")
    lock = WorkerLock(directory)
    lock.acquire()
    try:
        with tempfile.TemporaryDirectory(prefix="omnitest-backup-") as temporary:
            snapshot = Path(temporary) / "omnitest.db"
            with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as source:
                with closing(sqlite3.connect(snapshot)) as destination:
                    source.backup(destination)
            check_database(snapshot)
            files = {"omnitest.db": snapshot}
            for root in ROOTS:
                folder = directory / root
                if folder.exists() and not folder.resolve().is_relative_to(directory):
                    raise ValueError("Backup path escapes data directory")
                for file in folder.rglob("*"):
                    if file.is_symlink():
                        raise ValueError("Backup refuses symbolic links")
                    if file.is_file():
                        if not file.resolve().is_relative_to(directory):
                            raise ValueError("Backup file escapes data directory")
                        files[file.relative_to(directory).as_posix()] = file
            if len(files) > MAX_FILES or sum(p.stat().st_size for p in files.values()) > MAX_BYTES:
                raise ValueError("Archive exceeds file count or 2 GB size limit")
            manifest = {
                "format": 1,
                "created_at": time.time(),
                "files": {
                    name: {"sha256": digest(path), "size": path.stat().st_size}
                    for name, path in files.items()
                },
            }
            output.parent.mkdir(parents=True, exist_ok=True)
            with output.open("xb") as stream:
                try:
                    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                        for name, path in files.items():
                            archive.write(path, name)
                        archive.writestr("manifest.json", json.dumps(manifest))
                except BaseException:
                    stream.close()
                    output.unlink(missing_ok=True)
                    raise
            return {"archive": str(output), "files": len(files), "sha256": digest(output)}
    finally:
        lock.release()


def restore(archive_path, directory):
    archive_path, directory = Path(archive_path).resolve(), Path(directory).resolve()
    if directory.exists():
        raise ValueError("Restore requires a new, non-existing data directory")
    directory.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive_path) as archive:
        entries = archive.infolist()
        names = [item.filename for item in entries]
        if len(names) != len(set(names)) or len(names) > MAX_FILES + 1:
            raise ValueError("Archive contains duplicate names or too many files")
        if sum(item.file_size for item in entries) > MAX_BYTES or "manifest.json" not in names:
            raise ValueError("Invalid or oversized archive")
        if archive.getinfo("manifest.json").file_size > 10_000_000:
            raise ValueError("Manifest exceeds size limit")
        manifest = json.loads(archive.read("manifest.json"))
        if manifest.get("format") != 1 or not isinstance(manifest.get("files"), dict):
            raise ValueError("Unsupported backup format")
        files = manifest["files"]
        if set(names) != {*files, "manifest.json"} or "omnitest.db" not in files:
            raise ValueError("Archive contents differ from manifest")
        for name in files:
            path = PurePosixPath(name)
            if (
                "\\" in name
                or path.is_absolute()
                or path.as_posix() != name
                or any(part in {"..", ".", ""} or ":" in part for part in path.parts)
            ):
                raise ValueError("Unsafe archive path")
            if name != "omnitest.db" and (len(path.parts) < 2 or path.parts[0] not in ROOTS):
                raise ValueError("Unexpected archive content")
            item = files[name]
            if (
                not isinstance(item, dict)
                or not isinstance(item.get("size"), int)
                or item["size"] != archive.getinfo(name).file_size
                or not isinstance(item.get("sha256"), str)
                or len(item["sha256"]) != 64
            ):
                raise ValueError("Invalid file metadata in archive manifest")
        with tempfile.TemporaryDirectory(
            prefix=".omnitest-restore-", dir=directory.parent
        ) as temporary:
            staging = Path(temporary)
            for name, item in files.items():
                info = archive.getinfo(name)
                if (info.external_attr >> 16) & 0o170000 == 0o120000:
                    raise ValueError("Archive refuses symbolic links")
                path = staging / name
                path.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, path.open("xb") as destination:
                    shutil.copyfileobj(source, destination)
                if path.stat().st_size != item["size"] or digest(path) != item["sha256"]:
                    raise ValueError("Archive checksum mismatch")
            check_database(staging / "omnitest.db")
            # The destination must remain absent; rename never replaces a populated directory.
            if directory.exists():
                raise ValueError("Restore destination was created by another process")
            os.rename(staging, directory)
    return {"data_dir": str(directory), "files": len(files)}
