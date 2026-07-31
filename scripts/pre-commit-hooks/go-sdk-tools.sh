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
	local bin_dir bin_path

	bin_dir="$(go_sdk_tools_path)"
	bin_path="${bin_dir}/${bin_name}"

	GO111MODULE=on go install "${install_spec}"
	if [[ ! -x "${bin_path}" ]]; then
		echo "go-sdk-tools: ${install_spec} did not produce ${bin_path}" >&2
		exit 1
	fi
	printf '%s\n' "${bin_path}"
}
