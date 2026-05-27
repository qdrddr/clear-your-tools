# Capturing Cursor traffic with the MITM forward proxy

This guide explains how to route Cursor through the forward MITM proxy on port **8835** so decrypted `*.cursor.sh` request/response bodies are logged to `forward.log`.

## Quick start

1. Generate and trust the MITM CA (once).
2. Start the proxy with debug logging.
3. Configure Cursor's HTTP proxy setting.
4. **Launch Cursor with `NODE_EXTRA_CA_CERTS`** (required for `api2.cursor.sh`).

```shell
# 1. Generate MITM CA (once)
mkdir -p src/crt
openssl req -x509 -newkey rsa:4096 -nodes -days 3650 \
  -keyout src/crt/mitm-ca-key.pem -out src/crt/mitm-ca.pem \
  -subj "/CN=ToolAttention MITM CA" \
  -addext "basicConstraints=critical,CA:TRUE,pathlen:0"

# Trust on macOS
sudo security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain src/crt/mitm-ca.pem

# 2. Start proxy
uv run src/proxy.py serve --debug

# 3. Fully quit Cursor, then relaunch with the MITM CA for Node
./scripts/cursor-with-mitm-proxy.sh
```

## Cursor settings

Add to Cursor settings (`settings.json`):

```json
{
  "http.proxy": "http://127.0.0.1:8835",
  "http.noProxy": "127.0.0.1,localhost"
}
```

Notes:

- Use **`http://`** for the proxy URL, not `https://`. Port 8835 is a plain HTTP proxy listener; TLS is handled inside `CONNECT` tunnels.
- `"http.proxyStrictSSL": false` does **not** bypass MITM certificate validation in Chromium or Node. You still need the CA trusted (see below).
- `"http.noProxy"` avoids routing local extension-host traffic through the proxy (optional, reduces noise).

## Why two trust steps are needed

Cursor uses two TLS stacks:

| Stack | Used for | Trust source |
|-------|----------|--------------|
| Chromium (renderer) | e.g. `api3.cursor.sh` telemetry | macOS Keychain |
| Node.js (main / extension host) | e.g. `api2.cursor.sh` main API | `NODE_EXTRA_CA_CERTS` |

Keychain trust alone is enough for some hosts (`api3.cursor.sh` may appear in `forward.log` immediately). The main API (`api2.cursor.sh`) goes through Node and **requires** `NODE_EXTRA_CA_CERTS`.

### Launch Cursor correctly

**Do not** launch Cursor from Dock or Spotlight after enabling the proxy — those paths do not set `NODE_EXTRA_CA_CERTS` or `HTTPS_PROXY`.

**Do not** use `open -a Cursor` after exporting env vars in a shell — **macOS `open` does not pass environment variables** to the app. The helper script execs the Cursor binary directly.

Use the helper script (fully quit Cursor first):

```shell
./scripts/cursor-with-mitm-proxy.sh
```

Or exec the binary manually:

```shell
export NODE_EXTRA_CA_CERTS="$(pwd)/src/crt/mitm-ca.pem"
export HTTPS_PROXY="http://127.0.0.1:8835"
export HTTP_PROXY="http://127.0.0.1:8835"
/Applications/Cursor.app/Contents/MacOS/Cursor
```

Verify setup:

```shell
./scripts/verify-cursor-mitm.sh
rg 'mitm_connect_frame|api2\.cursor\.sh' forward.log
```

### Persist env var across GUI launches (macOS)

```shell
launchctl setenv NODE_EXTRA_CA_CERTS "/full/path/to/tool-attention/src/crt/mitm-ca.pem"
launchctl setenv HTTPS_PROXY "http://127.0.0.1:8835"
launchctl setenv HTTP_PROXY "http://127.0.0.1:8835"
```

Do **not** set `SSL_CERT_FILE` to the MITM CA globally — it replaces Python’s CA bundle and breaks the proxy’s **upstream** TLS to real `api2.cursor.sh` (`CERTIFICATE_VERIFY_FAILED`).

Quit and reopen Cursor. To remove later:

```shell
launchctl unsetenv NODE_EXTRA_CA_CERTS
launchctl unsetenv HTTPS_PROXY
launchctl unsetenv HTTP_PROXY
```

## Verify capture

With the proxy running (`uv run src/proxy.py serve --debug`):

1. Use Cursor normally (chat, completions, etc.).
2. Check `forward.log` for entries like:

```json
{
  "mode": "forward",
  "type": "mitm",
  "host": "api2.cursor.sh",
  ...
}
```

Quick external sanity check (proxy + CA working):

```shell
curl --cacert src/crt/mitm-ca.pem -x http://127.0.0.1:8835 \
  -sS -o /dev/null -w "api2 http_code=%{http_code}\n" \
  https://api2.cursor.sh/
```

Expected: `api2 http_code=200`.

## Common proxy warnings

