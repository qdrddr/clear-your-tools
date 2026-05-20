import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from litellm import rerank
from split_bulks import count_tokens, split_into_bulks

RERANK_SCORE: float = 0.001
RERANK_ENUMS: bool = True
RERANK_ENUM_SCORE: float = 0.0001
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
    """Load environment variables from src/.env next to this module."""
    env_path = Path(__file__).resolve().parent / ".env"
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

        bulk_errors: list[Exception] = []
        any_success = False
        for bulk in bulks:
            bulk_indices = [x[0] for x in bulk]
            bulk_docs = [x[1] for x in bulk]

            try:
                response = rerank(
                    model=RERANK_MODEL,
                    query=query,
                    documents=bulk_docs,
                    api_key=api_key,
                )
                process_response(response, bulk_indices, items)
                any_success = True
            except Exception as bulk_exc:
                bulk_errors.append(bulk_exc)
                print(f"Error during reranking bulk: {bulk_exc}", file=sys.stderr)

        if not any_success and bulk_errors:
            raise RuntimeError(
                f"All rerank bulks failed ({len(bulk_errors)}): {bulk_errors[-1]}",
            ) from bulk_errors[-1]

        # Sort using float to ensure correct numerical order
        items.sort(key=lambda x: float(x.get("score", 0)), reverse=True)
        if min_score is not None:
            return [item for item in items if float(item.get("score", 0)) >= min_score]
    except RuntimeError:
        raise
    except Exception as e:
        print(f"Error during reranking: {e}", file=sys.stderr)

    return items


def _extract_md_content(item: dict[str, Any]) -> str | None:
    content = item.get("content")
    return str(content) if content else None


def prune_reranked_catalog(data: dict[str, Any]) -> dict[str, Any]:
    """Drop catalog items below RERANK_SCORE / RERANK_ENUM_SCORE after rerank_items scored them."""
    json_items = data.get("json")
    if isinstance(json_items, list):
        data["json"] = [
            item for item in json_items if float(item.get("score", 0)) >= RERANK_SCORE
        ]

    if RERANK_ENUMS:
        md_items = data.get("md")
        if isinstance(md_items, list):
            data["md"] = [
                item for item in md_items if float(item.get("score", 0)) >= RERANK_ENUM_SCORE
            ]

    return data


def rerank_catalog_dict(
    data: dict[str, Any],
    query: str,
    *,
    prune: bool = True,
) -> dict[str, Any]:
    """Score in-place data['json'] and optionally data['md']; optionally prune by score."""
    load_env()
    key = os.environ.get("DEEPINFRA_API_KEY")

    if "json" in data and isinstance(data["json"], list):
        data["json"] = rerank_items(
            query,
            data["json"],
            key,
            extract_document_text,
            None,
        )

    if RERANK_ENUMS and "md" in data and isinstance(data["md"], list):
        data["md"] = rerank_items(
            query,
            data["md"],
            key,
            _extract_md_content,
            None,
        )

    if prune:
        data = prune_reranked_catalog(data)

    return data


def main() -> None:
    parser = argparse.ArgumentParser(description="Rerank JSON items using DeepInfra and LiteLLM.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--json", help="Input JSON file path")
    group.add_argument("--dir", help="Path to the directory containing decomposed tool files")
    parser.add_argument("--output-json", help="Optional output JSON file path")
    parser.add_argument("command", choices=["search"], nargs="?", default="search", help="Command to run (default: search)")
    parser.add_argument("query", help="Search query")

    args = parser.parse_args()

    load_env()
    api_key = os.environ.get("DEEPINFRA_API_KEY")

    if args.json:
        try:
            with open(args.json) as f:
                data = json.load(f)
        except Exception as e:
            print(f"Error reading JSON: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        # Use load_catalog from retrieve_catalog
        from retrieve_catalog import load_catalog
        try:
            data = load_catalog(args.dir)
        except Exception as e:
            print(f"Error loading catalog directory: {e}", file=sys.stderr)
            sys.exit(1)

    data = rerank_catalog_dict(data, args.query)

    output_data = json.dumps(data, indent=2)
    if args.output_json:
        with open(args.output_json, "w") as f:
            f.write(output_data)
        print(f"Results saved to {args.output_json}")
    else:
        print(output_data)


if __name__ == "__main__":
    main()
