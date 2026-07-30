#!/usr/bin/env bash
# shellcheck shell=bash
# Tag-pinned git worktrees for chunk-your-tools / chunk-your-skills submodules.
#
# Submodule checkouts (chunk-your-tools/, chunk-your-skills/) stay on main for
# day-to-day editing. sync-version adds sibling worktrees at:
#   chunk-your-tools-vX.Y.Z/
#   chunk-your-skills-vX.Y.Z/
# checked out at the Cargo.toml dependency tags.

if [[ -z "${CHUNK_WORKTREE_LIB_SOURCED:-}" ]]; then
	CHUNK_WORKTREE_LIB_SOURCED=1

	CARGO_INDEXER_TOML_REL="sdk/rust/cyt-indexer/Cargo.toml"

	read_cargo_dep_version() {
		local crate="$1"
		local cargo_toml="${2:-}"
		local line version=""

		[[ -n "${cargo_toml}" && -f "${cargo_toml}" ]] || return 0

		line="$(
			grep -E "^[[:space:]]*${crate}[[:space:]]*=" "${cargo_toml}" |
				head -1 ||
				true
		)"
		[[ -n "${line}" ]] || return 0

		if [[ "${line}" =~ version[[:space:]]*=[[:space:]]*\"([^\"]+)\" ]]; then
			version="${BASH_REMATCH[1]}"
		elif [[ "${line}" =~ =[[:space:]]*\"([^\"]+)\" ]]; then
			version="${BASH_REMATCH[1]}"
		fi

		if [[ -n "${version}" ]]; then
			printf '%s\n' "${version}"
		fi
	}

	chunk_worktree_dir() {
		local root="$1"
		local name="$2"
		local version="$3"
		printf '%s/%s-v%s' "${root}" "${name}" "${version}"
	}

	chunk_submodule_dir() {
		local root="$1"
		local name="$2"
		printf '%s/%s' "${root}" "${name}"
	}

	resolve_chunk_pin_dir() {
		local root="$1"
		local crate="$2"
		local cargo_toml="${root}/${CARGO_INDEXER_TOML_REL}"
		local version pin_dir sub_dir

		version="$(read_cargo_dep_version "${crate}" "${cargo_toml}")"
		[[ -n "${version}" ]] || return 1

		pin_dir="$(chunk_worktree_dir "${root}" "${crate}" "${version}")"
		if [[ -d "${pin_dir}" ]]; then
			printf '%s\n' "${pin_dir}"
			return 0
		fi

		sub_dir="$(chunk_submodule_dir "${root}" "${crate}")"
		if [[ -e "${sub_dir}/.git" ]]; then
			printf '%s\n' "${sub_dir}"
			return 0
		fi

		return 1
	}

	resolve_chunk_pin_rel() {
		local root="$1"
		local crate="$2"
		local abs

		abs="$(resolve_chunk_pin_dir "${root}" "${crate}")" || return 1
		if [[ "${abs}" == "${root}/"* ]]; then
			printf '%s\n' "${abs#"${root}/"}"
		else
			printf '%s\n' "${abs}"
		fi
	}

	chunk_submodule_git() {
		local dir="$1"
		shift
		env -u GIT_INDEX_FILE -u GIT_DIR -u GIT_WORK_TREE git -C "${dir}" "$@"
	}

	chunk_submodule_has_local_changes() {
		local dir="$1"
		! chunk_submodule_git "${dir}" diff-index --quiet HEAD -- 2>/dev/null ||
			! chunk_submodule_git "${dir}" diff-index --quiet --cached HEAD -- 2>/dev/null
	}

	chunk_worktree_at_tag() {
		local worktree_dir="$1"
		local repo_dir="$2"
		local tag="$3"
		local wt_commit target

		[[ -d "${worktree_dir}" ]] || return 1
		if ! chunk_submodule_git "${repo_dir}" rev-parse --verify "${tag}^{commit}" >/dev/null 2>&1; then
			return 1
		fi

		wt_commit="$(chunk_submodule_git "${worktree_dir}" rev-parse 'HEAD^{commit}' 2>/dev/null || true)"
		target="$(chunk_submodule_git "${repo_dir}" rev-parse "${tag}^{commit}")"
		[[ -n "${wt_commit}" && "${wt_commit}" == "${target}" ]]
	}

	chunk_remove_worktree_path() {
		local repo_dir="$1"
		local worktree_dir="$2"

		if chunk_submodule_git "${repo_dir}" worktree list --porcelain 2>/dev/null |
			grep -Fxq "worktree ${worktree_dir}"; then
			chunk_submodule_git "${repo_dir}" worktree remove --force "${worktree_dir}" 2>/dev/null ||
				chunk_submodule_git "${repo_dir}" worktree remove "${worktree_dir}" 2>/dev/null ||
				true
		fi
		rm -rf "${worktree_dir}"
		chunk_submodule_git "${repo_dir}" worktree prune 2>/dev/null || true
	}

	chunk_prune_stale_worktrees() {
		local root="$1"
		local name="$2"
		local repo_dir="$3"
		local current_version="$4"
		local dir keep

		keep="$(chunk_worktree_dir "${root}" "${name}" "${current_version}")"
		for dir in "${root}/${name}"-v*; do
			[[ -d "${dir}" ]] || continue
			[[ "${dir}" == "${keep}" ]] && continue
			chunk_remove_worktree_path "${repo_dir}" "${dir}"
		done
	}

	chunk_ensure_submodule_development_branch() {
		local name="$1"
		local dir="$2"
		local branch

		[[ -e "${dir}/.git" ]] || return 0
		if chunk_submodule_has_local_changes "${dir}"; then
			return 0
		fi
		if chunk_submodule_git "${dir}" symbolic-ref -q HEAD >/dev/null 2>&1; then
			return 0
		fi

		for branch in main master; do
			if chunk_submodule_git "${dir}" show-ref --verify --quiet "refs/heads/${branch}"; then
				if chunk_submodule_git "${dir}" checkout "${branch}" >/dev/null 2>&1; then
					if declare -F shorten_paths >/dev/null 2>&1; then
						printf 'submodule %s: restored %s for development\n' "${name}" "${branch}" |
							shorten_paths
					else
						printf 'submodule %s: restored %s for development\n' "${name}" "${branch}"
					fi
					return 0
				fi
			fi
		done
	}

	chunk_sync_worktree() {
		local root="$1"
		local name="$2"
		local submodule_dir="$3"
		local version="$4"
		local tag worktree_dir

		if [[ -z "${version}" ]]; then
			return 0
		fi

		tag="v${version}"
		worktree_dir="$(chunk_worktree_dir "${root}" "${name}" "${version}")"

		if [[ ! -e "${submodule_dir}/.git" ]]; then
			if declare -F shorten_paths >/dev/null 2>&1; then
				printf 'warning: submodule %s not initialized; skipping worktree %s\n' \
					"${name}" "${tag}" | shorten_paths >&2
			else
				printf 'warning: submodule %s not initialized; skipping worktree %s\n' \
					"${name}" "${tag}" >&2
			fi
			return 0
		fi

		if ! chunk_submodule_git "${submodule_dir}" rev-parse --verify "${tag}^{commit}" >/dev/null 2>&1; then
			if ! chunk_submodule_git "${submodule_dir}" fetch origin --tags 2>/dev/null; then
				if declare -F shorten_paths >/dev/null 2>&1; then
					printf 'warning: %s git fetch failed; using local tags only\n' \
						"${name}" | shorten_paths >&2
				else
					printf 'warning: %s git fetch failed; using local tags only\n' \
						"${name}" >&2
				fi
			fi
		fi

		if ! chunk_submodule_git "${submodule_dir}" rev-parse --verify "${tag}^{commit}" >/dev/null 2>&1; then
			if declare -F shorten_paths >/dev/null 2>&1; then
				printf 'error: %s missing tag %s\n' "${name}" "${tag}" | shorten_paths >&2
			else
				printf 'error: %s missing tag %s\n' "${name}" "${tag}" >&2
			fi
			return 1
		fi

		if chunk_worktree_at_tag "${worktree_dir}" "${submodule_dir}" "${tag}"; then
			if declare -F shorten_paths >/dev/null 2>&1; then
				printf 'worktree %s already at %s\n' "${name}" "${tag}" | shorten_paths
			else
				printf 'worktree %s already at %s\n' "${name}" "${tag}"
			fi
			chunk_prune_stale_worktrees "${root}" "${name}" "${submodule_dir}" "${version}"
			chunk_ensure_submodule_development_branch "${name}" "${submodule_dir}"
			return 0
		fi

		if [[ -e "${worktree_dir}" ]] ||
			chunk_submodule_git "${submodule_dir}" worktree list 2>/dev/null |
			grep -Fq "${worktree_dir}"; then
			chunk_remove_worktree_path "${submodule_dir}" "${worktree_dir}"
		fi

		chunk_submodule_git "${submodule_dir}" worktree add "${worktree_dir}" "${tag}"
		if declare -F shorten_paths >/dev/null 2>&1; then
			printf 'synced worktree %s -> %s (%s)\n' "${name}" "${worktree_dir}" "${tag}" | shorten_paths
		else
			printf 'synced worktree %s -> %s (%s)\n' "${name}" "${worktree_dir}" "${tag}"
		fi

		chunk_prune_stale_worktrees "${root}" "${name}" "${submodule_dir}" "${version}"
		chunk_ensure_submodule_development_branch "${name}" "${submodule_dir}"
	}

	chunk_write_worktree_manifest() {
		local root="$1"
		local cargo_toml="${root}/${CARGO_INDEXER_TOML_REL}"
		local manifest_dir="${root}/search"
		local manifest_file="${manifest_dir}/.chunk-worktrees"
		local tools_version skills_version

		mkdir -p "${manifest_dir}"
		tools_version="$(read_cargo_dep_version "chunk-your-tools" "${cargo_toml}")"
		skills_version="$(read_cargo_dep_version "chunk-your-skills" "${cargo_toml}")"

		{
			[[ -n "${tools_version}" ]] &&
				printf 'chunk-your-tools=%s\n' "chunk-your-tools-v${tools_version}"
			[[ -n "${skills_version}" ]] &&
				printf 'chunk-your-skills=%s\n' "chunk-your-skills-v${skills_version}"
		} >"${manifest_file}"
	}

	chunk_write_cargo_patch_config() {
		local root="$1"
		local cargo_toml="${root}/${CARGO_INDEXER_TOML_REL}"
		local config_dir="${root}/.cargo"
		local config_file="${config_dir}/config.toml"
		local tools_version skills_version
		local -a patches=()

		tools_version="$(read_cargo_dep_version "chunk-your-tools" "${cargo_toml}")"
		skills_version="$(read_cargo_dep_version "chunk-your-skills" "${cargo_toml}")"

		if [[ -n "${tools_version}" ]]; then
			local tools_worktree
			tools_worktree="$(chunk_worktree_dir "${root}" "chunk-your-tools" "${tools_version}")"
			if [[ -f "${tools_worktree}/Cargo.toml" ]]; then
				patches+=("chunk-your-tools = { path = \"chunk-your-tools-v${tools_version}\" }")
			fi
		fi

		if [[ -n "${skills_version}" ]]; then
			local skills_worktree
			skills_worktree="$(chunk_worktree_dir "${root}" "chunk-your-skills" "${skills_version}")"
			if [[ -f "${skills_worktree}/Cargo.toml" ]]; then
				patches+=("chunk-your-skills = { path = \"chunk-your-skills-v${skills_version}\" }")
			fi
		fi

		if ((${#patches[@]} == 0)); then
			[[ -f "${config_file}" ]] && rm -f "${config_file}"
			return 0
		fi

		mkdir -p "${config_dir}"
		{
			echo "# Generated by scripts/publish/sync-version.sh — do not edit."
			echo "[patch.crates-io]"
			for patch in "${patches[@]}"; do
				echo "${patch}"
			done
		} >"${config_file}"
	}

	chunk_sync_worktrees_from_cargo() {
		local root="$1"
		local cargo_toml="${root}/${CARGO_INDEXER_TOML_REL}"
		local tools_version skills_version

		tools_version="$(read_cargo_dep_version "chunk-your-tools" "${cargo_toml}")"
		skills_version="$(read_cargo_dep_version "chunk-your-skills" "${cargo_toml}")"

		if [[ -z "${tools_version}" && -z "${skills_version}" ]]; then
			return 0
		fi

		if [[ -n "${tools_version}" ]]; then
			chunk_sync_worktree "${root}" "chunk-your-tools" \
				"$(chunk_submodule_dir "${root}" "chunk-your-tools")" "${tools_version}"
		fi
		if [[ -n "${skills_version}" ]]; then
			chunk_sync_worktree "${root}" "chunk-your-skills" \
				"$(chunk_submodule_dir "${root}" "chunk-your-skills")" "${skills_version}"
		fi

		chunk_write_worktree_manifest "${root}"
		chunk_write_cargo_patch_config "${root}"
	}

fi
