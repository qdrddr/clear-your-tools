import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, TypeVar

import tiktoken
from dotenv import load_dotenv
from litellm import completion
from pydantic import BaseModel

T = TypeVar("T")

LLM_MCP_SELECTOR_MODEL: str = "openrouter/inception/mercury-2"

LLM_TRIM_TOP_K_JSON: int = 40
LLM_TRIM_MIN_JSON_SCORE: float = 0.11

LLM_TRIM_FIELDS: tuple[str, ...] = ("score", "language", "end_line", "start_line")
LLM_MODEL_EXCLUDED_FIELDS: tuple[str, ...] = (*LLM_TRIM_FIELDS, "id")

SELECTOR_SYSTEM_PROMPT = (
    'These are MCP tools and their enums and optional properties in a "decomposed" state. '
    "Your task is to select the most relevant tool(s), enums and properties based on the user query. "
    "Later on the results will re-compile MCP tools into their full definitions based on your selection. "
    "The goal is to return chunk ids that match the user query the most. "
    "It will be used as a hint for another LLM to use only these relevant tools, enums an doptional properties "
    "to save on tokens by removing the irrelevant to user query noise."
)


class RelevantChunkIds(BaseModel):
    ids: list[int]


def load_env() -> None:
    """Load environment variables from src/.env if it exists."""
    # OPENROUTER_API_KEY in env takes precedence over .env file
    if "OPENROUTER_API_KEY" in os.environ:
        return

    env_path = Path(__file__).resolve().parent / ".env"
    if env_path.exists():
        load_dotenv(dotenv_path=env_path)


def get_api_key() -> str:
    load_env()
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        print("Error: OPENROUTER_API_KEY not found.", file=sys.stderr)
        sys.exit(1)
    return api_key


def read_json_input(path: str) -> dict[str, Any]:
    try:
        with open(path) as f:
            data_loaded: Any = json.load(f)
            if not isinstance(data_loaded, dict):
                print(f"Error: JSON root must be a dictionary in {path}", file=sys.stderr)
                sys.exit(1)
            return data_loaded
    except Exception as e:
        print(f"Error reading JSON: {e}", file=sys.stderr)
        sys.exit(1)


def _json_item_score(item: dict[str, Any]) -> float:
    score = item.get("score")
    if score is None:
        return 0.0
    try:
        return float(score)
    except (TypeError, ValueError):
        return 0.0


def trim_catalog_dict(data: dict[str, Any]) -> dict[str, Any]:
    """Cap json entries after rerank; strip fields irrelevant to the LLM selector."""
    json_items = data.get("json")
    if not isinstance(json_items, list):
        return data

    filtered: list[dict[str, Any]] = []
    for item in json_items:
        if not isinstance(item, dict):
            continue
        if _json_item_score(item) < LLM_TRIM_MIN_JSON_SCORE:
            continue
        filtered.append(item)

    filtered.sort(key=lambda x: str(x.get("file_path", "")))

    trimmed = filtered[:LLM_TRIM_TOP_K_JSON]
    for item in trimmed:
        for field in LLM_TRIM_FIELDS:
            item.pop(field, None)

    data["json"] = trimmed
    return data


def prepare_chunks(data: dict[str, Any]) -> tuple[list[str], dict[int, Any], list[str]]:
    list_keys = [k for k, v in data.items() if isinstance(v, list)]

    if not list_keys:
        raise ValueError("No list found in JSON root.")

    formatted_chunks: list[str] = []
    item_metadata_storage: dict[int, Any] = {}
    keys_to_remove = list(LLM_TRIM_FIELDS)
    model_excluded_fields = set(LLM_MODEL_EXCLUDED_FIELDS)
    global_chunk_id = 1

    for target_key in list_keys:
        items = data[target_key]
        if isinstance(items, list):
            # Sort items by file_path if available
            try:
                items.sort(key=lambda x: str(x.get("file_path", "")) if isinstance(x, dict) else "")
            except (AttributeError, TypeError):
                pass

            for item in items:
                if not isinstance(item, dict):
                    continue
                item_metadata_storage[global_chunk_id] = {
                    "key": target_key,
                    "item": item,
                    "metadata": {k: item.get(k) for k in keys_to_remove},
                }

                item_for_llm = item.copy()
                for k in model_excluded_fields:
                    item_for_llm.pop(k, None)

                compact_json = json.dumps(item_for_llm, separators=(",", ":"))
                formatted_chunks.append(f"<chunk id={global_chunk_id}>\n{compact_json}\n</chunk>\n")
                global_chunk_id += 1

    return formatted_chunks, item_metadata_storage, list_keys


