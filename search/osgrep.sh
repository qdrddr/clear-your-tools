npm install -g osgrep
#optional
#osgrep setup

cat << EOF > .osgrepignore
!code/catalog/schemas/decomposed
!code/catalog/schemas/enums
*.py
*.md
*.yaml
*.toml
*.sh
.env.example
node_modules/
fastmcp/
EOF


osgrep index

# Background mode
OSGREP_PORT=4444
#osgrep serve --background -p $OSGREP_PORT
#osgrep serve status -p $OSGREP_PORT
#osgrep serve stop --all


# respects .gitignore
osgrep search "base64 for parquet" -m 25 --scores --min-score 0.3 --sync --compact