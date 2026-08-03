from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse

from fastmcp.server.transforms import Transform
from fastmcp.tools.tool import Tool

class FastMCP:
    def __init__(self, name: str) -> None: ...
    def mount(self, proxy: FastMCP, *, namespace: str) -> None: ...
    def add_transform(self, transform: Transform) -> None: ...
    async def list_tools(self) -> Sequence[Tool]: ...
    def custom_route(
        self,
        path: str,
        *,
        methods: list[str],
    ) -> Callable[
        [Callable[..., Awaitable[JSONResponse]]],
        Callable[..., Awaitable[JSONResponse]],
    ]: ...
    async def run_http_async(self, *, host: str, port: int, path: str) -> None: ...
    def run(
        self,
        transport: str | None = None,
        show_banner: bool | None = None,
        **transport_kwargs: Any,
    ) -> None: ...
