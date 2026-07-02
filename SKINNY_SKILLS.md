# Skills injection

Clear Your Tools can inject **the relevant parts** of agent skills into each turn —without sending
full skill files on every request, we call them skinny skills.

Agents already see skill **headers** (YAML frontmatter with name and description) in their system prompt. They only
open a skill file when they decide the header is relevant. If your question matches the skill **body**
but not its header, the agent often misses it and you have to say “read the X skill.”

CYT closes that gap by searching skill bodies, picking matching sections, and injecting a **skinny skill**
into context. It **skips skills** whose headers already match your query — those are skills the agent
would likely read on its own.

---

## Turn skills injection on or off

```bash
cyt setup
```

The setup wizard asks **“Enable skills injection?”** to
configure:

- `skills.inject_via` — **hook** or **proxy**
- `skills.pipeline` — **bm25**, **rerank**, or **llm**
- skill directories to search

Settings are saved to `~/.config/cyt/config.yaml`. You can also edit that file directly:

```yaml
skills:
  enabled: false   # turn off injection
```

---

## Two injection paths

Only **one** path is active at a time (`skills.inject_via`):

| Path | Command | When it runs | Where skills land |
| ---- | ------- | ------------ | ----------------- |
| **hook** | `cyt hook` | Agent `UserPromptSubmit` hook, before the turn | Hook `additionalContext` (Claude Code / Codex) |
| **proxy** | `cyt launch` | Each upstream LLM request, after tool pruning | Request body (system or developer message) |

Default: **`proxy`**.

With `inject_via: proxy`, the proxy injects skills into the HTTP body. Hooks may still be installed,
but `cyt hook --stdin` **skips injection**.

With `inject_via: hook`, the hook injects skills. The proxy still prunes tools if you use it, but
**does not** inject skills into the body.

---

## Skinny skill

A **skinny skill** is not the full skill file. CYT picks only the markdown sections that match your
query and stitches them back together so the **page index structure of the original file is preserved** — parent
headings, step order, and hierarchy stay intact. Irrelevant sections are left out.

Each skinny skill is wrapped in `<agent-skills>` with a `<skill path="…">` tag so the agent knows
where the full file lives and can `Read` it if needed.

**Example** — Context7 skill for a docs question. Only Step 1 and Step 3 match; the preamble and
other steps are omitted, but `## How to Fetch Documentation` is kept as the parent heading
to preserve the page index structure:

```md
---
name: context7-mcp
description: This skill should be used when the user asks about libraries, frameworks, API references, or needs code examples. Activates for setup questions, code generation involving libraries, or mentions of specific frameworks like React, Vue, Next.js, Prisma, Supabase, etc.
---

## How to Fetch Documentation

### Step 1: Resolve the Library ID

Call `resolve-library-id` with:

- `libraryName`: The library name extracted from the user's question
- `query`: The user's full question (improves relevance ranking)

### Step 3: Fetch the Documentation

Call `query-docs` with:

- `libraryId`: The selected Context7 library ID (e.g., `/vercel/next.js`)
- `query`: The user's specific question
```

<details>
<summary><strong>How CYT finds the right sections</strong></summary>

Both paths share the same search pipeline:

```text
User prompt + last assistant message
        │
        ▼
Frontmatter gate — skip skills whose header already matches
        │
        ▼
Skills pipeline (bm25 | rerank | llm)
        │
        ▼
Token budget — pick sections that fit
        │
        ▼
<agent-skills> skinny skill injected into context
```

**Search query** — built from the latest user message and last assistant reply:
`User_Asks: …; Assistant_Says: …`

| Path | Where assistant text comes from |
| ---- | ------------------------------- |
| **hook** (Claude Code) | Session transcript jsonl |
| **hook** (Codex) | Hook payload / transcript |
| **proxy** | Parsed from the upstream request body |

**Frontmatter gate** — skills whose YAML header already scores high against your query (default
limit **0.4**) are skipped.

**Skills pipeline** (`skills.pipeline`, default **`bm25`**):

