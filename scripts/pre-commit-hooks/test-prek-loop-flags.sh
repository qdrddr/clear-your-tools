#!/usr/bin/env bash
# Smoke tests for prek-loop.sh flags and py subgroup definitions.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
PREK_LOOP="${SCRIPT_DIR}/prek-loop.sh"
GROUPS_FILE="${SCRIPT_DIR}/prek-hook-groups.yaml"

failures=0

assert_contains() {
	local haystack="$1"
	local needle="$2"
	local label="$3"
	if [[ ${haystack} != *"${needle}"* ]]; then
		echo "FAIL: ${label} — expected to contain: ${needle}" >&2
		failures=$((failures + 1))
	fi
}

assert_eq() {
	local actual="$1"
	local expected="$2"
	local label="$3"
	if [[ ${actual} != "${expected}" ]]; then
		echo "FAIL: ${label} — expected '${expected}', got '${actual}'" >&2
		failures=$((failures + 1))
	fi
}

assert_not_contains() {
	local haystack="$1"
	local needle="$2"
	local label="$3"
	if printf '%s' "${haystack}" | grep -Fq "${needle}"; then
		echo "FAIL: ${label}" >&2
		failures=$((failures + 1))
	fi
}

echo "==> help lists new flags and groups"
help_text="$("${PREK_LOOP}" --help 2>&1 || true)"
assert_contains "${help_text}" "--changed-only" "help mentions --changed-only"
assert_contains "${help_text}" "--fail-fast" "help mentions --fail-fast"
assert_contains "${help_text}" "py-dev" "help mentions py-dev"
assert_contains "${help_text}" "prek-loop-py-parallel.sh" "help mentions parallel script"

echo "==> py-dev group hook count"
py_dev_hooks="$(
	cd "${ROOT}" && uv run python - py-dev "${GROUPS_FILE}" <<'PY'
import sys
import yaml

group, path = sys.argv[1], sys.argv[2]
with open(path, encoding="utf-8") as fh:
    data = yaml.safe_load(fh) or {}
hooks = [h for h in (data.get("groups") or {}).get(group, []) if h]
print(len(hooks))
PY
)"
assert_eq "${py_dev_hooks}" "16" "py-dev has 16 hooks"

echo "==> py group excludes local-dev smoke hooks and matches subgroup union"
py_audit="$(
	cd "${ROOT}" && uv run python - "${GROUPS_FILE}" <<'PY'
import sys
import yaml

path = sys.argv[1]
with open(path, encoding="utf-8") as fh:
    data = yaml.safe_load(fh) or {}
groups = data.get("groups") or {}
subgroups = ["py-sync", "py-lint", "py-test-core", "py-test-heavy", "py-test-sdk", "py-build"]
union = []
seen = set()
for name in subgroups:
    for hook in groups.get(name, []):
        if hook not in seen:
            union.append(hook)
            seen.add(hook)
py = groups.get("py", [])
missing = [h for h in union if h not in py]
extra = [h for h in py if h not in union]
print(f"count={len(py)}")
print(f"union={len(union)}")
print(f"missing={','.join(missing)}")
print(f"extra={','.join(extra)}")
print(f"ruff_before_pytest={py.index('ruff') < py.index('pytest-unit')}")
print(f"has_verify_app={'verify-app-python' in py}")
print(f"has_local_dev={any(h.startswith('local-dev') for h in py)}")
PY
)"
assert_contains "${py_audit}" "count=30" "py has 30 hooks"
assert_contains "${py_audit}" "union=30" "subgroup union has 30 hooks"
assert_eq "$(printf '%s\n' "${py_audit}" | awk -F= '/^missing=/{print $2}')" "" "py matches subgroup union (missing)"
assert_eq "$(printf '%s\n' "${py_audit}" | awk -F= '/^extra=/{print $2}')" "" "py matches subgroup union (extra)"
assert_contains "${py_audit}" "ruff_before_pytest=True" "py orders ruff before pytest-unit"
assert_contains "${py_audit}" "has_verify_app=True" "py includes verify-app-python"
assert_contains "${py_audit}" "has_local_dev=False" "py excludes local-dev smoke hooks"

echo "==> py-smoke group retains workflow hooks"
py_smoke_hooks="$(
	cd "${ROOT}" && uv run python - py-smoke "${GROUPS_FILE}" <<'PY'
import sys
import yaml

group, path = sys.argv[1], sys.argv[2]
with open(path, encoding="utf-8") as fh:
    data = yaml.safe_load(fh) or {}
hooks = [h for h in (data.get("groups") or {}).get(group, []) if h]
print(",".join(hooks))
PY
)"
assert_eq "${py_smoke_hooks}" "local-dev-app,local-dev-sdk-python" "py-smoke hooks"

