#!/usr/bin/env bash
# Audit C SDK license metadata (sdk/c has no package-manager dependencies).
#
# The C SDK is a thin header + CMake wrapper over the Rust FFI library.
# Runtime dependency licenses are covered by scripts/legal/audit-rust.sh.
#
# Usage:
#   ./scripts/legal/audit-c.sh [--output-dir DIR] [--check] [--report]
#
# Targets:
#   - sdk/c/
#   - chunk-your-tools/sdk/c/ (when present)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/legal/lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"

OUTPUT_DIR=""
DO_CHECK=1
DO_REPORT=1

while [[ $# -gt 0 ]]; do
	case "$1" in
	--output-dir)
		[[ $# -ge 2 ]] || legal_die "--output-dir requires a path"
		OUTPUT_DIR="$2"
		shift 2
		;;
	--output-dir=*)
		OUTPUT_DIR="${1#*=}"
		shift
		;;
	--check)
		DO_CHECK=1
		shift
		;;
	--no-check)
		DO_CHECK=0
		shift
		;;
	--report)
		DO_REPORT=1
		shift
		;;
	--no-report)
		DO_REPORT=0
		shift
		;;
	-h | --help)
		cat <<'EOF'
Usage: audit-c.sh [--output-dir DIR] [--check] [--no-check] [--report] [--no-report]

Writes first-party license metadata for sdk/c (FFI headers and release artifacts).
Native dependency licenses are audited separately via audit-rust.sh.
EOF
		exit 0
		;;
	*)
		legal_die "unknown arg: $1 (try --help)"
		;;
	esac
done

legal_require_repo_root

if [[ -n "${LEGAL_OUTPUT_DIR:-}" ]]; then
	:
elif [[ -n "${OUTPUT_DIR}" ]]; then
	legal_init_output_dir "${OUTPUT_DIR}"
else
	legal_init_output_dir ""
fi

if [[ "${DO_REPORT}" -eq 1 ]]; then
	legal_info "sdk-c: first-party license report"
	legal_require_cmd python3
	python3 "${SCRIPT_DIR}/lib/report-c-sdk.py" \
		"${LEGAL_REPO_ROOT}" "${LEGAL_OUTPUT_DIR}"
	legal_write_summary_line "sdk-c: report -> c-sdk-c.{json,md} (+ chunk-your-tools when present)"
fi

if [[ "${DO_CHECK}" -eq 1 ]]; then
	legal_info "sdk-c: license policy check"
	legal_require_cmd python3
	python3 - "${LEGAL_REPO_ROOT}" "${LEGAL_ALLOWED_LICENSES}" <<'PY'
import json
import sys
from pathlib import Path

repo_root = Path(sys.argv[1])
allowed = {item.strip() for item in sys.argv[2].split(";") if item.strip()}
targets = [
    repo_root / "sdk" / "c",
    repo_root / "chunk-your-tools" / "sdk" / "c",
]

checked = 0
for sdk_dir in targets:
    if not (sdk_dir / "CMakeLists.txt").is_file():
        continue
    license_file = None
    for name in ("LICENSE", "LICENSE.md", "LICENSE.txt", "COPYING"):
        candidate = sdk_dir / name
        if candidate.is_file():
            license_file = candidate
            break
    if license_file is None:
        raise SystemExit(f"missing LICENSE under {sdk_dir}")
    text = license_file.read_text(encoding="utf-8")
    if "Apache License" in text and "Version 2.0" in text:
        license_id = "Apache-2.0"
    else:
        raise SystemExit(f"could not identify license in {license_file}")
    if license_id not in allowed:
        raise SystemExit(f"{sdk_dir}: license {license_id!r} not in allow-list")
    checked += 1
    print(f"{sdk_dir.name}: {license_id} ({license_file})")

if checked == 0:
    raise SystemExit("no C SDK targets found")
PY
	legal_write_summary_line "sdk-c: LICENSE files present and allowed"
fi
