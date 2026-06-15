"""Diagnostics printing for skills frontmatter gate and search scoring."""

from __future__ import annotations

import sys
from collections.abc import Sequence
from typing import Any

from cyt.skills.diagnostics import (
    BudgetItemRow,
    FrontmatterGateRow,
    FrontmatterTokenContribution,
    SearchItemRow,
    SkillsSearchTrace,
)


def _format_score(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.4f}"


def _format_gate_score(row: FrontmatterGateRow) -> str:
    return _format_score(row.score)


def _format_term_list(terms: tuple[str, ...], *, fallback: str) -> str:
    if terms:
        return ", ".join(terms)
    return fallback


def _format_text_table(
    headers: tuple[str, ...],
    rows: list[Sequence[str]],
    *,
    indent: str = "    ",
) -> str:
    if not rows:
        return ""
    widths = [len(header) for header in headers]
    for row in rows:
        for idx, cell in enumerate(row):
            widths[idx] = max(widths[idx], len(cell))
    header_line = "  ".join(header.ljust(widths[idx]) for idx, header in enumerate(headers))
    divider = "  ".join("-" * widths[idx] for idx in range(len(headers)))
    body_lines = [
        "  ".join(cell.ljust(widths[idx]) for idx, cell in enumerate(row)) for row in rows
    ]
    lines = [header_line, divider, *body_lines]
    return "\n".join(f"{indent}{line}" for line in lines)


def _print_frontmatter_contributions(
    contributions: tuple[FrontmatterTokenContribution, ...],
) -> None:
    if not contributions:
        print("    (no overlapping query/frontmatter tokens)", file=sys.stderr)
        return
    rows: list[Sequence[str]] = [
        (
            contrib.stem,
            f"{contrib.score:.4f}",
            _format_term_list(contrib.query_terms, fallback=contrib.stem),
            _format_term_list(contrib.frontmatter_terms, fallback=contrib.stem),
        )
        for contrib in contributions
    ]
    print(
        _format_text_table(("stem", "similarity", "query", "frontmatter"), rows),
        file=sys.stderr,
    )


def _print_frontmatter_gate_rows(
    rows: list[FrontmatterGateRow],
    *,
    limit: float,
    verbose: bool,
) -> None:
    print(
        f"skills.frontmatter gate (BM25 similarity [0-1], block when score >= {limit:.4f}):\n",
        file=sys.stderr,
    )
    for row in sorted(rows, key=lambda item: (not item.passed, item.file_path)):
        if not verbose and row.passed:
            continue
        status = "pass" if row.passed else "blocked"
        print(
            f"  {row.file_path}  score={_format_gate_score(row)}  {status}",
            file=sys.stderr,
        )
        if verbose:
            _print_frontmatter_contributions(row.contributions)


def _print_budget_item_rows(
    rows: list[BudgetItemRow],
    *,
    item_kind: str,
    max_tokens: int,
    verbose: bool,
) -> None:
    if not rows:
        print(f"\nskills.inject budget: {max_tokens} tokens\n", file=sys.stderr)
        return

    visible_rows = [
        row
        for row in sorted(
            rows,
            key=lambda item: (not item.passed, -item.score, item.file_path, item.item_id),
        )
        if verbose or row.passed
    ]
    print(f"\nskills.inject budget: {max_tokens} tokens", file=sys.stderr)
    if not visible_rows:
        print(file=sys.stderr)
        return

    print(
        f"  ({item_kind}, pass when wrapped tokens fit within {max_tokens}):",
        file=sys.stderr,
    )
    table_rows: list[Sequence[str]] = [
        (
            row.file_path,
            row.item_id,
            f"{row.score:.4f}",
            str(row.tokens),
            "pass" if row.passed else "below",
        )
        for row in visible_rows
    ]
    print(
        _format_text_table(("file", item_kind, "score", "tokens", "status"), table_rows),
        file=sys.stderr,
    )
    print(file=sys.stderr)


