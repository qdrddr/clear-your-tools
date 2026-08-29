#!/usr/bin/env bash
# Pinned Go SDK dev-tool versions (installed via go install, not sdk/go/go.mod).
#
# Kept outside the runtime module so Snyk and license audits on sdk/go stay minimal.
# Bump these manually when refreshing linters; not tied to app semver in sync-version.sh.
set -euo pipefail

# shellcheck disable=SC2034
GO_SDK_TOOL_GOFUMPT='mvdan.cc/gofumpt@v0.11.0'
# shellcheck disable=SC2034
GO_SDK_TOOL_GOIMPORTS='golang.org/x/tools/cmd/goimports@v0.49.0'
# shellcheck disable=SC2034
GO_SDK_TOOL_STATICCHECK='honnef.co/go/tools/cmd/staticcheck@v0.8.0'
# shellcheck disable=SC2034
GO_SDK_TOOL_GOCRITIC='github.com/go-critic/go-critic/cmd/gocritic@v0.14.4'
# go-critic v0.14.4 still pins x/tools v0.38.0, which cannot read Go 1.27 export data.
# shellcheck disable=SC2034
GO_SDK_TOOL_GOCRITIC_XTOOLS='golang.org/x/tools@v0.49.0'
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

go_sdk_tool_bin_path() {
	local bin_dir="$1"
	local bin_name="$2"

	if [[ -x "${bin_dir}/${bin_name}" ]]; then
		printf '%s\n' "${bin_dir}/${bin_name}"
		return 0
	fi
	if [[ -x "${bin_dir}/${bin_name}.exe" ]]; then
		printf '%s\n' "${bin_dir}/${bin_name}.exe"
		return 0
	fi
	return 1
}

go_sdk_tool_pin_file() {
	local bin_dir="$1"
	local bin_name="$2"
	printf '%s/.%s.pin' "${bin_dir}" "${bin_name}"
}

go_sdk_current_go_version() {
	go version | awk '{print $3}'
}

go_sdk_tool_pin_is_current() {
	local pin_file="$1"
	local install_spec="$2"
	local extra_pin="${3:-}"

	[[ -f "${pin_file}" ]] || return 1
	# shellcheck disable=SC1090
	source "${pin_file}"
	[[ "${GO_SDK_TOOL_INSTALL_SPEC:-}" == "${install_spec}" ]] || return 1
	[[ "${GO_SDK_TOOL_GO_VERSION:-}" == "$(go_sdk_current_go_version)" ]] || return 1
	[[ "${GO_SDK_TOOL_EXTRA_PIN:-}" == "${extra_pin}" ]] || return 1
}

go_sdk_write_tool_pin() {
	local pin_file="$1"
	local install_spec="$2"
	local extra_pin="${3:-}"

	cat >"${pin_file}" <<EOF
GO_SDK_TOOL_INSTALL_SPEC='${install_spec}'
GO_SDK_TOOL_GO_VERSION='$(go_sdk_current_go_version)'
GO_SDK_TOOL_EXTRA_PIN='${extra_pin}'
EOF
}

go_sdk_install_with_lock() {
	local install_fn="$1"
	shift
	local bin_dir lock_file lock_wait=0

	bin_dir="$(go_sdk_tools_path)"
	lock_file="${bin_dir}/.go-sdk-tool-install.lock"
	mkdir -p "${bin_dir}"
	(
		if command -v flock >/dev/null 2>&1; then
			flock -x 200
		else
			while ! mkdir "${lock_file}.d" 2>/dev/null; do
				sleep 0.05
				lock_wait=$((lock_wait + 1))
				if ((lock_wait > 600)); then
					echo "go-sdk-tools: timed out waiting for install lock" >&2
					exit 1
				fi
			done
			trap 'rmdir "${lock_file}.d" 2>/dev/null || true' EXIT
		fi
		"${install_fn}" "$@"
	) 200>"${lock_file}"
}

_install_go_sdk_tool() {
	local bin_name="$1"
	local install_spec="$2"
	local bin_dir bin_path pin_file

	bin_dir="$(go_sdk_tools_path)"
	bin_path="$(go_sdk_tool_bin_path "${bin_dir}" "${bin_name}")" || bin_path="${bin_dir}/${bin_name}"
	pin_file="$(go_sdk_tool_pin_file "${bin_dir}" "${bin_name}")"

	if [[ -x "${bin_path}" ]] && go_sdk_tool_pin_is_current "${pin_file}" "${install_spec}"; then
		return 0
	fi

	rm -f "${bin_path}" "${bin_path}.exe" "${pin_file}"
	GO111MODULE=on go install "${install_spec}"
	bin_path="$(go_sdk_tool_bin_path "${bin_dir}" "${bin_name}")" || {
		echo "go-sdk-tools: ${install_spec} did not produce ${bin_dir}/${bin_name}" >&2
		return 1
	}
	go_sdk_write_tool_pin "${pin_file}" "${install_spec}"
}

_install_go_critic_tool() {
	local bin_dir bin_path pin_file tmpdir
	local install_spec="${GO_SDK_TOOL_GOCRITIC}"
	local extra_pin="${GO_SDK_TOOL_GOCRITIC_XTOOLS}"

	bin_dir="$(go_sdk_tools_path)"
	bin_path="$(go_sdk_tool_bin_path "${bin_dir}" gocritic)" || bin_path="${bin_dir}/gocritic"
	pin_file="$(go_sdk_tool_pin_file "${bin_dir}" gocritic)"

	if [[ -x "${bin_path}" ]] &&
		go_sdk_tool_pin_is_current "${pin_file}" "${install_spec}" "${extra_pin}"; then
		return 0
	fi

	rm -f "${bin_path}" "${bin_path}.exe" "${pin_file}"
	tmpdir="$(mktemp -d "${TMPDIR:-/tmp}/go-critic-install.XXXXXX")"
	(
		cd "${tmpdir}"
		go mod init go-critic-install
		go get "${install_spec}"
		go get "${extra_pin}"
		GOBIN="${bin_dir}" GO111MODULE=on go install github.com/go-critic/go-critic/cmd/gocritic
	)
	rm -rf "${tmpdir}"

	bin_path="$(go_sdk_tool_bin_path "${bin_dir}" gocritic)" || {
		echo "go-sdk-tools: ${install_spec} with ${extra_pin} did not produce gocritic" >&2
		return 1
	}
	go_sdk_write_tool_pin "${pin_file}" "${install_spec}" "${extra_pin}"
}

# Install (or refresh) a dev tool at the pinned module@version and print its binary path.
ensure_go_sdk_tool() {
	local bin_name="$1"
	local install_spec="$2"
	local bin_dir bin_path

	go_sdk_install_with_lock _install_go_sdk_tool "${bin_name}" "${install_spec}"
	bin_dir="$(go_sdk_tools_path)"
	bin_path="$(go_sdk_tool_bin_path "${bin_dir}" "${bin_name}")" || {
		echo "go-sdk-tools: missing ${bin_name} after install (${install_spec})" >&2
		exit 1
	}
	printf '%s\n' "${bin_path}"
}

ensure_go_critic_tool() {
	local bin_dir bin_path

	go_sdk_install_with_lock _install_go_critic_tool
	bin_dir="$(go_sdk_tools_path)"
	bin_path="$(go_sdk_tool_bin_path "${bin_dir}" gocritic)" || {
		echo "go-sdk-tools: missing gocritic after install (${GO_SDK_TOOL_GOCRITIC})" >&2
		exit 1
	}
	printf '%s\n' "${bin_path}"
}
