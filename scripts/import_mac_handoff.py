"""Standalone handoff importer; stdlib only, supports macOS system Python 3.9+."""

import argparse
import hashlib
import json
import subprocess
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", default="~/projects")
    parser.add_argument(
        "--apply", action="store_true", help="Clone into new directories after checks"
    )
    args = parser.parse_args()
    folder = Path(__file__).resolve().parent
    destination = Path(args.destination).expanduser().resolve()
    records = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    expected = {"nccu-grouping-backend": "main", "nccu-grouping-app": "kevin_dev"}
    if not isinstance(records, list) or len(records) != 2:
        raise SystemExit("Expected exactly two handoff repositories")
    seen = set()
    for record in records:
        name = record["repository"]
        if name in seen or expected.get(name) != record["branch"]:
            raise SystemExit("Unexpected repository or branch")
        seen.add(name)
        filename = record["file"]
        if Path(filename).name != filename or not filename.endswith(".bundle"):
            raise SystemExit("Invalid bundle filename")
        bundle = folder / filename
        if hashlib.sha256(bundle.read_bytes()).hexdigest() != record["sha256"]:
            raise SystemExit("Bundle checksum mismatch")
        if (destination / name).exists():
            raise SystemExit("Destination already exists; refusing to overwrite " + name)
    print("Both bundle checksums verified. Destination: " + str(destination))
    if not args.apply:
        print(
            "Check only. Add --apply to create new checkouts; no remote push or credentials copied."
        )
        return
    destination.mkdir(parents=True, exist_ok=True)
    for record in records:
        target = destination / record["repository"]
        subprocess.run(
            [
                "git",
                "clone",
                "--branch",
                record["branch"],
                str(folder / record["file"]),
                str(target),
            ],
            check=True,
        )
        actual = subprocess.check_output(
            ["git", "-C", str(target), "rev-parse", "HEAD"], text=True
        ).strip()
        if actual != record["commit"]:
            raise SystemExit("Imported commit differs from manifest; stop before running the app")
    print(
        "Checkouts ready. Follow docs/IPHONE_TESTFLIGHT.md in the backend. "
        "No install/build/upload started."
    )


if __name__ == "__main__":
    main()
