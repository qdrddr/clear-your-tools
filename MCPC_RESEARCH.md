# MCPC Research Notes

```bash
mcpc --json

mcpc
MCP sessions:
  @ctx7 → https://mcp.context7.com/mcp ● live
  @hedl → hedl-mcp ● live
  @fff → fff-mcp ● live

No OAuth profiles.
  ↳ run: mcpc login mcp.example.com

To view server capabilities and tools, run: mcpc @session
For usage and the agent guide, run: mcpc help [--skill]
```

```json
{
  "sessions": [
    {
      "name": "@ctx7",
      "server": {
        "url": "https://mcp.context7.com/mcp"
      },
      "createdAt": "2026-05-07T15:36:03.460Z",
      "status": "live",
      "lastConnectionAttemptAt": "2026-07-17T19:54:52.211Z",
      "lastSeenAt": "2026-07-17T19:55:55.178Z",
      "protocolVersion": "2025-11-25",
      "serverInfo": {
        "name": "Context7",
        "icons": [
          {
            "src": "https://context7.com/context7-icon-green.png",
            "mimeType": "image/png"
          }
        ],
        "version": "3.2.3",
        "websiteUrl": "https://context7.com",
        "description": "Context7 provides up-to-date documentation and code examples for libraries and frameworks."
      },
      "pid": 10208,
      "mcpSessionId": "5b45df1a-cdea-4f5f-ac58-c0e26ed74238",
      "stateless": false
    },
    {
      "name": "@hedl",
      "server": {
        "command": "hedl-mcp",
        "args": [],
        "env": {}
      },
      "createdAt": "2026-05-07T15:39:30.355Z",
      "status": "live",
      "lastConnectionAttemptAt": "2026-07-17T19:54:52.211Z",
      "lastSeenAt": "2026-07-17T19:55:54.879Z",
      "serverInfo": {
        "name": "hedl-mcp",
        "version": "2.0.0"
      },
      "pid": 10227,
      "stateless": false
    },
    {
      "name": "@fff",
      "server": {
        "command": "fff-mcp",
        "args": [],
        "env": {
          "PWD": "$PWD"
        }
      },
      "createdAt": "2026-05-07T16:05:49.763Z",
      "status": "live",
      "lastConnectionAttemptAt": "2026-07-17T19:54:52.211Z",
      "lastSeenAt": "2026-07-17T19:55:54.896Z",
      "serverInfo": {
        "name": "fff",
        "version": "0.8.4"
      },
      "pid": 10226,
      "stateless": false
    }
  ],
  "profiles": []
}
```

```bash
mcpc --json @ctx7 tools-list
```

