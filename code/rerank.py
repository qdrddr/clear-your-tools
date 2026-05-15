import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from litellm import rerank

DECOMPOSED_SCORE: float = 0.5
ENUM_SCORE: float = 0.2
RERANK_ENUMS: bool = False

def extract_level_info(data: Any) -> list[str]:
    """
    Recursively searches for description, default, and enum keys at all levels.
    Returns a list of formatted strings, one for each level where at least a description is found.
    """
    results = []

    if isinstance(data, dict):
        # Extract from current level
        desc = data.get("description")
        default_val = data.get("default")
        enums = data.get("enum")

        if desc:
            line = str(desc)
            if default_val is not None:
                line += f"; Default: {default_val}"
            if enums and isinstance(enums, list):
                enums_str = ", ".join(map(str, enums))
                line += f"; Options: {enums_str}"
            results.append(line)

        # Recurse into all values
        for val in data.values():
            results.extend(extract_level_info(val))

    elif isinstance(data, list):
        for item in data:
            results.extend(extract_level_info(item))

    return results


def extract_document_text(item_content: Any) -> str | None:
    """
    Combines information from all levels, with each level on its own newline.
    """
    level_lines = extract_level_info(item_content)
    if not level_lines:
        return None
    return "\n".join(level_lines)


def load_env() -> None:
    """Load environment variables from code/.env if it exists."""
    env_path = Path("code/.env")
    if env_path.exists():
        load_dotenv(dotenv_path=env_path)


def process_response(response: Any, valid_indices: list[int], items: list[dict[str, Any]]) -> None:
    """Processes the rerank response and updates item scores."""
    new_scores = [0.0] * len(items)

    # LiteLLM's rerank response usually has a 'results' attribute or key
    results_list = []
    if hasattr(response, "results"):
        results_list = response.results
    elif isinstance(response, dict) and "results" in response:
        results_list = response["results"]
    else:
        # Fallback if it's already a list
        results_list = response

    for result in results_list:
        try:
            # Try attribute access first
            doc_idx = getattr(result, "index", None)
            relevance_score = getattr(result, "relevance_score", None)

            # Fallback to dictionary access
            if doc_idx is None:
                doc_idx = result["index"]
            if relevance_score is None:
                relevance_score = result["relevance_score"]

            original_idx = valid_indices[doc_idx]
            new_scores[original_idx] = relevance_score
        except (KeyError, TypeError, IndexError) as e:
            print(f"Debug: Error processing result {result}: {e}", file=sys.stderr)
            continue

    for i, score in enumerate(new_scores):
        # Store as string with 20 decimal places to avoid scientific notation in JSON
        items[i]["score"] = f"{score:.20f}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Rerank JSON items using DeepInfra and LiteLLM.")
    parser.add_argument("--json", required=True, help="Input JSON file path")
    parser.add_argument("command", choices=["search"], help="Command to run")
    parser.add_argument("query", help="Search query")

    args = parser.parse_args()

    load_env()
    api_key = os.environ.get("DEEPINFRA_API_KEY")

    try:
        with open(args.json) as f:
            data = json.load(f)
    except Exception as e:
        print(f"Error reading JSON: {e}", file=sys.stderr)
        sys.exit(1)

    if not isinstance(data, dict) or "json" not in data or not isinstance(data["json"], list):
        print("Error: JSON must contain a 'json' key with an array of items.", file=sys.stderr)
        sys.exit(1)

    items = data["json"]
    documents = []
    valid_indices = []

    for i, item in enumerate(items):
        doc_text = extract_document_text(item)
        if doc_text:
            documents.append(doc_text)
            valid_indices.append(i)

    if not api_key:
        print("Error: DEEPINFRA_API_KEY not found in environment or code/.env", file=sys.stderr)
        sys.exit(1)

    if not documents:
        print("No valid documents found for reranking.", file=sys.stderr)
        print(json.dumps(data, indent=2))
        return

    try:
        response = rerank(
            model="deepinfra/Qwen/Qwen3-Reranker-8B",
            query=args.query,
            documents=documents,
            api_key=api_key,
        )

        process_response(response, valid_indices, items)
        # Sort using float to ensure correct numerical order
        items.sort(key=lambda x: float(x["score"]), reverse=True)
        # Exclude items with score less than DECOMPOSED_SCORE
        data["json"] = [item for item in items if float(item["score"]) >= DECOMPOSED_SCORE]
        print(json.dumps(data, indent=2))

    except Exception as e:
        print(f"Error during reranking: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
