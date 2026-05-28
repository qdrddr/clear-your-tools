# update pyproject.toml version

tag=v0.0.6


oco -n
git checkout main
git pull origin main
git tag ${tag}
git push origin ${tag}
