#!/usr/bin/env bash
# Monorepo parallel gate: rust → SDK c/go/ts → py → optional uni/release.
#
# Usage:
#   ./scripts/pre-commit-hooks/prek-loop-all-parallel.sh [--short] [--one-run]
#          [--no-git-add] [--fail-fast] [--xdist] [--changed-only]
#          [--mode full|publish|rust-only|py-only]
#
# Modes:
#   full (default)  — verify-pins, rust-parallel, sdk c/go/ts, py-parallel
#   publish         — full + prek-loop -g uni release (pre-publish smoke)
#   rust-only       — rust-parallel only
#   py-only         — py-parallel only
#
# Used by workflow.sh all|core-rust|app-test and publish-git.sh.
# Logs: target/.prek-parallel-logs/
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd -P)"
RUST_PARALLEL="${SCRIPT_DIR}/prek-loop-rust-parallel.sh"
PY_PARALLEL="${SCRIPT_DIR}/prek-loop-py-parallel.sh"
PREK_LOOP="${SCRIPT_DIR}/prek-loop.sh"
HELPERS="${ROOT}/scripts/local/dev/helpers.sh"

MODE=full
FORWARD_ARGS=(--one-run --no-git-add --git-add none)
USE_XDIST=false
GATE_SHORT=false

while (($#)); do
	case "$1" in
	--short)
		GATE_SHORT=true
		FORWARD_ARGS+=(--short)
		shift
		;;
	--one-run | --no-git-add | --fail-fast | --changed-only)
		FORWARD_ARGS+=("$1")
		shift
		;;
	--git-add)
		shift
		if ((${#} == 0)) || [[ ${1:-} == -* ]]; then
			echo "--git-add requires orchestrator or group." >&2
			exit 1
		fi
		FORWARD_ARGS+=(--git-add "$1")
		shift
		;;
	--xdist)
		USE_XDIST=true
		shift
		;;
	--mode)
		shift
		if ((${#} == 0)) || [[ ${1:-} == -* ]]; then
			echo "--mode requires full, publish, rust-only, or py-only." >&2
			exit 1
		fi
		MODE="$1"
		shift
		;;
	--full)
		MODE=full
		shift
		;;
	--publish)
		MODE=publish
		shift
		;;
	--rust-only)
		MODE=rust-only
		shift
		;;
	--py-only)
		MODE=py-only
		shift
		;;
	-h | --help)
		cat <<EOF
Usage: $(basename "$0") [--short] [--one-run] [--no-git-add] [--fail-fast] [--xdist] [--changed-only]
                          [--mode full|publish|rust-only|py-only]

Monorepo parallel gate for workflow.sh and publish-git.sh.

Modes:
  full (default)  verify-pins → rust-parallel → sdk c/go/ts → py-parallel
  publish         full + prek-loop -g uni release
  rust-only       rust-parallel only
  py-only         py-parallel only

Parallel logs: ${ROOT}/target/.prek-parallel-logs/
Failure summary: ${ROOT}/target/.prek-parallel-logs/failures.log

Shard tuning: PREK_RUST_UNIT_SHARDS, PREK_PYTEST_UNIT_SHARDS (auto by default).
Optional --xdist enables pytest -n auto inside each Python shard.

See also:
  ${RUST_PARALLEL} --help
  ${PY_PARALLEL} --help
EOF
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

case "${MODE}" in
full | publish | rust-only | py-only) ;;
*)
	echo "error: --mode must be full, publish, rust-only, or py-only (got: ${MODE})" >&2
	exit 1
	;;
esac

if [[ -n ${CYT_LOCAL_DEV_SHORT:-} && ${GATE_SHORT} == false ]]; then
	FORWARD_ARGS+=(--short)
	GATE_SHORT=true
fi

_run_rust_parallel() {
	local -a args=("${FORWARD_ARGS[@]}")
	if ! $GATE_SHORT; then
		echo "==> rust parallel gate"
	fi
	bash "${RUST_PARALLEL}" "${args[@]}"
}

_run_py_parallel() {
	local -a args=("${FORWARD_ARGS[@]}")
	if $USE_XDIST; then
		args+=(--xdist)
	fi
	if ! $GATE_SHORT; then
		echo "==> python parallel gate"
	fi
	bash "${PY_PARALLEL}" "${args[@]}"
}

_run_sdk_builds() {
	# shellcheck disable=SC1091
	source "${HELPERS}"
	export CYT_REPO_ROOT="${ROOT}"
	if [[ -n ${CYT_LOCAL_DEV_SHORT:-} ]]; then
		export CYT_LOCAL_DEV_SHORT=1
	fi
	if ! $GATE_SHORT; then
		echo "==> SDK: C"
	fi
	cyt_build_sdk_c
	if ! $GATE_SHORT; then
		echo "==> SDK: Go"
	fi
	cyt_build_sdk_go
	if ! $GATE_SHORT; then
		echo "==> SDK: TypeScript"
	fi
	cyt_build_sdk_typescript
}

_run_verify_pins() {
	# shellcheck disable=SC1091
	source "${HELPERS}"
	export CYT_REPO_ROOT="${ROOT}"
	if [[ -n ${CYT_LOCAL_DEV_SHORT:-} ]]; then
		export CYT_LOCAL_DEV_SHORT=1
	fi
	cyt_verify_dependency_pins
}

_run_publish_extras() {
	local -a args=("${FORWARD_ARGS[@]}")
	if ! $GATE_SHORT; then
		echo "==> prek-loop -g uni release"
	fi
	bash "${PREK_LOOP}" "${args[@]}" -g uni release
}

cd "${ROOT}"

case "${MODE}" in
rust-only)
	_run_rust_parallel
	;;
py-only)
	_run_py_parallel
	;;
full | publish)
	_run_verify_pins
	_run_rust_parallel
	_run_sdk_builds
	_run_py_parallel
	if [[ ${MODE} == publish ]]; then
		_run_publish_extras
	fi
	;;
esac

if ! $GATE_SHORT; then
	echo "All parallel gate phases passed (mode: ${MODE})."
fi
