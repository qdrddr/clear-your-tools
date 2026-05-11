uv tool install --upgrade 'cocoindex-code[full]' -p 3.13
uv pip install 'cocoindex-code[full]' 

mkdir -p code/catalog/.cocoindex_code/
cat << EOF > code/catalog/.cocoindex_code/settings.yml
exclude_patterns:
- '**/.*'
- '**/__pycache__'
- '**/node_modules'
- '**/target'
- '**/build/assets'
- '**/dist'
- '**/vendor/*.*/*'
- '**/vendor/*'
- '**/.cocoindex_code'
- '**/full/**'
- '**/full/*'
- 'full/**'
- 'full/*'
include_patterns:
- '**/*.json'
- '**/*.md'
- '*.md'

chunkers:
  - ext: json              # use a custom chunker for .toml files
    module: custom_json_chunker:json_chunker
EOF

cp code/custom_json_chunker.py code/catalog/custom_json_chunker.py

# ccc --install-completion
cd code/catalog

COCOINDEX_CODE_EXTRA_EXTENSIONS="json,md"
COCOINDEX_CODE_EXCLUDE_PATTERNS='["full/**"]'

ccc daemon status
ccc daemon stop
ccc init -f
ccc index


# Search from another forlder
export COCOINDEX_CODE_HOST_PATH_MAPPING="${PWD}=${PWD}"
export COCOINDEX_CODE_HOST_CWD="./code/catalog"

ccc search --refresh --offset 5 --limit 100 --path 'schemas/decomposed/*.md' "parquet"
ccc search --refresh --limit 100 --path 'schemas/decomposed/**/*.json' "List of operation IDs"
