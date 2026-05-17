import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from litellm import rerank
from src.split_bulks import count_tokens, split_into_bulks

DECOMPOSED_SCORE: float = 0.5
RERANK_ENUMS: bool = True
RERANK_MODEL: str = "deepinfra/Qwen/Qwen3-Reranker-8B"


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
    """Load environment variables from src/.env if it exists."""
    env_path = Path("src/.env")
    if env_path.exists():
        load_dotenv(dotenv_path=env_path)


def process_response(response: Any, valid_indices: list[int], items: list[dict[str, Any]]) -> None:
    """Processes the rerank response and updates item scores."""
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
            # Store as string with 20 decimal places to avoid scientific notation in JSON
            items[original_idx]["score"] = f"{relevance_score:.20f}"
        except (KeyError, TypeError, IndexError) as e:
            print(f"Debug: Error processing result {result}: {e}", file=sys.stderr)
            continue


def rerank_items(
    query: str,
    items: list[dict[str, Any]],
    api_key: str | None,
    extract_fn: Any,
    min_score: float | None = None,
) -> list[dict[str, Any]]:
    """Generic reranking logic for both json and md items."""
    documents = []
    valid_indices = []

    for i, item in enumerate(items):
        item["score"] = f"{0.0:.20f}"
        doc_text = extract_fn(item)
        if doc_text:
            documents.append(doc_text)
            valid_indices.append(i)

    if not documents:
        return items

    # Calculate base overhead (query)
    base_tokens = count_tokens(query) + 200  # buffer for wrapper tokens

    # Zip indices and documents to keep them together during splitting
    indexed_docs = list(zip(valid_indices, documents))

    try:
        bulks = split_into_bulks(
            items=indexed_docs,
            transform_fn=lambda x: x[1],  # text is the document
            base_tokens=base_tokens
        )

        for bulk in bulks:
            bulk_indices = [x[0] for x in bulk]
            bulk_docs = [x[1] for x in bulk]

            response = rerank(
                model=RERANK_MODEL,
                query=query,
                documents=bulk_docs,
                api_key=api_key,
            )
            process_response(response, bulk_indices, items)

        # Sort using float to ensure correct numerical order
        items.sort(key=lambda x: float(x.get("score", 0)), reverse=True)
        if min_score is not None:
            return [item for item in items if float(item.get("score", 0)) >= min_score]
    except Exception as e:
        print(f"Error during reranking: {e}", file=sys.stderr)

    return items


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

    # Process "json" key if present
    if "json" in data and isinstance(data["json"], list):
        data["json"] = rerank_items(
            args.query,
            data["json"],
            api_key,
            extract_document_text,
            DECOMPOSED_SCORE,
        )

    # Process "md" key (enums) if RERANK_ENUMS is true and "md" exists
    if RERANK_ENUMS and "md" in data and isinstance(data["md"], list):

        def extract_md_content(item: dict[str, Any]) -> str | None:
            content = item.get("content")
            return str(content) if content else None

        data["md"] = rerank_items(
            args.query,
            data["md"],
            api_key,
            extract_md_content,
            None,  # Not filtering enums by score based on original logic
        )

    print(json.dumps(data, indent=2))


if __name__ == "__main__":
    main()
