"""Private-LAN TLS setup and deterministic, fail-closed device configuration."""

import ipaddress
import json
import os
import ssl
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from dotenv import dotenv_values

from app.core.config import Settings
from app.core.device_test import device_ai_environment, private_ipv4, validate_device_settings


def device_addresses(host, clients):
    if (
        not private_ipv4(host)
        or not 1 <= len(clients) <= 8
        or len(set(clients)) != len(clients)
        or host in clients
        or not all(private_ipv4(c) for c in clients)
    ):
        raise ValueError(
            "Use distinct exact PRIVATE IPv4 host/clients; public campus IPs are refused"
        )


def protect_directory(path: Path):
    path.mkdir(mode=0o700, parents=True, exist_ok=False)
    if sys.platform == "win32":
        sid = subprocess.check_output(
            [
                "powershell.exe",
                "-NoProfile",
                "-Command",
                "[Security.Principal.WindowsIdentity]::GetCurrent().User.Value",
            ],
            text=True,
        ).strip()
        if not sid.startswith("S-1-") or not all(c.isdigit() or c in "S-" for c in sid):
            raise ValueError("Cannot identify current Windows user for private key ACL")
        subprocess.run(
            [
                "icacls.exe",
                str(path),
                "/inheritance:r",
                "/grant:r",
                f"*{sid}:(OI)(CI)F",
                "*S-1-5-18:(OI)(CI)F",
            ],
            check=True,
            capture_output=True,
        )


def issue_tls(path: Path, host: str):
    if not private_ipv4(host):
        raise ValueError("TLS host must be an exact private IPv4 address")
    if any(path.iterdir()):
        raise ValueError("Refusing to overwrite existing TLS material")
    now = datetime.now(UTC)
    # The CA private key is used only in memory and is NEVER persisted or exported.
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    server_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, "NCCU Grouping Private Device CA")]
    )
    ca = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=30))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(False, False, False, False, False, True, True, False, False),
            critical=True,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False
        )
        .sign(ca_key, hashes.SHA256())
    )
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host)]))
        .issuer_name(ca_name)
        .public_key(server_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=7))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address(host))]),
            critical=False,
        )
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(
            x509.KeyUsage(True, False, True, False, False, False, False, False, False),
            critical=True,
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False
        )
        .sign(ca_key, hashes.SHA256())
    )
    files = {
        "ca.pem": ca.public_bytes(serialization.Encoding.PEM),
        "ca.cer": ca.public_bytes(serialization.Encoding.DER),
        "server.pem": cert.public_bytes(serialization.Encoding.PEM),
        "server-key.pem": server_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
    }
    for name, value in files.items():
        with (path / name).open("xb") as target:
            target.write(value)
        (path / name).chmod(0o600)


def check_tls(host: str, cert_path: Path, key_path: Path, ca_path: Path):
    if not all(p.is_file() for p in (cert_path, key_path, ca_path)):
        raise ValueError("TLS certificate, key and public CA files are required")
    cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
    ca = x509.load_pem_x509_certificate(ca_path.read_bytes())
    now = datetime.now(UTC)
    for value in (cert, ca):
        if not value.not_valid_before_utc <= now < value.not_valid_after_utc - timedelta(hours=1):
            raise ValueError(
                "TLS certificate is expired, not valid yet, or expires within one hour"
            )
    if (
        cert.extensions.get_extension_for_class(x509.BasicConstraints).value.ca
        or not ca.extensions.get_extension_for_class(x509.BasicConstraints).value.ca
    ):
        raise ValueError("TLS leaf/CA roles are invalid")
    names = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    if ipaddress.ip_address(host) not in names.get_values_for_type(x509.IPAddress):
        raise ValueError(
            "Windows host IP is absent from TLS certificate SAN; regenerate for new IP"
        )
    if (
        ExtendedKeyUsageOID.SERVER_AUTH
        not in cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
    ):
        raise ValueError("TLS leaf is not permitted for server authentication")
    cert.verify_directly_issued_by(ca)
    ca.verify_directly_issued_by(ca)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(cert_path, key_path)
    return {
        "host": host,
        "ca_sha256": ca.fingerprint(hashes.SHA256()).hex(),
        "leaf_sha256": cert.fingerprint(hashes.SHA256()).hex(),
        "leaf_expires": cert.not_valid_after_utc.isoformat(),
    }


def device_environment(root: Path):
    def serialize(value):
        if hasattr(value, "get_secret_value"):
            return value.get_secret_value()
        return json.dumps(value) if isinstance(value, (bool, list)) else str(value)

    # Explicit defaults for EVERY field prevent shell/.env keys or old trial settings
    # from enabling another adapter, cloud listener, paid AI or admin credentials.
    values = {
        name.upper(): serialize(field.get_default(call_default_factory=True))
        for name, field in Settings.model_fields.items()
    }
    profile = dotenv_values(root / ".local/device-test.env")
    host = profile.get("DEVICE_HOST", "")
    clients = [c.strip() for c in profile.get("DEVICE_CLIENTS", "").split(",") if c.strip()]
    device_addresses(host, clients)
    for file, names in (
        (
            "supabase-dev.env",
            ("SUPABASE_URL", "SUPABASE_PUBLISHABLE_KEY", "CORE_GOOGLE_IOS_ENABLED"),
        ),
        ("media-signing.env", ("CORE_MEDIA_ENABLED", "CORE_MEDIA_SIGNING_KEY")),
        ("moderation.env", ("CORE_MODERATION_ENABLED",)),
    ):
        config = dotenv_values(root / ".local" / file)
        values.update({name: config[name] for name in names if config.get(name) is not None})
    confirmed = profile.get("DEVICE_GOOGLE_IOS_CONFIRMED", values["CORE_GOOGLE_IOS_ENABLED"])
    if confirmed not in ("true", "false"):
        raise ValueError("DEVICE_GOOGLE_IOS_CONFIRMED must be true or false")
    values.update(
        ENVIRONMENT="local",
        CORE_DEVICE_TEST_ENABLED="true",
        CORE_DEVICE_TEST_HOST=host,
        CORE_DEVICE_TEST_CLIENTS=json.dumps(clients),
        CORE_SUPABASE_ENABLED="true",
        CORE_GOOGLE_IOS_ENABLED=confirmed,
    )
    values.update(device_ai_environment(profile, dotenv_values(root / ".local/local-ai.env")))
    return profile, values


def load_device(root: Path, *, apply_environment=False):
    profile, env = device_environment(root)
    defaults = Settings.model_fields
    args = {
        k.lower(): json.loads(v) if isinstance(defaults[k.lower()].default, list) else v
        for k, v in env.items()
    }
    settings = Settings(_env_file=None, **args)
    validate_device_settings(settings)
    from app.core_cloud.store import SupabaseCoreStore

    SupabaseCoreStore(settings)  # Local format/project checks only, no HTTP request.
    paths = [
        Path(profile.get(name, ""))
        for name in ("DEVICE_TLS_CERT", "DEVICE_TLS_KEY", "DEVICE_TLS_CA")
    ]
    paths = [p if p.is_absolute() else root / p for p in paths]
    report = check_tls(settings.core_device_test_host, *paths)
    if apply_environment:
        os.environ.update(env)
        from app.core.config import get_settings

        get_settings.cache_clear()
    return settings, paths, report
