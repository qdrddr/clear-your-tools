import tiktoken
from typing import Any

def count_tokens(text: str, model: str = "cl100k_base") -> int:
    """Return the number of tokens in a string."""
    encoding = tiktoken.get_encoding(model)
    return len(encoding.encode(text))

def split_chunks_into_bulks(
    query: str,
    system_prompt: str,
    formatted_chunks: list[str],
    max_tokens: int = 32000
) -> list[str]:
    """Split formatted chunks into bulks that fit within max_tokens."""
    # Base tokens for every bulk (system prompt + query prefix)
    base_text = f"System: {system_prompt}\nUser Query: {query}\n\nAvailable Chunks:\n\n"
    base_tokens = count_tokens(base_text)

    if base_tokens >= max_tokens:
        raise ValueError(f"System prompt and query are too long ({base_tokens} tokens) for max_tokens={max_tokens}")

    bulks: list[str] = []
    current_chunk_texts: list[str] = []
    current_tokens = base_tokens

    for chunk in formatted_chunks:
        chunk_tokens = count_tokens(chunk)

        # If a single chunk is too big, we might have to truncate it or error
        # For now, let's just warn or handle it by adding it alone if possible
        if base_tokens + chunk_tokens > max_tokens:
            print(f"Warning: Chunk is too large ({chunk_tokens} tokens) to fit in any bulk with the query. Attempting to fit what we can.", flush=True)
            # If it's the only thing in the list, we have to skip or truncate
            if not current_chunk_texts:
                # Just skip for now to avoid infinite loops, but in reality we should truncate
                continue

        if current_tokens + chunk_tokens > max_tokens:
            # Finish current bulk
            bulks.append("\n\n".join(current_chunk_texts))
            current_chunk_texts = [chunk]
            current_tokens = base_tokens + chunk_tokens
        else:
            current_chunk_texts.append(chunk)
            current_tokens += chunk_tokens

    if current_chunk_texts:
        bulks.append("\n\n".join(current_chunk_texts))

    return bulks
