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
assert_contains "${help_text}" "prek-loop-rust-parallel.sh" "help mentions rust parallel script"

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
assert_contains "${loop_src}" "CYT_RUN_PAID_TESTS" "prek-loop.sh unsets paid test env"
assert_contains "$(cat "${ROOT}/pyproject.toml")" "not paid" "pytest default addopts exclude paid tests"
assert_contains "$(cat "${ROOT}/src/tests/conftest.py")" "--run-paid" "conftest defines paid opt-in flag"
assert_contains "$(cat "${ROOT}/src/tests/integration/test_llm_prune_integration.py")" "@pytest.mark.paid" "LLM prune integration marks paid tests"
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
assert_contains "${progress_src}" "_cyt_prek_filter_prek_loop_failure_output" "prek-progress filters prek-loop failure blocks"
assert_contains "${progress_src}" "_cyt_prek_filter_cargo_failure_output" "prek-progress filters cargo/rust failure blocks"
assert_contains "${progress_src}" "_cyt_prek_parallel_consolidate_failures_log" "prek-progress consolidates failures log from timings"
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

echo "==> balanced shard strategy improves wall-clock balance"
balance_audit="$(
	cd "${ROOT}" && uv run python scripts/local/tests/pytest-unit-shard.py --audit --shards 8 --strategy balanced
)"
assert_contains "${balance_audit}" '"strategy": "balanced"' "shard audit reports balanced strategy"
balance_ratio="$(
	cd "${ROOT}" && uv run python - <<'PY'
import json
import subprocess
from pathlib import Path

root = Path(".")
out = subprocess.check_output(
    ["uv", "run", "python", str(root / "scripts/local/tests/pytest-unit-shard.py"), "--audit", "--shards", "8", "--strategy", "balanced"],
    text=True,
)
data = json.loads(out)
print(f"ratio={data['max_min_ratio']:.2f}")
PY
)"
assert_contains "${balance_ratio}" "ratio=" "balanced audit emits max/min ratio"
ratio_val="${balance_ratio#ratio=}"
if awk "BEGIN {exit !(${ratio_val} <= 2.5)}"; then
	:
else
	echo "FAIL: balanced shard max/min ratio too high (${ratio_val})" >&2
	failures=$((failures + 1))
fi

echo "==> parallel orchestrator supports dynamic shards and xdist"
assert_contains "${parallel_src}" "PREK_PYTEST_UNIT_SHARDS" "parallel script resolves shard count"
assert_contains "${parallel_src}" "_cyt_prek_parallel_build_groups" "parallel script builds shard groups dynamically"
assert_contains "${parallel_src}" "py-test-unit-shard-" "parallel script uses py-test-unit-shard groups"
assert_contains "${parallel_src}" "py-test-gherkin" "parallel script includes py-test-gherkin"
assert_contains "${parallel_src}" "py-test-quality-metrics" "parallel script splits heavy quality metrics"
assert_contains "${parallel_src}" "py-test-coverage" "parallel script splits heavy coverage"
assert_contains "${groups_src}" "py-test-quality-metrics:" "yaml defines py-test-quality-metrics group"
assert_contains "${pytest_cat_src}" "PREK_PYTEST_UNIT_SHARDS" "pytest-category caps xdist workers per shard count"
assert_contains "${parallel_src}" "--xdist" "parallel script supports --xdist flag"
assert_contains "${progress_src}" "pytest-unit-shard-" "prek-progress maps shard hooks"

echo "==> rust group uses shard hooks and matches subgroup union"
rust_audit="$(
	cd "${ROOT}" && uv run python - "${GROUPS_FILE}" <<'PY'
import sys
import yaml

path = sys.argv[1]
with open(path, encoding="utf-8") as fh:
    data = yaml.safe_load(fh) or {}
groups = data.get("groups") or {}
subgroups = [
    "rust-sync",
    "rust-lint-audit",
    "rust-lint-deny",
    "rust-lint-udeps",
    "rust-clippy",
    "rust-header",
    "rust-test-unit-shard-0",
    "rust-test-unit-shard-1",
    "rust-test-unit-shard-2",
    "rust-test-unit-shard-3",
    "rust-test-unit-shard-4",
    "rust-test-unit-shard-5",
    "rust-test-unit-shard-6",
    "rust-test-unit-shard-7",
    "rust-test-integration",
    "rust-test-cucumber",
    "rust-test-ffi",
    "rust-test-coverage",
    "rust-test-mutation",
    "rust-test-quality-metrics",
    "rust-test-qa",
    "rust-build",
]
union = []
seen = set()
for name in subgroups:
    for hook in groups.get(name, []):
        if hook not in seen:
            union.append(hook)
            seen.add(hook)
