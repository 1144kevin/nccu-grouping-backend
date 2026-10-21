"""Export only public certificates and an offline Mac trust probe; no deployment."""

import json
import shutil
import sys
import zipfile
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import serialization

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.device_setup import load_device  # noqa: E402


def export(root=ROOT):
    settings, (cert, _, ca), facts = load_device(root)
    target = root / ".local/apple-tls-diagnostic"
    target.mkdir(parents=True, exist_ok=True)
    for source, name in ((cert, "server.cer"), (ca, "ca.cer")):
        value = x509.load_pem_x509_certificate(source.read_bytes())
        (target / name).write_bytes(value.public_bytes(serialization.Encoding.DER))
    (target / "tls-facts.json").write_text(
        json.dumps({"host": settings.core_device_test_host, **facts}, indent=2) + "\n",
        encoding="utf-8",
    )
    shutil.copyfile(
        ROOT / "scripts/check_apple_device_tls.swift", target / "check_apple_device_tls.swift"
    )
    shutil.copyfile(ROOT / "docs/MAC_EXPO_GO_TLS_DIAGNOSTIC.md", target / "README.md")
    names = ("ca.cer", "server.cer", "tls-facts.json", "check_apple_device_tls.swift", "README.md")
    archive = root / ".local/apple-tls-diagnostic.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        for name in names:
            bundle.write(target / name, "apple-tls-diagnostic/" + name)
    return archive


if __name__ == "__main__":
    print(export())
