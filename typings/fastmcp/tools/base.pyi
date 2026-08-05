from typing import Any

class ToolResult:
    content: list[Any]
    structured_content: Any | None
    meta: dict[str, Any] | None
    is_error: bool
    def __init__(
        self,
        content: Any | None = ...,
        structured_content: Any | None = ...,
        meta: dict[str, Any] | None = ...,
        is_error: bool = ...,
    ) -> None: ...
