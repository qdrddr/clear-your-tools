"""Demo chunker: splits JSON files at top-level [section] boundaries.

Each ``[section]`` header starts a new chunk, keeping the section header
and its key-value pairs together.  This produces semantically coherent units
instead of the arbitrary line-window slices from the default splitter.

Register in ``.cocoindex_code/settings.yml``::

    chunkers:
      - ext: json
        module: custom_json_chunker:json_chunker
"""

from __future__ import annotations

import json
import sys
from pathlib import Path as _Path
from typing import Any

from cocoindex_code.chunking import Chunk, TextPosition


def _pos(line: int) -> TextPosition:
    return TextPosition(byte_offset=0, char_offset=0, line=line, column=0)


def extract_semantic_lines(obj: Any, is_root: bool = False) -> list[str]:
    lines = []
    if isinstance(obj, dict):
        if is_root and "description" in obj:
            if isinstance(obj["description"], str):
                lines.append(obj["description"])

        for key, value in obj.items():
            if key == "properties" and isinstance(value, dict):
                for prop_name, prop_val in value.items():
                    lines.append(prop_name)
                    lines.extend(extract_semantic_lines(prop_val))
            elif key in ("description", "default") and not (is_root and key == "description"):
                if isinstance(value, str):
                    lines.append(value)
                elif isinstance(value, (int, float, bool)):
                    lines.append(str(value))
            elif key not in ("properties", "description", "default"):
                lines.extend(extract_semantic_lines(value))
    elif isinstance(obj, list):
        for item in obj:
            lines.extend(extract_semantic_lines(item))
    return lines


def json_chunker(path: _Path, content: str) -> tuple[str | None, list[Chunk]]:
    """Split a JSON file into semantic chunks."""
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return "json", []

    extracted_lines = extract_semantic_lines(data, is_root=True)
    if not extracted_lines:
        return "json", []

    chunks: list[Chunk] = []
    chunk_size = 100

    # Calculate file total lines for end position
    total_lines = content.count("\n") + 1

    for i in range(0, len(extracted_lines), chunk_size):
        chunk_lines = extracted_lines[i : i + chunk_size]
        text = "\n".join(chunk_lines)
        chunks.append(
            Chunk(
                text=text,
                start=_pos(1),
                end=_pos(total_lines),
            )
        )

    return "json", chunks


__all__ = ["json_chunker"]


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <file_path>")
        sys.exit(1)

    p = _Path(sys.argv[1])
    c_str = p.read_text()
    e, chs = json_chunker(p, c_str)

    print(f"Ext: {e}")
    for i, ch in enumerate(chs):
        print(f"--- Chunk {i+1} ---")
        print(ch.text)
