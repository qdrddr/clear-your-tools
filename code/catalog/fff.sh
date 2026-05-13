npm install -g @apify/mcpc
curl -L https://dmtrkovalenko.dev/install-fff-mcp.sh | bash
# irm https://raw.githubusercontent.com/dmtrKovalenko/fff.nvim/main/install-mcp.ps1 | iex #windows

mcpc close @fff
mcpc connect fff.json:fff @fff

mcpc @fff tools-call grep query:='"base64 for parquet"' cursor:='"*.json base64 for parquet"' maxResults:=3