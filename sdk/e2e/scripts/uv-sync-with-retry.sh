#!/usr/bin/env bash
# Retry uv sync until PyPI (or other indexes) have propagated the release.
# Usage: ./uv-sync-with-retry.sh [--group test] [other uv sync args...]
set -euo pipefail

MAX_ATTEMPTS="${UV_SYNC_MAX_ATTEMPTS:-12}"
SLEEP_SECS="${UV_SYNC_RETRY_SECS:-30}"

attempt=1
while [[ "$attempt" -le "$MAX_ATTEMPTS" ]]; do
	if uv sync "$@"; then
		echo "uv sync succeeded (attempt ${attempt})"
		exit 0
	fi
	if [[ "$attempt" -eq "$MAX_ATTEMPTS" ]]; then
		echo "::error::uv sync failed after ${MAX_ATTEMPTS} attempts" >&2
		exit 1
	fi
	echo "uv sync failed (attempt ${attempt}/${MAX_ATTEMPTS}); retrying in ${SLEEP_SECS}s..."
	sleep "$SLEEP_SECS"
	attempt=$((attempt + 1))
done
