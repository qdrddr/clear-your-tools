#!/usr/bin/env bash
# Smoke-test reverse (:8834) and MITM forward (:8835) proxies.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

CA="${MITM_CA:-src/crt/mitm-ca.pem}"
TEST_URLS="${TEST_URLS:-https://httpbin.org/get}"
PORT="${PROXY_PORT:-18834}"
FPORT="${FORWARD_PORT:-18835}"
REVERSE="http://127.0.0.1:${PORT}"

if [[ ! -f "$CA" ]]; then
  echo "MITM CA not found at $CA — see README Proxy section" >&2
  exit 1
fi

rm -f forward.log
uv run src/proxy.py serve --debug --port "$PORT" --forward-port "$FPORT" --no-http2-serve &
PID=$!
trap 'kill $PID 2>/dev/null || true' EXIT
sleep 3

echo "== reverse health =="
curl -sf "$REVERSE/health" | grep -q '"status":"ok"'

echo "== forward CONNECT (MITM) =="
for url in $TEST_URLS; do
  code=$(curl --cacert "$CA" -x "http://127.0.0.1:${FPORT}" -sS -o /dev/null -w "%{http_code}" --max-time 30 "$url")
  echo "$code $url"
  [[ "$code" != "000" && "$code" != "502" ]] || exit 1
done

echo "== forward POST body in forward.log =="
curl --cacert "$CA" -x "http://127.0.0.1:${FPORT}" \
  -sS -o /dev/null -w "post %{http_code}\n" --max-time 30 \
  -d '{"mitm_test":true}' -H "Content-Type: application/json" \
  "https://httpbin.org/post"
grep -q mitm_test forward.log

echo "== HTTP/2 inside MITM tunnel =="
ver=$(curl --http2 --cacert "$CA" -x "http://127.0.0.1:${FPORT}" \
  -sS -o /dev/null -w "%{http_version}" --max-time 30 \
  "${TEST_URLS%% *}")
echo "http_version=$ver"

echo "All forward proxy checks passed."
