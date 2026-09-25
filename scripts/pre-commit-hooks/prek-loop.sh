#!/usr/bin/env bash
# Usage: ./scripts/pre-commit-hooks/prek-loop.sh [--short] [--one-run] [--runtime] [--no-git-add]
#          [--changed-only] [--from-ref REF] [--fail-fast] [-g|--group GROUP...]
#
# Run prek hooks one at a time, staging fixes until all pass.
# Git staging: --git-add hook (default), group (once per group), or none (orchestrator handles it).
# Without --short, each hook prints a one-line Passed/Failed summary; details on failure only.
# Hook output streams live (cargo test hooks 87–94 can take many minutes each).
# With --short, passing hooks are silent unless they fail (CYT_LOCAL_DEV_SHORT=1 for workflow.sh).
# Groups are optional; see scripts/pre-commit-hooks/prek-hook-groups.yaml:
#   py, py-dev, py-sync, py-lint, py-test-*, py-build, py-smoke,
#   rust, rust-dev, rust-sync, rust-lint, rust-header, rust-test-*, rust-build, go, c, ts, uni
#
# Examples:
#   ./scripts/pre-commit-hooks/prek-loop.sh -g py
#   ./scripts/pre-commit-hooks/prek-loop.sh --short --one-run --changed-only --fail-fast -g py-dev
#   ./scripts/pre-commit-hooks/prek-loop-py-parallel.sh --short --one-run
#   ./scripts/pre-commit-hooks/prek-loop-rust-parallel.sh --short --one-run [--changed-only]
#
# Parallel runs (separate terminals — disjoint or overlapping groups are OK):
#   ./scripts/pre-commit-hooks/prek-loop.sh -g py
#   ./scripts/pre-commit-hooks/prek-loop.sh -g uni
#   ./scripts/pre-commit-hooks/prek-loop.sh -g rust
# Per-group locks allow concurrent loops; per-hook locks serialize shared hooks
# (e.g. maturin-develop); git staging is serialized repo-wide.
# Do not run without -g while group loops are active (full run takes _all lock).
#
# If prek-hook-groups.yaml is missing, all hooks run regardless of --group.
# Examples:
# Run iteratively, fix all issues, do not omit, comment out or ignore, instead investigate the root cause and fix. Preserve the functionality:

# ./scripts/pre-commit-hooks/prek-loop.sh --short --one-run --group py rust ts uni
# ./scripts/pre-commit-hooks/prek-loop.sh --short --one-run --group rust go c uni
#
# Run `scripts\pre-commit-hooks\prek-loop.sh --short --one-run` iteratively, do not omit or comment out issues, instead investigate root cause and fix the. Preserve the functionality.

set -uo pipefail

