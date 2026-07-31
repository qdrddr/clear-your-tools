#!/usr/bin/env python3
"""Fill first-party pip-licenses rows with project metadata and license file paths."""

from __future__ import annotations

import json
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore[no-redef]

from cyt.safe_path import require_under

FIRST_PARTY = {
    "clear-your-tools",
    "cyt-indexer-sdk",
    "chunk-your-tools",
    "chunk-your-skills",
}


def load_toml(path: Path) -> dict:
    with path.open("rb") as handle:
        return tomllib.load(handle)


def project_name(pyproject: dict) -> str | None:
    project = pyproject.get("project")
    if not isinstance(project, dict):
        return None
    name = project.get("name")
    return name if isinstance(name, str) and name else None


def project_license(pyproject: dict) -> str | None:
    project = pyproject.get("project")
    if not isinstance(project, dict):
        return None
    license_value = project.get("license")
    if isinstance(license_value, str) and license_value.strip():
        return license_value.strip()
    if isinstance(license_value, dict):
        text = license_value.get("text")
        if isinstance(text, str) and text.strip():
            return text.strip()
    return None


def project_urls(pyproject: dict) -> dict[str, str]:
    project = pyproject.get("project")
    if not isinstance(project, dict):
        return {}
    urls = project.get("urls")
    if not isinstance(urls, dict):
        return {}
    return {str(key): str(value) for key, value in urls.items() if value}


def cargo_repository(cargo_path: Path) -> str | None:
    if not cargo_path.is_file():
        return None
    data = load_toml(cargo_path)
    package = data.get("package")
    if not isinstance(package, dict):
        return None
    repository = package.get("repository")
    return repository if isinstance(repository, str) and repository else None


def resolve_url(
    *,
    package: str,
    project_dir: Path,
    repo_root: Path,
    urls: dict[str, str],
    repository: str | None,
) -> str:
    for key in ("PyPI", "pypi"):
        value = urls.get(key)
        if value:
            return value

    for key in ("Repository", "Homepage", "Documentation", "Issues"):
        value = urls.get(key)
        if value:
            return value

    if package:
        return f"https://pypi.org/project/{package}/"

    if repository:
        try:
            rel = project_dir.relative_to(repo_root).as_posix()
        except ValueError:
            rel = project_dir.name
        base = repository.rstrip("/")
        if base.endswith(".git"):
            base = base[:-4]
        return f"{base}/tree/main/{rel}"

    return project_dir.resolve().as_uri()


def resolve_license_file(project_dir: Path) -> Path | None:
    for name in ("LICENSE", "LICENSE.md", "LICENSE.txt", "COPYING"):
        candidate = project_dir / name
        if candidate.is_file():
            return candidate
    return None


def license_text_for_report(license_file: Path | None, *, markdown: bool) -> str:
    if license_file is None:
        return "UNKNOWN"
    if markdown:
        return f"(see {license_file})"
    return license_file.read_text(encoding="utf-8")


def enrich_rows(rows: list[dict], project_dir: Path, repo_root: Path) -> list[dict]:
    pyproject_path = project_dir / "pyproject.toml"
    pyproject = load_toml(pyproject_path) if pyproject_path.is_file() else {}
    expected_name = project_name(pyproject)
    declared_license = project_license(pyproject)
    urls = project_urls(pyproject)

    cargo_candidates = [
        project_dir / "Cargo.toml",
        repo_root / "Cargo.toml",
        repo_root / "sdk" / "rust" / "cyt-indexer" / "Cargo.toml",
    ]
    repository = None
    for cargo_path in cargo_candidates:
        repository = cargo_repository(cargo_path)
        if repository:
            break

    license_file = resolve_license_file(project_dir)

    enriched: list[dict] = []
    for row in rows:
        row = dict(row)
        name = str(row.get("Name", ""))
        is_first_party = name in FIRST_PARTY or (
            expected_name is not None and name == expected_name
        )
        if not is_first_party:
            enriched.append(row)
            continue

        if declared_license and str(row.get("License", "")).strip().upper() in (
            "",
            "UNKNOWN",
        ):
            row["License"] = declared_license

        row["URL"] = resolve_url(
            package=name,
            project_dir=project_dir,
            repo_root=repo_root,
            urls=urls,
            repository=repository,
        )

        if license_file:
            row["LicenseFile"] = str(license_file.resolve())
        elif str(row.get("LicenseFile", "")).strip() in ("", "UNKNOWN"):
            row["LicenseFile"] = project_dir.resolve().as_uri()

        enriched.append(row)
    return enriched


def rows_to_markdown(rows: list[dict]) -> str:
    headers = ["Name", "Version", "License", "URL", "LicenseFile", "LicenseText"]
    license_file_by_name = {
        str(row.get("Name", "")): str(row.get("LicenseFile", "")) for row in rows
    }

    def cell(row: dict, header: str) -> str:
        value = str(row.get(header, "") or "")
        if header == "LicenseText":
            license_file = license_file_by_name.get(str(row.get("Name", "")), "")
            if license_file and license_file not in ("", "UNKNOWN"):
                return f"(see {license_file})"
            if value in ("", "UNKNOWN"):
                return "UNKNOWN"
            if len(value) > 120:
                return f"{value[:117]}..."
        return value.replace("|", "\\|")

    lines = [
        "| " + " | ".join(headers) + " |",
        "|" + "|".join("---" for _ in headers) + "|",
    ]
    for row in rows:
        lines.append("| " + " | ".join(cell(row, header) for header in headers) + " |")
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    if len(argv) not in (4, 5):
        print(
            "usage: enrich-python-licenses.py PROJECT_DIR REPO_ROOT JSON_PATH [--json-only]",
            file=sys.stderr,
        )
        return 2

    repo_root = Path(argv[2]).resolve()
    if not (repo_root / "pyproject.toml").is_file():
        raise SystemExit(f"repo root not found: {repo_root}")

    project_dir = require_under(Path(argv[1]).resolve(), repo_root, label="project dir")
    json_path = require_under(Path(argv[3]).resolve(), repo_root, label="json path")
    json_only = len(argv) == 5 and argv[4] == "--json-only"

    rows = json.loads(json_path.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        print("expected pip-licenses JSON array", file=sys.stderr)
        return 1

    enriched = enrich_rows(rows, project_dir, repo_root)
    license_file = resolve_license_file(project_dir)

    for row in enriched:
        name = str(row.get("Name", ""))
        is_first_party = name in FIRST_PARTY or name == project_name(
            load_toml(project_dir / "pyproject.toml"),
        )
        if not is_first_party:
            continue
        if str(row.get("LicenseText", "")).strip() in ("", "UNKNOWN"):
            row["LicenseText"] = license_text_for_report(license_file, markdown=False)

    json_path.write_text(json.dumps(enriched, indent=2) + "\n", encoding="utf-8")

    if json_only:
        return 0

    md_path = json_path.with_suffix(".md")
    md_rows = []
    for row in enriched:
        md_row = dict(row)
        name = str(md_row.get("Name", ""))
        if name in FIRST_PARTY or name == project_name(
            load_toml(project_dir / "pyproject.toml"),
        ):
            md_row["LicenseText"] = license_text_for_report(license_file, markdown=True)
        md_rows.append(md_row)
    md_path.write_text(rows_to_markdown(md_rows), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
