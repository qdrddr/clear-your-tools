#!/usr/bin/env bash
# Run Python prek subgroups in parallel for faster wall-clock time.
#
# Usage: ./scripts/pre-commit-hooks/prek-loop-py-parallel.sh [--short] [--one-run]
#          [--changed-only] [--from-ref REF] [--to-ref REF] [--fail-fast] [--no-git-add]
#          [--git-add orchestrator|group] [--xdist]
#
# Order:
#   1. py-sync (serial)
#   2. py-lint, py-test-unit-shard-*, py-test-gherkin, py-test-heavy, py-test-sdk (parallel)
#   3. py-build (serial)
#
# With --short: silent on success; on failure prints only compact failure blocks
# (pytest FAILURES / short test summary, or prek Failed lines). All failures for the
# run are also appended to target/.prek-parallel-logs/failures.log (one file for
# agents). Full per-group logs remain under target/.prek-parallel-logs/<group>.log.
#
# Without --short: verbose pytest (-v, [timing] lines), live tee to terminal, and
# [watch ...] heartbeat lines every HEARTBEAT_SECS while a group runs.
#
# Git staging (skips when the tree is clean):
#   --git-add orchestrator (default) — one git add at end of the full parallel run
#   --git-add group            — each prek-loop group stages once at group end
#   --no-git-add               — disable all staging
#
# Shard count: PREK_PYTEST_UNIT_SHARDS (default auto: min(8, max(2, nproc/2))).
# Optional --xdist enables pytest -n auto inside each shard (heavy CPU/RAM).
#
# For full pre-push coverage use: ./scripts/pre-commit-hooks/prek-loop.sh --short --one-run -g py
# For optional workflow smoke:   ./scripts/pre-commit-hooks/prek-loop.sh --short --one-run -g py-smoke
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd -P)"
PREK_LOOP="${SCRIPT_DIR}/prek-loop.sh"
LOG_DIR="${ROOT}/target/.prek-parallel-logs"
FAILURES_LOG="${LOG_DIR}/failures.log"
TIMINGS_LOG="${LOG_DIR}/timings.log"
HEARTBEAT_SECS="${PREK_PARALLEL_HEARTBEAT_SECS:-15}"
PARALLEL_RUN_START=0
PREK_PYTEST_UNIT_SHARDS_MAX=8

# shellcheck source=scripts/lib/chunk-worktree.sh
source "${SCRIPT_DIR}/../lib/chunk-worktree.sh"
# shellcheck source=scripts/pre-commit-hooks/prek-progress.sh
source "${SCRIPT_DIR}/prek-progress.sh"

FORWARD_ARGS=()
USE_XDIST=false
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
	--one-run | --changed-only | --fail-fast | --runtime)
		FORWARD_ARGS+=("$1")
		shift
		;;
	--xdist)
		USE_XDIST=true
		shift
		;;
	--from-ref | --to-ref)
		FORWARD_ARGS+=("$1" "$2")
		shift 2
		;;
	-h | --help)
		echo "Usage: $0 [--short] [--one-run] [--changed-only] [--from-ref REF] [--to-ref REF] [--fail-fast] [--no-git-add] [--git-add orchestrator|group] [--xdist]" >&2
		echo "Runs py-sync → parallel py-lint/py-test-unit-shard-*/py-test-gherkin/py-test-* → py-build." >&2
		echo "Parallel logs: ${LOG_DIR}/<group>.log" >&2
		echo "Failure summary: ${FAILURES_LOG}" >&2
		echo "Git staging: --git-add orchestrator (default, one add at end) or group (once per subgroup)." >&2
		echo "Shard count: PREK_PYTEST_UNIT_SHARDS (default auto from CPU count, max ${PREK_PYTEST_UNIT_SHARDS_MAX})." >&2
		echo "--short: silent on success; failure-only output for agents." >&2
		echo "Tip: tail -f ${LOG_DIR}/py-test-unit-shard-0.log" >&2
		echo "--xdist adds pytest -n auto inside each shard (high CPU/RAM)." >&2
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

