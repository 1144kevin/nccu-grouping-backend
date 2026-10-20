"""Opt-in local image scan: public DB download, then networkless archive analysis.

No daemon socket mount, paid scanner, upload, deployment or Supabase connection.
Findings are retained including unfixed issues; scan success is not release approval.
"""

import argparse
import hashlib
import json
import tarfile
from collections import Counter
from datetime import UTC, datetime
from uuid import uuid4

from scripts.rehearse_container import ROOT
from scripts.rehearse_postgres_restore import command, local_docker

SCANNER = "aquasec/trivy@sha256:2416d1f74045b57274df1a340eee6b510cacd73c66a6ccb8da092b117eb7d222"


def archive_image_id(path):
    # Docker's containerd image-store Id may be an OCI index digest, whereas Trivy
    # reports the image CONFIG digest. Compare the actual exported config bytes.
    with tarfile.open(path) as archive:
        manifests = json.load(archive.extractfile("manifest.json"))
        if len(manifests) != 1:
            raise ValueError("Only one exported image is supported")
        member = archive.getmember(manifests[0]["Config"])
        if not member.isfile() or member.size > 1024 * 1024:
            raise ValueError("Invalid image configuration in archive")
        return "sha256:" + hashlib.sha256(archive.extractfile(member).read()).hexdigest()


def summarize(report):
    if report.get("SchemaVersion") != 2 or not report.get("Metadata", {}).get("ImageID"):
        raise ValueError("Incomplete image scan report")
    results = report.get("Results", [])
    if not any(r.get("Class") == "os-pkgs" for r in results) or not any(
        r.get("Type") == "python-pkg" for r in results
    ):
        raise ValueError("Both OS and Python package coverage are required")
    findings = [
        {
            k: v.get(k)
            for k in (
                "VulnerabilityID",
                "PkgName",
                "InstalledVersion",
                "FixedVersion",
                "Severity",
                "Status",
                "PrimaryURL",
            )
        }
        | {"type": r.get("Type")}
        for r in results
        for v in r.get("Vulnerabilities", [])
    ]
    secrets = sum(len(r.get("Secrets", [])) for r in results)
    return {
        "counts": dict(Counter(v["Severity"] for v in findings)),
        "unique_ids": len({v["VulnerabilityID"] for v in findings}),
        "fix_available": sum(bool(v["FixedVersion"]) for v in findings),
        "secret_count": secrets,
        "findings": findings,
        "release_gate": "blocked"
        if secrets or any(v["Severity"] in ("HIGH", "CRITICAL") for v in findings)
        else "passed",
    }


def scanner_flags(network, directory, cache):
    flags = [
        "run",
        "--rm",
        "--network",
        network,
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--memory",
        "1g",
        "--cpus",
        "2",
        "--pids-limit",
        "128",
        "--tmpfs",
        "/tmp:rw,nosuid,size=512m",
        "--mount",
        f"type=bind,src={cache},dst=/cache",
    ]
    if directory:
        if network != "none":
            raise ValueError("An image archive may only be mounted for an offline scan")
        flags += ["--mount", f"type=bind,src={directory},dst=/scan,readonly"]
    return flags


def run():
    docker = local_docker()
    root = ROOT / ".local" / "security-scan"
    cache = root / "cache"
    directory = root / uuid4().hex[:12]
    cache.mkdir(parents=True, exist_ok=True)
    directory.mkdir()
    image = "nccu-security-rehearsal:" + directory.name
    built = False
    try:
        command(docker + ["pull", SCANNER], timeout=300)
        command(
            docker
            + [
                "buildx",
                "build",
                "--builder",
                "default",
                "--pull",
                "--no-cache-filter",
                "runtime",
                "--load",
                "--tag",
                image,
                ".",
            ],
            timeout=900,
        )
        built = True
        image_id = json.loads(command(docker + ["image", "inspect", image]))[0]["Id"]
        archive = directory / "backend.tar"
        command(docker + ["image", "save", "--output", str(archive), image], timeout=180)
        config_id = archive_image_id(archive)
        common = [
            SCANNER,
            "image",
            "--cache-dir",
            "/cache",
            "--disable-telemetry",
            "--skip-version-check",
            "--no-progress",
        ]
        # No image/data mount during the only scanner phase that has network access.
        command(
            docker + scanner_flags("bridge", None, cache) + common + ["--download-db-only"],
            timeout=600,
        )
        metadata = json.loads((cache / "db" / "metadata.json").read_text(encoding="utf8"))
        updated = datetime.fromisoformat(metadata["UpdatedAt"].replace("Z", "+00:00"))
        age = (datetime.now(UTC) - updated).total_seconds()
        if not -3600 <= age <= 48 * 3600:
            raise ValueError("Vulnerability DB timestamp outside 48-hour freshness window")
        raw = command(
            docker
            + scanner_flags("none", directory, cache)
            + common
            + [
                "--input",
                "/scan/backend.tar",
                "--skip-db-update",
                "--skip-java-db-update",
                "--skip-check-update",
                "--offline-scan",
                "--scanners",
                "vuln,secret",
                "--ignorefile",
                "/dev/null",
                "--format",
                "json",
            ],
            timeout=300,
        )
        (directory / "trivy.json").write_text(raw, encoding="utf8")
        report = json.loads(raw)
        if report["Metadata"]["ImageID"] != config_id:
            raise ValueError("Scanner report does not identify the image just built")
        summary = summarize(report)
        summary.update(
            status="scan_completed",
            scanner=SCANNER,
            image_id=image_id,
            config_id=config_id,
            checked_at=datetime.now(UTC).isoformat(),
            db=metadata,
            raw_report_sha256=hashlib.sha256(raw.encode()).hexdigest(),
            archive_sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
            upload=False,
            deployed=False,
            scan_network="none",
            secret_scan_performed=True,
        )
        (directory / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf8")
        archive.unlink()
        return summary
    finally:
        if built:
            command(docker + ["image", "rm", image])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run", action="store_true", help="Build locally, download DB, scan offline"
    )
    if parser.parse_args().run:
        result = run()
        print(json.dumps({k: v for k, v in result.items() if k != "findings"}, indent=2))
        raise SystemExit(1 if result["release_gate"] == "blocked" else 0)
    print("Check-only: --run builds and scans locally; findings can block the release gate.")
