mcpc
mcpc close mymcp-stdio
mcpc clean
mcpc connect ~/.claude/claude.json:mymcp-stdio
mcpc @mymcp-stdio tools-list
mcpc @mymcp-stdio tools-get fff_find_files
rtk mcpc @mymcp-stdio tools-call fff_find_files query:='"aggregator"' maxResults:=20 cursor:=null


#uv run code/aggregator.py --transport stdio
