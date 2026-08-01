#!/usr/bin/env bash
# shellcheck shell=bash
# Tag-pinned git worktrees for chunk-your-tools / chunk-your-skills submodules.
#
# Submodule checkouts (chunk-your-tools/, chunk-your-skills/) stay on main for
# day-to-day editing. sync-version adds sibling worktrees at:
#   chunk-your-tools-vX.Y.Z/
#   chunk-your-skills-vX.Y.Z/
# checked out at the Cargo.toml dependency tags.
#
# Policy: scripts may create or remove worktrees and write repo-level metadata
# (Cargo.toml [patch.crates-io], search/.chunk-worktrees). They must not read source files
# from worktrees/submodules, use worktree paths as inputs to other tooling, or
# modify files inside submodule or worktree checkouts (including git checkout in
# the submodule working tree).

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

	chunk_submodule_git() {
		local dir="$1"
		shift
		env -u GIT_INDEX_FILE -u GIT_DIR -u GIT_WORK_TREE git -C "${dir}" "$@"
	}

	chunk_worktree_registered_at_tag() {
		local worktree_dir="$1"
		local repo_dir="$2"
		local tag="$3"
		local wt_commit target

		[[ -d "${worktree_dir}" ]] || return 1
		if ! chunk_submodule_git "${repo_dir}" rev-parse --verify "${tag}^{commit}" >/dev/null 2>&1; then
			return 1
		fi

		# Git metadata only — never read source files under the worktree tree.
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

		if chunk_worktree_registered_at_tag "${worktree_dir}" "${submodule_dir}" "${tag}"; then
			if declare -F shorten_paths >/dev/null 2>&1; then
				printf 'worktree %s already at %s\n' "${name}" "${tag}" | shorten_paths
			else
				printf 'worktree %s already at %s\n' "${name}" "${tag}"
			fi
			chunk_prune_stale_worktrees "${root}" "${name}" "${submodule_dir}" "${version}"
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

	WORKSPACE_CARGO_TOML_REL="Cargo.toml"

	# Workspace [patch.crates-io] applies only to this repo's Cargo workspace (cyt-indexer).
	# Do not use .cargo/config.toml — Cargo walks up the directory tree, so config.toml
	# patches would leak into chunk-your-tools/ and chunk-your-skills/ submodules and
	# append [[patch.unused]] to their Cargo.lock files.
	chunk_strip_workspace_patches() {
		local workspace_toml="$1"
		awk '
			BEGIN { skip = 0; n = 0 }
			/^\[patch\.crates-io\]/ { skip = 1; next }
			skip && /^\[/ { skip = 0 }
			/^# Generated by scripts\/publish\/sync-version\.sh/ { next }
			/^# Patched root cargo/ { next }
			/^# Use scripts\/local\/dev\/cargo-/ { next }
			skip { next }
			{ lines[++n] = $0 }
			END {
				while (n > 0 && lines[n] ~ /^[[:space:]]*$/) {
					n--
				}
				for (i = 1; i <= n; i++) {
					print lines[i]
				}
			}
		' "${workspace_toml}"
	}

	chunk_write_workspace_cargo_patches() {
		local root="$1"
		local cargo_toml="${root}/${CARGO_INDEXER_TOML_REL}"
		local workspace_toml="${root}/${WORKSPACE_CARGO_TOML_REL}"
		local legacy_config="${root}/.cargo/config.toml"
		local tools_version skills_version
		local tmp

		[[ -f "${workspace_toml}" ]] || return 0

		tools_version="$(read_cargo_dep_version "chunk-your-tools" "${cargo_toml}")"
		skills_version="$(read_cargo_dep_version "chunk-your-skills" "${cargo_toml}")"

		tmp="$(mktemp)"
		chunk_strip_workspace_patches "${workspace_toml}" >"${tmp}"
		if [[ -n "${tools_version}" || -n "${skills_version}" ]]; then
			{
				cat "${tmp}"
				echo ""
				echo "[patch.crates-io]"
				# Alphabetical crate names — matches cargo-sort hook output.
				if [[ -n "${skills_version}" ]]; then
					echo "chunk-your-skills = { path = \"chunk-your-skills-v${skills_version}\" }"
				fi
				if [[ -n "${tools_version}" ]]; then
					echo "chunk-your-tools = { path = \"chunk-your-tools-v${tools_version}\" }"
				fi
			} >"${workspace_toml}.next"
		else
			cp "${tmp}" "${workspace_toml}.next"
		fi
		rm -f "${tmp}"
		if [[ -f "${workspace_toml}" ]] && cmp -s "${workspace_toml}" "${workspace_toml}.next"; then
			rm -f "${workspace_toml}.next"
		else
			mv "${workspace_toml}.next" "${workspace_toml}"
		fi
		if [[ -f "${legacy_config}" ]]; then
			rm -f "${legacy_config}"
		fi
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
		chunk_write_workspace_cargo_patches "${root}"
	}

	NOPATCH_WORKSPACE_DIRNAME=".cyt-nopatch-ws"

	chunk_nopatch_workspace_dir() {
		local root="$1"
		printf '%s/target/%s' "${root}" "${NOPATCH_WORKSPACE_DIRNAME}"
	}

	# Shadow workspace under target/ with [patch.crates-io] stripped from Cargo.toml.
	# Cargo and maturin use this tree so root Cargo.toml (committed patches) is never
	# rewritten during verify-pins, pre-commit hooks, or local dev builds.
	chunk_ensure_nopatch_workspace() {
		local root="$1"
		local ws workspace_toml stripped_toml want

		ws="$(chunk_nopatch_workspace_dir "${root}")"
		workspace_toml="${root}/${WORKSPACE_CARGO_TOML_REL}"
		stripped_toml="${ws}/Cargo.toml"

		[[ -f "${workspace_toml}" ]] || return 0

		mkdir -p "${ws}"
		want="$(mktemp)"
		chunk_strip_workspace_patches "${workspace_toml}" >"${want}"
		if [[ ! -f "${stripped_toml}" ]] || ! cmp -s "${want}" "${stripped_toml}"; then
			mv "${want}" "${stripped_toml}"
		else
			rm -f "${want}"
		fi
		ln -sfn "${root}/sdk" "${ws}/sdk"
		# Always share the repo-root lockfile. Cargo may replace a symlink with a
		# regular file under target/; remove stale copies so metadata --locked
		# does not read an out-of-date target/.cyt-nopatch-ws/Cargo.lock.
		rm -f "${ws}/Cargo.lock"
		ln -sfn "${root}/Cargo.lock" "${ws}/Cargo.lock"
	}

	chunk_maturin_manifest_path() {
		local root="$1"
		chunk_ensure_nopatch_workspace "${root}"
		printf '%s/sdk/rust/cyt-indexer/Cargo.toml' "$(chunk_nopatch_workspace_dir "${root}")"
	}

	# maturin uses the real cyt-indexer manifest while [patch.crates-io] is briefly
	# stripped from root Cargo.toml; Cargo.lock is restored afterward (see
	# chunk_run_without_worktree_patches). The nopatch manifest breaks maturin's
	# artifact paths under sdk/python/.target.
	chunk_run_maturin_develop() {
		local root="$1"
		shift
		# Legacy callers pass sdk/python as a second argument; maturin uses cwd instead.
		if [[ $# -gt 0 && "${1##*/}" == "python" && "${1}" == *"/sdk/python" ]]; then
			shift
		fi
		"${root}/scripts/local/dev/maturin-develop.sh" "$@"
	}

	# Run a command with cwd in the nopatch workspace (for cargo invocations).
	chunk_run_in_nopatch_workspace() {
		local root="$1"
		shift
		chunk_ensure_nopatch_workspace "${root}"
		(
			cd "$(chunk_nopatch_workspace_dir "${root}")" || exit 1
			"$@"
		)
	}

	# Run cargo in the nopatch workspace (no post-heal).
	_chunk_cargo_exec_raw() {
		local root="$1"
		shift
		chunk_run_in_nopatch_workspace "${root}" \
			env CARGO_TARGET_DIR="${root}/target" cargo "$@"
	}

	# Run cargo against Cargo.lock (crates.io) without worktree path patches.
	_chunk_cargo_exec() {
		local root="$1"
		shift
		local rc=0 heal_rc=0

		_chunk_cargo_exec_raw "${root}" "$@" || rc=$?
		chunk_ensure_workspace_cargo_lock "${root}" || heal_rc=$?

		if ((rc != 0)); then
			return "${rc}"
		fi
		return "${heal_rc}"
	}

	chunk_cargo_locked() {
		local root="$1"
		shift
		_chunk_cargo_exec "${root}" "$@"
	}

	# Run cargo from the repo root with [patch.crates-io] active (local worktree
	# builds). Patched cargo may strip chunk-your-* registry lines from Cargo.lock;
	# heal reinserts source/checksum without restoring the whole file.
	chunk_run_patched_cargo() {
		local root="$1"
		shift
		local rc=0

		if ! (
			cd "${root}" || exit 1
			env CARGO_TARGET_DIR="${root}/target" cargo "$@"
		); then
			rc=$?
		fi
		chunk_ensure_workspace_cargo_lock "${root}" || rc=$?
		return "${rc}"
	}

	# Restore registry source/checksum lines when patched root cargo command
	# stripped them (suture first, then nopatch refresh if needed).
	chunk_ensure_workspace_cargo_lock() {
		chunk_refresh_workspace_cargo_lock "$@"
	}

	# Briefly strip [patch.crates-io] for tools that must not see path patches
	# (maturin sdist, cargo publish). Restores patches via chunk_write_workspace_cargo_patches
	# and heals Cargo.lock afterward — no full-file backup/restore.
	chunk_run_without_worktree_patches() {
		local root="$1"
		shift
		local workspace_toml="${root}/${WORKSPACE_CARGO_TOML_REL}"
		local legacy_config="${root}/.cargo/config.toml"
		local had_patches=0
		local legacy_backup=""
		local rc=0

		if [[ -f "${workspace_toml}" ]] &&
			grep -q '^\[patch\.crates-io\]' "${workspace_toml}"; then
			had_patches=1
			chunk_strip_workspace_patches "${workspace_toml}" >"${workspace_toml}.tmp"
			mv "${workspace_toml}.tmp" "${workspace_toml}"
		fi

		if [[ -f "${legacy_config}" ]]; then
			legacy_backup="$(mktemp "${TMPDIR:-/tmp}/cyt-cargo-config.XXXXXX")"
			mv "${legacy_config}" "${legacy_backup}"
		fi

		if ! "$@"; then
			rc=$?
		fi

		if [[ -n "${legacy_backup}" && -f "${legacy_backup}" ]]; then
			mv "${legacy_backup}" "${legacy_config}"
		fi
		if ((had_patches)); then
			chunk_write_workspace_cargo_patches "${root}"
		fi
		chunk_ensure_workspace_cargo_lock "${root}" || rc=$?

		return "${rc}"
	}

	chunk_registry_checksum_from_index() {
		local crate="$1"
		local version="$2"
		local registry="${CARGO_HOME:-${HOME}/.cargo}/registry/index"
		local index_path index_file

		index_path="${crate:0:1}/${crate:0:3}/${crate}"
		index_file="$(find "${registry}" -type f -path "*/.cache/${index_path}" 2>/dev/null | head -1)"
		if [[ -z "${index_file}" ]]; then
			index_file="$(find "${registry}" -type f -path "*/${index_path}" 2>/dev/null | head -1)"
		fi
		[[ -n "${index_file}" && -f "${index_file}" ]] || return 1

		if command -v strings >/dev/null 2>&1; then
			strings "${index_file}" |
				grep -F "\"vers\":\"${version}\"" |
				tail -1 |
				sed -n 's/.*"cksum":"\([^"]*\)".*/\1/p'
			return
		fi

		grep -F "\"vers\":\"${version}\"" "${index_file}" |
			tail -1 |
			sed -n 's/.*"cksum":"\([^"]*\)".*/\1/p'
	}

	# Reinsert crates.io source/checksum for chunk-your-* without rewriting the lockfile.
	chunk_suture_chunk_registry_pins() {
		local root="$1"
		local cargo_toml="${root}/${CARGO_INDEXER_TOML_REL}"
		local lock_file="${root}/Cargo.lock"
		local crate version checksum tmp
		local -a checksum_args=()
		local checksum_arg_count=0

		[[ -f "${lock_file}" ]] || return 1

		for crate in chunk-your-skills chunk-your-tools; do
			version="$(read_cargo_dep_version "${crate}" "${cargo_toml}")"
			[[ -n "${version}" ]] || continue
			chunk_lock_crate_has_registry_source "${crate}" "${lock_file}" && continue
			checksum="$(chunk_registry_checksum_from_index "${crate}" "${version}")"
			[[ -n "${checksum}" ]] || return 1
			case "${crate}" in
			chunk-your-skills) checksum_args+=("-v" "skills_checksum=${checksum}") ;;
			chunk-your-tools) checksum_args+=("-v" "tools_checksum=${checksum}") ;;
			esac
			checksum_arg_count=$((checksum_arg_count + 1))
		done

		((checksum_arg_count > 0)) || return 0

		tmp="$(mktemp)"
		awk "${checksum_args[@]}" '
			function crate_checksum(name) {
				if (name == "chunk-your-skills") {
					return skills_checksum
				}
				if (name == "chunk-your-tools") {
					return tools_checksum
				}
				return ""
			}
			function flush_block(    i, checksum) {
				checksum = crate_checksum(pkg)
				if (pkg != "" && checksum != "" && !has_source) {
					for (i = 1; i <= n; i++) {
						print lines[i]
						if (lines[i] ~ /^version = /) {
							print "source = \"registry+https://github.com/rust-lang/crates.io-index\""
							print "checksum = \"" checksum "\""
						}
					}
				} else {
					for (i = 1; i <= n; i++) {
						print lines[i]
					}
				}
				pkg = ""
				n = 0
				has_source = 0
			}
			/^\[\[package\]\]/ {
				flush_block()
				print
				next
			}
			/^\[\[/ {
				flush_block()
				print
				next
			}
			{
				if (pkg != "" && /^source = "registry+/) {
					has_source = 1
				}
				if ($0 ~ /^name = /) {
					gsub(/^name = "|"$/, "", $0)
					pkg = $0
				}
				lines[++n] = $0
			}
			END { flush_block() }
		' "${lock_file}" >"${tmp}"
		if cmp -s "${tmp}" "${lock_file}"; then
			rm -f "${tmp}"
		else
			mv "${tmp}" "${lock_file}"
		fi
		return 0
	}

	# Pin chunk-your-* in Cargo.lock to the exact versions declared in cyt-indexer
	# Cargo.toml, using the nopatch workspace so [patch.crates-io] never pollutes the
	# lockfile with [[patch.unused]] entries.
	chunk_read_lock_crate_version() {
		local crate="$1"
		local lock_file="$2"
		awk -v crate="${crate}" '
			/^\[\[package\]\]/ { in_pkg = 1; next }
			/^\[\[/ { in_pkg = 0 }
			in_pkg && $0 == "name = \"" crate "\"" {
				if (getline && $0 ~ /^version = /) {
					gsub(/^version = "/, "")
					gsub(/"$/, "")
					print
				}
			}
		' "${lock_file}" | head -1
	}

	chunk_lock_crate_has_registry_source() {
		local crate="$1"
		local lock_file="$2"
		awk -v crate="${crate}" '
			/^\[\[package\]\]/ { in_pkg = 1; pkg = ""; next }
			/^\[\[/ { in_pkg = 0 }
			in_pkg && $0 ~ /^name = / {
				gsub(/^name = "|"$/, "", $0)
				pkg = $0
			}
			in_pkg && pkg == crate && /^source = "registry+/ { found = 1 }
			END { exit(found ? 0 : 1) }
		' "${lock_file}"
	}

	# True when Cargo.lock already pins chunk-your-* to cyt-indexer manifest versions
	# with crates.io sources (no path-patch or [[patch.unused]] state).
	chunk_workspace_cargo_lock_is_current() {
		local root="$1"
		local cargo_toml="${root}/${CARGO_INDEXER_TOML_REL}"
		local lock_file="${root}/Cargo.lock"
		local crate version lock_version

		[[ -f "${lock_file}" ]] || return 1
		if grep -q '^\[\[patch\.unused\]\]' "${lock_file}"; then
			return 1
		fi

		for crate in chunk-your-tools chunk-your-skills; do
			version="$(read_cargo_dep_version "${crate}" "${cargo_toml}")"
			[[ -n "${version}" ]] || continue
			lock_version="$(chunk_read_lock_crate_version "${crate}" "${lock_file}")"
			[[ "${lock_version}" == "${version}" ]] || return 1
			chunk_lock_crate_has_registry_source "${crate}" "${lock_file}" || return 1
		done

		return 0
	}

	chunk_lock_chunk_deps_need_generate_lockfile() {
		local lock_file="$1"
		local crate

		[[ -f "${lock_file}" ]] || return 0
		if grep -q '^\[\[patch\.unused\]\]' "${lock_file}"; then
			return 0
		fi

		for crate in chunk-your-tools chunk-your-skills; do
			if ! chunk_lock_crate_has_registry_source "${crate}" "${lock_file}"; then
				return 0
			fi
		done

		return 1
	}

	chunk_refresh_workspace_cargo_lock() {
		local root="$1"
		local cargo_toml="${root}/${CARGO_INDEXER_TOML_REL}"
		local lock_file="${root}/Cargo.lock"
		local crate version lock_version
		local rc=0

		if chunk_workspace_cargo_lock_is_current "${root}"; then
			return 0
		fi

		if chunk_suture_chunk_registry_pins "${root}" &&
			chunk_workspace_cargo_lock_is_current "${root}"; then
			return 0
		fi

		if chunk_lock_chunk_deps_need_generate_lockfile "${lock_file}"; then
			if ! _chunk_cargo_exec_raw "${root}" generate-lockfile; then
				if declare -F shorten_paths >/dev/null 2>&1; then
					printf 'error: cargo generate-lockfile failed (nopatch workspace)\n' |
						shorten_paths >&2
				else
					printf 'error: cargo generate-lockfile failed (nopatch workspace)\n' >&2
				fi
				return 1
			fi
		fi

		for crate in chunk-your-tools chunk-your-skills; do
			version="$(read_cargo_dep_version "${crate}" "${cargo_toml}")"
			[[ -n "${version}" ]] || continue
			lock_version="$(chunk_read_lock_crate_version "${crate}" "${lock_file}")"
			if [[ "${lock_version}" == "${version}" ]] &&
				chunk_lock_crate_has_registry_source "${crate}" "${lock_file}"; then
				continue
			fi
			if ! _chunk_cargo_exec_raw "${root}" update -p "${crate}" --precise "${version}"; then
				if declare -F shorten_paths >/dev/null 2>&1; then
					printf 'error: cargo update -p %s --precise %s failed\n' \
						"${crate}" "${version}" | shorten_paths >&2
				else
					printf 'error: cargo update -p %s --precise %s failed\n' \
						"${crate}" "${version}" >&2
				fi
				rc=1
				continue
			fi
			lock_version="$(chunk_read_lock_crate_version "${crate}" "${lock_file}")"
			if [[ "${lock_version}" != "${version}" ]]; then
				if declare -F shorten_paths >/dev/null 2>&1; then
					printf 'error: Cargo.lock %s is %s after update (expected %s)\n' \
						"${crate}" "${lock_version:-<missing>}" "${version}" | shorten_paths >&2
				else
					printf 'error: Cargo.lock %s is %s after update (expected %s)\n' \
						"${crate}" "${lock_version:-<missing>}" "${version}" >&2
				fi
				rc=1
			fi
		done

		return "${rc}"
	}

fi
