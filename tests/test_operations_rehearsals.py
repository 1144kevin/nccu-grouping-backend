import copy
import json
from uuid import uuid4

import pytest

from scripts import rehearse_backup as backup
from scripts import rehearse_container as container
from scripts import rehearse_supabase_load as load


def graph():
    owners = [str(uuid4()) for _ in range(3)]
    post = str(uuid4())
    scope = "stealth-e2e-" + uuid4().hex
    return {
        "scope": scope,
        "profiles": [{"id": u} for u in owners],
        "posts": [{"id": post, "author_id": owners[0], "payload": {"content": scope}}],
        "joins": [],
        "messages": [{"id": str(uuid4()), "post_id": post, "sender_id": owners[1]}],
    }


def test_corrupt_backup_refuses_restore_before_any_sql():
    package = backup.pack(graph())
    changed = copy.deepcopy(package)
    changed["data"]["posts"][0]["payload"]["content"] += " changed"
    with pytest.raises(ValueError, match="checksum"):
        backup.restore_sql(changed)
    package["data"]["messages"][0]["post_id"] = str(uuid4())
    with pytest.raises(ValueError, match="relationship"):
        backup.restore_sql(package)


def test_restore_is_temp_only_and_never_executes_payload_sql():
    value = graph()
    value["posts"][0]["payload"]["content"] += "'; drop schema public cascade; --"
    sql = backup.restore_sql(backup.pack(value))
    assert "drop schema" not in sql.lower()
    assert "insert into public." not in sql.lower()
    assert "create temporary table" in sql.lower() and sql.strip().endswith("rollback;")
    assert "foreign key" in sql.lower()


def test_rehearsal_paths_stay_ignored_and_load_is_bounded():
    with pytest.raises(ValueError):
        backup.private_path("../somewhere/backup.json")
    with pytest.raises(ValueError):
        load.run(duration=100000)
    with pytest.raises(ValueError):
        load.run(rate=100)
    assert load.percentile([1, 2, 3, 4, 5], 0.95) == 5


@pytest.mark.parametrize(
    "endpoint", ["tcp://127.0.0.1:2375", "ssh://someone@remote", "tcp://remote:2376"]
)
def test_container_never_uses_remote_or_tcp_daemons(endpoint):
    assert not container.local_endpoint(endpoint)


def test_missing_engine_is_reported_as_blocked_not_success(tmp_path, monkeypatch):
    monkeypatch.setattr(container, "REPORT", tmp_path / "report.json")
    monkeypatch.setattr(container.shutil, "which", lambda name: None)
    report = container.run(build=True)
    assert report["status"] == "blocked" and not report["built"] and not report["pushed"]


def test_remote_context_cannot_reach_image_build(tmp_path, monkeypatch):
    monkeypatch.setattr(container, "REPORT", tmp_path / "report.json")
    monkeypatch.setattr(container.shutil, "which", lambda name: "docker")
    calls = []

    def execute(args, **kwargs):
        calls.append(args)
        return "ssh://someone@remote"

    monkeypatch.setattr(container, "execute", execute)
    report = container.run(build=True)
    assert report["status"] == "blocked" and len(calls) == 1
    assert "buildx" not in calls[0]


def test_container_smoke_payload_is_valid_python():
    compile(container.SMOKE, "linux-container-smoke", "exec")
    compile(container.HTTP_PROBE, "linux-container-http-probe", "exec")


@pytest.mark.parametrize("exit_code,oom", [(0, False), (137, False), (0, True)])
def test_native_lifecycle_requires_health_and_graceful_exit(monkeypatch, exit_code, oom):
    stopped = False

    def execute(args, **kwargs):
        nonlocal stopped
        if "inspect" in args:
            return json.dumps(
                {
                    "Running": not stopped,
                    "Health": {"Status": "healthy"},
                    "ExitCode": exit_code if stopped else 0,
                    "OOMKilled": oom,
                }
            )
        if "exec" in args:
            return '{"health":200,"anonymous_posts":401}'
        if "stop" in args:
            stopped = True
        return "synthetic-container"

    monkeypatch.setattr(container, "execute", execute)
    if exit_code or oom:
        with pytest.raises(RuntimeError, match="SIGTERM"):
            container.verify_lifecycle(["docker"], "image", "fixture")
    else:
        assert container.verify_lifecycle(["docker"], "image", "fixture")["sigterm_exit"] == 0


def test_native_lifecycle_refuses_dead_service_before_probe(monkeypatch):
    calls = []

    def execute(args, **kwargs):
        calls.append(args)
        return json.dumps({"Running": False}) if "inspect" in args else "fixture"

    monkeypatch.setattr(container, "execute", execute)
    with pytest.raises(RuntimeError, match="startup"):
        container.verify_lifecycle(["docker"], "image", "fixture")
    assert not any("exec" in args for args in calls)
