"""Filter and render tier status for the CLI."""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_TIER_LABELS = tuple(f"T{i}" for i in range(5))


@dataclass(frozen=True)
class StatusFilters:
    kind: str = "all"
    tier: str | None = None
    name: str | None = None
    server: str | None = None
    agent: str | None = None
    path: str | None = None
    path_display: str | None = None
    scope: str | None = None

    @classmethod
    def from_args(
        cls,
        args: object,
        *,
        path_root: Path | None = None,
        path_display: str | None = None,
    ) -> StatusFilters:
        kind = str(getattr(args, "kind", "all") or "all").lower()
        if kind not in {"all", "tools", "skill", "skills", "tool"}:
            kind = "all"
        if kind == "tool":
            kind = "tools"
        if kind == "skill":
            kind = "skills"
        tier_raw = getattr(args, "tier", None)
        tier = normalize_tier_filter(str(tier_raw)) if tier_raw else None
        name_raw = getattr(args, "name", None)
        name = (
            str(name_raw).strip().lower()
            if isinstance(name_raw, str) and name_raw.strip()
            else None
        )
        server_raw = getattr(args, "server", None)
        server = (
            _normalize_server_filter(str(server_raw))
            if isinstance(server_raw, str) and server_raw.strip()
            else None
        )
        if server is not None and kind == "all":
            kind = "tools"
        if path_root is not None and kind == "all":
            kind = "skills"
        agent_raw = getattr(args, "agent", None)
        agent = (
            str(agent_raw).strip().lower()
            if isinstance(agent_raw, str) and agent_raw.strip()
            else None
        )
        scope_raw = getattr(args, "scope", None)
        scope = (
            str(scope_raw).strip().lower()
            if isinstance(scope_raw, str)
            and str(scope_raw).strip().lower() in {"user", "workspace"}
            else None
        )
        path_value = str(path_root) if path_root is not None else None
        return cls(
            kind=kind,
            tier=tier,
            name=name,
            server=server,
            agent=agent,
            path=path_value,
            path_display=path_display,
            scope=scope,
        )

    @property
    def overview_mode(self) -> bool:
        return (
            self.kind == "all"
            and self.tier is None
            and self.name is None
            and self.server is None
            and self.path is None
            and self.scope is None
        )

    @property
    def active(self) -> bool:
        return not self.overview_mode

    def as_dict(self) -> dict[str, str]:
        out: dict[str, str] = {}
        if self.kind != "all":
            out["kind"] = self.kind
        if self.tier is not None:
            out["tier"] = self.tier
        if self.name is not None:
            out["name"] = self.name
        if self.server is not None:
            out["server"] = self.server
        if self.agent is not None:
            out["agent"] = self.agent
        if self.path_display is not None:
            out["path"] = self.path_display
        elif self.path is not None:
            out["path"] = self.path
        if self.scope is not None:
            out["scope"] = self.scope
        return out


def _normalize_server_filter(value: str) -> str:
    from cyt.tiers.entity_origin import _normalize_server_name

    return _normalize_server_name(value.strip().lower())


def resolve_status_path_filter(
    raw: str | None,
    workspace_root: Path | None,
) -> tuple[Path | None, str | None, str | None]:
    """Resolve ``--path`` to an absolute directory.

    Returns ``(resolved_path, display_path, error_message)``.
    """
    if not isinstance(raw, str) or not raw.strip():
        return None, None, None
    display = raw.strip()
    from cyt.skills.directories import resolve_skill_directory_path

    resolved = resolve_skill_directory_path(display, workspace_root)
    if resolved is None:
        return None, display, f"invalid --path value: {display!r}"
    return resolved, display, None


def skill_matches_path_filter(source_path: str | None, filter_root: Path) -> bool:
    if not isinstance(source_path, str) or not source_path.strip():
        return False
    try:
        resolved = Path(source_path).expanduser().resolve()
        root = filter_root.expanduser().resolve()
        resolved.relative_to(root)
        return True
    except (OSError, ValueError):
        return False


def normalize_tier_filter(value: str) -> str | None:
    text = value.strip().upper()
    if not text:
        return None
    if re.fullmatch(r"T?[0-4]", text):
        digit = text[-1]
        return f"T{digit}"
    return None


def flatten_status_entities(payload: dict[str, Any]) -> list[dict[str, Any]]:
    entities: list[dict[str, Any]] = []
    for kind in ("tools", "skills"):
        kind_block = payload.get(kind)
        if not isinstance(kind_block, dict):
            continue
        by_tier = kind_block.get("by_tier")
        if not isinstance(by_tier, dict):
            continue
        for tier_label, items in by_tier.items():
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                record = dict(item)
                record["kind"] = "tool" if kind == "tools" else "skill"
                record["tier_bucket"] = str(tier_label)
                entities.append(record)
    entities.sort(key=_status_entity_sort_key)
    return entities


