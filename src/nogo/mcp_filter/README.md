MCP protocol does not have 
- Reliable way to integrate with harness agent hooks
- MCP Client and Servers are initialized **before** the agent's session and can't reliably target tool filtering per agent session

As a result code in src/mcp_filter/ is a no go.