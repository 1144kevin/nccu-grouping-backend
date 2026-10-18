import json
import os

import pytest

from app.core.config import Settings
from scripts import run_cloud_core


@pytest.fixture
def cloud_file(tmp_path, monkeypatch):
    # Separate fake environment: never inherit or modify actual account credentials.
    environment = {
        k: v
        for k, v in os.environ.items()
        if k
        not in {name.upper() for name in Settings.model_fields}
        | {"PORT", "WEB_CONCURRENCY", "UVICORN_WORKERS", "UVICORN_RELOAD"}
    }
    monkeypatch.setattr(os, "environ", environment)
    monkeypatch.setattr(run_cloud_core, "ROOT", tmp_path)
    value = {
        "ENVIRONMENT": "cloud",
        "CORE_CLOUD_ENABLED": "true",
        "CORE_SUPABASE_ENABLED": "true",
        "CLOUD_PUBLIC_API_URL": "https://api.team-domain.org/api/v1",
        "CLOUD_PROXY_NETWORKS": '["127.0.0.1/32"]',
        "BACKEND_CORS_ORIGINS": "[]",
        "SUPABASE_URL": "https://axdwbfhrkbovgrbngrrh.supabase.co",
        "SUPABASE_PUBLISHABLE_KEY": "sb_publishable_synthetic_test_key",
    }
    path = tmp_path / "synthetic.env"
    path.write_text("\n".join(f"{k}={v}" for k, v in value.items()), encoding="utf-8")
    return path


def test_cloud_check_does_not_listen_or_probe_and_exports_no_secrets(
    cloud_file, monkeypatch, capsys
):
    monkeypatch.setattr(
        "sys.argv", ["run_cloud_core.py", "--env-file", str(cloud_file), "--write-client-profile"]
    )

    def forbidden(*args, **kwargs):
        raise AssertionError("Check must not start a server or call Supabase")

    monkeypatch.setattr("uvicorn.run", forbidden)
    monkeypatch.setattr("app.core_cloud.store.SupabaseCoreStore._request", forbidden)
    run_cloud_core.main()
    result = json.loads((cloud_file.parent / ".local/apple-release.json").read_text())
    assert result == {"profile": "release", "api": "https://api.team-domain.org/api/v1"}
    assert "sb_publishable" not in capsys.readouterr().out


@pytest.mark.parametrize(
    "line",
    [
        "PORT=synthetic-sensitive-value",
        'CLOUD_PROXY_NETWORKS=["synthetic-sensitive-value"]',
        "CLOUD_PUBLIC_API_URL=https://api.team-domain.org:synthetic-sensitive-value/api/v1",
        "MCP_TOKEN=synthetic-sensitive-value",
    ],
)
def test_invalid_cloud_environment_never_echoes_values(cloud_file, monkeypatch, line):
    with cloud_file.open("a", encoding="utf-8") as f:
        f.write("\n" + line)
    monkeypatch.setattr("sys.argv", ["run_cloud_core.py", "--env-file", str(cloud_file)])
    with pytest.raises(SystemExit) as error:
        run_cloud_core.main()
    assert "synthetic-sensitive-value" not in str(error.value)
