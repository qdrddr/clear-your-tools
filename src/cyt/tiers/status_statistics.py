"""Tier statistics aggregation and formatting for ``tiers stats`` overview mode."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

TableRowFormatter = Callable[[list[str], list[int]], str]

_TIER_LABELS = tuple(f"T{i}" for i in range(5))
_TIER_DISPLAY_ORDER = tuple(reversed(_TIER_LABELS))
_EFFECTIVE_BOUND_TIERS = frozenset({"T1", "T2", "T3", "T4"})


def _bounded_effective_tokens(
    *,
    tier: str,
    carried: int | None,
    effective: int | None,
) -> int | None:
    """Ensure tier-scoped effective counts never exceed carried (T4) tokens."""
    if effective is None:
        return None
    if tier in _EFFECTIVE_BOUND_TIERS and carried is not None:
        return min(effective, carried)
    return effective


def format_compact_tokens(tokens: int) -> str:
    """Format token counts for human-readable tables (``10342`` → ``10.3k``)."""
    if tokens < 1000:
        return str(tokens)
    if tokens < 1_000_000:
        thousands = tokens / 1000.0
        if tokens >= 10000 and abs(thousands - round(thousands)) < 0.05:
            return f"{round(thousands):.0f}k"
        return f"{thousands:.1f}k"
    millions = tokens / 1_000_000.0
    if abs(millions - round(millions)) < 0.05:
        return f"{round(millions):.0f}M"
    return f"{millions:.1f}M"


def _format_tokens_cell(*, tokens: int, tokens_known: int, count: int) -> str:
    if count <= 0:
        return "0"
    if tokens_known <= 0:
        return "—"
    return format_compact_tokens(tokens)


def _carried_tool_tokens(
    entity: dict[str, Any],
    *,
    catalog_by_entity_id: dict[str, dict[str, Any]],
) -> int | None:
    entity_id = str(entity.get("entity_id") or "").strip()
    catalog_tool = catalog_by_entity_id.get(entity_id)
    if catalog_tool is None:
        return None

    from cyt.tiers.tool_token_materialization import carried_tool_token_count

    return carried_tool_token_count(catalog_tool)


def _effective_tool_tokens(
    entity: dict[str, Any],
    *,
    tier: str,
    catalog_by_entity_id: dict[str, dict[str, Any]],
) -> int | None:
    if tier == "T0":
        return 0

    entity_id = str(entity.get("entity_id") or "").strip()
    catalog_tool = catalog_by_entity_id.get(entity_id)
    if catalog_tool is None:
        return None

    from cyt.tiers.tool_token_materialization import effective_tool_token_count

    return effective_tool_token_count(catalog_tool, tier)


def _skill_carried_tokens(entity: dict[str, Any]) -> int | None:
    from cyt.tiers.skill_token_materialization import carried_skill_token_count

    return carried_skill_token_count(entity)


def _effective_skill_tokens(entity: dict[str, Any], *, tier: str) -> int | None:
    if tier == "T0":
        return 0

    from cyt.tiers.skill_token_materialization import effective_skill_token_count

    return effective_skill_token_count(entity, tier)


def _aggregate_activity(by_tier: dict[str, Any]) -> dict[str, float | int]:
    injected = 0.0
    used = 0.0
    injected_entities = 0
    used_entities = 0
    for items in by_tier.values():
        if not isinstance(items, list):
            continue
        for entity in items:
            if not isinstance(entity, dict):
                continue
            stats = entity.get("stats")
            if not isinstance(stats, dict):
                continue
            raw_injected = stats.get("injected")
            raw_used = stats.get("used")
            if isinstance(raw_injected, (int, float)):
                injected_value = float(raw_injected)
                injected += injected_value
                if injected_value > 0:
                    injected_entities += 1
            if isinstance(raw_used, (int, float)):
                used_value = float(raw_used)
                used += used_value
                if used_value > 0:
                    used_entities += 1
    return {
        "injected": injected,
        "used": used,
        "injected_entities": injected_entities,
        "used_entities": used_entities,
    }


def _tier_row_from_entities(
    *,
    tier: str,
    count: int,
    entities: list[dict[str, Any]],
    kind: str,
    catalog_by_entity_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    temp = 0
    tokens = 0
    tokens_known = 0
    effective_tokens = 0
    effective_tokens_known = 0

    for entity in entities:
        if entity.get("temporary") is True:
            temp += 1
        if kind == "tool":
            carried = _carried_tool_tokens(entity, catalog_by_entity_id=catalog_by_entity_id)
            effective = _effective_tool_tokens(
                entity,
                tier=tier,
                catalog_by_entity_id=catalog_by_entity_id,
            )
        else:
            carried = _skill_carried_tokens(entity)
            effective = _effective_skill_tokens(entity, tier=tier)
        effective = _bounded_effective_tokens(tier=tier, carried=carried, effective=effective)
        if carried is not None:
            tokens += carried
            tokens_known += 1
        if effective is not None:
            effective_tokens += effective
            effective_tokens_known += 1

    return {
        "tier": tier,
        "count": count,
        "temp": temp,
        "tokens": tokens,
        "tokens_known": tokens_known,
        "effective_tokens": effective_tokens,
        "effective_tokens_known": effective_tokens_known,
        "tokens_total": len(entities),
    }


def _sum_rows(rows: list[dict[str, Any]], *, kind: str) -> dict[str, Any]:
    totals: dict[str, Any] = {
        "tier": "Total",
        "count": sum(int(row.get("count", 0)) for row in rows),
        "temp": sum(int(row.get("temp", 0)) for row in rows),
        "tokens": sum(int(row.get("tokens", 0)) for row in rows),
        "tokens_known": sum(int(row.get("tokens_known", 0)) for row in rows),
        "tokens_total": sum(int(row.get("tokens_total", 0)) for row in rows),
    }
    totals["effective_tokens"] = sum(int(row.get("effective_tokens", 0)) for row in rows)
    totals["effective_tokens_known"] = sum(
        int(row.get("effective_tokens_known", 0)) for row in rows
    )
    return totals


def build_kind_tier_statistics(
    kind_block: dict[str, Any],
    *,
    kind: str,
    catalog_tools: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    histogram_raw = kind_block.get("histogram")
    histogram: dict[str, int] = {}
    if isinstance(histogram_raw, dict):
        for label in _TIER_LABELS:
            value = histogram_raw.get(label, 0)
            histogram[label] = int(value) if isinstance(value, int) else 0

    by_tier_raw = kind_block.get("by_tier")
    by_tier: dict[str, list[dict[str, Any]]] = {}
    if isinstance(by_tier_raw, dict):
        for label in _TIER_LABELS:
            items = by_tier_raw.get(label)
            by_tier[label] = (
                [item for item in items if isinstance(item, dict)]
                if isinstance(items, list)
                else []
            )

    from cyt.tiers.tool_token_materialization import catalog_tools_by_entity_id

    catalog_by_entity_id = catalog_tools_by_entity_id(catalog_tools if kind == "tool" else None)
    rows: list[dict[str, Any]] = []
    for label in _TIER_LABELS:
        entities = by_tier.get(label, [])
        count = histogram.get(label, len(entities))
        rows.append(
            _tier_row_from_entities(
                tier=label,
                count=count,
                entities=entities,
                kind=kind,
                catalog_by_entity_id=catalog_by_entity_id,
            ),
        )

    totals = _sum_rows(rows, kind=kind)
    activity = _aggregate_activity(by_tier)
    return {
        "mode": kind_block.get("mode"),
        "rows": rows,
        "totals": totals,
        "activity": {
            "injected": activity["injected"],
            "used": activity["used"],
            "injected_entities": activity["injected_entities"],
            "used_entities": activity["used_entities"],
        },
    }


def build_tier_statistics(
    status: dict[str, Any],
    *,
    catalog_tools: list[dict[str, Any]] | None = None,
    include_skills: bool = True,
) -> dict[str, Any]:
    tools_raw = status.get("tools")
    skills_raw = status.get("skills")
    tools_block: dict[str, Any] = tools_raw if isinstance(tools_raw, dict) else {}
    skills_block: dict[str, Any] = skills_raw if isinstance(skills_raw, dict) else {}
    stats: dict[str, Any] = {
        "tools": build_kind_tier_statistics(
            tools_block,
            kind="tool",
            catalog_tools=catalog_tools,
        ),
    }
    if include_skills:
        stats["skills"] = build_kind_tier_statistics(
            skills_block,
            kind="skill",
            catalog_tools=catalog_tools,
        )
    return stats


def append_kind_tier_statistics_table(
    lines: list[str],
    stats: dict[str, Any],
    *,
    title: str,
    format_table_row: TableRowFormatter,
    show_effective: bool = False,
    entity_label: str = "entities",
) -> None:
    rows = stats.get("rows")
    totals = stats.get("totals")
    if not isinstance(rows, list) or not isinstance(totals, dict):
        return

    lines.append("")
    lines.append(title)
    if show_effective:
        widths = [5, 5, 4, 6, 9]
        header = ["Tier", "Count", "Temp", "Tokens", "Effective"]
    else:
        widths = [5, 5, 4, 6]
        header = ["Tier", "Count", "Temp", "Tokens"]
    lines.append(format_table_row(header, widths))
    rows_by_tier = {
        str(row.get("tier") or ""): row for row in rows if isinstance(row, dict)
    }
    for tier in _TIER_DISPLAY_ORDER:
        row = rows_by_tier.get(tier)
        if row is None:
            continue
        count = int(row.get("count", 0))
        tokens_known = int(row.get("tokens_known", 0))
        tokens = int(row.get("tokens", 0))
        columns = [
            str(row.get("tier") or ""),
            str(count),
            str(int(row.get("temp", 0))),
            _format_tokens_cell(tokens=tokens, tokens_known=tokens_known, count=count),
        ]
        if show_effective:
            effective_known = int(row.get("effective_tokens_known", 0))
            effective = int(row.get("effective_tokens", 0))
            columns.append(
                _format_tokens_cell(
                    tokens=effective,
                    tokens_known=effective_known,
                    count=count,
                ),
            )
        lines.append(format_table_row(columns, widths))

    total_count = int(totals.get("count", 0))
    total_tokens_known = int(totals.get("tokens_known", 0))
    total_tokens = int(totals.get("tokens", 0))
    total_columns = [
        "Total",
        str(total_count),
        str(int(totals.get("temp", 0))),
        _format_tokens_cell(
            tokens=total_tokens,
            tokens_known=total_tokens_known,
            count=total_count,
        ),
    ]
    if show_effective:
        total_effective_known = int(totals.get("effective_tokens_known", 0))
        total_effective = int(totals.get("effective_tokens", 0))
        total_columns.append(
            _format_tokens_cell(
                tokens=total_effective,
                tokens_known=total_effective_known,
                count=total_count,
            ),
        )
    lines.append(format_table_row(total_columns, widths))

    if total_count > 0 and total_tokens_known < total_count:
        lines.append(f"tokens: {total_tokens_known}/{total_count} {entity_label} have metadata")
    if show_effective and total_count > 0:
        total_effective_known = int(totals.get("effective_tokens_known", 0))
        if total_effective_known < total_count:
            lines.append(
                f"effective: {total_effective_known}/{total_count} {entity_label} computed",
            )


def _format_decayed_activity_value(value: object) -> str:
    if not isinstance(value, (int, float)):
        return "0"
    rounded = round(float(value), 1)
    if rounded == int(rounded):
        return str(int(rounded))
    return f"{rounded:.1f}"


def _activity_metric(
    stats: dict[str, Any] | None,
    *,
    metric: str,
    entities_key: str,
) -> tuple[str, str] | None:
    if not isinstance(stats, dict):
        return None
    activity = stats.get("activity")
    if not isinstance(activity, dict) or metric not in activity:
        return None
    value = _format_decayed_activity_value(activity.get(metric))
    entities = activity.get(entities_key)
    entity_count = str(int(entities)) if isinstance(entities, int) else "0"
    return value, entity_count


def _append_historical_activity_lines(
    lines: list[str],
    *,
    tools_stats: dict[str, Any] | None,
    skills_stats: dict[str, Any] | None,
) -> None:
    tools_injected = _activity_metric(
        tools_stats,
        metric="injected",
        entities_key="injected_entities",
    )
    skills_injected = _activity_metric(
        skills_stats,
        metric="injected",
        entities_key="injected_entities",
    )
    tools_used = _activity_metric(tools_stats, metric="used", entities_key="used_entities")
    skills_used = _activity_metric(skills_stats, metric="used", entities_key="used_entities")

    include_skills = skills_stats is not None
    if tools_injected is None and (not include_skills or skills_injected is None):
        return

    def _kind_metric(
        tools: tuple[str, str] | None,
        skills: tuple[str, str] | None,
        *,
        label: str,
    ) -> str:
        tools_text = (
            f"tools {label}={tools[0]} ({tools[1]})"
            if tools is not None
            else f"tools {label}=0 (0)"
        )
        if not include_skills:
            return tools_text
        skills_text = (
            f"skills {label}={skills[0]} ({skills[1]})"
            if skills is not None
            else f"skills {label}=0 (0)"
        )
        return f"{tools_text}  {skills_text}"

    lines.append("")
    lines.append("Historical signals (decayed sum):")
    lines.append(f"  Demand:  {_kind_metric(tools_injected, skills_injected, label='injected')}")
    lines.append(f"  Usage:   {_kind_metric(tools_used, skills_used, label='used')}")


def append_tier_statistics_tables(
    lines: list[str],
    overview: dict[str, Any],
    *,
    format_table_row: TableRowFormatter,
) -> None:
    tier_statistics = overview.get("tier_statistics")
    if not isinstance(tier_statistics, dict):
        return

    tools_stats = tier_statistics.get("tools")
    if isinstance(tools_stats, dict):
        append_kind_tier_statistics_table(
            lines,
            tools_stats,
            title="=== tools ===",
            format_table_row=format_table_row,
            show_effective=True,
            entity_label="tools",
        )

    skills_stats = tier_statistics.get("skills")
    if isinstance(skills_stats, dict):
        append_kind_tier_statistics_table(
            lines,
            skills_stats,
            title="=== skills ===",
            format_table_row=format_table_row,
            show_effective=True,
            entity_label="skills",
        )

    _append_historical_activity_lines(
        lines,
        tools_stats=tools_stats if isinstance(tools_stats, dict) else None,
        skills_stats=skills_stats if isinstance(skills_stats, dict) else None,
    )
