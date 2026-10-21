import json
import socket
import ssl
import threading
import time
from datetime import UTC, datetime, timedelta

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import serialization

from app.core.device_setup import check_tls, device_addresses, issue_tls, load_device
from scripts.device_link import export_public, network_problem


@pytest.fixture
def prepared(tmp_path):
    local = tmp_path / ".local"
    local.mkdir()
    tls = local / "tls"
    tls.mkdir()
    issue_tls(tls, "192.168.1.10")
    (local / "device-test.env").write_text(
        "DEVICE_HOST=192.168.1.10\nDEVICE_CLIENTS=192.168.1.20\n"
        "DEVICE_TLS_CERT=.local/tls/server.pem\nDEVICE_TLS_KEY=.local/tls/server-key.pem\n"
        "DEVICE_TLS_CA=.local/tls/ca.pem\nDEVICE_AI_ENABLED=false\n"
    )
    (local / "supabase-dev.env").write_text(
        "SUPABASE_URL=https://axdwbfhrkbovgrbngrrh.supabase.co\n"
        "SUPABASE_PUBLISHABLE_KEY=sb_publishable_synthetic_device_key\n"
    )
    return tmp_path, tls


def test_tls_key_san_and_ca_are_checked_and_ca_private_key_is_not_saved(prepared):
    _, tls = prepared
    report = check_tls("192.168.1.10", tls / "server.pem", tls / "server-key.pem", tls / "ca.pem")
    assert len(report["ca_sha256"]) == 64
    assert datetime.fromisoformat(report["leaf_expires"]) < datetime.now(UTC) + timedelta(days=8)
    assert {p.name for p in tls.iterdir()} == {"server.pem", "server-key.pem", "ca.pem", "ca.cer"}
    with pytest.raises(ValueError, match="SAN"):
        check_tls("192.168.1.11", tls / "server.pem", tls / "server-key.pem", tls / "ca.pem")
    with pytest.raises(ValueError, match="overwrite"):
        issue_tls(tls, "192.168.1.10")


def test_mismatched_leaf_key_is_refused(prepared):
    _, tls = prepared
    other = tls / "other"
    other.mkdir()
    issue_tls(other, "192.168.1.10")
    with pytest.raises(ssl.SSLError):
        check_tls("192.168.1.10", tls / "server.pem", other / "server-key.pem", tls / "ca.pem")


def test_device_configuration_ignores_ambient_paid_and_cloud_modes(prepared, monkeypatch):
    root, _ = prepared
    for key, value in {
        "CORE_CLOUD_ENABLED": "true",
        "OPENAI_API_KEY": "never-inherit",
        "CORE_LOCAL_ENABLED": "true",
        "DEBUG": "true",
        "STEALTH_AI_PROVIDER": "ollama_local",
    }.items():
        monkeypatch.setenv(key, value)
    settings, _, _ = load_device(root)
    assert settings.core_supabase_enabled and settings.core_device_test_enabled
    assert (
        not settings.core_cloud_enabled and not settings.core_local_enabled and not settings.debug
    )
    assert not settings.openai_api_key and settings.stealth_ai_provider == "offline_rules"
    assert not settings.core_google_ios_enabled


def test_public_mac_export_contains_no_private_material(prepared):
    root, tls = prepared
    public = export_public(root)
    folder = root / ".local/iphone-windows-link"
    assert {p.name for p in folder.iterdir()} == {"ca.cer", "device-connection.json"}
    assert (folder / "ca.cer").read_bytes() == x509.load_pem_x509_certificate(
        (tls / "ca.pem").read_bytes()
    ).public_bytes(serialization.Encoding.DER)
    assert "publishable" not in json.dumps(public) and "key" not in json.dumps(public)


@pytest.mark.parametrize(
    "host,clients",
    [
        ("140.119.124.78", ["140.119.125.3"]),
        ("0.0.0.0", ["192.168.1.2"]),
        ("192.168.1.1", ["192.168.1.1"]),
        ("192.168.1.1", ["192.168.1.2"] * 2),
    ],
)
def test_public_or_ambiguous_device_addresses_are_refused(host, clients):
    with pytest.raises(ValueError):
        device_addresses(host, clients)


def test_real_tls_requires_trusted_ca_and_matching_hostname(prepared):
    import uvicorn

    from app.main import create_app

    root, tls = prepared
    settings, _, _ = load_device(root)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(settings),
            log_level="error",
            lifespan="off",
            access_log=False,
            ssl_certfile=str(tls / "server.pem"),
            ssl_keyfile=str(tls / "server-key.pem"),
        )
    )
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    try:
        for _ in range(100):
            if server.started:
                break
            time.sleep(0.05)
        assert server.started
        trusted = ssl.create_default_context(cafile=str(tls / "ca.pem"))
        with socket.create_connection(("127.0.0.1", port), timeout=3) as raw:
            with trusted.wrap_socket(raw, server_hostname="192.168.1.10") as connection:
                connection.sendall(
                    b"GET /health HTTP/1.1\r\nHost: 192.168.1.10:8443\r\nConnection: close\r\n\r\n"
                )
                assert b"200 OK" in connection.recv(4096)
        for context, name in (
            (trusted, "192.168.1.11"),
            (ssl.create_default_context(), "192.168.1.10"),
        ):
            with socket.create_connection(("127.0.0.1", port), timeout=3) as raw:
                with pytest.raises(ssl.SSLCertVerificationError):
                    context.wrap_socket(raw, server_hostname=name)
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        listener.close()
        assert not thread.is_alive()


def test_public_or_changed_network_is_not_permitted_to_serve():
    assert network_problem("192.168.1.10", [{"host": "192.168.1.10", "category": "Public"}])
    assert network_problem("192.168.1.11", [{"host": "192.168.1.10", "category": "Private"}])
    assert (
        network_problem("192.168.1.10", [{"host": "192.168.1.10", "category": "Private"}]) is None
    )