```json
[
  {
    "name": "resolve-library-id",
    "title": "Resolve Context7 Library ID",
    "description": "Resolves a package/product name to a Context7-compatible library ID and returns matching libraries.\n\nYou MUST call this function before 'Query Documentation' tool to obtain a valid Context7-compatible library ID UNLESS the user explicitly provides a library ID in the format '/org/project' or '/org/project/version' in their query.\n\nEach result includes:\n- Library ID: Context7-compatible identifier (format: /org/project)\n- Name: Library or package name\n- Description: Short summary\n- Code Snippets: Number of available code examples\n- Source Reputation: Authority indicator (High, Medium, Low, or Unknown)\n- Benchmark Score: Quality indicator (100 is the highest score)\n- Versions: List of versions if available. Use one of those versions if the user provides a version in their query. The format of the version is /org/project/version.\n\nFor best results, select libraries based on name match, source reputation, snippet coverage, benchmark score, and relevance to your use case.\n\nSelection Process:\n1. Analyze the query to understand what library/package the user is looking for\n2. Return the most relevant match based on:\n- Name similarity to the query (exact matches prioritized)\n- Description relevance to the query's intent\n- Documentation coverage (prioritize libraries with higher Code Snippet counts)\n- Source reputation (consider libraries with High or Medium reputation more authoritative)\n- Benchmark Score: Quality indicator (100 is the highest score)\n\nResponseFormat:\n- Return the selected library ID in a clearly marked section\n- Provide a brief explanation for why this library was chosen\n- If multiple good matches exist, acknowledge this but proceed with the most relevant one\n- If no good matches exist, clearly state this and suggest query refinements\n\nFor ambiguous queries, request clarification before proceeding with a best-guess match.\n\nIMPORTANT: Do not call this tool more than 3 times per question. If you cannot find what you need after 3 calls, use the best result you have.",
    "inputSchema": {
      "type": "object",
      "properties": {
        "query": {
          "type": "string",
          "description": "The question or task you need help with. This is used to rank library results by relevance to what the user is trying to accomplish. The query is sent to the Context7 API for processing. Do not include any sensitive or confidential information such as API keys, passwords, credentials, personal data, or proprietary code in your query."
        },
        "libraryName": {
          "type": "string",
          "description": "Library name to search for and retrieve a Context7-compatible library ID. Use the official library name with proper punctuation — e.g., 'Next.js' instead of 'nextjs', 'Customer.io' instead of 'customerio', 'Three.js' instead of 'threejs'."
        }
      },
      "required": [
        "query",
        "libraryName"
      ],
      "$schema": "http://json-schema.org/draft-07/schema#"
    },
    "annotations": {
      "readOnlyHint": true,
      "destructiveHint": false,
      "idempotentHint": true,
      "openWorldHint": true
    },
    "execution": {
      "taskSupport": "forbidden"
    }
  },
  {
    "name": "query-docs",
    "title": "Query Documentation",
    "description": "Retrieves and queries up-to-date documentation and code examples from Context7 for any programming library or framework.\n\nYou must call 'Resolve Context7 Library ID' tool first to obtain the exact Context7-compatible library ID required to use this tool, UNLESS the user explicitlyprovides a library ID in the format '/org/project' or '/org/project/version' in their query.\n\nDo not call this tool more than 3 times per question.",
    "inputSchema": {
      "type": "object",
      "properties": {
        "libraryId": {
          "type": "string",
          "description": "Exact Context7-compatible library ID (e.g., '/mongodb/docs', '/vercel/next.js', '/supabase/supabase', '/vercel/next.js/v14.3.0-canary.87') retrieved from 'resolve-library-id' or directly from user query in the format '/org/project' or '/org/project/version'."
        },
        "query": {
          "type": "string",
          "description": "The question or task you need help with, scoped to a single concept. Be specific and include relevant details, but keep each query to one topic — if the user's question spans multiple distinct concepts, make a separate call per concept instead of combining them, unless the question is about how the concepts interact. Good: 'How to set up authentication with JWT in Express.js' or 'React useEffect cleanup function examples'. Bad (too vague): 'auth' or 'hooks'. Bad (too broad): 'routing and auth and caching in Next.js'. The query is sent to the Context7 API for processing. Do not include any sensitive or confidential information such as API keys, passwords, credentials, personal data, or proprietary code in your query."
        }
      },
      "required": [
        "libraryId",
        "query"
      ],
      "$schema": "http://json-schema.org/draft-07/schema#"
    },
    "annotations": {
      "readOnlyHint": true,
      "destructiveHint": false,
      "idempotentHint": true,
      "openWorldHint": true
    },
    "execution": {
      "taskSupport": "forbidden"
    }
  }
]
```

```bash
mcpc @ctx7 tools-list
[@ctx7 → https://mcp.context7.com/mcp]

Tools (2):
* `resolve-library-id (query:str, libraryName:str)` [read-only, idempotent, open-world]
* `query-docs (libraryId:str, query:str)` [read-only, idempotent, open-world]

For full tool details and schema, run `mcpc @ctx7 tools-list --full` or `mcpc @ctx7 tools-get <name>`
```

```bash
mcpc @ctx7 tools-call resolve-library-id '{"libraryName":"clear-your-tools","query":"supported OS"}'

[@ctx7 → https://mcp.context7.com/mcp]

✓ Tool resolve-library-id executed successfully with these results:

Content:
```text

Available Libraries:

- Title: Clear Your Tools
- Context7-compatible library ID: /qdrddr/clear-your-tools
- Description: Clear Your Tools (CYT) reverse proxy that dynamically prunes irrelevant tools from agent
  requests to reduce context overhead and improve LLM performance.
- Code Snippets: 145
- Source Reputation: High
- Benchmark Score: 79.41