rust = groups.get("rust", [])
missing = [h for h in union if h not in rust]
extra = [h for h in rust if h not in union]
print(f"count={len(rust)}")
print(f"union={len(union)}")
print(f"missing={','.join(missing)}")
print(f"extra={','.join(extra)}")
print(f"has_unit={'cargo-test-unit' in rust}")
print(f"has_shard={any(h.startswith('cargo-test-unit-shard-') for h in rust)}")
print(f"fmt_before_tests={rust.index('cargo-fmt') < rust.index('cargo-test-unit-shard-0')}")
PY
)"
assert_contains "${rust_audit}" "count=31" "rust has 31 hooks"
assert_contains "${rust_audit}" "union=31" "rust subgroup union has 31 hooks"
assert_eq "$(printf '%s\n' "${rust_audit}" | awk -F= '/^missing=/{print $2}')" "" "rust matches subgroup union (missing)"
assert_eq "$(printf '%s\n' "${rust_audit}" | awk -F= '/^extra=/{print $2}')" "" "rust matches subgroup union (extra)"
assert_contains "${rust_audit}" "has_unit=False" "rust group excludes legacy cargo-test-unit"
assert_contains "${rust_audit}" "has_shard=True" "rust group includes unit shard hooks"
assert_contains "${rust_audit}" "fmt_before_tests=True" "rust orders fmt before unit shards"

echo "==> cargo-unit-shard partitions without overlap"
cargo_shard_audit="$(
	cd "${ROOT}" && uv run python - <<'PY'
import subprocess
from pathlib import Path

root = Path(".")
script = root / "scripts/local/tests/cargo-unit-shard.py"
shards = 4
sets = []
for shard in range(shards):
    out = subprocess.check_output(
        ["uv", "run", "python", str(script), "--shard", str(shard), "--shards", str(shards), "--root", str(root)],
        text=True,
    )
    targets = [line.strip() for line in out.splitlines() if line.strip()]
    sets.append(set(targets))
union = set().union(*sets)
all_targets = set(
    line.strip()
    for line in subprocess.check_output(
        ["uv", "run", "python", str(script), "--shard", "0", "--shards", "1", "--root", str(root)],
        text=True,
    ).splitlines()
    if line.strip()
)
overlap = sum(len(sets[i] & sets[j]) for i in range(shards) for j in range(i + 1, shards))
print(f"union={len(union)}")
print(f"total={len(all_targets)}")
print(f"overlap={overlap}")
print(f"missing={len(all_targets - union)}")
PY
)"
assert_contains "${cargo_shard_audit}" "overlap=0" "cargo shard partitions do not overlap"
assert_contains "${cargo_shard_audit}" "missing=0" "cargo shard partitions cover all unit targets"

echo "==> cargo balanced shard audit"
cargo_balance_audit="$(
	cd "${ROOT}" && uv run python scripts/local/tests/cargo-unit-shard.py --audit --shards 8 --strategy balanced
)"
assert_contains "${cargo_balance_audit}" '"strategy": "balanced"' "cargo shard audit reports balanced strategy"