def _entity_sort_key(entity: dict[str, Any]) -> str:
    display = entity_display_label(entity).lower()
    return display or str(entity.get("entity_id", "")).lower()


def _status_entity_sort_key(entity: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(entity.get("kind", "")),
        str(entity.get("effective_tier") or entity.get("tier_bucket") or ""),
        str(entity.get("base_tier", "")),
        _entity_sort_key(entity),
    )


def _sort_status_entities(entities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(entities, key=_status_entity_sort_key)


def entity_display_label(entity: dict[str, Any]) -> str:
    kind = str(entity.get("kind") or "")
    display_name = entity.get("display_name")
    if isinstance(display_name, str) and display_name.strip():
        return display_name.strip()
    if kind == "skill":
        name = entity.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
        doc_id = entity.get("doc_id")
        if isinstance(doc_id, str) and doc_id.strip():
            return doc_id.strip()
    entity_id = str(entity.get("entity_id") or "")
    return entity_id.rsplit(":", 1)[-1] if ":" in entity_id else entity_id


def entity_matches_server(entity: dict[str, Any], needle: str) -> bool:
    from cyt.tiers.entity_origin import _normalize_server_name, infer_entity_mcp_server

    if str(entity.get("kind") or "") != "tool":
        return False
    server = infer_entity_mcp_server(entity)
    if not server:
        return False
    return _normalize_server_name(server.lower()) == needle


def entity_matches_scope(entity: dict[str, Any], scope: str) -> bool:
    value = entity.get("scope")
    if not isinstance(value, str) or not value.strip():
        return False
    return value.strip().lower() == scope.strip().lower()


def entity_matches_name(entity: dict[str, Any], needle: str) -> bool:
    haystacks: list[str] = []
    entity_id = str(entity.get("entity_id") or "")
    haystacks.append(entity_id)
    haystacks.append(entity_display_label(entity))
    for key in ("name", "doc_id", "source_path"):
        value = entity.get(key)
        if isinstance(value, str) and value.strip():
            haystacks.append(value)
    return any(needle in text.lower() for text in haystacks if text)


def filter_status_entities(
    entities: list[dict[str, Any]],
    filters: StatusFilters,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for entity in entities:
        kind = str(entity.get("kind") or "")
        if filters.kind == "tools" and kind != "tool":
            continue
        if filters.kind == "skills" and kind != "skill":
            continue
        effective = str(entity.get("effective_tier") or entity.get("tier_bucket") or "")
        if filters.tier is not None and effective != filters.tier:
            continue
        if filters.name is not None and not entity_matches_name(entity, filters.name):
            continue
        if filters.server is not None and not entity_matches_server(entity, filters.server):
            continue
        if filters.scope is not None and not entity_matches_scope(entity, filters.scope):
            continue
        if filters.path is not None and kind == "skill":
            if not skill_matches_path_filter(
                entity.get("source_path") if isinstance(entity.get("source_path"), str) else None,
                Path(filters.path),
            ):
                continue
        out.append(entity)
    return out


_FILTERED_JSON_HEADER_KEYS = (
    "project_id",
    "root_path",
    "agent",
    "epoch_id",
    "session_id",
    "epoch_start_ms",
    "last_request_ms",
)


def _entity_for_filtered_json(entity: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in entity.items() if key != "tier_bucket"}


def _filtered_status_view(
    payload: dict[str, Any],
    *,
    filtered: list[dict[str, Any]],
    entity_total: int,
    filters: StatusFilters,
) -> dict[str, Any]:
    view = {key: payload[key] for key in _FILTERED_JSON_HEADER_KEYS if key in payload}
    view["mode"] = "filtered"
    view["filters"] = filters.as_dict()
    view["entity_total"] = entity_total
    view["entity_count"] = len(filtered)
    view["entities"] = [_entity_for_filtered_json(entity) for entity in filtered]
    return view


def apply_status_view(payload: dict[str, Any], filters: StatusFilters) -> dict[str, Any]:
    if filters.overview_mode:
        view = dict(payload)
        view["mode"] = "overview"
        return view
    entities = flatten_status_entities(payload)
    filtered = filter_status_entities(entities, filters)
    if filters.active:
        return _filtered_status_view(
            payload,
            filtered=filtered,
            entity_total=len(entities),
            filters=filters,
        )
    view = dict(payload)
    view["entities"] = filtered
    view["entity_total"] = len(entities)
    view["entity_count"] = len(filtered)
    return view


def _format_histogram(counts: dict[str, Any]) -> str:
    parts: list[str] = []
    for label in _TIER_LABELS:
        value = counts.get(label, 0)
        if value:
            parts.append(f"{label}={value}")
    return " ".join(parts) if parts else "none"


def _format_stats(stats: object) -> str:
    if not isinstance(stats, dict):
        return ""
    keys = (
        "candidates",
        "injected",
        "used",
        "used_without_injection",
        "optional_used",
        "shadow_hits",
        "shadow_evaluations",
    )
    parts = [f"{key}={stats[key]}" for key in keys if key in stats]
    return " ".join(parts)


def _format_scores(scores: object) -> str:
    if not isinstance(scores, dict):
        return ""
    parts = [
        f"{key}={scores[key]:.3f}"
        for key in sorted(scores)
        if isinstance(scores.get(key), (int, float))
    ]
    return " ".join(parts)


def _format_entity_source_path(entity: dict[str, Any]) -> str | None:
    from cyt.tiers.entity_origin import format_source_path_display

    source_path = entity.get("source_path")
    if not isinstance(source_path, str) or not source_path:
        return None
    source_line = entity.get("source_line")
    line_number = source_line if isinstance(source_line, int) else None
    return format_source_path_display(
        source_path,
        source_line=line_number,
        include_line=str(entity.get("kind") or "") == "tool" and line_number is not None,
    )


def format_entity_basic_line(
    entity: dict[str, Any],
    *,
    include_source_path: bool = False,
    omit_kind_prefix: bool = False,
) -> str:
    """One-line tool/skill summary for server and other list filters."""
    kind = str(entity.get("kind") or "?")
    label = entity_display_label(entity)
    if omit_kind_prefix:
        line = f"  {label}  effective={entity.get('effective_tier')} base={entity.get('base_tier')}"
    else:
        line = f"[{kind}] {label}  effective={entity.get('effective_tier')} base={entity.get('base_tier')}"
    if entity.get("temporary_tier"):
        line += f" temporary={entity.get('temporary_tier')}"
    if include_source_path:
        source_path = _format_entity_source_path(entity) or entity.get("source_path")
        if isinstance(source_path, str) and source_path.strip():
            line += f"  {source_path.strip()}"
    return line


def format_entity_summary(
    entity: dict[str, Any],
    *,
    show_base: bool = True,
    indent: int = 2,
) -> str:
    label = entity_display_label(entity)
    prefix = " " * indent
    line = f"{prefix}{label}"
    if show_base:
        base = entity.get("base_tier")
        if base:
            line += f" base={base}"
    return line


def format_entity_detail(entity: dict[str, Any]) -> str:
    kind = str(entity.get("kind") or "?")
    label = entity_display_label(entity)
    lines = [format_entity_basic_line(entity)]
    entity_id = entity.get("entity_id")
    if isinstance(entity_id, str) and entity_id and entity_id != label:
        lines.append(f"  entity_id: {entity_id}")
    scope = entity.get("scope")
    if isinstance(scope, str) and scope:
        lines.append(f"  scope: {scope}")
    source_path = _format_entity_source_path(entity)
    if source_path:
        lines.append(f"  source_path: {source_path}")
    catalog_source = entity.get("catalog_source")
    if isinstance(catalog_source, str) and catalog_source and kind == "tool":
        lines.append(f"  catalog_source: {catalog_source}")
    mcp_server = entity.get("mcp_server")
    if isinstance(mcp_server, str) and mcp_server and kind == "tool":
        lines.append(f"  mcp_server: {mcp_server}")
    policy = entity.get("policy")
    if policy:
        lines.append(f"  policy: {policy}")
    stats = _format_stats(entity.get("stats"))
    if stats:
        lines.append(f"  stats: {stats}")
    scores = _format_scores(entity.get("scores"))
    if scores:
        lines.append(f"  scores: {scores}")
    hints = entity.get("hints")
    if isinstance(hints, list) and hints:
        lines.append(f"  hints: {', '.join(str(item) for item in hints)}")
    return "\n".join(lines)


def format_project_header(payload: dict[str, Any]) -> str:
    lines = [
        f"project_id: {payload.get('project_id')}",
        f"root_path: {payload.get('root_path')}",
    ]
    agent = payload.get("agent")
    if isinstance(agent, str) and agent.strip():
        lines.append(f"agent: {agent.strip()}")
    return "\n".join(lines)


def _entities_from_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    entities = payload.get("entities")
    if not isinstance(entities, list):
        return []
    return [entity for entity in entities if isinstance(entity, dict)]


def _show_entity_detail(
    *,
    filters: StatusFilters,
    verbose: bool,
    entity_count: int,
) -> bool:
    if verbose:
        return True
    if filters.name is not None:
        return True
    if filters.path is not None and entity_count == 1:
        return True
    return False


def _append_grouped_entity_summaries(
    lines: list[str],
    entities: list[dict[str, Any]],
) -> None:
    current_kind: str | None = None
    current_effective: str | None = None
    current_base: str | None = None
    for entity in entities:
        kind = str(entity.get("kind") or "")
        effective = str(entity.get("effective_tier") or entity.get("tier_bucket") or "?")
        base = str(entity.get("base_tier") or "?")
        if kind != current_kind:
            current_kind = kind
            current_effective = None
            current_base = None
            lines.append("")
            lines.append(f"=== {kind}s ===")
        if effective != current_effective:
            if current_effective is not None:
                lines.append("")
            current_effective = effective
            current_base = None
            lines.append(f"-- Effective {effective} --")
        if base != current_base:
            current_base = base
            lines.append(f"  -- Base {base} --")
        lines.append(format_entity_summary(entity, show_base=False, indent=4))


def _append_compact_entity_summaries(
    lines: list[str],
    entities: list[dict[str, Any]],
    *,
    include_source_path: bool = False,
    omit_kind_prefix: bool = False,
) -> None:
    current_kind: str | None = None
    for entity in entities:
        kind = str(entity.get("kind") or "")
        if kind != current_kind:
            current_kind = kind
            lines.append("")
            lines.append(f"=== {kind}s ===")
        lines.append(
            format_entity_basic_line(
                entity,
                include_source_path=include_source_path,
                omit_kind_prefix=omit_kind_prefix,
            ),
        )


def _append_verbose_status_header(lines: list[str], payload: dict[str, Any]) -> None:
    lines.append(
        f"epoch_id: {payload.get('epoch_id')} session_id: {payload.get('session_id')}",
    )
    if payload.get("epoch_start_ms"):
        lines.append(
            f"epoch_start_ms: {payload.get('epoch_start_ms')} "
            f"last_request_ms: {payload.get('last_request_ms')}",
        )

    histogram = payload.get("histogram")
    if isinstance(histogram, dict):
        for kind, counts in histogram.items():
            if isinstance(counts, dict):
                lines.append(f"{kind}: {_format_histogram(counts)}")

    for kind in ("tools", "skills"):
        block = payload.get(kind)
        if not isinstance(block, dict):
            continue
        enabled = block.get("enabled")
        shadow = block.get("shadow")
        hist = block.get("histogram")
        hist_text = _format_histogram(hist) if isinstance(hist, dict) else ""
        lines.append(f"{kind}: enabled={enabled} shadow={shadow} ({hist_text})")


def format_status_text(
    payload: dict[str, Any],
    *,
    filters: StatusFilters,
    verbose: bool = False,
) -> str:
    if filters.overview_mode:
        from cyt.tiers.status_overview import format_overview_text

        return format_overview_text(payload, verbose=verbose)

    lines: list[str] = format_project_header(payload).splitlines()
    entities = _sort_status_entities(_entities_from_payload(payload))
    shown = int(payload.get("entity_count", len(entities)))
    total = int(payload.get("entity_total", len(entities)))
    detail = _show_entity_detail(filters=filters, verbose=verbose, entity_count=shown)

    if verbose:
        _append_verbose_status_header(lines, payload)

    if filters.active:
        lines.append(f"filters: {json.dumps(filters.as_dict(), sort_keys=True)}")
        lines.append(f"entities: showing {shown} of {total}")

    if detail:
        current_kind: str | None = None
        for entity in entities:
            kind = str(entity.get("kind") or "")
            if kind != current_kind:
                current_kind = kind
                lines.append("")
                lines.append(f"=== {kind}s ===")
            lines.append(format_entity_detail(entity))
            lines.append("")
    elif filters.path is not None:
        _append_compact_entity_summaries(
            lines,
            entities,
            include_source_path=not detail,
            omit_kind_prefix=True,
        )
    else:
        _append_grouped_entity_summaries(lines, entities)

    return "\n".join(lines).rstrip() + "\n"


def add_status_filter_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Show epoch/config histograms and full per-entity details",
    )
    parser.add_argument(
        "--kind",
        choices=("all", "tools", "skills"),
        default="all",
        help="Limit listing to tools or skills (default: all)",
    )
    parser.add_argument(
        "--tier",
        metavar="T0-T4",
        help="Filter by effective tier (example: T2 or 2)",
    )
    parser.add_argument(
        "--name",
        metavar="TEXT",
        help="Case-insensitive substring filter on tool/skill name or entity id",
    )
    parser.add_argument(
        "--server",
        metavar="NAME",
        help="Filter tools by MCP server name (example: context-mode or fff)",
    )
    parser.add_argument(
        "--agent",
        choices=("cursor", "claude", "codex"),
        help="Limit skills to an agent's directories (default: mcp-config default_agent)",
    )
    parser.add_argument(
        "--path",
        metavar="DIR",
        help="List skills discovered under DIR (must be a configured skill root or subdirectory)",
    )
    parser.add_argument(
        "--scope",
        choices=("user", "workspace"),
        help="Limit entities to user (global) or workspace scope",
    )
