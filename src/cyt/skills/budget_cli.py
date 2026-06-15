"""CLI for skills budget visibility."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from cyt.config import load_config
from cyt.skills.budget import format_skills_budget_report, skills_budget_report_json


def run_budget(args: argparse.Namespace) -> None:
    config = load_config(getattr(args, "config", None))
    rate = args.savings_rate
    if args.json:
        payload = skills_budget_report_json(
            config,
            example_request_tokens=args.request_tokens,
            example_savings_tokens=args.savings_tokens,
            example_savings_rate=rate,
        )
        print(json.dumps(payload, indent=2))
        return

    report = format_skills_budget_report(
        config,
        example_request_tokens=args.request_tokens,
        example_savings_tokens=args.savings_tokens,
        example_savings_rate=rate,
    )
    print(report)


def add_skills_budget_parser(subparsers: argparse._SubParsersAction) -> None:
    budget = subparsers.add_parser(
        "budget",
        help="Show skills injection budget status and worked examples",
    )
    budget.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to config.yaml",
    )
    budget.add_argument(
        "--request-tokens",
        type=int,
        default=100_000,
        help="Example request token count for budget math",
    )
    budget.add_argument(
        "--savings-tokens",
        type=int,
        default=10_000,
        help="Example proxy savings token count",
    )
    budget.add_argument(
        "--savings-rate",
        type=float,
        default=None,
        help="Example savings rate (default: savings_tokens / request_tokens)",
    )
    budget.add_argument("--json", action="store_true", help="Emit JSON report")
    budget.set_defaults(skills_handler=run_budget)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Skills budget report")
    add_skills_budget_parser(parser.add_subparsers(dest="skills_command", required=True))
    args = parser.parse_args(argv)
    handler = getattr(args, "skills_handler", None)
    if handler is None:
        parser.error("skills subcommand required")
    handler(args)


if __name__ == "__main__":
    main(sys.argv[1:])