echo "==> rust parallel orchestrator flags and groups"
rust_parallel_src="$(cat "${SCRIPT_DIR}/prek-loop-rust-parallel.sh")"
cargo_cat_src="$(cat "${ROOT}/scripts/local/tests/cargo-test-category.sh")"
assert_contains "${rust_parallel_src}" "PREK_RUST_UNIT_SHARDS" "rust parallel resolves shard count"
assert_contains "${rust_parallel_src}" "_cyt_prek_rust_build_test_groups" "rust parallel builds test groups dynamically"
assert_contains "${rust_parallel_src}" "rust-test-unit-shard-" "rust parallel uses rust-test-unit-shard groups"
assert_contains "${rust_parallel_src}" "rust-test-coverage" "rust parallel includes coverage lane"
assert_contains "${rust_parallel_src}" "rust-lint-audit rust-lint-deny rust-lint-udeps" "rust parallel runs audit/deny/udeps in parallel"
assert_contains "${rust_parallel_src}" "_run_group rust-clippy" "rust parallel runs focused clippy after warm-build"
assert_contains "${groups_src}" "rust-clippy:" "yaml defines rust-clippy group"
assert_contains "$(cat "${ROOT}/scripts/local/dev/cargo-clippy-cyt-indexer.sh")" "_run_core" "clippy script defines core scope"
assert_contains "$(cat "${ROOT}/scripts/local/dev/cargo-clippy-cyt-indexer.sh")" "mapfile -t CLIPPY_LINT_ARGS" "clippy script loads lint args line-by-line"
assert_contains "${rust_parallel_src}" "still running (\${running_count}/\${total_count}):" "rust parallel prints running count"
assert_contains "${rust_parallel_src}" "CYT_DEFER_HEAL_CARGO_LOCK" "rust parallel defers heal during test lanes"
assert_contains "${rust_parallel_src}" "CYT_RUST_PARALLEL_TESTS" "rust parallel enables direct test binary runs"
assert_contains "${cargo_cat_src}" "CYT_RUST_PARALLEL_TESTS" "cargo-test-category supports parallel direct runs"
assert_contains "${cargo_cat_src}" "CYT_DEFER_HEAL_CARGO_LOCK" "cargo-test-category defers heal when requested"
assert_contains "${cargo_cat_src}" '! -name '"'"'*.*'"'"'' "cargo-test-category skips incremental dep artifacts"
assert_contains "${cargo_cat_src}" "CYT_INDEXER_CRATE_DIR" "cargo-test-category runs direct binaries from crate cwd"
assert_contains "$(cat "${ROOT}/scripts/lib/chunk-worktree.sh")" 'CYT_DEFER_HEAL_CARGO_LOCK:-}" != 1' "chunk-worktree defers lock heal when requested"
assert_contains "${progress_src}" "_cyt_prek_parallel_count_running_pids" "prek-progress counts running parallel jobs"
assert_contains "${rust_parallel_src}" "--changed-only" "rust parallel forwards changed-only"
assert_contains "${rust_parallel_src}" "--update-shard-weights" "rust parallel supports weight refresh"
assert_contains "${rust_parallel_src}" "_cyt_prek_rust_block_conflicting_loops" "rust parallel blocks conflicting loops"
assert_contains "${rust_parallel_src}" "rust-unit-binary-timings.jsonl" "rust parallel records unit binary timings"
assert_contains "${rust_parallel_src}" "_cyt_prek_rust_consolidate_failures_log" "rust parallel consolidates failures log on exit"
assert_contains "${rust_parallel_src}" "_cyt_prek_rust_on_exit" "rust parallel exit trap rewrites failures log"
assert_contains "${groups_src}" "rust-sync:" "yaml defines rust-sync group"
assert_contains "${groups_src}" "rust-build:" "yaml defines rust-build group"
assert_contains "${groups_src}" "rust-test-unit-shard-0:" "yaml defines rust-test-unit-shard-0 group"
assert_contains "${cargo_cat_src}" "unit-shard" "cargo-test-category defines unit-shard mode"
assert_contains "${cargo_cat_src}" "[timing] cargo-test" "cargo-test-category emits timing lines"
assert_contains "${progress_src}" "_cyt_prek_cargo_direct_cmd" "prek-progress maps cargo shard hooks"
assert_contains "${progress_src}" "cargo-test-unit-shard-" "prek-progress summarizes cargo unit shards"

echo "==> cargo-unit-shard-update-weights parses timing lines"
update_weights_src="$(cat "${ROOT}/scripts/local/tests/cargo-unit-shard-update-weights.py")"
assert_contains "${update_weights_src}" "TIMING_LINE" "update-weights defines timing parser"
sample_parse="$(
	cd "${ROOT}" && uv run python - <<'PY'
import importlib.util
from pathlib import Path

path = Path("scripts/local/tests/cargo-unit-shard-update-weights.py")
spec = importlib.util.spec_from_file_location("cargo_update", path)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
parsed = mod.parse_timing_lines(
    "[timing] cargo-test unit_paths 1.23s\n[timing] cargo-test unit_analyzer 4.56s\n"
)
print(f"count={len(parsed)}")
print(f"paths={parsed.get('unit_paths')}")
PY
)"
assert_contains "${sample_parse}" "count=2" "update-weights parses sample timing lines"
assert_contains "${sample_parse}" "paths=1.23" "update-weights extracts target seconds"

if ((failures > 0)); then
	echo "${failures} test(s) failed." >&2
	exit 1
fi

echo "All prek-loop flag smoke tests passed."
