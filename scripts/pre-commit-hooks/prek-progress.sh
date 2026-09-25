#!/usr/bin/env bash
# Progress heartbeats and verbose pytest helpers for prek-loop scripts.
# shellcheck shell=bash

PREK_PROGRESS_HEARTBEAT_SECS="${PREK_PROGRESS_HEARTBEAT_SECS:-15}"
declare -a CYT_PREK_PROGRESS_WATCH_PIDS=()

_cyt_prek_descendant_pids() {
	local root_pid="$1"
	local -a queue=("${root_pid}") seen=("${root_pid}") cur child
	while ((${#queue[@]})); do
		cur="${queue[0]}"
		queue=("${queue[@]:1}")
		while IFS= read -r child; do
			[[ -z ${child} ]] && continue
			[[ " ${seen[*]} " == *" ${child} "* ]] && continue
			seen+=("${child}")
			queue+=("${child}")
			printf '%s\n' "${child}"
		done < <(pgrep -P "${cur}" 2>/dev/null || true)
	done
}

_cyt_prek_pytest_progress_from_log() {
	local log_file="$1"
	local -n _out_lines="$2"
	local passed=0 failed=0 skipped=0 collected=0
	local -a done_lines=() timing_lines=()
	local line current_file="" last_done="" current pct=""

	[[ -f ${log_file} ]] || return 0

	while IFS= read -r line; do
		[[ -z ${line} ]] && continue
		if [[ ${line} == *" collected in "* ]] || [[ ${line} =~ collected[[:space:]]+[0-9]+[[:space:]]+items ]]; then
			if [[ ${line} =~ ([0-9]+)[[:space:]]+items ]]; then
				collected="${BASH_REMATCH[1]}"
			fi
		fi
		if [[ ${line} =~ \[[[:space:]]*([0-9]+)%[[:space:]]*\] ]]; then
			pct="${BASH_REMATCH[1]}"
		fi
		if [[ ${line} == *" PASSED"* ]]; then
			passed=$((passed + 1))
			last_done="${line}"
			done_lines+=("${line}")
			if [[ ${line} =~ ^([^:]+\.py):: ]]; then
				current_file="${BASH_REMATCH[1]}"
			fi
		elif [[ ${line} == *" FAILED"* ]] || [[ ${line} == *" ERROR"* ]]; then
			failed=$((failed + 1))
			last_done="${line}"
			done_lines+=("${line}")
			if [[ ${line} =~ ^([^:]+\.py):: ]]; then
				current_file="${BASH_REMATCH[1]}"
			fi
		elif [[ ${line} == *" SKIPPED"* ]]; then
			skipped=$((skipped + 1))
			last_done="${line}"
			done_lines+=("${line}")
		elif [[ ${line} =~ ^([^[:space:]]+\.py)[[:space:]] ]]; then
			# pytest default progress: "src/tests/unit/test_foo.py .... [ 22%]"
			current_file="${BASH_REMATCH[1]}"
		elif [[ ${line} == "[timing "* ]] || [[ ${line} == "[timing] cargo-test "* ]]; then
			timing_lines+=("${line}")
			last_done="${line}"
		fi
	done <"${log_file}"

	if ((collected > 0)); then
		if [[ -n ${pct} ]]; then
			_out_lines+=("  tests: ${passed}/${collected} passed (${pct}%), ${failed} failed, ${skipped} skipped")
		else
			_out_lines+=("  tests: ${passed}/${collected} passed, ${failed} failed, ${skipped} skipped")
		fi
	elif ((passed + failed + skipped > 0)); then
		if [[ -n ${pct} ]]; then
			_out_lines+=("  tests: ${passed} passed (${pct}%), ${failed} failed, ${skipped} skipped")
		else
			_out_lines+=("  tests: ${passed} passed, ${failed} failed, ${skipped} skipped")
		fi
	else
		_out_lines+=("  tests: collecting/starting (no results yet)")
	fi

	if [[ -n ${current_file} ]]; then
		_out_lines+=("  current file: ${current_file}")
	fi

	if ((${#timing_lines[@]})); then
		local start=$((${#timing_lines[@]} > 5 ? ${#timing_lines[@]} - 5 : 0))
		local i
		for ((i = start; i < ${#timing_lines[@]}; i++)); do
			_out_lines+=("  ${timing_lines[i]}")
		done
	elif ((${#done_lines[@]})); then
		local start=$((${#done_lines[@]} > 3 ? ${#done_lines[@]} - 3 : 0))
		local i
		for ((i = start; i < ${#done_lines[@]}; i++)); do
			_out_lines+=("  done: ${done_lines[i]}")
		done
	fi

	current="$(tail -n 1 "${log_file}" 2>/dev/null || true)"
	if [[ -n ${current} && ${current} != "${last_done:-}" ]]; then
		if [[ ${current} == *" PASSED"* || ${current} == *" FAILED"* || ${current} == *" SKIPPED"* ]]; then
			:
		else
			_out_lines+=("  now: ${current}")
		fi
	fi
}

_cyt_prek_append_progress_line() {
	local label="$1"
	local pid="$2"
	local dest="${3:-}"
	local tag="${4:-progress}"
	local log_file="${5:-}"
	local ts elapsed cpu rss child child_cmd child_elapsed child_cpu
	local -a lines=() pytest_child=""

	ts="$(date '+%Y-%m-%dT%H:%M:%S%z')"
	lines+=("[${tag} ${ts}] ${label} pid=${pid} still running")
	if read -r elapsed cpu rss < <(ps -o etime= -o pcpu= -o rss= -p "${pid}" 2>/dev/null | tr -s ' '); then
		lines+=("  process: elapsed=${elapsed} cpu=${cpu}% rss=${rss}KB")
	fi

	if [[ -n ${log_file} ]]; then
		_cyt_prek_pytest_progress_from_log "${log_file}" lines
		while IFS= read -r child; do
			[[ -z ${child} ]] && continue
			child_cmd="$(ps -o command= -p "${child}" 2>/dev/null || true)"
			[[ ${child_cmd} == *pytest* ]] || continue
			read -r child_elapsed child_cpu < <(ps -o etime= -o pcpu= -p "${child}" 2>/dev/null | tr -s ' ')
			pytest_child="  pytest: pid=${child} elapsed=${child_elapsed:-?} cpu=${child_cpu:-?}%"
			break
		done < <(_cyt_prek_descendant_pids "${pid}")
		[[ -n ${pytest_child} ]] && lines+=("${pytest_child}")
	else
		while IFS= read -r child; do
			[[ -z ${child} ]] && continue
			child_cmd="$(ps -o command= -p "${child}" 2>/dev/null || true)"
			[[ -z ${child_cmd} ]] && continue
			read -r child_elapsed child_cpu < <(ps -o etime= -o pcpu= -p "${child}" 2>/dev/null | tr -s ' ')
			lines+=("  child pid=${child} elapsed=${child_elapsed:-?} cpu=${child_cpu:-?}% ${child_cmd}")
		done < <(_cyt_prek_descendant_pids "${pid}")
	fi

	if [[ -n ${dest} ]]; then
		{
			echo ""
			printf '%s\n' "${lines[@]}"
		} >>"${dest}"
	else
		printf '%s\n' "${lines[@]}" >&2
	fi
}

_cyt_prek_start_progress_watcher() {
	local label="$1"
	local pid="$2"
	local dest="${3:-}"
	local tag="${4:-progress}"
	local log_file="${5:-}"
	(
		while kill -0 "${pid}" 2>/dev/null; do
			sleep "${PREK_PROGRESS_HEARTBEAT_SECS}"
			kill -0 "${pid}" 2>/dev/null || break
			_cyt_prek_append_progress_line "${label}" "${pid}" "${dest}" "${tag}" "${log_file}"
		done
	) &
	CYT_PREK_PROGRESS_WATCH_PIDS+=("$!")
}

_cyt_prek_stop_progress_watchers() {
	local wp
	for wp in "${CYT_PREK_PROGRESS_WATCH_PIDS[@]}"; do
		kill "${wp}" 2>/dev/null || true
	done
	CYT_PREK_PROGRESS_WATCH_PIDS=()
}

PREK_GIT_STAGE_LOCK_HELD=""

_cyt_prek_git_stage_lock() {
	PREK_GIT_STAGE_LOCK_HELD="$(_cyt_acquire_lock_dir "${PREK_GIT_STAGE_LOCK_PATH}" "prek-git-stage" "${PREK_HOOK_LOCK_MAX_WAIT:-720000}")" || return 1
}

_cyt_prek_git_stage_unlock() {
	_cyt_release_lock_dir "${PREK_GIT_STAGE_LOCK_HELD:-}"
	PREK_GIT_STAGE_LOCK_HELD=""
}

_cyt_prek_tree_has_changes() {
	! git diff --quiet --ignore-submodules 2>/dev/null ||
		! git diff --cached --quiet --ignore-submodules 2>/dev/null
}

_cyt_prek_cargo_tree_needs_heal() {
	local path
	while IFS= read -r path; do
		[[ ${path} == Cargo.toml || ${path} == Cargo.lock || ${path} == sdk/rust/cyt-indexer/Cargo.toml ]] && return 0
	done < <(
		git diff --name-only --ignore-submodules 2>/dev/null
		git diff --cached --name-only --ignore-submodules 2>/dev/null
	)
	return 1
}

# Stage hook auto-fixes. Skips when the tree is clean unless force=true (retry path).
_cyt_prek_stage_fixes() {
	local force="${1:-false}"

	if [[ ${NO_GIT_ADD:-false} == true ]]; then
		return 0
	fi
	if [[ ${force} != true ]] && ! _cyt_prek_tree_has_changes; then
		return 0
	fi
	_cyt_prek_git_stage_lock || return 1
	if _cyt_prek_cargo_tree_needs_heal && [[ "${CYT_DEFER_HEAL_CARGO_LOCK:-}" != 1 ]]; then
		rtk bash scripts/local/dev/heal-cargo-lock.sh >/dev/null 2>&1 || true
	fi
	rtk git add -A >/dev/null 2>&1 || true
	_cyt_prek_git_stage_unlock
}

_cyt_prek_pytest_verbose_env() {
	export PYTHONUNBUFFERED=1
	export CYT_PREK_VERBOSE_PYTEST=1
	export CYT_PREK_PARALLEL_LOG=1
}

_cyt_prek_rust_verbose_env() {
	export CYT_PREK_VERBOSE_RUST=1
	export CYT_PREK_PARALLEL_LOG=1
}

_cyt_prek_pytest_hook_summary() {
	local hook="$1"
	local root="$2"
	local count="" shard_index="" shards=""

	case "${hook}" in
	pytest-unit)
		count="$(find "${root}/src/tests/unit" -name 'test_*.py' ! -path '*/gherkin/*' 2>/dev/null | wc -l | tr -d ' ')"
		echo "Scope: src/tests/unit (~${count} test files). Mode: pytest -v, [timing] per test, slowest summary at end."
		;;
	pytest-unit-shard-*)
		shard_index="${hook#pytest-unit-shard-}"
		shards="${PREK_PYTEST_UNIT_SHARDS:-4}"
		count="$(uv run python "${root}/scripts/local/tests/pytest-unit-shard.py" --shard "${shard_index}" --shards "${shards}" --root "${root}" 2>/dev/null | wc -l | tr -d ' ')"
		echo "Scope: unit shard ${shard_index}/${shards} (~${count} test files). Mode: pytest -v, [timing] per test."
		;;
	pytest-gherkin-unit)
		count="$(find "${root}/src/tests/unit/gherkin" \( -name 'test_*.py' -o -name '*.feature' \) 2>/dev/null | wc -l | tr -d ' ')"
		echo "Scope: src/tests/unit/gherkin (~${count} files). Mode: pytest -v, [timing] per test."
		;;
	pytest-quality-metrics)
		echo "Scope: src/tests/quality_metrics. Mode: pytest -v, live per-test lines, heartbeats show completed tests."
		;;
	pytest-coverage)
		echo "Scope: src/tests/coverage. Mode: pytest -v, live per-test lines, heartbeats show completed tests."
		;;
	pytest-mutation)
		echo "Scope: src/tests/mutation. Mode: pytest -v, live per-test lines, heartbeats show completed tests."
		;;
	pytest-qa)
		echo "Scope: src/tests/qa. Mode: pytest -v, live per-test lines, heartbeats show completed tests."
		;;
	pytest-runtime)
		echo "Scope: src/tests/runtime. Mode: pytest -v, live per-test lines, heartbeats show completed tests."
		;;
	pytest-sdk-python)
		echo "Scope: sdk/python tests. Mode: pytest -v, live per-test lines, heartbeats show completed tests."
		;;
	*)
		echo "Mode: pytest -v, live per-test lines, heartbeats show completed tests."
		;;
	esac
}

# Keep pytest/lint failure output compact for --short agent runs.
_cyt_prek_filter_test_failure_output() {
	awk '
		/^=+ FAILURES =+/ { capture = 1 }
		/^=+ ERRORS =+/ { capture = 1 }
		/^=+ short test summary info =+/ { capture = 1; saw_summary = 1 }
		capture { print }
		/^=+ [0-9]+ (passed|failed|error|warnings)/ && !/short test summary/ {
			if (capture && saw_summary) {
				exit
			}
		}
	'
}

_cyt_prek_filter_hook_failure_output() {
	local hook="$1"
	local text="$2"

	if [[ ${hook} == pytest-* ]]; then
		if printf '%s\n' "${text}" | grep -qE '= FAILURES =|= ERRORS =|= short test summary info ='; then
			_cyt_prek_filter_test_failure_output <<<"${text}"
			return 0
		fi
	fi

	_cyt_prek_filter_prek_loop_failure_output <<<"${text}"
}

# Strip optional "[group] " prefix from parallel prek-loop log lines.
_cyt_prek_filter_prek_loop_failure_output() {
	awk '
		function strip_prefix(line) {
			if (match(line, /^\[[^]]+\] /)) {
				return substr(line, RSTART + RLENGTH)
			}
			return line
		}
		{
			line = strip_prefix($0)
			if (line ~ /^Failed \[/ || line ~ /^Failures:/) {
				capture = 1
				print line
				next
			}
			if (line ~ /^Loop [0-9]+:/ && line ~ / [1-9][0-9]* failed/) {
				print line
				next
			}
			if (capture) {
				if (line ~ /^Passed \[/ || line ~ /^Running \[/ || line ~ /^Hooks:/) {
					capture = 0
					next
				}
				if (length(line) > 0) {
					print line
				}
				next
			}
		}
	'
}

_cyt_prek_filter_cargo_failure_output() {
	awk '
		function strip_prefix(line) {
			if (match(line, /^\[[^]]+\] /)) {
				return substr(line, RSTART + RLENGTH)
			}
			return line
		}
		{
			line = strip_prefix($0)
			if (line ~ /^error(\[E[0-9]+\])?: / || line ~ /^error: /) {
				print line
				next
			}
			if (line ~ / test .* FAILED$/ || line ~ /: FAILED$/) {
				print line
				next
			}
			if (line ~ /^test result: FAILED/) {
				print line
				next
			}
			if (line ~ /^failures:$/) {
				capture = 1
				print line
				next
			}
			if (capture) {
				print line
				next
			}
			if (line ~ /panicked at/ || line ~ /^thread .* panicked/) {
				print line
				next
			}
			if (line ~ /^(error|warning): .*clippy::/) {
				print line
				next
			}
		}
	'
}

_cyt_prek_parallel_grep_failure_fallback() {
	local log="$1"

	grep -E '(^|\] )Failed \[|(^|\] )Failures:|^error(\[E[0-9]+\])?: |^error: | test .* FAILED$|: FAILED$|test result: FAILED|panicked at|^failures:$' \
		"${log}" 2>/dev/null |
		sed -E 's/^\[[^]]+\] //' |
		tail -n 60
}

_cyt_prek_parallel_extract_log_failures() {
	local group="$1"
	local log="$2"
	local filtered=""
	local cargo_filtered=""

	[[ -f ${log} ]] || {
		printf '=== %s: no log at %s ===\n' "${group}" "${log}"
		return 0
	}

	if grep -qE '= FAILURES =|= ERRORS =|= short test summary info =' "${log}"; then
		filtered="$(_cyt_prek_filter_test_failure_output <"${log}")"
	elif grep -qE '(^|\] )Failed \[|(^|\] )Failures:' "${log}"; then
		filtered="$(_cyt_prek_filter_prek_loop_failure_output <"${log}")"
	elif grep -q '^Failed \[' "${log}"; then
		filtered="$(_cyt_prek_filter_prek_loop_failure_output <"${log}")"
	fi

	if grep -qE '(^|\] )error(\[E[0-9]+\])?: |(^|\] )error: | test .* FAILED|test result: FAILED|panicked at|^failures:$' "${log}"; then
		cargo_filtered="$(_cyt_prek_filter_cargo_failure_output <"${log}")"
		if [[ -n ${filtered} && -n ${cargo_filtered} ]]; then
			filtered="${filtered}"$'\n'"${cargo_filtered}"
		elif [[ -n ${cargo_filtered} ]]; then
			filtered="${cargo_filtered}"
		fi
	fi

	if [[ -z ${filtered//[$'\t\r\n ']/} ]]; then
		filtered="$(_cyt_prek_parallel_grep_failure_fallback "${log}")"
	fi

	if [[ -n ${filtered} ]]; then
		filtered="$(printf '%s\n' "${filtered}" | awk 'NF && !seen[$0]++')"
	fi

	printf '%s\n' "${filtered}"
}

_cyt_prek_parallel_record_log_failures() {
	local group="$1"
	local log="$2"
	local failures_log="${3:-${PREK_PARALLEL_FAILURES_LOG:-}}"
	local emit="${4:-false}"
	local filtered=""

	filtered="$(_cyt_prek_parallel_extract_log_failures "${group}" "${log}")"
	[[ -n ${filtered//[$'\t\r\n ']/} ]] || return 0

	if [[ -n ${failures_log} ]]; then
		{
			echo "=== ${group} ==="
			printf '%s\n' "${filtered}"
			echo ""
		} >>"${failures_log}"
	fi

	if [[ ${emit} == true ]]; then
		echo "=== ${group} ===" >&2
		printf '%s\n' "${filtered}" >&2
	fi
}

# Rewrite failures.log from timings entries marked failed (parallel orchestrators).
_cyt_prek_parallel_consolidate_failures_log() {
	local failures_log="${1:-${PREK_PARALLEL_FAILURES_LOG:-}}"
	local timings_log="${2:-}"
	local log_dir="${3:-}"
	local tmp=""
	local -a failed_groups=()
	local group timing_status log filtered

	[[ -n ${failures_log} && -n ${timings_log} && -f ${timings_log} && -n ${log_dir} ]] || return 0

	while read -r _ group _ timing_status; do
		[[ ${timing_status} == failed ]] || continue
		[[ ${group} == _total ]] && continue
		failed_groups+=("${group}")
	done <"${timings_log}"

	tmp="$(mktemp "${failures_log}.XXXXXX")"
	: >"${tmp}"
	for group in "${failed_groups[@]}"; do
		log="${log_dir}/${group}.log"
		[[ -f ${log} ]] || continue
		filtered="$(_cyt_prek_parallel_extract_log_failures "${group}" "${log}")"
		[[ -n ${filtered//[$'\t\r\n ']/} ]] || continue
		{
			echo "=== ${group} ==="
			printf '%s\n' "${filtered}"
			echo ""
		} >>"${tmp}"
	done
	mv "${tmp}" "${failures_log}"
}

_cyt_prek_parallel_emit_log_failures() {
	_cyt_prek_parallel_record_log_failures "$1" "$2" "${PREK_PARALLEL_FAILURES_LOG:-}" true
}

_cyt_prek_parallel_report_failures_log() {
	local failures_log="${1:-${PREK_PARALLEL_FAILURES_LOG:-}}"
	[[ -n ${failures_log} ]] || return 0
	[[ -s ${failures_log} ]] || return 0
	echo "Failures: ${failures_log}" >&2
}

_cyt_prek_parallel_count_running_pids() {
	local running=0 pid
	for pid in "$@"; do
		kill -0 "${pid}" 2>/dev/null && running=$((running + 1))
	done
	printf '%s\n' "${running}"
}

_cyt_prek_parallel_status_line_from_log() {
	local log="$1"
	local line=""

	[[ -f ${log} ]] || {
		printf '%s\n' "(no log yet)"
		return 0
	}

	line="$(
		grep -v '^\[watch ' "${log}" 2>/dev/null |
			grep -v 'Blocking waiting for file lock on build directory' |
			tail -n 1 || true
	)"
	while [[ ${line} == "  now: "* || ${line} == "now: "* ]]; do
		line="${line#  now: }"
		line="${line#now: }"
	done

	if [[ -n ${line} ]]; then
		printf '%s\n' "${line}"
		return 0
	fi

	line="$(grep '^\[watch ' "${log}" 2>/dev/null | tail -n 1 || true)"
	if [[ -n ${line} ]]; then
		printf '%s\n' "${line}"
		return 0
	fi

	if grep -q 'Blocking waiting for file lock on build directory' "${log}" 2>/dev/null; then
		printf '%s\n' "(waiting for shared target/ cargo lock)"
		return 0
	fi

	printf '%s\n' "(no output yet)"
}

_cyt_prek_pytest_direct_cmd() {
	local hook="$1"
	local shard_index shards
	case "${hook}" in
	pytest-unit) printf '%s\n' "bash scripts/local/tests/pytest-category.sh unit" ;;
	pytest-unit-shard-*)
		shard_index="${hook#pytest-unit-shard-}"
		shards="${PREK_PYTEST_UNIT_SHARDS:-4}"
		printf '%s\n' "bash scripts/local/tests/pytest-category.sh unit-shard ${shard_index} ${shards}"
		;;
	pytest-gherkin-unit) printf '%s\n' "bash scripts/local/tests/pytest-category.sh gherkin-unit" ;;
	pytest-quality-metrics) printf '%s\n' "bash scripts/local/tests/pytest-category.sh quality_metrics" ;;
	pytest-coverage) printf '%s\n' "bash scripts/local/tests/pytest-category.sh coverage" ;;
	pytest-mutation) printf '%s\n' "bash scripts/local/tests/pytest-category.sh mutation" ;;
	pytest-qa) printf '%s\n' "bash scripts/local/tests/pytest-category.sh qa" ;;
	pytest-runtime) printf '%s\n' "CYT_RUN_RUNTIME_TESTS=1 bash scripts/local/tests/pytest-category.sh runtime" ;;
	pytest-sdk-python) printf '%s\n' "bash scripts/local/tests/pytest-sdk-python.sh" ;;
	*) return 1 ;;
	esac
}

_cyt_prek_cargo_hook_summary() {
	local hook="$1"
	local root="$2"
	local shard_index="" shards="" count=""

	case "${hook}" in
	cargo-test-unit)
		count="$(grep -cE '^name = \"unit_' "${root}/sdk/rust/cyt-indexer/Cargo.toml" || true)"
		echo "Scope: cyt-indexer unit (~${count} test binaries). Mode: [timing] cargo-test per binary."
		;;
	cargo-test-unit-shard-*)
		shard_index="${hook#cargo-test-unit-shard-}"
		shards="${PREK_RUST_UNIT_SHARDS:-4}"
		count="$(uv run python "${root}/scripts/local/tests/cargo-unit-shard.py" --shard "${shard_index}" --shards "${shards}" --root "${root}" 2>/dev/null | wc -l | tr -d ' ')"
		echo "Scope: unit shard ${shard_index}/${shards} (~${count} test binaries). Mode: [timing] cargo-test per binary."
		;;
	cargo-test-integration) echo "Scope: cyt-indexer integration tests." ;;
	cargo-test-cucumber) echo "Scope: cyt-indexer cucumber tests." ;;
	cargo-test-ffi) echo "Scope: cyt-indexer ffi tests." ;;
	cargo-test-coverage) echo "Scope: cyt-indexer coverage tests." ;;
	cargo-test-mutation) echo "Scope: cyt-indexer mutation tests." ;;
	cargo-test-quality-metrics) echo "Scope: cyt-indexer quality_metrics tests." ;;
	cargo-test-qa) echo "Scope: cyt-indexer qa tests." ;;
	cargo-warm-build) echo "Scope: pre-compile cyt-indexer test binaries (--no-run)." ;;
	*) echo "Mode: cargo test with live [timing] lines when verbose." ;;
	esac
}