SHORT=false
ONE_RUN=false
NO_GIT_ADD=false
RUN_RUNTIME=false
CHANGED_ONLY=false
FAIL_FAST=false
PREK_ALL_FILES=true
PREK_FROM_REF=""
PREK_TO_REF="HEAD"
PREK_GIT_ADD_MODE="${PREK_GIT_ADD_MODE:-hook}"
SELECTED_GROUPS=()
while (($#)); do
	case "$1" in
	--short)
		SHORT=true
		shift
		;;
	--one-run)
		ONE_RUN=true
		shift
		;;
	--runtime)
		RUN_RUNTIME=true
		shift
		;;
	--no-git-add)
		NO_GIT_ADD=true
		PREK_GIT_ADD_MODE=none
		shift
		;;
	--git-add)
		shift
		if ((${#} == 0)) || [[ ${1:-} == -* ]]; then
			echo "--git-add requires hook, group, or none." >&2
			exit 1
		fi
		case "$1" in
		hook | group | none) PREK_GIT_ADD_MODE="$1" ;;
		*)
			echo "--git-add must be hook, group, or none (got: $1)." >&2
			exit 1
			;;
		esac
		shift
		;;
	--changed-only)
		CHANGED_ONLY=true
		PREK_ALL_FILES=false
		shift
		;;
	--from-ref)
		shift
		if ((${#} == 0)) || [[ ${1:-} == -* ]]; then
			echo "--from-ref requires a git ref." >&2
			exit 1
		fi
		PREK_FROM_REF="$1"
		CHANGED_ONLY=true
		PREK_ALL_FILES=false
		shift
		;;
	--to-ref)
		shift
		if ((${#} == 0)) || [[ ${1:-} == -* ]]; then
			echo "--to-ref requires a git ref." >&2
			exit 1
		fi
		PREK_TO_REF="$1"
		shift
		;;
	--fail-fast)
		FAIL_FAST=true
		shift
		;;
	-g | --group)
		shift
		if ((${#} == 0)) || [[ ${1:-} == -* ]]; then
			echo "--group requires at least one group name." >&2
			echo "Usage: $0 [--short] [--one-run] [--changed-only] [--fail-fast] [-g|--group GROUP...]" >&2
			exit 1
		fi
		while (($#)) && [[ $1 != -* ]]; do
			SELECTED_GROUPS+=("$1")
			shift
		done
		;;
	-h | --help)
		echo "Usage: $0 [--short] [--one-run] [--runtime] [--no-git-add] [--git-add MODE] [--changed-only] [--from-ref REF] [--to-ref REF] [--fail-fast] [-g|--group GROUP...]" >&2
		echo "Groups: py py-dev py-sync py-lint py-test-core py-test-gherkin py-test-unit-shard-* py-test-heavy py-test-quality-metrics py-test-coverage py-test-mutation py-test-qa py-test-sdk py-build py-smoke" >&2
		echo "         rust rust-dev rust-sync rust-lint rust-header rust-test-unit-shard-* rust-test-* rust-build go c ts uni runtime release" >&2
		echo "         (see scripts/pre-commit-hooks/prek-hook-groups.yaml)" >&2
		echo "Multiple -g instances may run in parallel in separate terminals." >&2
		echo "  --git-add MODE   When to stage auto-fixes: hook (default), group, none" >&2
		echo "  --changed-only   Run hooks on changed files only (omit --all-files)" >&2
		echo "  --from-ref REF   Diff REF..HEAD for --changed-only (default: merge-base with upstream)" >&2
		echo "  --fail-fast      Stop after the first hook failure" >&2
		echo "Parallel Python:  ./scripts/pre-commit-hooks/prek-loop-py-parallel.sh --short --one-run [--xdist]" >&2
		echo "Parallel Rust:    ./scripts/pre-commit-hooks/prek-loop-rust-parallel.sh --short --one-run [--changed-only]" >&2
		exit 0
		;;
	-*)
		echo "Unknown option: $1" >&2
		echo "Usage: $0 [--short] [--one-run] [--changed-only] [--fail-fast] [-g|--group GROUP...]" >&2
		exit 1
		;;
	*)
		echo "Unexpected argument: $1" >&2
		echo "Usage: $0 [--short] [--one-run] [--changed-only] [--fail-fast] [-g|--group GROUP...]" >&2
		exit 1
		;;
	esac
done

ROOT="$(cd "$(git rev-parse --show-toplevel)" && pwd -P)"
cd "$ROOT" || exit 1

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# shellcheck source=scripts/lib/chunk-worktree.sh
source "${SCRIPT_DIR}/../lib/chunk-worktree.sh"
# shellcheck source=scripts/pre-commit-hooks/prek-progress.sh
source "${SCRIPT_DIR}/prek-progress.sh"

PREK_LOCK_BASE="${ROOT}/target/.prek-loop.lock.d"
PREK_HOOK_LOCK_BASE="${ROOT}/target/.prek-hook.lock.d"
PREK_GIT_STAGE_LOCK_PATH="${ROOT}/target/.prek-git-stage.lock.d"
# Long wait: a hook may block on another group's cargo test / maturin-develop.
PREK_HOOK_LOCK_MAX_WAIT=720000
# Shorter wait: duplicate same group key in another terminal.
PREK_LOOP_DUP_MAX_WAIT=14400
# Print lock-wait heartbeats so duplicate group runs do not look hung.
PREK_LOCK_HEARTBEAT_SECS=5

PREK_LOOP_LOCK_DIR=""
PREK_LOOP_LOCK_KEY=""
PREK_HOOK_LOCK_DIR=""

_cyt_prek_loop_lock_key() {
	if ((${#SELECTED_GROUPS[@]} == 0)); then
		printf '%s\n' '_all'
		return 0
	fi
	local -a sorted=()
	mapfile -t sorted < <(printf '%s\n' "${SELECTED_GROUPS[@]}" | sort -u)
	local IFS='-'
	printf '%s\n' "${sorted[*]}"
}

_cyt_prek_active_loop_lock_names() {
	local base="$1"
	local d name
	[[ -d "${base}" ]] || return 0
	for d in "${base}"/*; do
		[[ -d "${d}" ]] || continue
		if _cyt_lock_dir_is_stale "${d}"; then
			rm -rf "${d}"
			continue
		fi
		name="$(basename "${d}")"
		printf '%s\n' "${name}"
	done
}

_cyt_prek_echo_err() {
	if [[ -n ${PREK_LOOP_LOCK_KEY:-} ]]; then
		echo "[${PREK_LOOP_LOCK_KEY}] $*" >&2
	else
		echo "$*" >&2
	fi
}

_cyt_prek_echo() {
	if [[ -n ${PREK_LOOP_LOCK_KEY:-} ]]; then
		echo "[${PREK_LOOP_LOCK_KEY}] $*"
	else
		echo "$*"
	fi
}

_cyt_prek_cleanup_stale_locks() {
	local lock_root="$1"
	local d
	[[ -d "${lock_root}" ]] || return 0
	for d in "${lock_root}"/*; do
		[[ -d "${d}" ]] || continue
		if _cyt_lock_dir_is_stale "${d}"; then
			rm -rf "${d}"
		fi
	done
}

_cyt_prek_loop_lock() {
	local key="$1"
	local lock_dir="${PREK_LOCK_BASE}/${key}"
	local -a active=() other=()

	mkdir -p "${PREK_LOCK_BASE}" "${PREK_HOOK_LOCK_BASE}"
	_cyt_prek_cleanup_stale_locks "${PREK_LOCK_BASE}"
	_cyt_prek_cleanup_stale_locks "${PREK_HOOK_LOCK_BASE}"

	mapfile -t active < <(_cyt_prek_active_loop_lock_names "${PREK_LOCK_BASE}")

	if [[ ${key} == "_all" ]]; then
		if ((${#active[@]})); then
			_cyt_prek_echo_err "Cannot run all hooks while parallel group loops are active: ${active[*]}"
			exit 1
		fi
	else
		for other in "${active[@]}"; do
			if [[ ${other} == "_all" ]]; then
				_cyt_prek_echo_err "Cannot start group loop while a full prek loop (_all) is running."
				exit 1
			fi
		done
	fi

	PREK_LOOP_LOCK_DIR="$(_cyt_acquire_lock_dir "${lock_dir}" "prek-loop:${key}" "${PREK_LOOP_DUP_MAX_WAIT}" "${PREK_LOCK_HEARTBEAT_SECS}")" || exit 1
	PREK_LOOP_LOCK_KEY="${key}"
	export CYT_PREK_LOOP_LOCK_KEY="${key}"
	echo "${key}" >"${PREK_LOOP_LOCK_DIR}/groups"
	echo $$ >"${PREK_LOOP_LOCK_DIR}/pid"
}

_cyt_prek_loop_unlock() {
	_cyt_release_lock_dir "${PREK_LOOP_LOCK_DIR:-}"
	PREK_LOOP_LOCK_DIR=""
}

_cyt_prek_hook_lock() {
	local hook="$1"
	PREK_HOOK_LOCK_DIR="$(_cyt_acquire_lock_dir "${PREK_HOOK_LOCK_BASE}/${hook}" "prek-hook:${hook}" "${PREK_HOOK_LOCK_MAX_WAIT}" "${PREK_LOCK_HEARTBEAT_SECS}")" || return 1
}

_cyt_prek_hook_unlock() {
	_cyt_release_lock_dir "${PREK_HOOK_LOCK_DIR:-}"
	PREK_HOOK_LOCK_DIR=""
}

_cyt_prek_stage_after_hook() {
	[[ ${PREK_GIT_ADD_MODE} == hook ]] || return 0
	_cyt_prek_stage_fixes false || true
}

_cyt_prek_finish_group_staging() {
	[[ ${PREK_GIT_ADD_MODE} == group ]] || return 0
	_cyt_prek_stage_fixes false || true
}

_cyt_prek_default_from_ref() {
	local base=""
	if base="$(git merge-base HEAD '@{upstream}' 2>/dev/null)"; then
		printf '%s\n' "${base}"
		return 0
	fi
	if base="$(git merge-base HEAD origin/main 2>/dev/null)"; then
		printf '%s\n' "${base}"
		return 0
	fi
	if base="$(git merge-base HEAD main 2>/dev/null)"; then
		printf '%s\n' "${base}"
		return 0
	fi
	return 1
}

_cyt_prek_resolve_from_ref() {
	if $PREK_ALL_FILES || [[ -n ${PREK_FROM_REF} ]]; then
		return 0
	fi
	if $CHANGED_ONLY; then
		PREK_FROM_REF="$(_cyt_prek_default_from_ref || true)"
	fi
}

_cyt_prek_hook_skipped() {
	[[ "${PREK_STATUSES}" == Skipped* ]]
}

_cyt_prek_build_prek_cmd() {
	local hook="$1"
	PREK_RUN_CMD=(uv run prek run "${hook}")
	if $PREK_ALL_FILES; then
		PREK_RUN_CMD+=(--all-files)
	elif [[ -n ${PREK_FROM_REF} ]]; then
		PREK_RUN_CMD+=(--from-ref "${PREK_FROM_REF}" --to-ref "${PREK_TO_REF}")
	fi
}

# Integration tests call real external APIs; never run them in automated hook loops.
unset CYT_RUN_INTEGRATION_TESTS
unset CYT_RUN_PAID_TESTS
unset CYT_RUN_QA_TESTS
unset CYT_RUN_RUNTIME_TESTS

# Propagate short mode to workflow.sh hooks (e.g. simulate-registry).
if $SHORT; then
	export CYT_LOCAL_DEV_SHORT=1
else
	export CYT_LOCAL_DEV_SHORT=
fi

if $RUN_RUNTIME; then
	export CYT_RUN_RUNTIME_TESTS=1
fi

GROUPS_FILE="$SCRIPT_DIR/prek-hook-groups.yaml"
export SHORTEN_ROOT="$ROOT"

if ! $SHORT; then
	echo "Discovering prek hooks..." >&2
fi
mapfile -t ALL_HOOKS < <(uv run prek list | sed 's/^\.://' | tr -d '\r' | awk '!seen[$0]++')
((${#ALL_HOOKS[@]})) || {
	echo "No prek hooks found." >&2
	exit 1
}
if ! $SHORT; then
	echo "Discovered ${#ALL_HOOKS[@]} prek hooks." >&2
fi

declare -A ALL_HOOK_SET=()
for hook in "${ALL_HOOKS[@]}"; do
	hook="${hook//$'\r'/}"
	ALL_HOOK_SET["$hook"]=1
done

load_group_hooks() {
	local group="$1"
	uv run python - "$group" "$GROUPS_FILE" <<'PY'
import sys

import yaml

group, path = sys.argv[1], sys.argv[2]
with open(path, encoding="utf-8") as fh:
    data = yaml.safe_load(fh) or {}

groups = data.get("groups") or {}
if group not in groups:
    known = ", ".join(sorted(groups))
    print(f"ERROR:unknown-group:{group}:{known}")
    sys.exit(2)

hooks = groups[group] or []
for hook in hooks:
    if hook:
        # LF-only so Windows Git Bash does not leave stray CR in hook names.
        sys.stdout.buffer.write(f"{hook}\n".encode())
PY
}

resolve_hooks() {
	if ((${#SELECTED_GROUPS[@]} == 0)); then
		HOOKS=("${ALL_HOOKS[@]}")
		return 0
	fi

	if [[ ! -f $GROUPS_FILE ]]; then
		echo "Warning: $GROUPS_FILE not found; running all hooks (ignoring groups: ${SELECTED_GROUPS[*]})." >&2
		HOOKS=("${ALL_HOOKS[@]}")
		return 0
	fi

	declare -A SEEN_HOOKS=()
	HOOKS=()
	local group group_hooks_raw group_exit missing=()

	for group in "${SELECTED_GROUPS[@]}"; do
		group_exit=0
		group_hooks_raw=$(load_group_hooks "$group") || group_exit=$?
		if ((group_exit == 2)); then
			if [[ $group_hooks_raw == ERROR:unknown-group:* ]]; then
				local known="${group_hooks_raw#ERROR:unknown-group:"${group}":}"
				echo "Unknown group '$group'. Valid groups: $known" >&2
				exit 1
			fi
		fi
		if ((group_exit != 0)); then
			echo "Failed to read hook groups from $GROUPS_FILE." >&2
			exit 1
		fi

		mapfile -t GROUP_HOOKS <<<"${group_hooks_raw:-}"

		for hook in "${GROUP_HOOKS[@]}"; do
			[[ -z $hook ]] && continue
			hook="${hook//$'\r'/}"
			if [[ -z ${ALL_HOOK_SET[$hook]+x} ]]; then
				missing+=("$group:$hook")
				continue
			fi
			if [[ -z ${SEEN_HOOKS[$hook]+x} ]]; then
				HOOKS+=("$hook")
				SEEN_HOOKS[$hook]=1
			fi
		done
	done

	if ((${#missing[@]})); then
		echo "Warning: groups list hooks not in prek config: ${missing[*]}" >&2
	fi

	if ((${#SELECTED_GROUPS[@]})) && ((${#HOOKS[@]} < 3)) && ((${#missing[@]} > 5)); then
		echo "Hint: most group hooks did not match prek list (often Windows CRLF in hook names)." >&2
		echo "      Re-run from Git Bash or update scripts/pre-commit-hooks/prek-loop.sh." >&2
	fi

	if ((${#HOOKS[@]} == 0)); then
		echo "Groups (${SELECTED_GROUPS[*]}) have no runnable hooks." >&2
	fi
}

resolve_hooks

_cyt_prek_resolve_from_ref

if ! $SHORT; then
	if ((${#SELECTED_GROUPS[@]})); then
		echo "Acquiring loop lock for group(s): ${SELECTED_GROUPS[*]} (${#HOOKS[@]} hooks)..." >&2
	else
		echo "Acquiring full prek loop lock (${#HOOKS[@]} hooks)..." >&2
	fi
fi

PREK_LOOP_LOCK_KEY="$(_cyt_prek_loop_lock_key)"
_cyt_prek_loop_lock "${PREK_LOOP_LOCK_KEY}"
printf '%s\n' "${HOOKS[@]}" >"${PREK_LOOP_LOCK_DIR}/hooks"

trap '_cyt_prek_stop_progress_watchers; _cyt_prek_hook_unlock; _cyt_prek_git_stage_unlock; _cyt_prek_loop_unlock' EXIT
trap '_cyt_prek_stop_progress_watchers; _cyt_prek_hook_unlock; _cyt_prek_git_stage_unlock; _cyt_prek_loop_unlock; echo; _cyt_prek_echo "Interrupted."; exit 130' INT TERM

if ! $SHORT; then
	mapfile -t _prek_other_loops < <(_cyt_prek_active_loop_lock_names "${PREK_LOCK_BASE}")
	if ((${#_prek_other_loops[@]} > 1)); then
		_cyt_prek_echo_err "Parallel prek loops active: ${_prek_other_loops[*]}"
	elif ((${#_prek_other_loops[@]} == 1)) && [[ ${_prek_other_loops[0]} != "${PREK_LOOP_LOCK_KEY}" ]]; then
		_cyt_prek_echo_err "Parallel prek loops active: ${_prek_other_loops[*]}"
	fi
fi

total=${#HOOKS[@]}
mode="Prek loop"
$SHORT && mode+=" (short)"
$ONE_RUN && mode+=" (one run)"
$RUN_RUNTIME && mode+=" (runtime)"
$CHANGED_ONLY && mode+=" (changed-only)"
$FAIL_FAST && mode+=" (fail-fast)"
if ((${#SELECTED_GROUPS[@]})); then
	mode+=" [${SELECTED_GROUPS[*]}]"
fi
if $CHANGED_ONLY && [[ -n ${PREK_FROM_REF} ]] && ! $SHORT; then
	_cyt_prek_echo "Changed-only diff: ${PREK_FROM_REF}..${PREK_TO_REF}"
fi
if ! $SHORT; then
	if $ONE_RUN; then
		_cyt_prek_echo "$mode: $total hooks, single iteration."
	else
		_cyt_prek_echo "$mode: $total hooks until all pass."
	fi
	if ((${#SELECTED_GROUPS[@]})) && [[ -f $GROUPS_FILE ]] && ((total > 0)); then
		_cyt_prek_echo "Hooks: ${HOOKS[*]}"
	fi
	_cyt_prek_echo
fi

# prek prints "hook-name.....<status>"; extract <status> from dot-padded lines.
parse_prek_output() {
	local parsed
	parsed=$(printf '%s\n' "$1" | awk '
		/\.{3,}/ {
			s = $0
			sub(/^.*\.{3,}/, "", s)
			if (s ~ /^\([^)]*\)Skipped$/) {
				reason = s
				sub(/^\(/, "", reason)
				sub(/\)Skipped$/, "", reason)
				s = "Skipped (" reason ")"
			}
			if (n++) statuses = statuses ", "
			statuses = statuses s
			next
		}
		{ details = details $0 ORS }
		END {
			gsub(/\n$/, "", details)
			if (statuses != "") {
				n = split(statuses, parts, ", ")
				deduped = ""
				for (i = 1; i <= n; i++) {
					seen = 0
					for (j = 1; j < i; j++) {
						if (parts[j] == parts[i]) {
							seen = 1
							break
						}
					}
					if (!seen) {
						if (deduped != "") deduped = deduped ", "
						deduped = deduped parts[i]
					}
				}
				n = split(deduped, parts, ", ")
				has_failed = has_skipped = 0
				for (i = 1; i <= n; i++) {
					if (parts[i] ~ /^Failed/) has_failed = 1
					else if (parts[i] ~ /^Skipped/) has_skipped = 1
				}
				filtered = ""
				for (i = 1; i <= n; i++) {
					keep = 0
					if (has_failed) keep = (parts[i] ~ /^Failed/)
					else if (has_skipped) keep = (parts[i] ~ /^Skipped/)
					else keep = (parts[i] == "Passed")
					if (keep) {
						if (filtered != "") filtered = filtered ", "
						filtered = filtered parts[i]
					}
				}
				statuses = filtered
			}
			print statuses "\031" details
		}
	')
	PREK_STATUSES="${parsed%%$'\031'*}"
	PREK_DETAILS="${parsed#*$'\031'}"
}

LAST_HOOK_RAW_OUTPUT=""

_run_hook_capture() {
	local label="$1"
	shift
	local tmp output exit_code=0

	if $SHORT; then
		output=$("$@" 2>&1) || exit_code=$?
		PREK_HOOK_STREAMED=false
		printf '%s' "${output}"
		return "${exit_code}"
	fi

	tmp="$(mktemp "${TMPDIR:-/tmp}/prek-hook.${label}.XXXXXX")"
	set +o pipefail
	"$@" 2>&1 | tee "${tmp}"
	exit_code=${PIPESTATUS[0]}
	set -o pipefail
	output="$(cat "${tmp}")"
	rm -f "${tmp}"
	PREK_HOOK_STREAMED=true
	LAST_HOOK_RAW_OUTPUT="${output}"
	printf '%s' "${output}"
	return "${exit_code}"
}

_run_hook_capture_progress() {
	local label="$1"
	shift
	local tmp output exit_code=0 runner_pid

	# Do not wrap this function in $(...) — that captures stdout and hides live pytest lines.
	tmp="$(mktemp "${TMPDIR:-/tmp}/prek-hook.${label}.XXXXXX")"
	(
		set +o pipefail
		if command -v stdbuf >/dev/null 2>&1; then
			stdbuf -oL -eL "$@" 2>&1 | stdbuf -oL -eL tee "${tmp}"
		else
			"$@" 2>&1 | tee "${tmp}"
		fi
		exit "${PIPESTATUS[0]}"
	) &
	runner_pid=$!
	_cyt_prek_start_progress_watcher "${label}" "${runner_pid}" "" "progress" "${tmp}"
	wait "${runner_pid}" || exit_code=$?
	_cyt_prek_stop_progress_watchers
	output="$(cat "${tmp}")"
	rm -f "${tmp}"
	PREK_HOOK_STREAMED=true
	LAST_HOOK_RAW_OUTPUT="${output}"
	return "${exit_code}"
}

run_hook() {
	local hook="$1"
	local output exit_code=0

	if ! _cyt_prek_hook_lock "${hook}"; then
		_cyt_prek_echo_err "Timed out waiting for hook lock: ${hook}"
		return 1
	fi

	if [[ "${hook}" == pytest-* ]]; then
		if $SHORT; then
			:
		elif _cyt_prek_pytest_direct_cmd "${hook}" >/dev/null; then
			local pytest_cmd=""
			pytest_cmd="$(_cyt_prek_pytest_direct_cmd "${hook}")"
			_cyt_prek_pytest_verbose_env
			_cyt_prek_echo_err "$(_cyt_prek_pytest_hook_summary "${hook}" "${ROOT}")"
			_cyt_prek_echo_err "[progress] heartbeats every ${PREK_PROGRESS_HEARTBEAT_SECS}s list completed tests."
			_run_hook_capture_progress "${hook}" bash -c "cd \"${ROOT}\" && ${pytest_cmd}" || exit_code=$?
			output="${LAST_HOOK_RAW_OUTPUT}"
			if ((exit_code == 0)); then
				PREK_STATUSES="Passed"
			else
				PREK_STATUSES="Failed"
			fi
			PREK_DETAILS="${output}"
			_cyt_prek_hook_unlock
			return "${exit_code}"
		else
			_cyt_prek_echo_err "Note: ${hook} may take several minutes."
		fi
	fi

	if [[ "${hook}" == cargo-test-* || "${hook}" == "cargo-warm-build" ]]; then
		if $SHORT; then
			:
		elif _cyt_prek_cargo_direct_cmd "${hook}" >/dev/null; then
			local cargo_cmd=""
			cargo_cmd="$(_cyt_prek_cargo_direct_cmd "${hook}")"
			_cyt_prek_rust_verbose_env
			_cyt_prek_echo_err "$(_cyt_prek_cargo_hook_summary "${hook}" "${ROOT}")"
			_cyt_prek_echo_err "[progress] heartbeats every ${PREK_PROGRESS_HEARTBEAT_SECS}s list completed test binaries."
			_run_hook_capture_progress "${hook}" bash -c "cd \"${ROOT}\" && ${cargo_cmd}" || exit_code=$?
			output="${LAST_HOOK_RAW_OUTPUT}"
			if ((exit_code == 0)); then
				PREK_STATUSES="Passed"
			else
				PREK_STATUSES="Failed"
			fi
			PREK_DETAILS="${output}"
			_cyt_prek_hook_unlock
			return "${exit_code}"
		else
			_cyt_prek_echo_err "Note: ${hook} may take several minutes."
		fi
	fi

	if [[ "${hook}" == "export-rust-sbom" ]]; then
		output=$(_run_hook_capture "export-rust-sbom" rtk bash scripts/deps/export-rust-sbom-precommit.sh) || exit_code=$?
		LAST_HOOK_RAW_OUTPUT="${output}"
		if $SHORT; then
			PREK_HOOK_STREAMED=false
		else
			PREK_HOOK_STREAMED=true
		fi
		if ((exit_code == 0)); then
			PREK_STATUSES="Passed"
		else
			PREK_STATUSES="Failed"
		fi
		PREK_DETAILS="${output}"
		_cyt_prek_hook_unlock
		return "${exit_code}"
	fi

	_cyt_prek_build_prek_cmd "${hook}"
	output=$(_run_hook_capture "${hook}" rtk "${PREK_RUN_CMD[@]}") || exit_code=$?
	LAST_HOOK_RAW_OUTPUT="${output}"
	if $SHORT; then
		PREK_HOOK_STREAMED=false
	else
		PREK_HOOK_STREAMED=true
	fi
	parse_prek_output "$output"
	if $CHANGED_ONLY && _cyt_prek_hook_skipped && ((exit_code == 0)); then
		_cyt_prek_hook_unlock
		return 0
	fi
	_cyt_prek_hook_unlock
	return "$exit_code"
}

_short_failure_details() {
	local hook="$1"
	local text="${PREK_DETAILS}"
	if [[ ${hook} == pytest-* ]]; then
		text="$(_cyt_prek_filter_hook_failure_output "${hook}" "${PREK_DETAILS}")"
	fi
	printf '%s\n' "${text}"
}

_hook_output_text() {
	local text="${PREK_DETAILS}"
	local log_path=""

	if [[ "${LAST_HOOK_RAW_OUTPUT}" == *"files were modified by this hook"* ]]; then
		printf '%s\n' "${LAST_HOOK_RAW_OUTPUT}"
		return 0
	fi
	if [[ "${text}" == *"files were modified by this hook"* ]]; then
		printf '%s\n' "${text}"
		return 0
	fi
	if [[ "${text}" =~ \[full\ output:\ \"([^\"]+)\"\] ]]; then
		log_path="${BASH_REMATCH[1]}"
		log_path="${log_path/#\$HOME/${HOME}}"
		if [[ -f "${log_path}" ]]; then
			cat "${log_path}"
		fi
	fi
}

hook_modified_files_only() {
	local blob
	blob="$(_hook_output_text)"
	[[ "${blob}" == *"files were modified by this hook"* ]]
}

run_hook_with_retry() {
	local hook="$1"
	local first_details=""

	if run_hook "$hook"; then
		return 0
	fi

	if ! $ONE_RUN || ! hook_modified_files_only; then
		return 1
	fi

	first_details="${PREK_DETAILS}"
	_cyt_prek_stage_fixes true || true
	if run_hook "$hook"; then
		return 0
	fi

	# Keep the first failure details when the retry also fails.
	if [[ -n ${first_details} ]]; then
		PREK_DETAILS="${first_details}

(retry after staging hook output also failed)"
	fi
	return 1
}

if ((total == 0)); then
	echo "Nothing to run."
	exit 0
fi

iteration=0
while true; do
	iteration=$((iteration + 1))
	passed=0 failed=0
	failed_hooks=()
	loop_header_printed=false

	if ! $SHORT; then
		_cyt_prek_echo "# LOOP $iteration"
	fi

	for hook in "${HOOKS[@]}"; do
		n=$((passed + failed + 1))
		hook_failed=false
		if ! $SHORT; then
			_cyt_prek_echo_err "Running [$n/$total] $hook ..."
		fi
		if run_hook_with_retry "$hook"; then
			passed=$((passed + 1))
			if $CHANGED_ONLY && _cyt_prek_hook_skipped; then
				result="Skipped"
			else
				result="Passed"
			fi
		else
			hook_failed=true
			failed=$((failed + 1))
			failed_hooks+=("$hook")
			result="Failed"
		fi

		if $FAIL_FAST && $hook_failed; then
			if $SHORT && ! $loop_header_printed; then
				if [[ -z ${CYT_PREK_QUIET_LOOP:-} ]]; then
					_cyt_prek_echo "# LOOP $iteration"
				fi
				loop_header_printed=true
			fi
			if [[ -n $PREK_STATUSES ]]; then
				_cyt_prek_echo "$PREK_STATUSES [$n/$total] $hook ($passed passed, $failed failed)"
			else
				_cyt_prek_echo "$result [$n/$total] $hook ($passed passed, $failed failed)"
			fi
			if [[ -n $PREK_DETAILS ]] && ! $PREK_HOOK_STREAMED; then
				_short_failure_details "${hook}" | "$SCRIPT_DIR/../lib/shorten-paths.sh" |
					while IFS= read -r line; do _cyt_prek_echo "${line}"; done
			fi
			if [[ -z ${CYT_PREK_QUIET_LOOP:-} ]]; then
				_cyt_prek_echo "Fail-fast: stopping after ${hook}"
			fi
			_cyt_prek_finish_group_staging
			exit 1
		fi

		if $SHORT && ! $hook_failed; then
			[[ ${PREK_GIT_ADD_MODE} == hook ]] && _cyt_prek_stage_after_hook
			continue
		fi

		if $SHORT && ! $loop_header_printed; then
			if [[ -z ${CYT_PREK_QUIET_LOOP:-} ]]; then
				_cyt_prek_echo "# LOOP $iteration"
			fi
			loop_header_printed=true
		fi

		if [[ -n $PREK_STATUSES ]]; then
			_cyt_prek_echo "$PREK_STATUSES [$n/$total] $hook ($passed passed, $failed failed)"
		else
			_cyt_prek_echo "$result [$n/$total] $hook ($passed passed, $failed failed)"
		fi
		if [[ -n $PREK_DETAILS ]] && ! $PREK_HOOK_STREAMED; then
			_short_failure_details "${hook}" | "$SCRIPT_DIR/../lib/shorten-paths.sh" |
				while IFS= read -r line; do _cyt_prek_echo "${line}"; done
		fi
		[[ ${PREK_GIT_ADD_MODE} == hook ]] && _cyt_prek_stage_after_hook
	done

	if ((failed == 0)); then
		_cyt_prek_finish_group_staging
		if ! $SHORT; then
			_cyt_prek_echo
			_cyt_prek_echo "Loop $iteration: $passed passed, $failed failed."
			_cyt_prek_echo "All $total hooks passed."
		fi
		exit 0
	fi
	if [[ -z ${CYT_PREK_QUIET_LOOP:-} ]]; then
		_cyt_prek_echo
		_cyt_prek_echo "Loop $iteration: $passed passed, $failed failed."
		_cyt_prek_echo "Failures: ${failed_hooks[*]}"
	fi
	_cyt_prek_finish_group_staging
	if $ONE_RUN; then
		exit 1
	fi
	_cyt_prek_echo "Re-running..."
	if ! $SHORT; then
		_cyt_prek_echo
	fi
done
