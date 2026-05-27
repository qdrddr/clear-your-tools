from collections.abc import Callable

from build_index import count_tokens


def split_into_bulks[T](
    items: list[T],
    transform_fn: Callable[[T], str],
    base_tokens: int,
    max_tokens: int = 32000,
) -> list[list[T]]:
    """Generic splitter that returns list of lists (bulks of items)."""
    bulks = []
    current_bulk: list[T] = []
    current_tokens = base_tokens

    for item in items:
        text = transform_fn(item)
        item_tokens = count_tokens(text)

        # If a single item is too big to fit in any bulk, we skip it or handle it.
        # For now, we skip but warn if it exceeds the absolute limit.
        if base_tokens + item_tokens > max_tokens:
            print(
                f"Warning: Item tokens ({item_tokens}) + base tokens ({base_tokens}) exceeds max_tokens ({max_tokens}). Skipping item.",
                flush=True,
            )
            continue

        if current_tokens + item_tokens > max_tokens:
            # Finish current bulk
            if current_bulk:
                bulks.append(current_bulk)
            current_bulk = [item]
            current_tokens = base_tokens + item_tokens
        else:
            current_bulk.append(item)
            current_tokens += item_tokens

    if current_bulk:
        bulks.append(current_bulk)

    return bulks


def split_chunks_into_bulks(
    query: str,
    system_prompt: str,
    formatted_chunks: list[str],
    max_tokens: int = 32000,
) -> list[str]:
    """
    Split formatted chunks into bulks that fit within max_tokens.
    Maintained for backward compatibility with llm.py.
    """
    # Base tokens for every bulk (system prompt + query prefix)
    base_text = f"System: {system_prompt}\nUser Query: {query}\n\nAvailable Chunks:\n\n"
    base_tokens = count_tokens(base_text)

    if base_tokens >= max_tokens:
        raise ValueError(
            f"System prompt and query are too long ({base_tokens} tokens) for max_tokens={max_tokens}",
        )

    # Use the new generic splitter
    bulks_of_chunks = split_into_bulks(
        items=formatted_chunks,
        transform_fn=lambda x: x,
        base_tokens=base_tokens,
        max_tokens=max_tokens,
    )

    # Convert back to the list of strings format expected by llm.py
    return ["\n\n".join(bulk) for bulk in bulks_of_chunks]
