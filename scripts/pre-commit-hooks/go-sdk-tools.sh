#!/usr/bin/env bash
# Pinned Go SDK dev-tool versions (installed via go install, not sdk/go/go.mod).
#
# Kept outside the runtime module so Snyk and license audits on sdk/go stay minimal.
# Bump these manually when refreshing linters; not tied to app semver in sync-version.sh.
set -euo pipefail

# shellcheck disable=SC2034
GO_SDK_TOOL_GOFUMPT='mvdan.cc/gofumpt@v0.11.0'
# shellcheck disable=SC2034
GO_SDK_TOOL_GOIMPORTS='golang.org/x/tools/cmd/goimports@v0.48.0'
# shellcheck disable=SC2034
GO_SDK_TOOL_STATICCHECK='honnef.co/go/tools/cmd/staticcheck@v0.7.0'
# shellcheck disable=SC2034
GO_SDK_TOOL_GOCRITIC='github.com/go-critic/go-critic/cmd/gocritic@v0.14.4'
# shellcheck disable=SC2034
GO_SDK_TOOL_GOSEC='github.com/securego/gosec/v2/cmd/gosec@v2.28.0'

go_sdk_tools_bin_dir() {
	if [[ -n "${GOBIN:-}" ]]; then
		printf '%s\n' "${GOBIN}"
		return 0
	fi
	printf '%s/bin\n' "${GOPATH:-${HOME}/go}"
}

go_sdk_tools_path() {
	local bin_dir
	bin_dir="$(go_sdk_tools_bin_dir)"
	mkdir -p "${bin_dir}"
	printf '%s\n' "${bin_dir}"
}

# Install (or refresh) a dev tool at the pinned module@version and print its binary path.
ensure_go_sdk_tool() {
	local bin_name="$1"
	local install_spec="$2"
	local bin_dir bin_path lock_file

	bin_dir="$(go_sdk_tools_path)"
	bin_path="${bin_dir}/${bin_name}"
	lock_file="${bin_dir}/.go-sdk-tool-install.lock"

	if [[ -x "${bin_path}" ]]; then
		printf '%s\n' "${bin_path}"
		return 0
	fi

	mkdir -p "${bin_dir}"
	(
		if command -v flock >/dev/null 2>&1; then
			flock -x 200
		else
			# Portable lock when flock is unavailable (e.g. Windows Git Bash).
			lock_wait=0
			while ! mkdir "${lock_file}.d" 2>/dev/null; do
				sleep 0.05
				lock_wait=$((lock_wait + 1))
				if ((lock_wait > 600)); then
					echo "go-sdk-tools: timed out waiting for ${install_spec} install lock" >&2
					exit 1
				fi
			done
			trap 'rmdir "${lock_file}.d" 2>/dev/null || true' EXIT
		fi
		if [[ ! -x "${bin_path}" ]]; then
			GO111MODULE=on go install "${install_spec}"
		fi
	) 200>"${lock_file}"

	if [[ ! -x "${bin_path}" ]]; then
		echo "go-sdk-tools: ${install_spec} did not produce ${bin_path}" >&2
		exit 1
	fi
	printf '%s\n' "${bin_path}"
}
