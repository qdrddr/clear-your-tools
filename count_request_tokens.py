"""Estimate token savings on a captured request JSON.

Example usage:
uv run count_request_tokens.py --requestfile temp_example_claude_call.json --tool-savings-percent 95

Output:
    Total Request Tokens: 73624
    Amount of Tools: 86
    Total Tools Tokens: 28159
    Tools reduced tokens (95.0% savings): 1408
    Tokens after pruning: 46873
    Total token savings: 26751 (36.3%)
"""

import argparse
import json

from cyt.indexer.tokens import count_json_tokens

parser = argparse.ArgumentParser()
parser.add_argument("--requestfile", required=True, help="Path to the request JSON file")
parser.add_argument("--tool-savings-percent", type=float, default=None)
args = parser.parse_args()

with open(args.requestfile) as f:
    data = json.load(f)

tokens = count_json_tokens(data)
tools = data.get("tools", [])
tools_tokens = count_json_tokens(tools)

print(f"Total Request Tokens: {tokens}")
print(f"Amount of Tools: {len(tools)}")
print(f"Total Tools Tokens: {tools_tokens}")

if args.tool_savings_percent is not None:
    pct = args.tool_savings_percent
    tools_reduced_tokens = round(tools_tokens * (1 - pct / 100))
    tokens_after_pruning = tokens - tools_tokens + tools_reduced_tokens
    total_savings = tokens - tokens_after_pruning
    total_savings_pct = total_savings / tokens * 100

    print(f"Tools reduced tokens ({pct}% savings): {tools_reduced_tokens}")
    print(f"Tokens after pruning: {tokens_after_pruning}")
    print(f"Total token savings: {total_savings} ({total_savings_pct:.1f}%)")