| Log message | Meaning |
|-------------|---------|
| `MITM TLS handshake failed for api2.cursor.sh:443 ... UNEXPECTED_EOF` | Client rejected the forged cert — usually Node without `NODE_EXTRA_CA_CERTS`. Relaunch via `./scripts/cursor-with-mitm-proxy.sh`. |
| `SSLV3_ALERT_CERTIFICATE_UNKNOWN` | Same as above — explicit cert rejection. |
| `MITM TLS handshake failed for mobile.events.data.microsoft.com:443` | Microsoft telemetry; often fails MITM. Harmless if you do not need to capture it. Add to `http.noProxy` to suppress. |
| `upstream connect failed host=api2.cursor.sh: CERTIFICATE_VERIFY_FAILED` | Proxy process has `SSL_CERT_FILE` set to the MITM CA — upstream can't verify real Amazon certs. Unset it (`unset SSL_CERT_FILE`) and restart the proxy. Use `NODE_EXTRA_CA_CERTS` for Cursor only, not `SSL_CERT_FILE`. |

If `api3.cursor.sh` appears in `forward.log` but `api2.cursor.sh` does not, agent chat is **not** going through the proxy. Fix:

1. Fully quit Cursor, relaunch via `./scripts/cursor-with-mitm-proxy.sh` (sets `NODE_EXTRA_CA_CERTS` **and** `HTTPS_PROXY`).
2. Watch proxy stdout for `MITM CONNECT api2.cursor.sh:443` when you send a chat message.
3. If you see `api3` but never `MITM CONNECT api2`, Node is bypassing the proxy — set `HTTPS_PROXY`/`HTTP_PROXY` as above.

## Cursor enterprise network docs (relevant to MITM)

