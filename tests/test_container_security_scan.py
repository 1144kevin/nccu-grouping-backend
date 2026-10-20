import copy
import hashlib
import io
import json
import tarfile

import pytest

from scripts import scan_container as scan


def test_scanner_cannot_mount_image_with_network_access(tmp_path):
    with pytest.raises(ValueError, match="offline"):
        scan.scanner_flags("bridge", tmp_path, tmp_path / "cache")
    flags = scan.scanner_flags("none", tmp_path, tmp_path / "cache")
    assert "readonly" in " ".join(flags)
    assert "docker.sock" not in " ".join(flags)


def scan_report():
    return {
        "SchemaVersion": 2,
        "Metadata": {"ImageID": "sha256:fixture"},
        "Results": [
            {"Class": "os-pkgs", "Type": "debian", "Vulnerabilities": []},
            {"Type": "python-pkg", "Vulnerabilities": []},
        ],
    }


@pytest.mark.parametrize("fixed", [None, "2.0"])
def test_unfixed_and_fixed_high_findings_both_block_release(fixed):
    report = scan_report()
    report["Results"][0]["Vulnerabilities"] = [
        {"VulnerabilityID": "fixture-id", "Severity": "HIGH", "FixedVersion": fixed}
    ]
    result = scan.summarize(report)
    assert result["release_gate"] == "blocked" and result["counts"]["HIGH"] == 1


def test_missing_coverage_or_secret_cannot_pass():
    report = scan_report()
    missing = copy.deepcopy(report)
    missing["Results"].pop()
    with pytest.raises(ValueError, match="coverage"):
        scan.summarize(missing)
    report["Results"][0]["Secrets"] = [{"Match": "synthetic-secret"}]
    result = scan.summarize(report)
    assert result["release_gate"] == "blocked" and result["secret_count"] == 1
    assert "synthetic-secret" not in json.dumps(result)


def test_archive_config_identity_uses_bytes_without_extracting_paths(tmp_path):
    path = tmp_path / "image.tar"
    config = b'{"architecture":"amd64"}'
    with tarfile.open(path, "w") as archive:
        for name, data in [
            ("manifest.json", b'[{"Config":"config.json"}]'),
            ("config.json", config),
        ]:
            member = tarfile.TarInfo(name)
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
    assert scan.archive_image_id(path) == "sha256:" + hashlib.sha256(config).hexdigest()
