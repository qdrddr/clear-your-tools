#!/usr/bin/env bash
# Quick checks before expecting agent prompts in forward.log
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
CA="${MITM_CA:-src/crt/mitm-ca.pem}"
FPORT="${FORWARD_PORT:-8835}"
LOG="${FORWARD_LOG:-forward.log}"

echo "== 1. Proxy listening on :${FPORT}? =="
if ! curl -sf -x "http://127.0.0.1:${FPORT}" -o /dev/null --max-time 2 http://127.0.0.1:${FPORT} 2>/dev/null; then
  # CONNECT proxy may not answer GET; try a CONNECT target instead
  code=$(curl --cacert "$CA" -x "http://127.0.0.1:${FPORT}" -sS -o /dev/null -w "%{http_code}" --max-time 10 https://api2.cursor.sh/ || true)
  if [[ "$code" != "200" ]]; then
    echo "FAIL: forward proxy not reachable or MITM broken (api2 http_code=$code)" >&2
    echo "Start: uv run src/proxy.py serve --debug" >&2
    exit 1
  fi
fi
echo "OK (api2 through MITM returns 200)"

echo ""
echo "== 2. Recent forward.log cursor hosts =="
if [[ ! -f "$LOG" ]]; then
  echo "No $LOG yet — run proxy with --debug and use Cursor"
  exit 0
fi
rg -o '"host": "[^"]+"' "$LOG" 2>/dev/null | sort | uniq -c | sort -rn | head -10 || echo "(empty log)"

echo ""
echo "== 3. Agent stream paths (Stream/Bidi/Chat) =="
if rg -q 'Stream|Bidi|Chat' "$LOG" 2>/dev/null; then
  rg '"path":' "$LOG" | rg 'Stream|Bidi|Chat' | head -5
else
  echo "NONE — agent chat is not hitting the proxy yet"
  echo "Fix: fully quit Cursor, run ./scripts/cursor-with-mitm-proxy.sh (not Dock/Spotlight)"
fi

echo ""
echo "When working, search: rg 'say hiiii|mitm_connect_frame' $LOG"
