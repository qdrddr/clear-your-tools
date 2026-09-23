#!/usr/bin/env bash
# Verify Mach-O LC_SYMTAB string pool alignment for macOS 27+ dyld compatibility.
#
# macOS 27+ rejects dylibs whose LC_SYMTAB.stroff is not 8-byte aligned
# (rust-lang/rust#157750). Run after release builds of .dylib / .so / .node artifacts.
#
# Usage: verify-mach-o-linkedit.sh FILE [FILE...]
set -euo pipefail

usage() {
	cat <<'EOF'
Usage: verify-mach-o-linkedit.sh FILE [FILE...]

Exit 0 when every Mach-O file has LC_SYMTAB.stroff aligned to 8 bytes.
Skip non-Mach-O files and binaries without LC_SYMTAB.
EOF
}

die() {
	echo "error: $*" >&2
	exit 1
}

verify_one() {
	local file="$1"
	local stroff

	[[ -f "${file}" ]] || die "not a file: ${file}"

	if ! file "${file}" | grep -q 'Mach-O'; then
		echo "skip (not Mach-O): ${file}"
		return 0
	fi

	stroff="$(otool -l "${file}" | awk '/^[[:space:]]*stroff/{print $2; exit}')"
	if [[ -z "${stroff}" ]]; then
		echo "skip (no LC_SYMTAB): ${file}"
		return 0
	fi

	if ((stroff % 8 != 0)); then
		echo "::error::misaligned LINKEDIT string pool in ${file} (stroff=${stroff}, stroff%8=$((stroff % 8)))"
		return 1
	fi

	echo "OK: ${file} (stroff=${stroff})"
}

main() {
	if (($# == 0)); then
		usage >&2
		exit 1
	fi

	local file failed=0
	for file in "$@"; do
		if ! verify_one "${file}"; then
			failed=1
		fi
	done

	if ((failed != 0)); then
		die "one or more Mach-O files have misaligned LC_SYMTAB.stroff"
	fi
}

main "$@"
