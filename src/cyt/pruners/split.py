import sys
from collections.abc import Callable

from cyt.indexer.tokens import count_tokens_batch


def split_into_bulks[T](
    items: list[T],
    transform_fn: Callable[[T], str],
    base_tokens: int,
    max_tokens: int = 32000,
) -> list[list[T]]:
    """Generic splitter that returns list of lists (bulks of items)."""
    if not items:
        return []

    texts = [transform_fn(item) for item in items]
    token_counts = count_tokens_batch(texts)

    bulks = []
    current_bulk: list[T] = []
    current_tokens = base_tokens

    for item, _text, item_tokens in zip(items, texts, token_counts, strict=True):
        # If a single item is too big to fit in any bulk, we skip it or handle it.
        # For now, we skip but warn if it exceeds the absolute limit.
        if base_tokens + item_tokens > max_tokens:
            print(
                f"Warning: Item tokens ({item_tokens}) + base tokens ({base_tokens}) exceeds max_tokens ({max_tokens}). Skipping item.",
                file=sys.stderr,
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
    *,
    chunk_token_counts: list[int] | None = None,
    wrap_agent_tools: bool = False,
    max_tokens: int = 32000,
) -> list[str]:
    """
    Split formatted chunks into bulks that fit within max_tokens.
    Maintained for backward compatibility with llm.py.
    """
    from cyt.pruners.llm import llm_selector_bulk_base_tokens
    from cyt.pruners.selector_xml import wrap_agent_tools_bulk

    # Base tokens for every bulk (system prompt + chunk header + query suffix)
    base_tokens = llm_selector_bulk_base_tokens(query, system_prompt)

    if base_tokens >= max_tokens:
        raise ValueError(
            f"System prompt and query are too long ({base_tokens} tokens) for max_tokens={max_tokens}",
        )

    token_counts = chunk_token_counts
    if token_counts is not None and len(token_counts) != len(formatted_chunks):
        token_counts = None

    # Use the new generic splitter
    bulks_of_chunks = split_into_bulks(
        items=list(zip(formatted_chunks, token_counts or [0] * len(formatted_chunks), strict=True)),
        transform_fn=lambda pair: pair[0],
        base_tokens=base_tokens,
        max_tokens=max_tokens,
    )

    # Convert back to the list of strings format expected by llm.py
    result: list[str] = []
    for bulk in bulks_of_chunks:
        inner = "\n\n".join(chunk for chunk, _count in bulk)
        if wrap_agent_tools:
            total_tokens = sum(count for _chunk, count in bulk)
            wrapped = wrap_agent_tools_bulk(inner, total_tokens=total_tokens)
            if wrapped:
                result.append(wrapped)
        else:
            result.append(inner)
    return result