_cyt_prek_default_unit_shards() {
	local cpus=4 shards=2
	if command -v nproc >/dev/null 2>&1; then
		cpus="$(nproc)"
	elif command -v sysctl >/dev/null 2>&1; then
		cpus="$(sysctl -n hw.ncpu 2>/dev/null || echo 4)"
	fi
	shards=$((cpus / 2))
	((shards < 2)) && shards=2
	((shards > PREK_PYTEST_UNIT_SHARDS_MAX)) && shards="${PREK_PYTEST_UNIT_SHARDS_MAX}"
	printf '%s\n' "${shards}"
}

_cyt_prek_parallel_resolve_unit_shards() {
	if [[ -z ${PREK_PYTEST_UNIT_SHARDS:-} ]]; then
		PREK_PYTEST_UNIT_SHARDS="$(_cyt_prek_default_unit_shards)"
	fi
	if [[ ! ${PREK_PYTEST_UNIT_SHARDS} =~ ^[0-9]+$ ]] || ((PREK_PYTEST_UNIT_SHARDS < 1)); then
		echo "error: PREK_PYTEST_UNIT_SHARDS must be a positive integer (got: ${PREK_PYTEST_UNIT_SHARDS})" >&2
		exit 1
	fi
	if ((PREK_PYTEST_UNIT_SHARDS > PREK_PYTEST_UNIT_SHARDS_MAX)); then
		echo "error: PREK_PYTEST_UNIT_SHARDS=${PREK_PYTEST_UNIT_SHARDS} exceeds max ${PREK_PYTEST_UNIT_SHARDS_MAX} (add more hooks in .pre-commit-config.yaml)" >&2
		exit 1
	fi
	export PREK_PYTEST_UNIT_SHARDS
}

_cyt_prek_parallel_build_groups() {
	local i
	PARALLEL_GROUPS=(py-lint)
	for ((i = 0; i < PREK_PYTEST_UNIT_SHARDS; i++)); do
		PARALLEL_GROUPS+=("py-test-unit-shard-${i}")
	done
	PARALLEL_GROUPS+=(
		py-test-gherkin
		py-test-quality-metrics
		py-test-coverage
		py-test-mutation
		py-test-qa
		py-test-sdk
	)
}

_cyt_prek_parallel_cleanup_stale_loop_locks() {
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

_cyt_prek_parallel_block_conflicting_loops() {
	local lock_root="${ROOT}/target/.prek-loop.lock.d"
	local d name pid
	for name in _all py; do
		d="${lock_root}/${name}"
		[[ -d "${d}" ]] || continue
		if _cyt_lock_dir_is_stale "${d}"; then
			rm -rf "${d}"
			continue
		fi
		pid="$(cat "${d}/pid" 2>/dev/null || true)"
		echo "error: ${name} prek loop already running (pid ${pid:-unknown})." >&2
		echo "Stop it before starting parallel Python subgroups, or wait for it to finish." >&2
		exit 1
	done
}

_cyt_prek_parallel_loop_git_add_args() {
	if $PARALLEL_NO_GIT_ADD; then
		printf '%s\n' --no-git-add
		return 0
	fi
	case "${PARALLEL_GIT_ADD}" in
	orchestrator) printf '%s\n' --git-add none ;;
	group) printf '%s\n' --git-add group ;;
	esac
}

_cyt_prek_parallel_invoke_loop() {
	local group="$1"
	local -a git_add_args=()
	if ! $PARALLEL_SHORT; then
		_cyt_prek_pytest_verbose_env
	fi
	export PREK_PYTEST_UNIT_SHARDS
	if $PARALLEL_SHORT; then
		export CYT_PREK_QUIET_LOOP=1
	fi
	if $USE_XDIST; then
		export CYT_PYTEST_XDIST=1
	fi
	mapfile -t git_add_args < <(_cyt_prek_parallel_loop_git_add_args)
	if command -v stdbuf >/dev/null 2>&1; then
		stdbuf -oL -eL "${PREK_LOOP}" "${FORWARD_ARGS[@]}" "${git_add_args[@]}" -g "${group}"
	else
		"${PREK_LOOP}" "${FORWARD_ARGS[@]}" "${git_add_args[@]}" -g "${group}"
	fi
}

