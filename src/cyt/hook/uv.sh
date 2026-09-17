#!/usr/bin/env bash
# Run cyt with consumer workspace env. Shipped with clear-your-tools (installed or dev).
set -euo pipefail

git_workspace_root() {
  local dir="$1"
  while [[ -n "${dir}" && "${dir}" != "/" ]]; do
    if [[ -e "${dir}/.git" ]]; then
      (cd "${dir}" && pwd -P)
      return 0
    fi
    dir="$(dirname "${dir}")"
  done
  return 1
}

find_cyt_dev_repo() {
  local dir="$1"
  while [[ -n "${dir}" && "${dir}" != "/" ]]; do
    if [[ -f "${dir}/pyproject.toml" ]] \
      && grep -q 'name = "clear-your-tools"' "${dir}/pyproject.toml" 2>/dev/null; then
      (cd "${dir}" && pwd -P)
      return 0
    fi
    dir="$(dirname "${dir}")"
  done
  return 1
}

script_is_packaged_cyt_hook() {
  local script_dir="$1"
  case "${script_dir}" in
    */site-packages/cyt/hook|*/src/cyt/hook) return 0 ;;
    *) return 1 ;;
  esac
}

append_hook_workspace_args() {
  local workspace="$1"
  shift
  local -a cyt_args=("$@")
  if [[ -z "${workspace}" || ${#cyt_args[@]} -lt 2 || "${cyt_args[0]}" != "hook" ]]; then
    printf '%s\n' "${cyt_args[@]}"
    return
  fi
  for arg in "${cyt_args[@]}"; do
    if [[ "${arg}" == "--workspace" ]]; then
      printf '%s\n' "${cyt_args[@]}"
      return
    fi
  done
  printf '%s\n' "${cyt_args[@]}" "--workspace" "${workspace}"
}

read_cyt_invocation_sidecar() {
  local sidecar="${1}/cyt-invocation.json"
  if [[ ! -f "${sidecar}" ]]; then
    return 1
  fi
  printf '%s\n' "${sidecar}"
}

load_cyt_sidecar_field() {
  local sidecar="$1"
  local field="$2"
  local default="${3:-}"
  python -c 'import json, sys; data=json.load(open(sys.argv[1], encoding="utf-8")); print(data.get(sys.argv[2], sys.argv[3]))' \
    "${sidecar}" "${field}" "${default}"
}

resolve_absolute_path() {
  local path="$1"
  python -c 'import pathlib, sys; print(pathlib.Path(sys.argv[1]).expanduser().resolve())' "${path}"
}

invoke_dev_repo() {
  local repo="$1"
  shift
  local -a args=("$@")
  local uses_dev_cli=false
  if [[ ${#args[@]} -gt 0 && "${args[0]}" == *cli/app.py ]]; then
    uses_dev_cli=true
  fi
  if [[ "${uses_dev_cli}" == true ]]; then
    exec uv run --directory "${repo}" "${args[@]}"
  fi
  exec uv run --directory "${repo}" src/cyt/cli/app.py "${args[@]}"
}

invoke_cyt_via_uv() {
  local -a args=("$@")
  if [[ ${#args[@]} -eq 0 ]]; then
    echo "Usage: uv.sh hook cursor  (or other cyt subcommands)" >&2
    exit 1
  fi

  local script_dir repo sidecar_path mode repo_root package executable
  script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

  if script_is_packaged_cyt_hook "${script_dir}" && repo="$(find_cyt_dev_repo "${script_dir}")"; then
    invoke_dev_repo "${repo}" "${args[@]}"
  fi

  if sidecar_path="$(read_cyt_invocation_sidecar "${script_dir}")"; then
    mode="$(load_cyt_sidecar_field "${sidecar_path}" mode "")"
    repo_root="$(load_cyt_sidecar_field "${sidecar_path}" repo_root "")"
    package="$(load_cyt_sidecar_field "${sidecar_path}" package "clear-your-tools")"
    executable="$(load_cyt_sidecar_field "${sidecar_path}" executable "cyt")"
    if [[ "${mode}" == "dev" && -n "${repo_root}" && -d "${repo_root}" ]]; then
      invoke_dev_repo "${repo_root}" "${args[@]}"
    fi
    exec uv tool run --from "${package}" "${executable}" "${args[@]}"
  fi

  if [[ -n "${CYT_DEV_REPO:-}" ]]; then
    if [[ -d "${CYT_DEV_REPO}" ]]; then
      invoke_dev_repo "${CYT_DEV_REPO}" "${args[@]}"
    fi
    echo "CYT_DEV_REPO is not a valid directory: ${CYT_DEV_REPO}" >&2
    exit 1
  fi

  exec uv tool run --from clear-your-tools cyt "${args[@]}"
}

shell_workspace="$(git_workspace_root "$(pwd)" || true)"
if [[ -n "${shell_workspace}" ]]; then
  export CYT_SHELL_WORKSPACE="${shell_workspace}"
fi

terminal_workspace="${CYT_WORKSPACE:-}"
mapfile -t cyt_args < <(append_hook_workspace_args "${shell_workspace}" "$@")

if [[ -n "${terminal_workspace}" && "${terminal_workspace}" != \$\{*\} ]]; then
  if [[ "${terminal_workspace}" == ~* || "${terminal_workspace}" == . || "${terminal_workspace}" == .. || "${terminal_workspace}" == ./* || "${terminal_workspace}" == ../* ]]; then
    echo "CYT_WORKSPACE must be a full absolute path (not ./, ../, or ~): ${terminal_workspace}" >&2
    exit 1
  fi
  terminal_resolved="$(resolve_absolute_path "${terminal_workspace}")"
  if [[ -n "${shell_workspace}" && "${terminal_resolved}" != "${shell_workspace}" ]]; then
    echo "CYT workspace conflict: terminal CYT_WORKSPACE=${terminal_resolved} vs shell CYT_SHELL_WORKSPACE=${shell_workspace}. Use --workspace to override or align .vscode/settings.json CYT_WORKSPACE." >&2
    exit 1
  fi
fi

invoke_cyt_via_uv "${cyt_args[@]}"
