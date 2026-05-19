

uv run src/cs.py --root src/catalog/ search --refresh --limit 100 --path 'schemas/decomposed/*.md' "List of operation IDs" --json
uv run src/cs.py --root src/catalog/ search --refresh --limit 100 --path 'schemas/decomposed/**/*.json' "List of operation IDs" --json

# --path is optional. defaults to 'schemas/decomposed/'
MY_WORK_DIR=src/catalog/
uv run src/cs.py --root ${MY_WORK_DIR} search --refresh --limit 100 "List of operation IDs" --json > temp_embedings.json
uv run src/cs.py --root ${MY_WORK_DIR} search --refresh --limit 100 "List of operation IDs" --file-only "\n"

# need to add src/catalog/
uv run src/retrieve_catalog.py --files src/catalog/schemas/decomposed/code-review-graph/get_community_tool/repo_root.json src/catalog/schemas/decomposed/code-review-graph/cross_repo_search_tool/limit.json src/catalog/schemas/decomposed/hedl/convert_from/options/delimiter.json
uv run src/retrieve_catalog.py --json-file temp_embedings.json > temp_tools.json


rtk uv run src/rerank.py --json temp_embedings.json search "List of operation IDs" --output-json temp_rerank.json
rtk uv run src/rerank.py --dir src/catalog/schemas/decomposed --output-json temp_rerank2.json "List of operation IDs"

rtk uv run src/llm.py --json temp_embedings.json "find code graph tools" --output-json temp_llm.json
rtk uv run src/llm.py --dir src/catalog/schemas/decomposed "find code graph tools" --output-json temp_llm2.json


rtk uv run src/aggregator.py --debug --only-relay --port 8123
rtk uv run src/aggregator.py --debug --port 8123 --servers temp_empty_config.json