```

```bash
echo '{"libraryName":"clear-your-tools","query":"supported OS"}' | mcpc @ctx7 tools-call resolve-library-id
```

```bash
mcpc @ctx7 tools-list
mcpc @ctx7 prompts-list
mcpc @ctx7 resources-list
mcpc @ctx7 tasks-list
mcpc @ctx7 skills-list
```

```bash
mcpc @ctx7 --json
```

```json
{
  "_mcpc": {
    "sessionName": "@ctx7",
    "server": {
      "url": "https://mcp.context7.com/mcp"
    },
    "stateless": false,
    "logPath": "/Users/dberezenko/.mcpc/logs/bridge-@ctx7.log"
  },
  "protocolVersion": "2025-11-25",
  "capabilities": {
    "prompts": {},
    "resources": {},
    "tools": {
      "listChanged": true
    }
  },
  "serverInfo": {
    "name": "Context7",
    "icons": [
      {
        "src": "https://context7.com/context7-icon-green.png",
        "mimeType": "image/png"
      }
    ],
    "version": "3.2.3",
    "websiteUrl": "https://context7.com",
    "description": "Context7 provides up-to-date documentation and code examples for libraries and frameworks."
  },
  "instructions": "Use this server to fetch current documentation whenever the user asks about a library, framework, SDK, API, CLI tool, or cloud service — even well-known ones like React, Next.js, Prisma, Express, Tailwind, Django, or Spring Boot. This includes API syntax, configuration, version migration, library-specific debugging, setup instructions, and CLI tool usage. Use even when you think you know the answer — your training data may not reflect recent changes. Prefer this over web search for library docs.\n\nDo not use for: refactoring, writing scripts from scratch, debugging business logic,code review, or general programming concepts.",
  "toolNames": [
    "resolve-library-id",
    "query-docs"
  ]
}
```

---

name: mcpc
description: Use the mcpc CLI to work with MCP (Model Context Protocol) servers from the shell - connect to a server as a persistent session, then list and call tools, read resources, get prompts, and run async tasks. Use --json for scripting and code mode. Reach for this whenever interacting with MCP servers, calling MCP tools, or accessing MCP resources programmatically.
allowed-tools: Bash(mcpc:*), Read, Grep
---

# mcpc: MCP command-line client

`mcpc` maps every MCP operation to a shell command. For agents this is often more
efficient than function calling: discover the right tool on demand, then generate
shell commands (ideally with `--json`) instead of carrying tool definitions in context.

## Mental model

1. **Connect once** to a server — this creates a persistent, named `@session`. A
   background bridge process keeps the connection (and its state) alive.
2. **Run commands against the `@session`**: list/call tools, read resources, get
   prompts, run async tasks. There is no one-shot `mcpc <url> tools-list` — connect first.
3. **Default output is human-readable**; add `--json` for machine-readable, MCP-spec
   shaped output that composes with `jq` and shell pipelines (code mode).

Everything is self-documenting — when unsure, ask the CLI:

```bash
mcpc --help                       # all commands + global options
mcpc help connect                 # help for one command
mcpc @apify tools-call foo --help # that tool's details + schema
```

## First steps

```bash
mcpc                                   # list sessions + auth profiles (start here)
mcpc connect mcp.apify.com @apify      # connect, create the @apify session
mcpc @apify                            # server info, capabilities, tools overview
mcpc @apify tools-list                 # list tools
mcpc @apify tools-call <tool> q:="hi"  # call a tool
```

## Connecting

Server formats accepted by `connect`:

- `mcp.example.com` — remote HTTP server (`https://` is added automatically)
- `localhost:8080` or `127.0.0.1:8080` — local HTTP server (`http://` is the default for `localhost` and `127.0.0.1`)
- `~/.vscode/mcp.json:filesystem` — a single entry from a config file (`file:entry`)
- `~/.vscode/mcp.json` — connect **every** entry in a config file
- _(no server)_ — auto-discover standard configs and connect all of them

```bash
mcpc connect mcp.apify.com @apify        # remote server, explicit session name
mcpc connect mcp.apify.com               # auto-name the session → @apify
mcpc connect ./.vscode/mcp.json:fs @fs   # one config entry (stdio or http)
mcpc connect                             # discover standard configs + connect everything
```

- `@session` is optional — omit it to auto-generate a name from the server
  (`mcp.apify.com` → `@apify`). A matching session (same server + auth) is reused.
- **Stdio (command-based) entries launch a local process on connect** — only connect
  to configs you trust. Bulk connects skip stdio entries unless you pass `--stdio`.
- `login` / `logout` only accept an MCP server URL (a bare host or full
  `http(s)://` URL) — not config files or auto-discovery.

## Sessions

```bash
mcpc                     # list all sessions and their state
mcpc @apify              # session details, capabilities, tools (also reports the
                         # negotiated protocol version and stateful vs stateless)
mcpc restart @apify      # restart (after server updates, or to recover an 'expired' session)
mcpc close @apify        # tear the session down
```

