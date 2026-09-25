#!/usr/bin/env bash
# Run Rust prek subgroups in parallel for faster wall-clock time.
#
# Usage: ./scripts/pre-commit-hooks/prek-loop-rust-parallel.sh [--short] [--one-run]
#          [--changed-only] [--from-ref REF] [--to-ref REF] [--fail-fast] [--no-git-add]
#          [--git-add orchestrator|group] [--update-shard-weights] [--skip-rebalance]
#
# Order:
#   1. rust-sync (serial — fmt/sort/C lib/warm-build)
#   2. rust-lint-audit, rust-lint-deny, rust-lint-udeps (parallel — no compiles)
#   3. rust-clippy, rust-header (serial — after warm-build; focused clippy scopes)
#   4. rust-test-unit-shard-*, rust-test-* (parallel; prebuilt test binaries when possible)
#   5. rust-build (serial) + one heal-cargo-lock + one git add (orchestrator bookend)
#
# Git staging: child loops use --git-add none; orchestrator stages once after rust-sync
# (fmt/sort fixes) and once on exit (final bookend). No per-hook git add during parallel tests.
#
# Shard count: PREK_RUST_UNIT_SHARDS (default auto: min(8, max(2, nproc/2))).
# Timings: target/.prek-parallel-logs/rust-unit-binary-timings.jsonl
#
# For full pre-push coverage use: ./scripts/pre-commit-hooks/prek-loop.sh --short --one-run -g rust
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd -P)"
PREK_LOOP="${SCRIPT_DIR}/prek-loop.sh"
LOG_DIR="${ROOT}/target/.prek-parallel-logs"
FAILURES_LOG="${LOG_DIR}/failures.log"
TIMINGS_LOG="${LOG_DIR}/timings.log"
RUST_UNIT_TIMINGS_JSONL="${LOG_DIR}/rust-unit-binary-timings.jsonl"
HEARTBEAT_SECS="${PREK_PARALLEL_HEARTBEAT_SECS:-15}"
PARALLEL_RUN_START=0
PREK_RUST_UNIT_SHARDS_MAX=8
UPDATE_SHARD_WEIGHTS=false
SKIP_REBALANCE=false
TIMINGS_BEFORE_RUN=0

# shellcheck source=scripts/lib/chunk-worktree.sh
source "${SCRIPT_DIR}/../lib/chunk-worktree.sh"
# shellcheck source=scripts/pre-commit-hooks/prek-progress.sh
source "${SCRIPT_DIR}/prek-progress.sh"

FORWARD_ARGS=()
PARALLEL_SHORT=false
PARALLEL_GIT_ADD=orchestrator
PARALLEL_NO_GIT_ADD=false
while (($#)); do
	case "$1" in
	--short)
		PARALLEL_SHORT=true
		FORWARD_ARGS+=("$1")
		shift
		;;
	--no-git-add)
		PARALLEL_NO_GIT_ADD=true
		FORWARD_ARGS+=("$1")
		shift
		;;
	--git-add)
		shift
		if ((${#} == 0)) || [[ ${1:-} == -* ]]; then
			echo "--git-add requires orchestrator or group." >&2
			exit 1
		fi
		case "$1" in
		orchestrator | group) PARALLEL_GIT_ADD="$1" ;;
		*)
			echo "--git-add must be orchestrator or group (got: $1)." >&2
			exit 1
			;;
		esac
		shift
		;;
	--one-run | --changed-only | --fail-fast)
		FORWARD_ARGS+=("$1")
		shift
		;;
	--update-shard-weights)
		UPDATE_SHARD_WEIGHTS=true
		shift
		;;
	--skip-rebalance)
		SKIP_REBALANCE=true
		shift
		;;
	--from-ref | --to-ref)
		FORWARD_ARGS+=("$1" "$2")
		shift 2
		;;
	-h | --help)
		echo "Usage: $0 [--short] [--one-run] [--changed-only] [--from-ref REF] [--to-ref REF] [--fail-fast] [--no-git-add] [--git-add orchestrator|group] [--update-shard-weights] [--skip-rebalance]" >&2
		echo "Runs rust-sync → parallel audit/deny/udeps → clippy/header → parallel tests → rust-build." >&2
		echo "Parallel logs: ${LOG_DIR}/<group>.log" >&2
		echo "Failure summary: ${FAILURES_LOG}" >&2
		echo "Unit timings: ${RUST_UNIT_TIMINGS_JSONL}" >&2
		echo "Shard count: PREK_RUST_UNIT_SHARDS (default auto from CPU count, max ${PREK_RUST_UNIT_SHARDS_MAX})." >&2
		echo "--update-shard-weights refreshes cargo-unit-shard-weights.json before tests." >&2
		echo "See also: ${PREK_LOOP} --help" >&2
		exit 0
		;;
	-*)
		echo "Unknown option: $1" >&2
		exit 1
		;;
	*)
		echo "Unexpected argument: $1" >&2
		exit 1
		;;
	esac
