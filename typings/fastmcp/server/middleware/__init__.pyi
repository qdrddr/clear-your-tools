from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any, Generic, Literal, TypeVar

T = TypeVar("T")
R = TypeVar("R")

CallNext = Callable[["MiddlewareContext[T]"], Awaitable[R]]

class MiddlewareContext(Generic[T]):
    message: T
    fastmcp_context: Any | None
    source: Literal["client", "server"]
    type: Literal["request", "notification"]
    method: str | None
    timestamp: datetime

    def __init__(
        self,
        *,
        message: T,
        fastmcp_context: Any | None = None,
        source: Literal["client", "server"] = "client",
        type: Literal["request", "notification"] = "request",
        method: str | None = None,
        timestamp: datetime | None = None,
    ) -> None: ...

class Middleware:
    async def on_initialize(
        self,
        context: MiddlewareContext[Any],
        call_next: CallNext[Any, Any],
    ) -> Any | None: ...
