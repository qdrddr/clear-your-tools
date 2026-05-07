brew install yoanbernabeu/tap/grepai
ollama pull nomic-embed-text

grepai init
grepai watch --stop
grepai watch --background
grepai watch --status

# supports --toon
grepai search "List of operation IDs" --limit 5 --path code/catalog/schemas --json --compact