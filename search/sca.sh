

uv run code/cs.py --root code/catalog/ search --refresh --limit 100 --path 'schemas/decomposed/*.md' "List of operation IDs" --json
uv run code/cs.py --root code/catalog/ search --refresh --limit 100 --path 'schemas/decomposed/**/*.json' "List of operation IDs" --json

# --path is optional. defaults to 'schemas/decomposed/'
MY_WORK_DIR=code/catalog/
uv run code/cs.py --root ${MY_WORK_DIR} search --refresh --limit 100 "List of operation IDs" --json > code/tests/temp_recomposed.json
uv run code/cs.py --root ${MY_WORK_DIR} search --refresh --limit 100 "List of operation IDs" --file-only "\n"

# need to add code/catalog/
uv run code/retrieve_catalog.py --files code/catalog/schemas/decomposed/code-review-graph/get_community_tool/repo_root.json code/catalog/schemas/decomposed/code-review-graph/cross_repo_search_tool/limit.json code/catalog/schemas/decomposed/hedl/convert_from/options/delimiter.json
uv run code/retrieve_catalog.py --json-file code/tests/temp_recomposed.json > temp_tools.json


rtk uv run code/rerank.py --json code/tests/test_recomposed.json search "List of operation IDs" > temp_rerank.json

rtk uv run code/llm.py --json code/tests/temp_recomposed.json "find code graph tools" > temp_llm.json