| Pipeline | Needs remote API | Notes |
| -------- | ---------------- | ----- |
| `bm25` | No | Local search over skill sections; good default |
| `rerank` | Yes | Reranker picks the best sections |
| `llm` | Yes | LLM picks sections; can run inside proxy pruning |

Indexes are built on first use under `~/.config/cyt/skills/entries/{content_sha256}/` and reused afterward.
Each entry stores node files under `nodes/` and BM25 chunk variants under `chunks/bm25/{index_params_hash}/`.

### Build and retrieve manually (`cyt-indexer`)

CYT uses the same **`cyt-indexer`** engine under the hood. You can build the decomposed catalog
yourself and pull out a skinny skill by **node ID** — useful for debugging or custom tooling.

**Install:**

```bash
cargo install cyt-indexer
# or from this repo:
cargo build -p cyt-indexer --release
```

**Build the catalog:**

```bash
cyt-indexer build skills --skills ~/.claude/skills --output ./.catalog
```

| Path | What it is |
| ---- | ---------- |
| `.catalog/nodes/page_index.json` | Page tree metadata for one skill (node-only structure) |
| `.catalog/nodes/n{node_id}.md` | One file per section (node), e.g. `n0.md`, `n2.md` |
| `.catalog/chunks/bm25/{hash}/chunk_index.json` | Chunk-aware structure for a pipeline variant |
| `.catalog/chunks/bm25/{hash}/c{chunk_id}.md` | One file per BM25 chunk, e.g. `c0.md` |

| `node_id` | Section |
| --------- | ------- |
| `0` | YAML frontmatter |
| `1` | Preamble (text before the first heading) |
| `2+` | Headings and nested sections |

Find `doc_id` in `nodes/page_index.json` under the `id` field (for example `context7-mcp__skill`).

**Inspect the tree** (section titles and node IDs):

```bash
cyt-indexer retrieve skills \
  --catalog ./.catalog \
  --doc-id context7-mcp__skill \
  --query structure
```

**Retrieve a skinny skill by node ID** — parent headings are added automatically (nodes `4` and
`6` also pull in parent node `3`):

```bash
cyt-indexer retrieve skills \
  --catalog ./.catalog \
  --doc-id context7-mcp__skill \
  --query content \
  --node_id 4 \
  --node_id 6
```

Output: `.catalog/skill_out.json` and `.catalog/skills/retrieve/{skill-dir}/SKILL.md`.

You can also use `--line_num`, `--chunk_id`, or ranges like `--node_id 4-6`. More options:
[sdk/rust/cyt-indexer/README.md](sdk/rust/cyt-indexer/README.md).

</details>

---

## Path 1: `cyt hook`

Use this when you want injection via agent hooks — with or without the proxy. We recommend using hook its simple and efficient.

### Hook setup

```bash
cyt hook
```

The wizard enables skills, sets `inject_via: hook`, and installs a `UserPromptSubmit` hook in
Claude Code or Codex.

Agent examples: [Claude Code](examples/agents/anthropic/claude/README.MD) ·
[Codex](examples/agents/openai/codex/README.MD)

Remove hooks: `cyt hook --uninstall`

<details>
<summary><strong>Per-turn flow</strong></summary>

```text
Agent fires UserPromptSubmit hook
        │
        ▼
cyt hook --stdin
        │
        ├─ build search query (prompt + transcript)
        ├─ search skills → pick sections within budget
        │
        ▼
stdout: {"hookSpecificOutput": {"additionalContext": "<agent-skills>…"}}
        │
        ▼
Agent merges additionalContext into the turn
```

</details>

<details>
<summary><strong>Hook token budget</strong></summary>

The hook path limits how much skill text can be injected per turn, based on the size of your prompt.
CYT will not inject more than fits that limit.

Check your limits:

```bash
cyt skills budget
```

</details>

<details>
<summary><strong>Test the hook</strong></summary>

Check that skills injection is set up correctly (enabled, pipeline, API keys):