**Session states:**

- 🟢 **live** — ready to use
- 🟡 **connecting** / **reconnecting** — transient; retry in a moment
- 🟡 **disconnected** — bridge alive but the server has gone quiet; retry to reconnect
- 🟡 **crashed** — bridge process died; auto-restarts on next use
- 🔴 **unauthorized** — auth failed; run `mcpc login <server>` then `mcpc restart @session`
- 🔴 **expired** — server dropped the session; run `mcpc restart @session`

## Discovering and inspecting tools

```bash
mcpc @apify tools-list                  # compact list with inline param signatures
mcpc @apify tools-list --full           # full JSON schemas
mcpc @apify tools-get <tool>            # one tool's details + schema
mcpc @apify tools-call <tool> --help    # shortcut for tools-get: that tool's details + schema

mcpc grep "search"                      # search tools + instructions across ALL sessions
mcpc @apify grep "actor" --resources    # search one session
# grep filters: --tools/--resources/--prompts/--instructions, -E regex, -s case-sensitive, -m <n> max
```

Prefer progressive discovery: `grep` to find the right tool, then `tools-get` for its
schema. This keeps token use low instead of dumping every tool definition.

## Calling tools (passing arguments)

Arguments go after the tool name. Three interchangeable styles:

```bash
# 1) key:=value — values are auto-parsed as JSON, falling back to string
mcpc @apify tools-call search query:="hello world" limit:=10 enabled:=true
mcpc @apify tools-call search config:='{"nested":"value"}' items:='[1,2,3]'
mcpc @apify tools-call search id:='"123"'          # force a string with JSON quotes

# 2) inline JSON — when the first arg starts with { or [
mcpc @apify tools-call search '{"query":"hello","limit":10}'

# 3) stdin — auto-detected when piped and no positional args are given
echo '{"query":"hello"}' | mcpc @apify tools-call search
```

## JSON output (code mode)

Add `--json` for machine-readable output: results on stdout, errors on stderr,
shaped strictly per the MCP spec.

```bash
mcpc --json @apify tools-list | jq -r '.[].name'
mcpc --json @apify tools-call search query:="test" | jq -r '.content[0].text'

# chain tools across calls/sessions
mcpc --json @apify tools-call search-actors keywords:="scraper" \
  | jq -r '.content[0].text | fromjson | .items[0].id' \
  | xargs -I{} mcpc --json @apify tools-call get-actor actorId:="{}"
```

`mcpc --json` with no command returns `{ "sessions": [...], "profiles": [...] }`.

## Resources and prompts

```bash
mcpc @apify resources-list
mcpc @apify resources-read "file:///path/to/file"   # -o <file> to save (binary-safe), --raw to pipe
mcpc @apify resources-templates-list
mcpc @apify resources-subscribe <uri> <file>        # keep local <file> in sync with the resource
mcpc @apify resources-unsubscribe <uri>             # stop syncing, keep the file

mcpc @apify prompts-list
mcpc @apify prompts-get <name> arg1:=value1         # same argument syntax as tools-call (values coerced to strings)
```

## Async tasks (long-running tools)

```bash
mcpc @apify tools-call <tool> --task <args>     # run as a task with a progress spinner; Ctrl+C (or
                                                # ESC) leaves it running and prints the task ID.
                                                # Falls back to a normal sync call if the server has no task support.
mcpc @apify tools-call <tool> --detach <args>   # start and return the task ID immediately
mcpc @apify tasks-list
mcpc @apify tasks-get <taskId>                  # status
mcpc @apify tasks-result <taskId>               # block until the final result is ready
mcpc @apify tasks-cancel <taskId>
```

## Authentication

```bash
# OAuth — interactive browser login, saved as a reusable profile
mcpc login mcp.apify.com                    # "default" profile
mcpc login mcp.apify.com --profile work     # a named profile (multiple accounts per server)
mcpc connect mcp.apify.com @apify --profile work
mcpc logout mcp.apify.com

# Bearer token — not stored as a profile; kept per-session
mcpc connect mcp.apify.com @s -H "Authorization: Bearer $TOKEN"
mcpc @s tools-list
```

With no auth flags, mcpc uses the `default` profile if one exists, otherwise it
connects anonymously. Use `--no-profile` to force an anonymous connection, or
`--profile <name>` to require a specific one.

## Proxy for AI isolation

Expose an authenticated session as a local MCP server, so sandboxed AI code can use it
without ever seeing your real credentials:

