import hashlib

import pytest

from scripts import rehearse_postgres_restore as restore


def test_archive_tampering_refused_before_restore(tmp_path):
    archive = tmp_path / "fixture.dump"
    archive.write_bytes(b"synthetic custom dump")
    checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
    assert restore.checked_archive(archive, checksum) == archive.read_bytes()
    archive.write_bytes(b"modified custom dump")
    with pytest.raises(ValueError, match="checksum"):
        restore.checked_archive(archive, checksum)


def test_existing_target_is_refused_even_with_only_public_objects(monkeypatch):
    def sql(docker, name, statement):
        assert "'public'::regnamespace" in statement
        return "1"

    monkeypatch.setattr(restore, "sql", sql)
    with pytest.raises(ValueError, match="fresh"):
        restore.require_blank(["docker"], "fixture")


def test_remote_daemon_never_reaches_database_container(monkeypatch):
    monkeypatch.setattr(restore.shutil, "which", lambda name: "docker")
    calls = []

    def command(args, **kwargs):
        calls.append(args)
        return "ssh://external-server"

    monkeypatch.setattr(restore, "command", command)
    with pytest.raises(ValueError, match="local"):
        restore.local_docker()
    assert len(calls) == 1 and "run" not in calls[0]
