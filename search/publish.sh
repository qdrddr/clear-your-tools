# update pyproject.toml version

tag=v0.0.7


oco -n
git checkout main
git pull origin main
git tag ${tag}
git push origin ${tag}
