"""Re-export semantic search CLI (moved to nogo.embedder.cs)."""

from nogo.embedder.cs import app, main, search

__all__ = ["app", "main", "search"]

if __name__ == "__main__":
    app()
