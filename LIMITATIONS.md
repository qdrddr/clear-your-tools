# Limitations

This implementation requires running as a reverse proxy with supported agents such as Claude Code,
and others like Codex, OpenCode, etc (not tested yet). It could be used with Copilot only with the BYOK.

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

The savings shown in the `cyt stats totals` output are estimated using the `tiktoken`
tokenizer, because the pruned content is never actually sent to the LLM provider. As a result,
the reported token savings may slightly differ from the provider's own token counts. However,
since the pruned content is never transmitted, this discrepancy does not affect the actual billed
usage.

Local applications only. The proxy intercepts outgoing network traffic from locally running agent
applications before the requests are sent to the LLM provider, allowing it to prune irrelevant
tools from the payload:

- Cloud-hosted applications cannot use this approach, because their traffic does not pass through
  the locally running proxy.
