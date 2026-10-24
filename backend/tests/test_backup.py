import hashlib
import json
import zipfile

import pytest

from omnitest.db import Database, Resource
from omnitest.services.backup import backup, restore
from omnitest.services.lock import WorkerLock


def test_snapshot_includes_wal_evidence_and_default_assets_without_admin_secrets(stack, tmp_path):
    settings, db, _, resources, _ = stack
    project = resources.save("projects", {"name": "Backup project"})
    for relative in [
        "evidence/run/result/1/log.txt",
        "descriptors/service.pb",
        "fixtures/catalog.db",
    ]:
        file = settings.data_dir / relative
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text("fixture content", encoding="utf-8")
    (settings.data_dir / "administrator.json").write_text("private settings", encoding="utf-8")
    archive = tmp_path / "archives/snapshot.zip"
    result = backup(settings.data_dir, archive)
    assert len(result["sha256"]) == 64 and result["files"] == 4
    with zipfile.ZipFile(archive) as content:
        assert "administrator.json" not in content.namelist()
    restored = tmp_path / "restored"
    restore(archive, restored)
    reopened = Database(f"sqlite:///{(restored / 'omnitest.db').as_posix()}")
    reopened.migrate()
    try:
        with reopened.session() as session:
            assert session.get(Resource, project["id"]).body["name"] == "Backup project"
        assert (restored / "evidence/run/result/1/log.txt").read_text() == "fixture content"
    finally:
        reopened.engine.dispose()
    with pytest.raises(ValueError, match="non-existing"):
        restore(archive, restored)
    with pytest.raises(FileExistsError):
        backup(settings.data_dir, archive)


def test_backup_respects_worker_lock(stack, tmp_path):
    lock = WorkerLock(stack[0].data_dir)
    lock.acquire()
    try:
        with pytest.raises(RuntimeError, match="already in use"):
            backup(stack[0].data_dir, tmp_path / "locked.zip")
    finally:
        lock.release()


@pytest.mark.parametrize(
    "name",
    [
        "../escape",
        "/absolute",
        "evidence/../escape",
        "evidence\\escape",
        "evidence/C:drive",
        "administrator.json",
    ],
)
def test_restore_rejects_untrusted_archive_paths_before_writing(tmp_path, name):
    archive = tmp_path / "malicious.zip"
    manifest = {
        "format": 1,
        "files": {
            "omnitest.db": {"size": 0, "sha256": hashlib.sha256(b"").hexdigest()},
            name: {"size": 0, "sha256": hashlib.sha256(b"").hexdigest()},
        },
    }
    with zipfile.ZipFile(archive, "w") as content:
        content.writestr("manifest.json", json.dumps(manifest))
        content.writestr("omnitest.db", b"")
        content.writestr(name, b"")
    target = tmp_path / "new"
    with pytest.raises(ValueError):
        restore(archive, target)
    assert not target.exists() and not (tmp_path / "escape").exists()


def test_corrupt_backup_never_installs_partial_database(stack, tmp_path):
    good = tmp_path / "good.zip"
    backup(stack[0].data_dir, good)
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(good) as source, zipfile.ZipFile(bad, "w") as destination:
        for name in source.namelist():
            data = source.read(name)
            destination.writestr(name, b"x" * len(data) if name == "omnitest.db" else data)
    with pytest.raises(ValueError, match="checksum"):
        restore(bad, tmp_path / "failed")
    assert not (tmp_path / "failed").exists()