_cyt_prek_parallel_finish_staging() {
	[[ ${PARALLEL_GIT_ADD} == orchestrator ]] || return 0
	if $PARALLEL_NO_GIT_ADD; then
		return 0
	fi
	export PREK_GIT_STAGE_LOCK_PATH="${ROOT}/target/.prek-git-stage.lock.d"
	export PREK_HOOK_LOCK_MAX_WAIT=720000
	NO_GIT_ADD=false
	_cyt_prek_stage_fixes false || true
}

_cyt_prek_parallel_start_log_watcher() {
	local group="$1"
	local log="$2"
	local pid="$3"
	PREK_PROGRESS_HEARTBEAT_SECS="${HEARTBEAT_SECS}"
	_cyt_prek_start_progress_watcher "${group} loop" "${pid}" "${log}" "watch" "${log}"
}

_cyt_prek_parallel_stop_watchers() {
	_cyt_prek_stop_progress_watchers
}

_cyt_prek_parallel_init_failures_log() {
	mkdir -p "${LOG_DIR}"
	: >"${FAILURES_LOG}"
	: >"${TIMINGS_LOG}"
	export PREK_PARALLEL_FAILURES_LOG="${FAILURES_LOG}"
}

_cyt_prek_parallel_record_timing() {
	local group="$1"
	local elapsed="$2"
	local status="$3"
	printf '%s %s %ss %s\n' "$(date '+%Y-%m-%dT%H:%M:%S%z')" "${group}" "${elapsed}" "${status}" >>"${TIMINGS_LOG}"
}

_run_group() {
	local group="$1"
	local log="${LOG_DIR}/${group}.log"
	local exit_code=0
	local started finished elapsed
	mkdir -p "${LOG_DIR}"
	started=$(date +%s)
	if $PARALLEL_SHORT; then
		_cyt_prek_parallel_invoke_loop "${group}" >"${log}" 2>&1 || exit_code=$?
		finished=$(date +%s)
		elapsed=$((finished - started))
		if ((exit_code != 0)); then
			_cyt_prek_parallel_record_timing "${group}" "${elapsed}" "failed"
			_cyt_prek_parallel_record_log_failures "${group}" "${log}" "${FAILURES_LOG}" true
			_cyt_prek_parallel_report_failures_log "${FAILURES_LOG}"
			return "${exit_code}"
		fi
		_cyt_prek_parallel_record_timing "${group}" "${elapsed}" "ok"
		return 0
	fi
	echo "==> prek-loop -g ${group} (log: ${log})"
	set +o pipefail
	_cyt_prek_parallel_invoke_loop "${group}" 2>&1 | tee "${log}" || exit_code=$?
	set -o pipefail
	finished=$(date +%s)
	elapsed=$((finished - started))
	if ((exit_code != 0)); then
		_cyt_prek_parallel_record_timing "${group}" "${elapsed}" "failed"
		_cyt_prek_parallel_record_log_failures "${group}" "${log}" "${FAILURES_LOG}" false
		_cyt_prek_parallel_report_failures_log "${FAILURES_LOG}"
	else
		_cyt_prek_parallel_record_timing "${group}" "${elapsed}" "ok"
	fi
	return "${exit_code}"
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
			echo "[log] parallel verbose pytest enabled (CYT_PREK_VERBOSE_PYTEST=1)"
			echo "[log] unit shards: ${PREK_PYTEST_UNIT_SHARDS}"
			if $USE_XDIST; then
				echo "[log] xdist enabled (CYT_PYTEST_XDIST=1, -n auto per shard)"
			fi
			echo "[log] tail this file: tail -f ${log}"
		} >>"${log}"
	fi
	_cyt_prek_parallel_invoke_loop "${group}" >>"${log}" 2>&1 &
	pid=$!
	PARALLEL_GROUP_NAMES+=("${group}")
	PARALLEL_PIDS+=("${pid}")
	PARALLEL_LOGS+=("${log}")
	PARALLEL_GROUP_STARTS+=("$(date +%s)")
	if ! $PARALLEL_SHORT; then
		_cyt_prek_parallel_start_log_watcher "${group}" "${log}" "${pid}"
	fi
}

