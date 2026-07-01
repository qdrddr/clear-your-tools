#!/usr/bin/env bash
# Print the host Rust target triplet (rustc host: line).
set -euo pipefail

rustc -vV | sed -n 's/^host: //p'