Official reference: [Network Configuration — SSL inspection and DLP](https://cursor.com/docs/enterprise/network-configuration#ssl-inspection-and-dlp)

Cursor documents the same constraints enterprise SSL-inspection proxies face — and the same wire format our MITM proxy must preserve:

| Cursor behavior | Implication for `:8835` MITM |
|-----------------|------------------------------|
| Agent/chat defaults to **HTTP/2 bidirectional streaming** | Must relay H2 without buffering; parse **Connect envelopes** on each DATA frame, not one request body |
| Falls back to **HTTP/1.1 SSE** when H2 bidi breaks (Zscaler-like proxies) | If agent stops working through MITM, Cursor may silently downgrade; look for `StreamSSE` paths |
| Connect payload framing: **1-byte flags + 4-byte BE length + payload** | Matches `src/connect_envelope.py`; official test uses `application/connect+json` |
| SSL inspection often breaks Agent (timeouts, buffering) | Our proxy must **not buffer** streaming responses; long-lived connections OK |
| **Certificate pinning** on critical services | May block full MITM on some native paths; Node needs `NODE_EXTRA_CA_CERTS`, Chromium needs Keychain trust |
| Domains: `*.cursor.sh`, `api2.cursor.sh`, `*.cursorapi.com` | MITM targets; enterprise docs recommend *excluding* these from corporate SSL inspection |

### Official streaming test commands (through MITM)

Run the proxy with `--debug`, then route Cursor's health-check RPCs through `:8835`:

```bash
# HTTP/1.1 Connect SSE (envelope: flags + length + JSON)
echo -ne "\x0\x0\x0\x0\x11{\"payload\":\"foo\"}" | \
  curl --http1.1 --cacert src/crt/mitm-ca.pem -x http://127.0.0.1:8835 -No - -XPOST \
  -H "Content-Type: application/connect+json" \
  --data-binary @- \
  https://api2.cursor.sh/aiserver.v1.HealthService/StreamSSE

# HTTP/2 bidirectional streaming (one frame per second)
(for i in 1 2 3 4 5; do \
  echo -ne "\x0\x0\x0\x0\x12{\"payload\":\"foo$i\"}"; \
  sleep 1; \
done) | curl --cacert src/crt/mitm-ca.pem -x http://127.0.0.1:8835 -No - -XPOST \
  -H "Content-Type: application/connect+json" \
  -T - \
  https://api2.cursor.sh/aiserver.v1.HealthService/StreamBidi
```

With `--debug`, `forward.log` should show:

- `"type": "mitm_stream"` — stream opened (`connect_streaming: true` for Connect paths)
- `"type": "mitm_connect_frame"` — each decoded envelope with readable `frame_body` (e.g. `{"payload":"foo1"}`)

If output appears all at once after 5 seconds (official docs), the proxy is **buffering** — agent chat will not work correctly.

### What actually appears in `forward.log`

The proxy **is working** if you see `"host": "api2.cursor.sh"` entries. That means TLS MITM and routing are fine.

However, **you will not find agent chat prompts as plain text** (e.g. searching for `"crate CURSOR.md file with the information"`). Three separate reasons:

### 1. Agent chat uses different RPCs than what we log today

`forward.log` currently captures unary Connect calls such as:

- `/aiserver.v1.AiService/RefreshTabContext`
- `/aiserver.v1.DashboardService/GetTeams`
- `/aiserver.v1.AnalyticsService/Batch`

It does **not** contain streaming agent endpoints unless H2 Connect frame logging is active. Cursor agent chat uses **Connect bidirectional streaming over HTTP/2** by default ([Cursor network docs](https://cursor.com/docs/enterprise/network-configuration#http2-vs-http11)). The forward proxy logs:

- `"type": "mitm_stream"` — H2 stream metadata on open
- `"type": "mitm_connect_frame"` — incremental Connect envelopes (`frame_body`) on agent/chat streams
- `"type": "mitm"` — unary RPC snapshots (path + headers; body when present)

### 2. Most `api2` request bodies are empty in the log

For Connect POST requests, Cursor often sends `content-encoding: gzip` and `content-type: application/proto` **without** `Content-Length` or `Transfer-Encoding: chunked` in the HTTP/1.1 headers the proxy sees. The proxy logs metadata (path, headers) but **`request_body: null`** for ~98% of POSTs.

Even when a body is captured (e.g. `api3` telemetry at `/tev1/v1/rgstr`), it is stored as **gzip-compressed protobuf**, not readable JSON:

```json
"request_body": {
  "_base64": "H4sIAAAAAAAAE+..."
}
```

Plain-text search in `forward.log` will not match.

### 3. `http.proxy` does not mean every agent byte goes through `:8835`

`http.proxy` routes many Cursor HTTP clients through the forward proxy, which is why you see `api2.cursor.sh` traffic and the agent still works. Agent chat streaming may use connections or protocols the current MITM layer does not parse into log entries.

### How to inspect what you do have

```shell
# List captured api2 paths
rg '"host": "api2.cursor.sh"' forward.log -A3 | rg '"path"'

# Decode a base64 telemetry body (example)
python3 - <<'PY'
import json, re, base64, gzip
from pathlib import Path
for block in Path("forward.log").read_text().split("--- ")[1:]:
    obj = json.loads(block.split("\n", 1)[1])
    rb = obj.get("request_body")
    if isinstance(rb, dict) and "_base64" in rb:
        raw = base64.b64decode(rb["_base64"])
        try:
            raw = gzip.decompress(raw)
        except OSError:
            pass
        print(obj.get("path"), raw[:200])
        break
PY
```

### What would be needed to capture agent prompts

- **Connect protocol body framing** fixes for edge-case HTTP/1.1 unary RPCs (bodies without `Content-Length`)

Restart the proxy after code changes to pick up HTTP/2 support.

### Body format in `forward.log`

With `--debug`, bodies are logged as **plain text** (not base64):

- **JSON** (`content-type: application/json`) — parsed JSON object
- **Gzip/deflate** — decompressed using `content-encoding` / `connect-content-encoding` headers
- **Protobuf** (`application/proto`) — embedded UTF-8 strings extracted from the binary payload

Restart the proxy after code changes; existing log entries keep the old `_base64` format.


```
Cursor (http.proxy → :8835)
  │
  ├─ CONNECT api2.cursor.sh:443  ──► Node TLS ──► needs NODE_EXTRA_CA_CERTS
  ├─ CONNECT api3.cursor.sh:443  ──► Chromium TLS ──► needs Keychain trust
  └─ CONNECT *.cursor.sh:443     ──► MITM decrypt ──► forward.log (--debug)
```

The forward proxy:

1. Receives `CONNECT host:443`
2. Responds `200 Connection Established`
3. Terminates TLS with a per-host leaf cert signed by `src/crt/mitm-ca.pem`
4. Negotiates **HTTP/1.1 or HTTP/2** via TLS ALPN (`h2`, `http/1.1`)
5. Relays and logs decrypted bodies to `forward.log` (`--debug`)

| ALPN | Behavior |
|------|----------|
| `h2` | Multiplexed HTTP/2 relay to upstream (falls back to per-stream HTTP/1.1 if upstream has no h2) |
| `http/1.1` | Keep-alive HTTP/1.1 loop (multiple requests per connection) |

## Alternative: reverse proxy for `api2` only

If MITM for `api2.cursor.sh` still fails after the Node CA step (e.g. cert pinning in native code), use the reverse proxy on port **8834** instead. It is already configured in `src/config.yaml`:

```yaml
upstreams:
  - upstream: cursor
    url: https://api2.cursor.sh
    kind: cursor
endpoints:
  - cursor
```

Traffic to `http://localhost:8834/cursor/...` is forwarded upstream over TLS by the proxy — no client-side MITM needed. This captures only the reverse-routed path, not all `*.cursor.sh` hosts.

## Related files

| File | Purpose |
|------|---------|
| `src/crt/mitm-ca.pem` | MITM CA certificate (trust this) |
| `src/crt/mitm-ca-key.pem` | MITM CA private key (keep secret) |
| `forward.log` | Decrypted forward-proxy debug log (`--debug`) |
| `scripts/cursor-with-mitm-proxy.sh` | Launch Cursor with `NODE_EXTRA_CA_CERTS` |
| `scripts/test_forward_proxy.sh` | Smoke-test forward MITM with curl |

See also the **Proxy** section in [README.md](README.md) for reverse proxy (Claude Code) and general proxy CLI options.