_cyt_prek_parallel_wait_jobs() {
	local still_running=0 failed=0 running_count=0 total_count=0
	local -a failed_groups=()
	local now last_heartbeat=0
	local i group pid log last_line started finished elapsed

	total_count=${#PARALLEL_PIDS[@]}
	if ! $PARALLEL_SHORT; then
		echo "Parallel groups started (${total_count} groups, ${PREK_PYTEST_UNIT_SHARDS} unit shards). Watch: tail -f ${LOG_DIR}/<group>.log"
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
			_cyt_prek_parallel_record_timing "${group}" "${elapsed}" "ok"
			if ! $PARALLEL_SHORT; then
				echo "[parallel] finished: ${group} (${elapsed}s)"
			fi
		else
			failed=1
			failed_groups+=("${group}")
			finished=$(date +%s)
			elapsed=$((finished - started))
			_cyt_prek_parallel_record_timing "${group}" "${elapsed}" "failed"
			if ! $PARALLEL_SHORT; then
				echo "[parallel] FAILED: ${group} (${elapsed}s, see ${log})" >&2
			fi
		fi
	done

	if ((failed != 0)); then
		for group in "${failed_groups[@]}"; do
			log="${LOG_DIR}/${group}.log"
			if $PARALLEL_SHORT; then
				_cyt_prek_parallel_record_log_failures "${group}" "${log}" "${FAILURES_LOG}" true
			else
				_cyt_prek_parallel_record_log_failures "${group}" "${log}" "${FAILURES_LOG}" false
				echo "--- last 40 lines of ${log} ---" >&2
				tail -n 40 "${log}" >&2 || true
			fi
		done
		if ! $PARALLEL_SHORT; then
			echo "Parallel Python subgroups failed: ${failed_groups[*]}" >&2
		fi
		_cyt_prek_parallel_report_failures_log "${FAILURES_LOG}"
		return 1
	fi
	return 0
}

cd "${ROOT}"
export PREK_GIT_STAGE_LOCK_PATH="${ROOT}/target/.prek-git-stage.lock.d"
export PREK_HOOK_LOCK_MAX_WAIT=720000
trap '_cyt_prek_parallel_stop_watchers; _cyt_prek_parallel_finish_staging' EXIT
_cyt_prek_parallel_init_failures_log
PARALLEL_RUN_START=$(date +%s)
_cyt_prek_parallel_resolve_unit_shards
_cyt_prek_parallel_cleanup_stale_loop_locks
_cyt_prek_parallel_block_conflicting_loops

if ! $PARALLEL_SHORT; then
	xdist_suffix=""
	if $USE_XDIST; then
		xdist_suffix=" with xdist (-n auto)"
	fi
	echo "Using ${PREK_PYTEST_UNIT_SHARDS} pytest-unit shard(s)${xdist_suffix}."
fi

_run_group py-sync

declare -a PARALLEL_GROUP_NAMES=()
declare -a PARALLEL_PIDS=()
declare -a PARALLEL_LOGS=()
declare -a PARALLEL_GROUP_STARTS=()
declare -a PARALLEL_GROUPS=()
_cyt_prek_parallel_build_groups
for group in "${PARALLEL_GROUPS[@]}"; do
	_run_group_background "${group}"
done

_cyt_prek_parallel_wait_jobs
_cyt_prek_parallel_stop_watchers

_run_group py-build

if ! $PARALLEL_SHORT; then
	_run_elapsed=$(($(date +%s) - PARALLEL_RUN_START))
	_cyt_prek_parallel_record_timing "_total" "${_run_elapsed}" "ok"
	echo "All parallel Python subgroups passed (${_run_elapsed}s wall)."
	echo "Logs retained under: ${LOG_DIR}/"
	echo "Per-group timings: ${TIMINGS_LOG}"
fi