echo "==> changed-only builds prek args without --all-files"
_build_prek_args_for_test() {
	local hook="$1"
	local all_files="$2"
	local from_ref="$3"
	local to_ref="$4"
	local -a cmd=(uv run prek run "${hook}")
	if [[ ${all_files} == true ]]; then
		cmd+=(--all-files)
	elif [[ -n ${from_ref} ]]; then
		cmd+=(--from-ref "${from_ref}" --to-ref "${to_ref}")
	fi
	printf '%s\n' "${cmd[*]}"
}
prek_args="$(_build_prek_args_for_test ruff false abc123 HEAD)"
assert_contains "${prek_args}" "run ruff" "prek cmd includes hook name"
assert_contains "${prek_args}" "--from-ref abc123" "prek cmd includes from-ref"
if [[ ${prek_args} == *"--all-files"* ]]; then
	echo "FAIL: changed-only prek cmd must not include --all-files" >&2
	failures=$((failures + 1))
fi

echo "==> all-files mode still uses --all-files"
prek_args="$(_build_prek_args_for_test ruff true '' HEAD)"
assert_contains "${prek_args}" "--all-files" "all-files mode includes --all-files"

echo "==> prek-loop.sh defines changed-only and fail-fast handlers"
loop_src="$(cat "${PREK_LOOP}")"
assert_contains "${loop_src}" "--changed-only" "prek-loop.sh parses --changed-only"
assert_contains "${loop_src}" "--fail-fast" "prek-loop.sh parses --fail-fast"
assert_contains "${loop_src}" "_cyt_prek_hook_skipped" "prek-loop.sh treats skipped hooks"
assert_contains "${loop_src}" "--git-add" "prek-loop.sh parses --git-add mode"
assert_contains "${loop_src}" "PREK_GIT_ADD_MODE" "prek-loop.sh tracks git-add mode"
assert_contains "${loop_src}" "_cyt_prek_finish_group_staging" "prek-loop.sh stages once per group"
assert_contains "${loop_src}" "_cyt_prek_stage_after_hook" "prek-loop.sh supports per-hook staging"
assert_contains "${loop_src}" "Fail-fast: stopping after" "prek-loop.sh fail-fast exit"
assert_contains "${loop_src}" "Acquiring loop lock" "prek-loop.sh announces lock acquisition"
assert_contains "${loop_src}" "PREK_LOCK_HEARTBEAT_SECS" "prek-loop.sh configures lock heartbeats"
assert_contains "$(cat "${ROOT}/scripts/lib/chunk-worktree.sh")" "heartbeat_secs" "chunk-worktree lock supports heartbeats"

echo "==> parallel orchestrator logs and heartbeats"
parallel_src="$(cat "${SCRIPT_DIR}/prek-loop-py-parallel.sh")"
pytest_cat_src="$(cat "${ROOT}/scripts/local/tests/pytest-category.sh")"
assert_contains "${parallel_src}" ".prek-parallel-logs" "parallel script uses per-group logs"
assert_contains "${parallel_src}" "still running" "parallel script prints heartbeat"
assert_contains "${parallel_src}" "_cyt_prek_parallel_cleanup_stale_loop_locks" "parallel script cleans stale locks"
assert_contains "${parallel_src}" "_cyt_prek_parallel_block_conflicting_loops" "parallel script blocks concurrent py/_all loops"
progress_src="$(cat "${SCRIPT_DIR}/prek-progress.sh")"
loop_src="$(cat "${PREK_LOOP}")"
assert_contains "${progress_src}" "_cyt_prek_start_progress_watcher" "prek-progress defines progress watcher"
assert_contains "${progress_src}" "_cyt_prek_pytest_direct_cmd" "prek-progress maps pytest hooks to direct commands"
assert_contains "${progress_src}" "_cyt_prek_pytest_progress_from_log" "prek-progress summarizes completed tests"
assert_contains "${loop_src}" "Do not wrap this function in" "prek-loop keeps pytest stdout live"
assert_contains "${pytest_cat_src}" "capture=tee-sys" "pytest-category streams completed test lines"
assert_contains "$(cat "${ROOT}/src/tests/conftest.py")" "[timing" "conftest emits per-test timing in verbose mode"
assert_contains "${loop_src}" "_run_hook_capture_progress" "prek-loop streams pytest with progress heartbeats"
assert_contains "${loop_src}" "_cyt_prek_pytest_hook_summary" "prek-loop prints pytest scope summary"
assert_contains "${parallel_src}" "prek-progress.sh" "parallel script sources shared progress helpers"
assert_contains "${progress_src}" "_cyt_prek_tree_has_changes" "prek-progress skips staging on clean tree"
assert_contains "${progress_src}" "_cyt_prek_cargo_tree_needs_heal" "prek-progress heals cargo lock only when needed"
assert_contains "${parallel_src}" "PARALLEL_GIT_ADD=orchestrator" "parallel script defaults to orchestrator staging"
assert_contains "${parallel_src}" "_cyt_prek_parallel_finish_staging" "parallel script stages once at orchestrator end"
assert_contains "${parallel_src}" "_cyt_prek_parallel_loop_git_add_args" "parallel script forwards git-add mode to children"
assert_contains "${parallel_src}" "PARALLEL_SHORT" "parallel script tracks --short mode"
assert_contains "${parallel_src}" "failures.log" "parallel script defines consolidated failures log"
assert_contains "${parallel_src}" "_cyt_prek_parallel_init_failures_log" "parallel script initializes failures log per run"
assert_contains "${parallel_src}" "_cyt_prek_parallel_record_log_failures" "parallel script records failures to consolidated log"
assert_contains "${progress_src}" "_cyt_prek_parallel_extract_log_failures" "prek-progress extracts compact failure blocks"
assert_contains "${progress_src}" "_cyt_prek_parallel_report_failures_log" "prek-progress reports failures log path"
assert_contains "${parallel_src}" "if ! \$PARALLEL_SHORT; then" "parallel script skips verbose pytest in short mode"
assert_contains "${progress_src}" "_cyt_prek_filter_test_failure_output" "prek-progress filters pytest failures for short mode"
assert_contains "${progress_src}" "_cyt_prek_parallel_emit_log_failures" "prek-progress defines parallel failure emitter"
assert_not_contains "${loop_src}" "may take several minutes (--short hides output until done)" "prek-loop short mode suppresses pytest wait notes"
assert_contains "${pytest_cat_src}" "CYT_PREK_VERBOSE_PYTEST" "pytest-category honors verbose pytest env"
assert_contains "${pytest_cat_src}" "PYTEST_VERBOSE_ARGS" "pytest-category defines verbose args"
assert_contains "${pytest_cat_src}" "CYT_PYTEST_XDIST" "pytest-category supports xdist env"
assert_contains "${pytest_cat_src}" "unit-shard" "pytest-category defines unit-shard category"

