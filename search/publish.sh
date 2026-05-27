# update pyproject.toml version

tag=v0.0.3

git checkout main
git pull origin main
git tag ${tag}
git push origin ${tag}