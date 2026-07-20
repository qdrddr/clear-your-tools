# Stop Drowning Your Cursor Agent in MCP Noise

Are you manually toggling MCP tools on and off? Typing *"use the XYZ tool"* to force skipping irrelevant
tools?
You're fighting a losing battle against context bloat—and paying for every wasted token.

## The Problem: Too Much Signal, Not Enough Relevance

Agent harnesses dump **every** MCP tool into the LLM context on **every** request. Even frontier
models suffer from information overload, hunting for needles in haystacks while your token bill grows.

Take Google Calendar's `events.list`: it exposes 20+ properties. For a simple prompt like *"When is
my first meeting tomorrow?"*, only 5 fields matter. The rest are schema noise that confuse the model
and burn context window.
See more tool examples in the [chunk-your-tools examples](https://github.com/qdrddr/chunk-your-tools/tree/main/examples).

Skills have the opposite problem. The agent sees only a short frontmatter description. Write it too
short, and the LLM misses it semantically. Write it too long, and you drown the context. Either way,
you end up manually adding and removing `SKILL.md` files mid-project.
See more skill examples in the [chunk-your-skills examples](https://github.com/qdrddr/chunk-your-skills/tree/main/examples).

## The Fix: CYT

**CYT** (`clear-your-tools`) automatically injects only the relevant context your agent needs, when
it needs it.

It works by **chunking** tools and skills into granular pieces, running them through a pruning
pipeline, and **recomposing** a "skinny" version:

- **Tools:** Drops irrelevant tools, optional properties, and enum values. Keeps schema intact.
- **Skills:** Decomposes markdown into semantic nodes (frontmatter, headers, prose), then restores
  only the relevant sections in their original hierarchy.

CYT uses three gating strategies—**BM25 lexical search**, **reranking**, and **LLM evaluation**—to
decide what survives without breaking JSON schema or markdown structure.

**Example:** A GitHub `create_issue` tool has properties `title` (required), `body`, and `labels`.
If your prompt doesn't need labeling, CYT drops `labels` entirely. The tool still validates; the LLM
just sees less noise.

## Why Cursor Needs MCPC + Hooks

Claude Code and Codex allow HTTP request interception via a reverse proxy. **Cursor does not**
(unless you BYOK). So CYT uses **hooks** instead.

Here's the problem: Cursor's `beforeSubmitProm` hook currently cannot mutate the tool list or add
direct context. Until it supports `additional_content`, we work around it by moving all content into
the `.cursor/rule/cyt-indexer.mdc` file that Cursor always reads.

Here's the catch with hooks: It does not allow manipulate tools in the agent; so we have to move them
out of Cursor and into an MCP aggregator.

**MCPC** is that aggregator. It hosts your servers; CYT dynamically exposes only the relevant
subset back to Cursor via project rules.

### Install & Configure

Install [UV](https://docs.astral.sh/uv/getting-started/installation/) and
[NVM](https://github.com/nvm-sh/nvm), then

```bash
# 1. Install CYT and MCPC
uv tool install 'clear-your-tools[all]'
npm install -g @apify/mcpc

# 2. Add the hooks
cyt hook cursor

# 3. Backup your Cursor MCP config, then clear it
cp ~/.cursor/mcp.json ~/.mcpc/cursor.json
jq '. + {mcpServers: {}}' ~/.mcpc/cursor.json > ~/.cursor/mcp.json

# 4. Re-connect servers via MCPC instead
mcpc connect ~/.mcpc/cursor.json:servername @servername
# Or if all are STDIO:
mcpc connect ~/.mcpc/cursor.json --stdio
```

CYT will now create and update `.cursor/rules/cyt-indexer.mdc` automatically as you submit prompts,
injecting only the tool schemas and skill chunks relevant to your current intent.

## What You Gain

- **~30% token reduction** on typical agent sessions.
- **Higher accuracy:** Less noise means fewer hallucinations and wrong tool picks.
- **Fewer steps:** No more "chicken-and-egg" discovery loops where the LLM can't choose a tool it
  hasn't seen.
- **Smart deduplication:** If a skill or tool is already exposed in the session, CYT won't re-inject
  it. If a skill's name + description is already strong enough, CYT gates itself to avoid
  double-spending context.

## Bonus: Skills over MCP

Stop copying `SKILL.md` files across projects. Host skills remotely and let CYT pull and prune them
on the fly:

```bash
mcpc connect https://mcp.skillsovermcp.com/mcp/upstash/context7 @context7-skill
cyt hook daemon restart
cyt hook cursor
```

## Build Your Own

Want granular pruning in your own harness? The underlying chunkers are open-source (Apache 2.0) with
bindings for Python, TypeScript, C, Go, and natively in Rust:

```bash
cargo install chunk-your-tools
cargo install chunk-your-skills

# Test with CLI before embedding into your app
chunk-your-skills decompose --help
chunk-your-skills recompose --help

chunk-your-tools decompose --help
chunk-your-tools recompose --help
```

- **`chunk-your-tools`:** Decomposes tools into required properties, optional properties, and enums.
  Restores the shape based on the survivors.
- **`chunk-your-skills`:** Decomposes skills into frontmatter and markdown header nodes. Restores the
  SKILL file hierarchy, index, and structure.

Give the repos a star if you find them useful.

---

<details>
<summary><strong>FAQ</strong></summary>

**Why not just use progressive tool discovery?**
Discovery adds expensive reasoning steps, and the LLM still can't select a tool it has never seen.
CYT closes that gap by direct injection.

**Why MCPC specifically?**
It is reliable under heavy load where other aggregators flake or flap.

**What if Cursor adds `additional_content` support to hooks?**
CYT already implements it. The moment Cursor enables it, injection will work natively without the
MCPC workaround.

</details>

---

**Ready to cut the noise?**

```bash
uv tool install 'clear-your-tools[all]'
cyt hook cursor
```