done

_cyt_prek_rust_default_unit_shards() {
	local cpus=4 shards=2
	if command -v nproc >/dev/null 2>&1; then
		cpus="$(nproc)"
	elif command -v sysctl >/dev/null 2>&1; then
		cpus="$(sysctl -n hw.ncpu 2>/dev/null || echo 4)"
	fi
	shards=$((cpus / 2))
	((shards < 2)) && shards=2
	((shards > PREK_RUST_UNIT_SHARDS_MAX)) && shards="${PREK_RUST_UNIT_SHARDS_MAX}"
	printf '%s\n' "${shards}"
}

_cyt_prek_rust_resolve_unit_shards() {
	if [[ -z ${PREK_RUST_UNIT_SHARDS:-} ]]; then
		PREK_RUST_UNIT_SHARDS="$(_cyt_prek_rust_default_unit_shards)"
	fi
	if [[ ! ${PREK_RUST_UNIT_SHARDS} =~ ^[0-9]+$ ]] || ((PREK_RUST_UNIT_SHARDS < 1)); then
		echo "error: PREK_RUST_UNIT_SHARDS must be a positive integer (got: ${PREK_RUST_UNIT_SHARDS})" >&2
		exit 1
	fi
	if ((PREK_RUST_UNIT_SHARDS > PREK_RUST_UNIT_SHARDS_MAX)); then
		echo "error: PREK_RUST_UNIT_SHARDS=${PREK_RUST_UNIT_SHARDS} exceeds max ${PREK_RUST_UNIT_SHARDS_MAX}" >&2
		exit 1
	fi
	export PREK_RUST_UNIT_SHARDS
}

_cyt_prek_rust_build_test_groups() {
	local i
	PARALLEL_GROUPS=()
	for ((i = 0; i < PREK_RUST_UNIT_SHARDS; i++)); do
		PARALLEL_GROUPS+=("rust-test-unit-shard-${i}")
	done
	PARALLEL_GROUPS+=(
		rust-test-integration
		rust-test-cucumber
		rust-test-ffi
		rust-test-coverage
		rust-test-mutation
		rust-test-quality-metrics
		rust-test-qa
	)
}

_cyt_prek_rust_run_parallel_groups() {
	local group
	PARALLEL_GROUP_NAMES=()
	PARALLEL_PIDS=()
	PARALLEL_LOGS=()
	PARALLEL_GROUP_STARTS=()
	PARALLEL_GROUPS=("$@")
	for group in "${PARALLEL_GROUPS[@]}"; do
		_run_group_background "${group}"
	done
	_cyt_prek_rust_wait_jobs
	_cyt_prek_stop_progress_watchers
}

