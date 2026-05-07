npm install -g @tobilu/qmd
qmd collection rm mcp-collection
qmd collection add code/catalog/schemas/ --name mcp-collection --mask "**/*.json"
qmd embed
qmd update
qmd query "List of operation IDs" --json --min-score 0.3 -n 10