def _print_search_item_rows(
    rows: list[SearchItemRow],
    *,
    item_kind: str,
    threshold: float | None,
    verbose: bool,
) -> None:
    if not rows:
        print(f"\nskills.search ({item_kind}): (no scored items)", file=sys.stderr)
        return

    if threshold is None:
        threshold_label = "selected by LLM"
    else:
        threshold_label = f"pass when score >= {threshold:.4f}"

    visible_rows = [
        row
        for row in sorted(
            rows,
            key=lambda item: (not item.passed, -item.score, item.file_path, item.item_id),
        )
        if verbose or row.passed
    ]
    if not visible_rows:
        return

    print(f"\nskills.search ({item_kind}, {threshold_label}):", file=sys.stderr)
    by_path: dict[str, list[SearchItemRow]] = {}
    for row in visible_rows:
        by_path.setdefault(row.file_path, []).append(row)

    for file_path in sorted(by_path):
        print(f"  {file_path}", file=sys.stderr)
        table_rows: list[Sequence[str]] = [
            (
                row.item_id,
                f"{row.score:.4f}",
                "pass" if row.passed else "below",
            )
            for row in by_path[file_path]
        ]
        print(
            _format_text_table((item_kind, "score", "status"), table_rows),
            file=sys.stderr,
        )


def print_skills_search_trace(trace: SkillsSearchTrace, *, debug: bool) -> None:
    """Print gate and search scores to stderr."""
    eligible_paths = {row.file_path for row in trace.frontmatter_rows if row.passed}
    eligible_search_rows = [row for row in trace.search_rows if row.file_path in eligible_paths]

    if debug:
        _print_frontmatter_gate_rows(
            trace.frontmatter_rows,
            limit=trace.frontmatter_limit,
            verbose=True,
        )
    else:
        passed_gate_rows = [row for row in trace.frontmatter_rows if row.passed]
        if passed_gate_rows:
            print(
                f"skills.frontmatter gate (BM25 similarity [0-1], block when score >= "
                f"{trace.frontmatter_limit:.4f}):\n",
                file=sys.stderr,
            )
            for row in sorted(passed_gate_rows, key=lambda item: item.file_path):
                print(
                    f"  {row.file_path}  score={_format_gate_score(row)}  pass",
                    file=sys.stderr,
                )

    if trace.search_item_kind and eligible_search_rows:
        _print_search_item_rows(
            eligible_search_rows,
            item_kind=trace.search_item_kind,
            threshold=trace.search_score_threshold,
            verbose=debug,
        )

    if trace.inject_budget_max is not None:
        item_kind = trace.search_item_kind or "skill"
        if trace.budget_rows:
            _print_budget_item_rows(
                trace.budget_rows,
                item_kind=item_kind,
                max_tokens=trace.inject_budget_max,
                verbose=debug,
            )
        else:
            print(f"\nskills.inject budget: {trace.inject_budget_max} tokens\n", file=sys.stderr)
            if trace.pre_budget_matches:
                print("\nskills.inject budget blocked:\n", file=sys.stderr)
                for match in trace.pre_budget_matches:
                    print(
                        f"  {match.file_path}  reconstructed={match.token_count} tokens  "
                        "exceeds budget",
                        file=sys.stderr,
                    )


def trace_to_debug_details(trace: SkillsSearchTrace) -> dict[str, Any]:
    """Serialize trace for hook debug JSON logs."""
    return {
        "frontmatter_limit": trace.frontmatter_limit,
        "frontmatter_gate": [
            {
                "file_path": row.file_path,
                "doc_id": row.doc_id,
                "score": row.score,
                "raw_score": row.raw_score,
                "passed": row.passed,
                "contributions": [
                    {
                        "stem": contrib.stem,
                        "score": contrib.score,
                        "query_terms": list(contrib.query_terms),
                        "frontmatter_terms": list(contrib.frontmatter_terms),
                    }
                    for contrib in row.contributions
                ],
            }
            for row in trace.frontmatter_rows
        ],
        "search_item_kind": trace.search_item_kind,
        "search_score_threshold": trace.search_score_threshold,
        "search_items": [
            {
                "file_path": row.file_path,
                "doc_id": row.doc_id,
                "item_kind": row.item_kind,
                "item_id": row.item_id,
                "score": row.score,
                "passed": row.passed,
            }
            for row in trace.search_rows
        ],
        "pipeline_run": trace.pipeline_run,
        "inject_budget_max": trace.inject_budget_max,
        "pre_budget_matches": [
            {
                "file_path": match.file_path,
                "name": match.name,
                "token_count": match.token_count,
                "score": match.score,
            }
            for match in trace.pre_budget_matches
        ],
        "budget_items": [
            {
                "file_path": row.file_path,
                "item_kind": row.item_kind,
                "item_id": row.item_id,
                "score": row.score,
                "tokens": row.tokens,
                "passed": row.passed,
            }
            for row in trace.budget_rows
        ],
        "injected": trace.injected,
    }