```bash
# Human: authenticated session + proxy listening on :8080
mcpc connect mcp.apify.com @ai-proxy --profile ai-access --proxy 8080

# AI in a sandbox limited to localhost: no access to the original tokens
mcpc connect localhost:8080 @sandboxed
mcpc @sandboxed tools-list
```

A proxy does not make an untrusted server safe — stdio servers still touch your system,
and HTTP servers still hold your credentials. Only connect to servers you trust.

## Server-published skills (experimental)

Distinct from this guide: some MCP **servers** publish their own agent skills
(draft MCP extension, SEP-2640). Read them with:

```bash
mcpc @apify skills-list
mcpc @apify skills-get <name> --raw    # print the SKILL.md markdown (pipe to a file or an LLM)
```

(`mcpc help --skill` documents mcpc itself; `skills-list` / `skills-get` fetch skills from the server.)

## Global flags worth knowing

```bash
--json                  # machine-readable, MCP-spec-shaped output (code mode)
--verbose               # protocol-level debug logging (JSON-RPC, transport)
--profile <name>        # OAuth profile to use ("default" if omitted)
--timeout <seconds>     # request timeout in seconds (default: 60)
--max-chars <n>         # truncate human-readable output to n chars (ignored with --json)
--insecure              # skip TLS verification (self-signed certs only)
```

(`--no-profile`, `--stdio`, `--proxy`, and `-H` are options of `connect`, not global flags.)

`mcpc` also has experimental `--x402` auto-payment for paid MCP tools — see `mcpc help x402`.

## Debugging

```bash
mcpc --verbose @apify tools-call <tool>   # protocol-level detail (JSON-RPC, transport)
mcpc @apify logs                          # bridge log; -n <N>, --follow, --since 1h
mcpc @apify ping                          # round-trip health check
mcpc clean                                # tidy stale sessions/logs (also: mcpc clean all)
```

## Exit codes

- `0` — success
- `1` — client error (invalid arguments, unknown command)
- `2` — server error (tool failed, resource not found)
- `3` — network error
- `4` — authentication error

Usage: mcpc [<@session>] [<command>] [options]

Universal command-line client for the Model Context Protocol (MCP).

Commands:
  connect [<server>] [@session]  Connect to an MCP server and start a new named @session
  close <@session>               Close a session
  restart <@session>             Restart a session (losing all state)
  login <server>                 Log in to a server and save an OAuth profile
  logout <server>                Delete an OAuth profile for a server
  clean [resources...]           Clean up mcpc data (sessions, profiles, logs, all)
  grep <pattern>                 Search tools and instructions across all active sessions
  x402 [subcommand] [args...]    Configure an x402 payment wallet (EXPERIMENTAL)
  help [command] [subcommand]    Show help for a command

Options:
  --json                         Output in JSON format for scripting
  --verbose                      Enable debug logging
  --profile <name>               OAuth profile for the server ("default" if not provided)
  --timeout <seconds>            Request timeout in seconds (default: 60)
  --max-chars <n>                Truncate output to n characters (ignored in --json mode)
  --insecure                     Skip TLS certificate verification (for self-signed certs)
  -v, --version                  Output the version number
  -h, --help                     Display help

MCP session commands (after connecting):
  <@session>                     Show MCP server info, capabilities, and tools overview
  <@session> grep <pattern>      Search tools and instructions
  <@session> tools-list          List all server tools
  <@session> tools-get <name>    Get tool details and schema
  <@session> tools-call <name> [arg:=val ... | <json> | <stdin]
  <@session> tasks-list
  <@session> tasks-get <taskId>
  <@session> tasks-result <taskId>
  <@session> tasks-cancel <taskId>
  <@session> prompts-list
  <@session> prompts-get <name> [arg:=val ... | <json> | <stdin]
  <@session> resources-list
  <@session> resources-read <uri> [-o <file> | --raw]
  <@session> resources-subscribe <uri> <file>
  <@session> resources-unsubscribe <uri>
  <@session> resources-templates-list
  <@session> skills-list
  <@session> skills-get <name> [--raw]
  <@session> logging-set-level <level>
  <@session> ping
  <@session> logs [-n N] [--follow] [--since 1h]

Run "mcpc" without arguments to show active sessions and OAuth profiles.
Run "mcpc --json" to get the same data as `{ sessions: [...], profiles: [...] }`.

Agent guide: mcpc help --skill
Full docs: <https://github.com/apify/mcpc/raw/refs/tags/v0.4.0/README.md>
