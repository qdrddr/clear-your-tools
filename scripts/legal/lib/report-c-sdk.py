#!/usr/bin/env python3
"""Write first-party license reports for C SDK directories (sdk/c)."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore[no-redef]

from cyt.safe_path import join_under, require_repo_root, require_under

TARGETS = (
    {
        "rel_dir": "sdk/c",
        "slug": "sdk-c",
        "cargo_toml": "sdk/rust/cyt-indexer/Cargo.toml",
        "default_name": "cyt-indexer-c",
        "repository": "https://github.com/qdrddr/clear-your-tools",
    },
)


def load_toml(path: Path) -> dict:
    with path.open("rb") as handle:
        return tomllib.load(handle)


def parse_cmake_project(cmake_file: Path) -> tuple[str, str]:
    text = cmake_file.read_text(encoding="utf-8")
    match = re.search(
        r"project\s*\(\s*(.+?)\s+VERSION\s+([^\s)]+)",
        text,
        re.MULTILINE,
    )
    if not match:
        raise SystemExit(f"could not parse project name/version from {cmake_file}")
    return match.group(1), match.group(2)


def cargo_license(cargo_path: Path) -> str:
    if not cargo_path.is_file():
        return "Apache-2.0"
    package = load_toml(cargo_path).get("package", {})
    if isinstance(package, dict):
        license_value = package.get("license")
        if isinstance(license_value, str) and license_value.strip():
            return license_value.strip()
    return "Apache-2.0"


def resolve_license_file(sdk_dir: Path) -> Path | None:
    for name in ("LICENSE", "LICENSE.md", "LICENSE.txt", "COPYING"):
        candidate = sdk_dir / name
        if candidate.is_file():
            return candidate
    return None


def resolve_url(*, repository: str, sdk_dir: Path, repo_root: Path) -> str:
    try:
        rel = sdk_dir.relative_to(repo_root).as_posix()
    except ValueError:
        rel = sdk_dir.name
    base = repository.rstrip("/")
    if base.endswith(".git"):
        base = base[:-4]
    return f"{base}/tree/main/{rel}"


def rows_to_markdown(rows: list[dict]) -> str:
    headers = [
        "Name",
        "Component",
        "Version",
        "License",
        "URL",
        "LicenseFile",
        "Notes",
    ]

    def cell(row: dict, header: str) -> str:
        return str(row.get(header, "") or "").replace("|", "\\|")

    lines = [
        "| " + " | ".join(headers) + " |",
        "|" + "|".join("---" for _ in headers) + "|",
    ]
    for row in rows:
        lines.append("| " + " | ".join(cell(row, header) for header in headers) + " |")
    return "\n".join(lines) + "\n"


def build_reports(repo_root: Path) -> dict[str, list[dict]]:
    reports: dict[str, list[dict]] = {}
    for target in TARGETS:
        sdk_dir = join_under(repo_root, target["rel_dir"], label="sdk dir")
        cmake_file = sdk_dir / "CMakeLists.txt"
        if not cmake_file.is_file():
            continue

        name, version = parse_cmake_project(cmake_file)
        license_file = resolve_license_file(sdk_dir)
        if license_file is None:
            raise SystemExit(f"missing LICENSE under {sdk_dir}")

        cargo_path = join_under(repo_root, target["cargo_toml"], label="cargo toml")
        license_id = cargo_license(cargo_path)
        repository = target["repository"]
        if cargo_path.is_file():
            package = load_toml(cargo_path).get("package", {})
            if isinstance(package, dict):
                repo = package.get("repository")
                if isinstance(repo, str) and repo.strip():
                    repository = repo.strip()

        reports[target["slug"]] = [
            {
                "Name": name or target["default_name"],
                "Component": "C SDK (FFI)",
                "Version": version,
                "License": license_id,
                "URL": resolve_url(
                    repository=repository,
                    sdk_dir=sdk_dir,
                    repo_root=repo_root,
                ),
                "LicenseFile": str(license_file.resolve()),
                "Notes": (
                    "Transitive native dependencies are audited in the rust step "
                    "(cargo-deny / rust-deny-*.txt)."
                ),
            },
        ]
    return reports


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: report-c-sdk.py REPO_ROOT OUTPUT_DIR", file=sys.stderr)
        return 2

    repo_root = require_repo_root(argv[1])

    output_dir = require_under(Path(argv[2]).resolve(), repo_root, label="output dir")
    output_dir.mkdir(parents=True, exist_ok=True)

    reports = build_reports(repo_root)
    if not reports:
        print("no C SDK targets found", file=sys.stderr)
        return 1

    for slug, rows in reports.items():
        json_path = output_dir / f"c-{slug}.json"
        md_path = output_dir / f"c-{slug}.md"
        json_path.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
        md_path.write_text(rows_to_markdown(rows), encoding="utf-8")
        print(f"{rows[0]['Name']} {rows[0]['Version']} {rows[0]['License']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
