mcpc
mcpc close @sca-http
mcpc clean
mcpc connect ~/.claude/claude.json:sca-http
mcpc @sca-http tools-list
mcpc @sca-http tools-get mcp__fff_find_files
rtk mcpc @sca-http tools-call mcp__fff_find_files query:='"aggregator"' maxResults:=20 cursor:=null


#uv run src/aggregator.py --port 8000
