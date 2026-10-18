"""Check Mac prerequisites or export current committed branches without pushing or secrets."""

import argparse
import hashlib
import json
import platform
import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def export():
    repos = [(ROOT, "main"), (ROOT.parent / "nccu-grouping-app", "kevin_dev")]
    for repo, branch in repos:
        if git(repo, "branch", "--show-current") != branch or git(repo, "status", "--porcelain"):
            raise SystemExit(
                "Commit changes on the expected branches first. No export was created."
            )
    records = []
    destination = ROOT / ".local" / "mac-handoff"
    destination.mkdir(parents=True, exist_ok=True)
    for repo, branch in repos:
        commit = git(repo, "rev-parse", "HEAD")
        target = destination / f"{repo.name}-{commit[:12]}.bundle"
        if not target.exists():
            git(repo, "bundle", "create", str(target), branch)
        git(repo, "bundle", "verify", str(target))
        records.append(
            {
                "repository": repo.name,
                "branch": branch,
                "commit": commit,
                "file": target.name,
                "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            }
        )
    manifest = destination / "manifest.json"
    manifest.write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")
    shutil.copyfile(ROOT / "scripts/import_mac_handoff.py", destination / "import_mac_handoff.py")
    (destination / "START_HERE.txt").write_text(
        "On Mac: python3 import_mac_handoff.py --destination ~/projects --apply\n"
        "Then read ~/projects/nccu-grouping-backend/docs/IPHONE_TESTFLIGHT.md\n"
        "Contains committed Git history only. Keep within your team. No credentials included.\n",
        encoding="utf-8",
    )
    print(f"Committed Git history only: {destination}")
    print(
        "No .local files, untracked files, node_modules or virtual environments copied. "
        "Previously committed history must still be reviewed for secrets before sharing."
    )


def check():
    print(f"Python: {platform.python_version()} (required: 3.12 or 3.13)")
    missing = []
    if not (3, 12) <= tuple(map(int, platform.python_version_tuple()[:2])) < (3, 14):
        missing.append("Python 3.12/3.13")
    for name in ("git", "node", "uv"):
        if not shutil.which(name):
            missing.append(name)
    if shutil.which("node"):
        version = subprocess.run(["node", "--version"], capture_output=True, text=True)
        parsed = re.match(r"v(\d+)\.(\d+)", version.stdout.strip())
        if version.returncode or not parsed or tuple(map(int, parsed.groups())) < (22, 13):
            missing.append("Node 22.13+ (24 LTS recommended)")
    if platform.system() == "Darwin":
        result = (
            subprocess.run(["xcodebuild", "-version"], capture_output=True, text=True)
            if shutil.which("xcodebuild")
            else None
        )
        if not result or result.returncode:
            missing.append("Full Xcode 26.4+ selected and first launch completed")
        else:
            print(result.stdout.strip())
            parsed = re.search(r"Xcode (\d+)\.(\d+)", result.stdout)
            if not parsed or tuple(map(int, parsed.groups())) < (26, 4):
                missing.append("Xcode 26.4+")
    else:
        print("Windows: native compilation/signing must be checked on Mac.")
    print(
        "Missing: "
        + (", ".join(missing) if missing else "none detected; verify Node >=22.13 and Xcode >=26.4")
    )
    print(
        "No installation, credential reads, deployment, login or network configuration performed."
    )
    return bool(missing)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("check", "export"))
    action = parser.parse_args().action
    if action == "export":
        export()
    else:
        raise SystemExit(check())