echo "==> shard groups and py gate exclude shard hooks"
groups_src="$(cat "${GROUPS_FILE}")"
assert_contains "${groups_src}" "py-test-gherkin:" "yaml defines py-test-gherkin group"
assert_contains "${groups_src}" "py-test-unit-shard-0:" "yaml defines py-test-unit-shard-0 group"
assert_contains "${groups_src}" "py-test-unit-shard-7:" "yaml defines py-test-unit-shard-7 group"
py_hooks="$(
	cd "${ROOT}" && uv run python - "${GROUPS_FILE}" <<'PY'
import sys
import yaml

with open(sys.argv[1], encoding="utf-8") as fh:
    data = yaml.safe_load(fh) or {}
py = data.get("groups", {}).get("py", [])
print("has_unit=", "pytest-unit" in py)
print("has_shard=", any(h.startswith("pytest-unit-shard-") for h in py))
PY
)"
assert_contains "${py_hooks}" "has_unit= True" "py group keeps single pytest-unit hook"
assert_contains "${py_hooks}" "has_shard= False" "py group excludes shard hooks"

echo "==> pytest-unit-shard partitions without overlap"
shard_audit="$(
	cd "${ROOT}" && uv run python - <<'PY'
import subprocess
from pathlib import Path

root = Path(".")
script = root / "scripts/local/tests/pytest-unit-shard.py"
shards = 4
sets = []
for shard in range(shards):
    out = subprocess.check_output(
        ["uv", "run", "python", str(script), "--shard", str(shard), "--shards", str(shards), "--root", str(root)],
        text=True,
    )
    files = [line.strip() for line in out.splitlines() if line.strip()]
    sets.append(set(files))
union = set().union(*sets)
all_files = set(
    str(p)
    for p in sorted((root / "src/tests/unit").rglob("test_*.py"))
    if "gherkin" not in p.parts
)
overlap = sum(len(sets[i] & sets[j]) for i in range(shards) for j in range(i + 1, shards))
print(f"union={len(union)}")
print(f"total={len(all_files)}")
print(f"overlap={overlap}")
print(f"missing={len(all_files - union)}")
PY
)"
assert_contains "${shard_audit}" "overlap=0" "shard partitions do not overlap"
assert_contains "${shard_audit}" "missing=0" "shard partitions cover all unit files"

echo "==> parallel orchestrator supports dynamic shards and xdist"
assert_contains "${parallel_src}" "PREK_PYTEST_UNIT_SHARDS" "parallel script resolves shard count"
assert_contains "${parallel_src}" "_cyt_prek_parallel_build_groups" "parallel script builds shard groups dynamically"
assert_contains "${parallel_src}" "py-test-unit-shard-" "parallel script uses py-test-unit-shard groups"
assert_contains "${parallel_src}" "py-test-gherkin" "parallel script includes py-test-gherkin"
assert_contains "${parallel_src}" "--xdist" "parallel script supports --xdist flag"
assert_contains "${progress_src}" "pytest-unit-shard-" "prek-progress maps shard hooks"

if ((failures > 0)); then
	echo "${failures} test(s) failed." >&2
	exit 1
fi

echo "All prek-loop flag smoke tests passed."
