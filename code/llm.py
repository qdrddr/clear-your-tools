import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from litellm import completion
from pydantic import BaseModel


LLM_MCP_SELECTOR_MODEL: str = "openrouter/inception/mercury-2"

class RelevantChunkIds(BaseModel):
    ids: list[int]


def load_env() -> None:
    """Load environment variables from code/.env if it exists."""
    # OPENROUTER_API_KEY in env takes precedence over .env file
    if "OPENROUTER_API_KEY" in os.environ:
        return

    env_path = Path("code/.env")
    if env_path.exists():
        load_dotenv(dotenv_path=env_path)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Filter JSON items using an LLM on OpenRouter."
    )
    parser.add_argument("--json", required=True, help="Input JSON file path")
    parser.add_argument("query", help="User search query")

    args = parser.parse_args()

    load_env()

    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        print("Error: OPENROUTER_API_KEY not found.", file=sys.stderr)
        sys.exit(1)

    try:
        with open(args.json) as f:
            data = json.load(f)
    except Exception as e:
        print(f"Error reading JSON: {e}", file=sys.stderr)
        sys.exit(1)

    # Determine which list keys to process
    list_keys = [k for k, v in data.items() if isinstance(v, list)]

    if not list_keys:
        print("Error: No list found in JSON root.", file=sys.stderr)
        sys.exit(1)

    # 1. & 2. & 3. Prepare items and format chunks for ALL list keys
    formatted_chunks = []
    # Store metadata and key mapping
    # item_metadata_storage[chunk_id] = {"key": original_key, "item": original_item_ref, "metadata": {...}}
    item_metadata_storage = {}
    keys_to_remove = ["score", "start_line", "end_line", "language"]
    global_chunk_id = 1

    for target_key in list_keys:
        items = data[target_key]
        # Sort items within each key
        items.sort(key=lambda x: x.get("file_path", ""))

        for item in items:
            # Store original metadata and link to parent key and item reference
            item_metadata_storage[global_chunk_id] = {
                "key": target_key,
                "item": item,
                "metadata": {k: item.get(k) for k in keys_to_remove}
            }

            # Create a copy for sending to LLM and remove the noisy keys
            item_for_llm = item.copy()
            for k in keys_to_remove:
                item_for_llm.pop(k, None)

            compact_json = json.dumps(item_for_llm, separators=(",", ":"))
            formatted_chunks.append(f'<chunk id={global_chunk_id}>\n{compact_json}\n</chunk>\n')
            global_chunk_id += 1

    all_chunks_text = "\n\n".join(formatted_chunks)

    system_prompt = (
        "These are MCP tools and their enums and optional properties in a \"decomposed\" state. "
        "Your task is to select the most relevant tool(s), enums and properties based on the user query. "
        "Later on the results will re-compile MCP tools into their full definitions based on your selection. "
        "The goal is to return chunk ids that match the user query the most. "
        "It will be used as a hint for another LLM to use only these relevant tools, enums an doptional properties "
        "to save on tokens by removing the irrelevant to user query noise."
    )

    user_message = f"User Query: {args.query}\n\nAvailable Chunks:\n\n{all_chunks_text}"

    try:
        response = completion(
            model=LLM_MCP_SELECTOR_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            api_key=api_key,
            response_format=RelevantChunkIds,
        )

        content = response.choices[0].message.content
        # LiteLLM sometimes returns the JSON string if response_format is used
        try:
            parsed_response = RelevantChunkIds.model_validate_json(content)
        except Exception:
            # Fallback if content is not direct JSON (unlikely with response_format but safer)
            import re
            json_match = re.search(r'\{.*\}', content, re.DOTALL)
            if json_match:
                parsed_response = RelevantChunkIds.model_validate_json(json_match.group(0))
            else:
                raise ValueError(f"Could not parse LLM response: {content}")

        selected_ids = set(parsed_response.ids)

        # 4. Update scores and restore keys for all processed lists
        new_data_lists = {k: [] for k in list_keys}

        for chunk_id, storage in item_metadata_storage.items():
            target_key = storage["key"]
            item = storage["item"]
            metadata = storage["metadata"]

            # Restore original metadata keys ("score", "start_line", "end_line", "language")
            # This covers "restore the remove keys... in the returned output content"
            for k, v in metadata.items():
                if v is not None:
                    item[k] = v

            is_selected = chunk_id in selected_ids

            if target_key == "md":
                # For "md" (enums), we keep the FULL list and punish those not selected
                if "score" in item and item["score"] is not None:
                    try:
                        orig_score_val = item["score"]
                        is_str = isinstance(orig_score_val, str)
                        score_float = float(orig_score_val)

                        if not is_selected:
                            new_score = score_float / 10.0
                        else:
                            new_score = score_float

                        if is_str:
                            item["score"] = f"{new_score:.4f}"
                        else:
                            item["score"] = new_score
                    except (ValueError, TypeError):
                        pass
                new_data_lists[target_key].append(item)
            else:
                # For other keys (like "json"), we TRUNCATE: only keep selected ones
                # Metadata was already restored above
                if is_selected:
                    new_data_lists[target_key].append(item)

        # Update the original data structure with processed lists
        for k, v in new_data_lists.items():
            data[k] = v

        print(json.dumps(data, indent=2))

    except Exception as e:
        print(f"Error during LLM processing: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