_cyt_prek_rust_cleanup_stale_loop_locks() {
	local lock_root="${ROOT}/target/.prek-loop.lock.d"
	local d
	[[ -d "${lock_root}" ]] || return 0
	for d in "${lock_root}"/*; do
		[[ -d "${d}" ]] || continue
		if _cyt_lock_dir_is_stale "${d}"; then
			rm -rf "${d}"
		fi
	done
}

_cyt_prek_rust_block_conflicting_loops() {
	local lock_root="${ROOT}/target/.prek-loop.lock.d"
	local d name pid
	for name in _all rust py; do
		d="${lock_root}/${name}"
		[[ -d "${d}" ]] || continue
		if _cyt_lock_dir_is_stale "${d}"; then
			rm -rf "${d}"
			continue
		fi
		pid="$(cat "${d}/pid" 2>/dev/null || true)"
		echo "error: ${name} prek loop already running (pid ${pid:-unknown})." >&2
		echo "Stop it before starting parallel Rust subgroups, or wait for it to finish." >&2
		exit 1
	done
}

_cyt_prek_rust_loop_git_add_args() {
	if $PARALLEL_NO_GIT_ADD; then
		printf '%s\n' --no-git-add
		return 0
	fi
	case "${PARALLEL_GIT_ADD}" in
	orchestrator) printf '%s\n' --git-add none ;;
	group) printf '%s\n' --git-add group ;;
	esac
}

_cyt_prek_rust_invoke_loop() {
	local group="$1"
	local -a git_add_args=()
	if ! $PARALLEL_SHORT; then
		_cyt_prek_rust_verbose_env
	fi
	export PREK_RUST_UNIT_SHARDS
	export CYT_DEFER_HEAL_CARGO_LOCK=1
	if [[ ${group} == rust-test-* ]]; then
		export CYT_RUST_PARALLEL_TESTS=1
	fi
	if $PARALLEL_SHORT; then
		export CYT_PREK_QUIET_LOOP=1
	fi
	mapfile -t git_add_args < <(_cyt_prek_rust_loop_git_add_args)
	if command -v stdbuf >/dev/null 2>&1; then
		stdbuf -oL -eL "${PREK_LOOP}" "${FORWARD_ARGS[@]}" "${git_add_args[@]}" -g "${group}"
	else
		"${PREK_LOOP}" "${FORWARD_ARGS[@]}" "${git_add_args[@]}" -g "${group}"
	fi
}

_cyt_prek_rust_stage_bookend() {
	[[ ${PARALLEL_GIT_ADD} == orchestrator ]] || return 0
	if $PARALLEL_NO_GIT_ADD; then
		return 0
	fi
	export PREK_GIT_STAGE_LOCK_PATH="${ROOT}/target/.prek-git-stage.lock.d"
	export PREK_HOOK_LOCK_MAX_WAIT=720000
	_cyt_prek_stage_fixes false || true
}

_cyt_prek_rust_finish_staging() {
	_cyt_prek_rust_stage_bookend
}

_cyt_prek_rust_heal_cargo_lock_once() {
	bash "${ROOT}/scripts/local/dev/heal-cargo-lock.sh"
}

_cyt_prek_rust_init_logs() {
	mkdir -p "${LOG_DIR}"
	: >"${FAILURES_LOG}"
	: >"${TIMINGS_LOG}"
	export PREK_PARALLEL_FAILURES_LOG="${FAILURES_LOG}"
}

_cyt_prek_rust_consolidate_failures_log() {
	_cyt_prek_parallel_consolidate_failures_log "${FAILURES_LOG}" "${TIMINGS_LOG}" "${LOG_DIR}"
}

_cyt_prek_rust_on_exit() {
	local exit_code=$?
	_cyt_prek_stop_progress_watchers
	_cyt_prek_rust_consolidate_failures_log
	if ((exit_code != 0)) && [[ -s ${FAILURES_LOG} ]]; then
		_cyt_prek_parallel_report_failures_log "${FAILURES_LOG}"
	fi
	_cyt_prek_rust_finish_staging
	exit "${exit_code}"
}

_cyt_prek_rust_record_timing() {
	local group="$1"
	local elapsed="$2"
	local status="$3"
	printf '%s %s %ss %s\n' "$(date '+%Y-%m-%dT%H:%M:%S%z')" "${group}" "${elapsed}" "${status}" >>"${TIMINGS_LOG}"
}

_cyt_prek_rust_audit_ratio() {
	uv run python "${ROOT}/scripts/local/tests/cargo-unit-shard.py" \
		--audit --shards "${PREK_RUST_UNIT_SHARDS}" --root "${ROOT}" 2>/dev/null |
		uv run python -c 'import json,sys; print(json.load(sys.stdin)["max_min_ratio"])'
}

_cyt_prek_rust_maybe_update_weights() {
	local force="$1"
	local -a update_args=(--root "${ROOT}" --shards "${PREK_RUST_UNIT_SHARDS}")
	local log_file has_source=false

	if $SKIP_REBALANCE && ! $force; then
		return 0
	fi

	if [[ -s "${RUST_UNIT_TIMINGS_JSONL}" ]]; then
		update_args+=(--from-timings-log "${RUST_UNIT_TIMINGS_JSONL}")
		has_source=true
	fi
	for log_file in "${LOG_DIR}"/rust-test-unit-shard-*.log; do
		[[ -f ${log_file} ]] || continue
		update_args+=(--from-log "${log_file}")
		has_source=true
	done

	if ! $has_source; then
		if $force && ! $PARALLEL_SHORT; then
			echo "No rust unit timings collected yet; skipping shard weight update." >&2
		fi
		return 0
	fi

	uv run python "${ROOT}/scripts/local/tests/cargo-unit-shard-update-weights.py" "${update_args[@]}"
}

_run_group() {
	local group="$1"
	local log="${LOG_DIR}/${group}.log"
	local exit_code=0
	local started finished elapsed
	mkdir -p "${LOG_DIR}"
	started=$(date +%s)
	if $PARALLEL_SHORT; then
		_cyt_prek_rust_invoke_loop "${group}" >"${log}" 2>&1 || exit_code=$?
	else
		echo "==> prek-loop -g ${group} (log: ${log})"
		set +o pipefail
		_cyt_prek_rust_invoke_loop "${group}" 2>&1 | tee "${log}" || exit_code=$?
		set -o pipefail
	fi
	finished=$(date +%s)
	elapsed=$((finished - started))
	if ((exit_code != 0)); then
		_cyt_prek_rust_record_timing "${group}" "${elapsed}" "failed"
		_cyt_prek_parallel_record_log_failures "${group}" "${log}" "${FAILURES_LOG}" $PARALLEL_SHORT
		_cyt_prek_parallel_report_failures_log "${FAILURES_LOG}"
		return "${exit_code}"
	fi
	_cyt_prek_rust_record_timing "${group}" "${elapsed}" "ok"
	return 0
}

_run_group_background() {
	local group="$1"
	local log="${LOG_DIR}/${group}.log"
	local pid
	mkdir -p "${LOG_DIR}"
	: >"${log}"
	if ! $PARALLEL_SHORT; then
		echo "==> prek-loop -g ${group} (log: ${log})"
		{
			echo "[log] parallel verbose rust enabled (CYT_PREK_VERBOSE_RUST=1)"
			echo "[log] unit shards: ${PREK_RUST_UNIT_SHARDS}"
			echo "[log] tail this file: tail -f ${log}"
		} >>"${log}"
	fi
	_cyt_prek_rust_invoke_loop "${group}" >>"${log}" 2>&1 &
	pid=$!
	PARALLEL_GROUP_NAMES+=("${group}")
	PARALLEL_PIDS+=("${pid}")
	PARALLEL_LOGS+=("${log}")
	PARALLEL_GROUP_STARTS+=("$(date +%s)")
	if ! $PARALLEL_SHORT; then
		PREK_PROGRESS_HEARTBEAT_SECS="${HEARTBEAT_SECS}"
		_cyt_prek_start_progress_watcher "${group} loop" "${pid}" "${log}" "watch" "${log}"
	fi
}

_cyt_prek_rust_wait_jobs() {
	local still_running=0 failed=0 running_count=0 total_count=0
	local -a failed_groups=()
	local now last_heartbeat=0
	local i group pid log last_line started finished elapsed

	total_count=${#PARALLEL_PIDS[@]}
	if ! $PARALLEL_SHORT; then
		echo "Parallel test groups started (${total_count} groups, ${PREK_RUST_UNIT_SHARDS} unit shards). Watch: tail -f ${LOG_DIR}/<group>.log"
	fi

	while :; do
		still_running=0
		running_count="$(_cyt_prek_parallel_count_running_pids "${PARALLEL_PIDS[@]}")"
		((running_count > 0)) && still_running=1
		((still_running == 0)) && break

		if $PARALLEL_SHORT; then
			sleep 1
			continue
		fi

		now=$(date +%s)
		if ((now - last_heartbeat >= HEARTBEAT_SECS)); then
			last_heartbeat=${now}
			echo "[parallel] still running (${running_count}/${total_count}):"
			for i in "${!PARALLEL_PIDS[@]}"; do
				pid="${PARALLEL_PIDS[$i]}"
				if kill -0 "${pid}" 2>/dev/null; then
					group="${PARALLEL_GROUP_NAMES[$i]}"
					log="${PARALLEL_LOGS[$i]}"
					last_line="$(_cyt_prek_parallel_status_line_from_log "${log}")"
					echo "  - ${group} (pid ${pid}): ${last_line}"
				fi
			done
		fi
		sleep 1
	done

	for i in "${!PARALLEL_PIDS[@]}"; do
		pid="${PARALLEL_PIDS[$i]}"
		group="${PARALLEL_GROUP_NAMES[$i]}"
		log="${PARALLEL_LOGS[$i]}"
		started="${PARALLEL_GROUP_STARTS[$i]}"
		if wait "${pid}"; then
			finished=$(date +%s)
			elapsed=$((finished - started))
			_cyt_prek_rust_record_timing "${group}" "${elapsed}" "ok"
			if ! $PARALLEL_SHORT; then
				echo "[parallel] finished: ${group} (${elapsed}s)"
			fi
		else
			failed=1
			failed_groups+=("${group}")
			finished=$(date +%s)
			elapsed=$((finished - started))
			_cyt_prek_rust_record_timing "${group}" "${elapsed}" "failed"
			if ! $PARALLEL_SHORT; then
				echo "[parallel] FAILED: ${group} (${elapsed}s, see ${log})" >&2
			fi
		fi
	done

	if ((failed != 0)); then
		for group in "${failed_groups[@]}"; do
			log="${LOG_DIR}/${group}.log"
			_cyt_prek_parallel_record_log_failures "${group}" "${log}" "${FAILURES_LOG}" $PARALLEL_SHORT
			if ! $PARALLEL_SHORT; then
				echo "--- last 40 lines of ${log} ---" >&2
				tail -n 40 "${log}" >&2 || true
			fi
		done
		if ! $PARALLEL_SHORT; then
			echo "Parallel Rust subgroups failed: ${failed_groups[*]}" >&2
		fi
		_cyt_prek_parallel_report_failures_log "${FAILURES_LOG}"
		return 1
	fi
	return 0
}

cd "${ROOT}"
export PREK_GIT_STAGE_LOCK_PATH="${ROOT}/target/.prek-git-stage.lock.d"
export PREK_HOOK_LOCK_MAX_WAIT=720000
export CYT_DEFER_HEAL_CARGO_LOCK=1
trap '_cyt_prek_rust_on_exit' EXIT
_cyt_prek_rust_init_logs
PARALLEL_RUN_START=$(date +%s)
_cyt_prek_rust_resolve_unit_shards
_cyt_prek_rust_cleanup_stale_loop_locks
_cyt_prek_rust_block_conflicting_loops

if [[ -f "${RUST_UNIT_TIMINGS_JSONL}" ]]; then
	TIMINGS_BEFORE_RUN="$(wc -l <"${RUST_UNIT_TIMINGS_JSONL}" | tr -d ' ')"
fi

if ! $PARALLEL_SHORT; then
	echo "Using ${PREK_RUST_UNIT_SHARDS} cargo unit shard(s)."
	audit_ratio="$(_cyt_prek_rust_audit_ratio || echo unknown)"
	echo "Shard balance audit max/min ratio: ${audit_ratio}"
	if [[ ${audit_ratio} != unknown ]] && awk "BEGIN {exit !(${audit_ratio} > 2.5)}"; then
		echo "Hint: shard imbalance detected; run with --update-shard-weights after collecting timings." >&2
	fi
fi

if $UPDATE_SHARD_WEIGHTS; then
	_cyt_prek_rust_maybe_update_weights true
fi

_run_group rust-sync
_cyt_prek_rust_stage_bookend

if ! $PARALLEL_SHORT; then
	echo "Parallel lint: audit, deny, udeps..."
fi
_cyt_prek_rust_run_parallel_groups rust-lint-audit rust-lint-deny rust-lint-udeps

_run_group rust-clippy
_run_group rust-header

if ! $PARALLEL_SHORT; then
	echo "Parallel test groups (${PREK_RUST_UNIT_SHARDS} unit shards)..."
fi
declare -a PARALLEL_GROUP_NAMES=()
declare -a PARALLEL_PIDS=()
declare -a PARALLEL_LOGS=()
declare -a PARALLEL_GROUP_STARTS=()
declare -a PARALLEL_GROUPS=()
_cyt_prek_rust_build_test_groups
_cyt_prek_rust_run_parallel_groups "${PARALLEL_GROUPS[@]}"

if ! $PARALLEL_SHORT; then
	echo "Parallel test phase complete; healing Cargo.lock once before rust-build..."
fi
_cyt_prek_rust_heal_cargo_lock_once

_run_group rust-build

if ! $SKIP_REBALANCE; then
	timings_after=0
	if [[ -f "${RUST_UNIT_TIMINGS_JSONL}" ]]; then
		timings_after="$(wc -l <"${RUST_UNIT_TIMINGS_JSONL}" | tr -d ' ')"
	fi
	if ((timings_after > TIMINGS_BEFORE_RUN)); then
		if ! $PARALLEL_SHORT; then
			echo "Updating cargo unit shard weights from new timings..."
		fi
		_cyt_prek_rust_maybe_update_weights false
	fi
fi

if ! $PARALLEL_SHORT; then
	_run_elapsed=$(($(date +%s) - PARALLEL_RUN_START))
	_cyt_prek_rust_record_timing "_total" "${_run_elapsed}" "ok"
	echo "All parallel Rust subgroups passed (${_run_elapsed}s wall)."
	echo "Logs retained under: ${LOG_DIR}/"
	echo "Per-group timings: ${TIMINGS_LOG}"
	echo "Unit binary timings: ${RUST_UNIT_TIMINGS_JSONL}"
fi
