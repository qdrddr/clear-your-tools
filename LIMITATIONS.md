# Limitations

This implementation requires running as a reverse proxy with supported agents such as Claude Code,
and others like Codex, OpenCode, etc (not tested yet). It could be used with Copilot only with the BYOK.

**Hook path (`pruning.tools.inject_via: hook`):** tool definitions can be pruned and injected via
`cyt hook --stdin` without a reverse proxy when skills also use hook injection (or skills are
disabled). This injects pruned tool context into the agent turn; it does not replace native MCP tool
registration in the agent runtime. Live executor catalog loading requires a running Executor MCP
aggregator and `EXECUTOR_TOKEN` (or `pruning.tools.hook.executor_token_var`).

Cursor, or VSCode/Copilot for example, does not support reverse proxying and only supports forward proxies.
In that configuration, requests remain end-to-end encrypted, so the proxy cannot inspect, manipulate,
or prune the request payload.

The token savings applies to **input tokens only** and **only tool definitions**,
the rest of the request remains unchanged. Output/completion or reasoning tokens are not affected.

Conceptually, this functionality is better suited to an MCP Aggregator that connects to backend MCP
servers and exposes only the relevant tools to the agent. However, the current MCP specification
has several limitations that make this difficult in practice:

- MCP is not designed to integrate with agent lifecycle hooks.
- MCP clients and servers are initialized before the agent session starts, so MCP is not aware of
  agent sessions, sub-agents, or execution context boundaries.
- Because of this, an MCP Aggregator cannot reliably determine which agent session or sub-agent should
  see a specific subset of tools, making dynamic tool pruning unreliable.

The savings shown in the `cyt stats` output are estimated using `cl100k_base` token counting from
`cyt-indexer-sdk` (Rust `tiktoken-rs`), because the pruned content is never actually sent to the LLM provider. As a result,
the reported token savings may slightly differ from the provider's own token counts. However,
since the pruned content is never transmitted, this discrepancy does not affect the actual billed
usage.

Local applications only. The proxy intercepts outgoing network traffic from locally running agent
applications before the requests are sent to the LLM provider, allowing it to prune irrelevant
tools from the payload:

- Cloud-hosted applications cannot use this approach, because their traffic does not pass through
  the locally running proxy.

If the provider enforces a fixed *tools → system → messages* ordering at the API level, you gain input‑token savings,
but you also introduce a tradeoff: any change to the tool list invalidates the entire downstream cache.
This deserves deeper investigation. What is obvious is that excess context leads to
[context rot](https://www.trychroma.com/research/context-rot),
[Context Bloat](https://eval.16x.engineer/blog/llm-context-management-guide),
[Context Delusion](https://diffray.ai/blog/context-dilution/)
and removing irrelevant information consistently improves an LLM’s cognitive performance.

Codex already reduces tool sets by removing unused tools, but CYT goes further:
it also prunes irrelevant optional fields and enums, something Codex never touches.
Even when both are used together, CYT still cuts input tokens by an additional ~20%.

## Model Cache

Mutating tools invalidates the provider’s prompt cache, because tool definitions usually
appear before the system and user messages. Any change to that prefix forces a cache miss
for everything that follows.

That trade-off is usually acceptable: system and user messages are typically short, so the
lost cache hit rate (often saves 50%) is smaller compared with the token savings from pruning
the tool list (typically 80–95% of the original size of the tools).

## Pruner Strategy and Accuracy

CYT’s default pruner is BM25: fast, local, and free. It isn’t the most advanced method,
but you can swap it for a reranker or a small, cheap LLM if you want higher‑quality pruning.
This is often worthwhile when using Claude Code, since Sonnet is expensive.

### BM25 language and tokenization

Languages with complex morphology (Arabic, Finnish, Turkish) or no whitespace (Chinese, Japanese)
require specialized tokenizers. CYT’s BM25 stack is built on **Tantivy** (Rust) and aligned with
its tokenization model.

#### Tantivy (Rust)

- Supports BM25 with tokenizers for English and multilingual ICU.
- Additional tokenizers are available via plugins.
- Tantivy’s default tokenizer is language‑agnostic (splits on whitespace + punctuation).
  It works for any language that uses whitespace.

#### Stemming

Stemming is only available for these 18 languages:

Arabic, Danish, Dutch, English, Finnish, French, German, Greek, Hungarian, Italian, Norwegian,
Portuguese, Romanian, Spanish, Swedish, Tamil, Turkish.

You can implement your own tokenizer or integrate external ones (e.g., for Chinese/Japanese/Korean).
Tantivy encourages using `tantivy-tokenizer-api` for custom tokenizers.

A common worry is that pruning might cause multi‑step agents to “lose” tools or degrade semantics
by removing tools. In practice, we haven’t seen this happen, for two reasons:

1. **The user query is always preserved.**
Each step is a new agent message, but the original user query is always included.
Pruning is anchored to that query—not to intermediate reasoning—so every step receives the same stable,
pruned tool list.

2. **Losing intent is hallucination, not pruning failure.**
If an agent drifts away from the user’s goal or tries to use irrelevant tools,
that’s agent behavior, not missing tools. A larger tool list doesn’t fix hallucinations.

3. **The latest assistant turn refines pruning.**
On multi-step requests, CYT scores tools against both the original user query and
the agent's most recent assistant message (`User_Asks: …; Assistant_Says: …`).
The user's goal stays anchored across steps; the latest turn adds context for what the agent is doing now.

- Dynamic tool reduction remains stable across multi‑step reasoning because pruning
is tied to the user query.
- If an agent “forgets” what it’s doing, that’s a hallucination issue,
not a limitation of the pruning mechanism.
- Semantic degradation is a legitimate concern that should be tested,
and it can be reduced by using a stronger pruner pipeline and higher‑quality models.
