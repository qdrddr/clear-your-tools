# update pyproject.toml version first

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
version="$(
  grep -E '^version[[:space:]]*=' "${ROOT}/pyproject.toml" \
    | head -1 \
    | sed -E 's/^version[[:space:]]*=[[:space:]]*"(.*)".*/\1/'
)"
tag="v${version}"

oco -n
git checkout main
git pull origin main
git tag ${tag}
git push origin ${tag}