```bash
cyt hook --test
```

Try a search without installing hooks:

```bash
cyt hook --prompt "configure agent hooks"
```

Simulate a real hook call:

```bash
cat <<'EOF' | cyt hook --stdin --debug
{
  "hook_event_name": "UserPromptSubmit",
  "session_id": "test",
  "model": "example-model",
  "prompt": "configure agent hooks",
  "cwd": "/path/to/project"
}
EOF
```

</details>

---

## Path 2: `cyt launch` (proxy injection)

Use this when the agent runs through CYT for tool pruning. Skills are injected into the upstream
request **after** tools are pruned, using tokens saved by pruning.

### Setup

1. Run `cyt setup` and enable skills with `inject_via: proxy`.
2. Launch the agent through CYT:

```bash
cyt launch -- claude
cyt launch -- codex

# 3rd party provider
export ANTHROPIC_AUTH_TOKEN="$OPENROUTER_API_KEY"
cyt launch --upstream https://openrouter.ai/api -- claude --model haiku
```

`cyt launch` starts the proxy if needed, then runs the agent. See [README](README.md) for Codex
`--configure` / `--restore`.

| Upstream kind | Where the skinny skill goes |
| ------------- | --------------------------- |
| **anthropic** (Claude Code) | Appended to the **system** message |
| **openai** (Codex) | **Developer** message before the last user message |

<details>
<summary><strong>Per-request flow</strong></summary>

```text
Agent → cyt proxy (started by cyt launch)
        │
        ├─ prune tools
        ├─ compute budget from pruning savings
        ├─ search skills → pick sections within budget
        │
        ▼
Inject skinny skill into request → upstream provider
```

If the body already contains `<agent-skills>`, the proxy skips re-injection.

When `skills.pipeline` is `rerank` or `llm`, skill search may wait until after tool pruning.

</details>

### Proxy token budget

Proxy injection reuses part of the tokens saved by tool pruning. More tools pruned → more room for
skinny skills, without a bigger bill.

Track injection and savings:

```bash
cyt stats
cyt skills budget
```

Optional: run `cyt setup` first, then `cyt stats --add` for cost estimates.

---

## Choosing hook vs proxy

| Prefer **hook** when… | Prefer **proxy** (`cyt launch`) when… |
| ----------------------- | ------------------------------------- |
| Agent is not using CYT proxy | Agent already runs through `cyt launch` |
| You want hook `additionalContext` | You want skills in the upstream request body |
| You run without tool pruning | You want injection budget tied to pruning savings |
| Standalone skill injection is enough | You use rerank/llm skills pipeline inside the proxy |

Both paths use the same skill directories, search pipeline, and skinny-skill format — only **timing**,
**budget**, and **delivery** differ. The skinny skills are injected by default via proxy, no configuration needed.

<details>
<summary><strong>Configuration reference</strong></summary>

```yaml
skills:
  enabled: true
  inject_via: proxy          # hook | proxy
  pipeline: bm25             # bm25 | rerank | llm
  frontmatter_upper_limit: 0.4
  max_tokens_per_request: 20000
  directories:
    - ~/.claude/skills
    - .claude/skills
    - ~/.codex/skills
    - .codex/skills
```

Full defaults: [`src/cyt/config/defaults.yaml`](src/cyt/config/defaults.yaml).

</details>

<details>
<summary><strong>Debugging</strong></summary>

**Hook debug** — `CYT_SKILLS_DEBUG=1` or `--debug`; logs under `.debug/skills/` and
`~/.config/cyt/debug/skills/`.

**Common hook outcomes:**

| Outcome | Meaning |
| ------- | ------- |
| `skipped_inject_via_proxy` | `inject_via: proxy` — use proxy path instead |
| `user_prompt_no_matches` | No sections matched after frontmatter gate |
| `user_prompt_budget_exceeded` | Matches found but exceeded token budget |
| `skipped_budget_zero` | Budget math yielded zero tokens |

</details>
