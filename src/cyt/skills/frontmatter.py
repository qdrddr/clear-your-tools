"""Parse skill YAML frontmatter."""

from __future__ import annotations

import yaml


def _frontmatter_yaml_body(frontmatter: str) -> str | None:
    text = frontmatter.strip()
    if not text.startswith("---"):
        return text or None
    end = text.find("\n---", 3)
    if end == -1:
        return None
    body = text[3:end].strip()
    return body or None


def strip_leading_frontmatter(content: str) -> str:
    """Remove a leading YAML frontmatter block, if present."""
    if not content.startswith("---"):
        return content
    end = content.find("\n---", 3)
    if end == -1:
        return content
    return content[end + 4 :].lstrip("\n")


def injection_markdown_body(markdown: str) -> str:
    """Strip YAML frontmatter; keep reconstructed body."""
    return strip_leading_frontmatter(markdown).lstrip("\n")


def _parsed_frontmatter_dict(frontmatter: str | None) -> dict[str, object] | None:
    if not frontmatter or not frontmatter.strip():
        return None
    yaml_body = _frontmatter_yaml_body(frontmatter)
    if not yaml_body:
        return None
    try:
        parsed = yaml.safe_load(yaml_body)
    except yaml.YAMLError:
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


def frontmatter_search_text(frontmatter: str | None) -> str:
    """Build BM25-searchable text from YAML name, description, and other string fields."""
    parsed = _parsed_frontmatter_dict(frontmatter)
    if not parsed:
        return ""

    priority = ("name", "description")
    lines: list[str] = []
    seen: set[str] = set()

    for key in priority:
        value = parsed.get(key)
        if isinstance(value, str) and value.strip():
            lines.append(value.strip())
            seen.add(key)

    for key in sorted(parsed):
        if key in seen:
            continue
        value = parsed[key]
        if isinstance(value, str) and value.strip():
            lines.append(value.strip())

    return "\n".join(lines)


def skill_name_from_frontmatter(frontmatter: str | None) -> str | None:
    """Return trimmed YAML ``name`` when present and non-empty."""
    parsed = _parsed_frontmatter_dict(frontmatter)
    if not parsed:
        return None
    name = parsed.get("name")
    if not isinstance(name, str) or not name.strip():
        return None
    return name.strip()
