

uv run src/cs.py --root src/catalog/ search --refresh --limit 100 --path 'schemas/decomposed/*.md' "List of operation IDs" --json
uv run src/cs.py --root src/catalog/ search --refresh --limit 100 --path 'schemas/decomposed/**/*.json' "List of operation IDs" --json

# --path is optional. defaults to 'schemas/decomposed/'
MY_WORK_DIR=src/catalog/
uv run src/cs.py --root ${MY_WORK_DIR} search --refresh --limit 100 "List of operation IDs" --json > src/tests/temp_recomposed.json
uv run src/cs.py --root ${MY_WORK_DIR} search --refresh --limit 100 "List of operation IDs" --file-only "\n"

# need to add src/catalog/
uv run src/retrieve_catalog.py --files src/catalog/schemas/decomposed/code-review-graph/get_community_tool/repo_root.json src/catalog/schemas/decomposed/code-review-graph/cross_repo_search_tool/limit.json src/catalog/schemas/decomposed/hedl/convert_from/options/delimiter.json
uv run src/retrieve_catalog.py --json-file src/tests/temp_recomposed.json > temp_tools.json


rtk uv run src/rerank.py --json src/tests/test_recomposed.json search "List of operation IDs" > temp_rerank.json

rtk uv run src/llm.py --json src/tests/temp_recomposed.json "find code graph tools" > temp_llm.json