def call_llm(api_key: str, query: str, chunks_text: str) -> RelevantChunkIds:
    user_message = f"User Query: {query}\n\nAvailable Chunks:\n\n{chunks_text}"

    # litellm.completion returns a ModelResponse object but it's often treated as Any
    response: Any = completion(
        model=LLM_MCP_SELECTOR_MODEL,
        messages=[
            {"role": "system", "content": SELECTOR_SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
        api_key=api_key,
        response_format=RelevantChunkIds,
    )

    content_val: Any = response.choices[0].message.content
    if not isinstance(content_val, str):
        raise ValueError(f"Unexpected response content type: {type(content_val)}")

    try:
        return RelevantChunkIds.model_validate_json(content_val)
    except Exception:
        import re

        json_match = re.search(r"\{.*\}", content_val, re.DOTALL)
        if json_match:
            return RelevantChunkIds.model_validate_json(json_match.group(0))
        raise ValueError(f"Could not parse LLM response: {content_val}") from None


def score_item(item: dict[str, Any], is_selected: bool) -> None:
    if "score" not in item or item["score"] is None:
        return

    try:
        orig_score_val = item["score"]
        is_str = isinstance(orig_score_val, str)
        score_float = float(orig_score_val)

        new_score = score_float if is_selected else score_float / 10.0

        if is_str:
            item["score"] = f"{new_score:.4f}"
        else:
            item["score"] = new_score
    except (ValueError, TypeError):
        pass


def process_results(
    data: dict[str, Any],
    item_metadata_storage: dict[int, Any],
    selected_ids: set[int],
    list_keys: list[str],
) -> dict[str, Any]:
    new_data_lists: dict[str, list[dict[str, Any]]] = {k: [] for k in list_keys}

    for chunk_id, storage in item_metadata_storage.items():
        target_key: str = storage["key"]
        item: dict[str, Any] = storage["item"]
        metadata: dict[str, Any | None] = storage["metadata"]

        for k, v in metadata.items():
            if v is not None:
                item[k] = v

        is_selected = chunk_id in selected_ids

        if target_key == "md":
            score_item(item, is_selected)
            new_data_lists[target_key].append(item)
        elif is_selected:
            new_data_lists[target_key].append(item)

    for k, v in new_data_lists.items():
        data[k] = v

    return data


def llm_catalog_dict(data: dict[str, Any], query: str) -> dict[str, Any]:
    """Select relevant catalog chunks via LLM; same contract as rerank_catalog_dict."""
    api_key = get_api_key()
    formatted_chunks, item_metadata_storage, list_keys = prepare_chunks(data)

    from split_bulks import split_chunks_into_bulks

    bulks = split_chunks_into_bulks(query, SELECTOR_SYSTEM_PROMPT, formatted_chunks)
    selected_ids: set[int] = set()

    for bulk_text in bulks:
        parsed_response = call_llm(api_key, query, bulk_text)
        selected_ids.update(parsed_response.ids)

    return process_results(data, item_metadata_storage, selected_ids, list_keys)


def main() -> None:
    parser = argparse.ArgumentParser(description="Filter JSON items using an LLM on OpenRouter.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--json", help="Input JSON file path")
    group.add_argument("--dir", help="Path to the directory containing decomposed tool files")
    parser.add_argument("--output-json", help="Optional output JSON file path")
    parser.add_argument("query", help="User search query")

    args = parser.parse_args()

    if args.json:
        data = read_json_input(args.json)
    else:
        from retrieve_catalog import load_catalog
        try:
            data = load_catalog(args.dir)
        except Exception as e:
            print(f"Error loading catalog directory: {e}", file=sys.stderr)
            sys.exit(1)

    try:
        result = llm_catalog_dict(data, args.query)
        output_data = json.dumps(result, indent=2)
        if args.output_json:
            with open(args.output_json, "w") as f:
                f.write(output_data)
            print(f"Results saved to {args.output_json}")
        else:
            print(output_data)
    except Exception as e:
        print(f"Error during LLM processing: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
