mcpc
mcpc close @sca-stdio-proj
mcpc clean
mcpc connect ~/.claude/claude.json:sca-stdio-proj
mcpc connect .mcp.json:sca-stdio-proj
mcpc @sca-stdio-proj tools-list
mcpc @sca-stdio-proj tools-get mcp__fff_find_files
rtk mcpc @sca-stdio-proj tools-call mcp__fff_find_files query:='"aggregator"' maxResults:=20 cursor:=null


#uv run src/aggregator.py --port 8000
