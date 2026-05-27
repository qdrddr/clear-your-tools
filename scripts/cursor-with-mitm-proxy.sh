#!/usr/bin/env bash
# Launch Cursor with env vars so Node trusts the MITM CA and routes api2 through :8835.
#
# IMPORTANT (macOS): `open -a Cursor` does NOT inherit shell env vars. We exec the
# binary directly so NODE_EXTRA_CA_CERTS and HTTPS_PROXY reach Node/Electron.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CA="$ROOT/src/crt/mitm-ca.pem"
PROXY="${CURSOR_HTTP_PROXY:-http://127.0.0.1:8835}"

if [[ ! -f "$CA" ]]; then
  echo "MITM CA not found at $CA — see README Proxy section" >&2
  exit 1
fi

export NODE_EXTRA_CA_CERTS="$CA"
export HTTP_PROXY="$PROXY"
export HTTPS_PROXY="$PROXY"
export ALL_PROXY="$PROXY"
export NO_PROXY="${NO_PROXY:-127.0.0.1,localhost}"

echo "NODE_EXTRA_CA_CERTS=$NODE_EXTRA_CA_CERTS"
echo "HTTPS_PROXY=$HTTPS_PROXY"
echo "Fully quit Cursor (Cmd+Q), then this script starts it with the env above."

if [[ "$(uname -s)" == "Darwin" ]]; then
  CURSOR_BIN="${CURSOR_BIN:-/Applications/Cursor.app/Contents/MacOS/Cursor}"
  if [[ ! -x "$CURSOR_BIN" ]]; then
    echo "Cursor binary not found at $CURSOR_BIN" >&2
    echo "Set CURSOR_BIN to Contents/MacOS/Cursor inside your .app bundle." >&2
    exit 1
  fi
  exec env \
    NODE_EXTRA_CA_CERTS="$NODE_EXTRA_CA_CERTS" \
    HTTP_PROXY="$HTTP_PROXY" \
    HTTPS_PROXY="$HTTPS_PROXY" \
    ALL_PROXY="$ALL_PROXY" \
    NO_PROXY="$NO_PROXY" \
    "$CURSOR_BIN" "$@"
else
  exec cursor "$@"
fi
