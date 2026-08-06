"""Format ``<agent-resources>`` injection blocks."""

from __future__ import annotations

from dataclasses import dataclass

from cyt.indexer.tokens import count_tokens
from cyt.skills.frontmatter import injection_markdown_body
from cyt.tools.inject import _xml_single_quoted_attr

_INTRO = (
    "Based on the user query added chunks of descriptions of resources (not entire resource). "
    "The entire resource could be retrieved with the command attribute."
)


def resources_inject_intro() -> str:
    return _INTRO


@dataclass(frozen=True)
class MatchedResource:
    doc_id: str
    file_path: str
    markdown: str
    name: str | None
    command: str
    description: str
    score: float
    token_count: int
    content_hash: str | None = None


def _resource_open_tag(*, command: str, description: str, name: str | None) -> str:
    attrs = [f"command='{_xml_single_quoted_attr(command)}'"]
    if description.strip():
        attrs.append(f"description='{_xml_single_quoted_attr(description.strip())}'")
    if name:
        attrs.append(f"name='{_xml_single_quoted_attr(name)}'")
    return f"<resource {' '.join(attrs)}>"


def format_resource_item(match: MatchedResource, *, full: bool = False) -> str:
    if full:
        body = match.markdown.rstrip()
    else:
        body = injection_markdown_body(match.markdown).rstrip()
    if not body:
        return ""
    return "\n".join(
        [
            _resource_open_tag(
                command=match.command,
                description=match.description,
                name=match.name,
            ),
            body,
            "</resource>",
        ],
    )


def format_agent_resources(
    matches: list[MatchedResource],
    *,
    full_flags: dict[str, bool] | None = None,
    combined_text: str = "",
) -> str:
    if not matches:
        return ""
    from cyt.injection.session_log_build import resource_item_key

    item_lines: list[str] = []
    for match in matches:
        key = resource_item_key(match)
        full = bool(full_flags.get(key)) if full_flags else False
        item = format_resource_item(match, full=full)
        if item:
            item_lines.append(item)
    item_lines = [line for line in item_lines if line]
    if not item_lines:
        return ""
    from cyt.injection.pre_exposed import is_pre_exposed

    include_intro = not (combined_text.strip() and is_pre_exposed(_INTRO, combined_text))
    if include_intro:
        lines = [_INTRO, "", "<agent-resources>", *item_lines, "</agent-resources>"]
    else:
        lines = ["<agent-resources>", *item_lines, "</agent-resources>"]
    return "\n".join(lines)


def injection_token_count(matches: list[MatchedResource] | str) -> int:
    if isinstance(matches, str):
        return count_tokens(matches)
    return count_tokens(format_agent_resources(matches))
