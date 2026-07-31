#!/usr/bin/env python3
"""Ensure go-licenses CSV includes first-party sdk/go metadata from LICENSE."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from cyt.safe_path import require_under

REPOSITORY = "https://github.com/qdrddr/clear-your-tools"


def parse_go_mod(path: Path) -> str:
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("module "):
            return stripped.split()[1].strip()
    raise SystemExit(f"could not parse module path from {path}")


def parse_version(go_dir: Path) -> str:
    version_file = go_dir / "moduleversion" / "version.go"
    if not version_file.is_file():
        return "unknown"
    match = re.search(r'Version\s*=\s*"([^"]+)"', version_file.read_text(encoding="utf-8"))
    return match.group(1) if match else "unknown"


def resolve_license_file(go_dir: Path) -> Path:
    for name in ("LICENSE", "LICENSE.md", "LICENSE.txt", "COPYING"):
        candidate = go_dir / name
        if candidate.is_file():
            return candidate
    raise SystemExit(f"missing LICENSE under {go_dir}")


def identify_license(license_file: Path) -> str:
    text = license_file.read_text(encoding="utf-8")
    if "Apache License" in text and "Version 2.0" in text:
        return "Apache-2.0"
    return "UNKNOWN"


def license_url(*, go_dir: Path, repo_root: Path) -> str:
    rel = go_dir.relative_to(repo_root).as_posix()
    base = REPOSITORY.rstrip("/")
    if base.endswith(".git"):
        base = base[:-4]
    return f"{base}/blob/HEAD/{rel}/LICENSE"


def parse_csv_rows(raw_csv: Path) -> list[tuple[str, str, str]]:
    if not raw_csv.is_file() or raw_csv.stat().st_size == 0:
        return []
    rows: list[tuple[str, str, str]] = []
    for line in raw_csv.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        parts = stripped.split(",", 2)
        if len(parts) != 3:
            continue
        rows.append((parts[0], parts[1], parts[2]))
    return rows


def write_csv_rows(path: Path, rows: list[tuple[str, str, str]]) -> None:
    lines = [",".join(row) for row in rows]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def first_party_row(
    *,
    module_path: str,
    go_dir: Path,
    repo_root: Path,
    license_file: Path,
) -> tuple[str, str, str]:
    return (
        module_path,
        license_url(go_dir=go_dir, repo_root=repo_root),
        identify_license(license_file),
    )


def enrich_csv(
    *,
    raw_csv: Path,
    output_csv: Path,
    go_dir: Path,
    repo_root: Path,
) -> dict:
    module_path = parse_go_mod(go_dir / "go.mod")
    license_file = resolve_license_file(go_dir)
    version = parse_version(go_dir)
    canonical = first_party_row(
        module_path=module_path,
        go_dir=go_dir,
        repo_root=repo_root,
        license_file=license_file,
    )

    third_party: list[tuple[str, str, str]] = []
    for package, url, license_id in parse_csv_rows(raw_csv):
        if package == module_path or package.startswith(f"{module_path}/"):
            continue
        third_party.append((package, url, license_id))

    merged = [canonical, *third_party]
    write_csv_rows(output_csv, merged)

    first_party = {
        "Module": module_path,
        "Version": version,
        "License": canonical[2],
        "URL": canonical[1],
        "LicenseFile": str(license_file.resolve()),
        "Notes": (
            "First-party Go SDK module. Subpackages inherit this LICENSE at module root."
        ),
    }
    return first_party


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print(
            "usage: enrich-go-licenses.py GO_MODULE_DIR REPO_ROOT OUTPUT_DIR",
            file=sys.stderr,
        )
        return 2

    repo_root = Path(argv[2]).resolve()
    if not (repo_root / "pyproject.toml").is_file():
        raise SystemExit(f"repo root not found: {repo_root}")

    go_dir = require_under(Path(argv[1]).resolve(), repo_root, label="go module dir")
    output_dir = require_under(Path(argv[3]).resolve(), repo_root, label="output dir")
    output_dir.mkdir(parents=True, exist_ok=True)

    raw_csv = output_dir / "go-sdk.raw.csv"
    output_csv = output_dir / "go-sdk.csv"
    first_party = enrich_csv(
        raw_csv=raw_csv,
        output_csv=output_csv,
        go_dir=go_dir,
        repo_root=repo_root,
    )

    json_path = output_dir / "go-sdk-first-party.json"
    json_path.write_text(json.dumps([first_party], indent=2) + "\n", encoding="utf-8")
    print(f"{first_party['Module']} {first_party['Version']} {first_party['License']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