_cyt_prek_cargo_direct_cmd() {
	local hook="$1"
	local shard_index shards
	case "${hook}" in
	cargo-test-unit) printf '%s\n' "bash scripts/local/tests/cargo-test-category.sh unit" ;;
	cargo-test-unit-shard-*)
		shard_index="${hook#cargo-test-unit-shard-}"
		shards="${PREK_RUST_UNIT_SHARDS:-4}"
		printf '%s\n' "bash scripts/local/tests/cargo-test-category.sh unit-shard ${shard_index} ${shards}"
		;;
	cargo-test-integration) printf '%s\n' "bash scripts/local/tests/cargo-test-category.sh integration" ;;
	cargo-test-cucumber) printf '%s\n' "bash scripts/local/tests/cargo-test-category.sh cucumber" ;;
	cargo-test-ffi) printf '%s\n' "bash scripts/local/tests/cargo-test-category.sh ffi" ;;
	cargo-test-coverage) printf '%s\n' "bash scripts/local/tests/cargo-test-category.sh coverage" ;;
	cargo-test-mutation) printf '%s\n' "bash scripts/local/tests/cargo-test-category.sh mutation" ;;
	cargo-test-quality-metrics) printf '%s\n' "bash scripts/local/tests/cargo-test-category.sh quality_metrics" ;;
	cargo-test-qa) printf '%s\n' "bash scripts/local/tests/cargo-test-category.sh qa" ;;
	cargo-warm-build) printf '%s\n' "bash scripts/local/tests/cargo-warm-build.sh" ;;
	*) return 1 ;;
	esac
}
