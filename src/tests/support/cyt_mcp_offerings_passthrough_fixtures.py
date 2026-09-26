"""Fixtures and helpers for cyt-mcp MCP offerings passthrough regression tests."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from unittest.mock import MagicMock

from fastmcp import FastMCP
from fastmcp.server.middleware import MiddlewareContext
from mcp.types import (
    ListPromptsRequest,
    ListResourcesRequest,
    ListResourceTemplatesRequest,
    ListToolsRequest,
)

from cyt_mcp.aggregator import build_aggregator
from cyt_mcp.config import AggregatorConfig, sample_aggregator_config
from cyt_mcp.config_holder import ConfigHolder
from cyt_mcp.runtime_cache import RuntimeToolCache
from cyt_mcp.session_runtime import MultiWorkspaceCoordinator
from cyt_mcp.session_workspace_middleware import SessionWorkspaceMiddleware

FIXTURES_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "cyt_mcp_offerings_passthrough"
BACKEND_OFFERINGS_PATH = FIXTURES_ROOT / "backend_offerings.json"
SCENARIOS_PATH = FIXTURES_ROOT / "scenarios.json"


@dataclass(frozen=True)
class BackendPromptSpec:
    name: str
    description: str


@dataclass(frozen=True)
class BackendResourceSpec:
    uri: str
    name: str
    description: str


@dataclass(frozen=True)
class BackendResourceTemplateSpec:
    uri_template: str
    name: str
    description: str


@dataclass(frozen=True)
class BackendOfferingsSpec:
    server_key: str
    prompts: tuple[BackendPromptSpec, ...]
    resources: tuple[BackendResourceSpec, ...]
    resource_templates: tuple[BackendResourceTemplateSpec, ...]


@dataclass(frozen=True)
class OfferingsPassthroughScenario:
    id: str
    method: str
    expected_prompt_names: tuple[str, ...] = ()
    expected_resource_uris: tuple[str, ...] = ()
    expected_template_names: tuple[str, ...] = ()
    cached_tool_name: str | None = None
    expects_call_next: bool | None = None


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: expected object")
    return payload


def load_backend_offerings(path: Path = BACKEND_OFFERINGS_PATH) -> BackendOfferingsSpec:
    payload = _load_json(path)
    server_key = str(payload.get("server_key") or "").strip()
    if not server_key:
        raise ValueError(f"{path}: server_key is required")

    def _prompts() -> tuple[BackendPromptSpec, ...]:
        raw = payload.get("prompts") or []
        if not isinstance(raw, list):
            raise ValueError(f"{path}: prompts must be an array")
        return tuple(
            BackendPromptSpec(
                name=str(item["name"]),
                description=str(item.get("description") or ""),
            )
            for item in raw
            if isinstance(item, dict)
        )

    def _resources() -> tuple[BackendResourceSpec, ...]:
        raw = payload.get("resources") or []
        if not isinstance(raw, list):
            raise ValueError(f"{path}: resources must be an array")
        return tuple(
            BackendResourceSpec(
                uri=str(item["uri"]),
                name=str(item.get("name") or item["uri"]),
                description=str(item.get("description") or ""),
            )
            for item in raw
            if isinstance(item, dict)
        )

    def _templates() -> tuple[BackendResourceTemplateSpec, ...]:
        raw = payload.get("resource_templates") or []
        if not isinstance(raw, list):
            raise ValueError(f"{path}: resource_templates must be an array")
        return tuple(
            BackendResourceTemplateSpec(
                uri_template=str(item["uri_template"]),
                name=str(item.get("name") or item["uri_template"]),
                description=str(item.get("description") or ""),
            )
            for item in raw
            if isinstance(item, dict)
        )

    return BackendOfferingsSpec(
        server_key=server_key,
        prompts=_prompts(),
        resources=_resources(),
        resource_templates=_templates(),
    )


def load_offerings_passthrough_scenarios(
    path: Path = SCENARIOS_PATH,
) -> tuple[OfferingsPassthroughScenario, ...]:
    payload = _load_json(path)
    raw = payload.get("scenarios")
    if not isinstance(raw, list):
        raise ValueError(f"{path}: expected scenarios array")
    scenarios: list[OfferingsPassthroughScenario] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        scenarios.append(
            OfferingsPassthroughScenario(
                id=str(item["id"]),
                method=str(item["method"]),
                expected_prompt_names=tuple(
                    str(v) for v in item.get("expected_prompt_names") or []
                ),
                expected_resource_uris=tuple(
                    str(v) for v in item.get("expected_resource_uris") or []
                ),
                expected_template_names=tuple(
                    str(v) for v in item.get("expected_template_names") or []
                ),
                cached_tool_name=(
                    str(item["cached_tool_name"]) if item.get("cached_tool_name") else None
                ),
                expects_call_next=(
                    bool(item["expects_call_next"]) if "expects_call_next" in item else None
                ),
            ),
        )
    return tuple(scenarios)


def build_backend_offerings_server(spec: BackendOfferingsSpec) -> FastMCP:
    backend = FastMCP(f"cyt-mcp-backend-{spec.server_key}")

    def _register_prompt(prompt_name: str, prompt_description: str) -> None:
        @backend.prompt(name=prompt_name, description=prompt_description)
        def _prompt_handler() -> str:
            return f"{prompt_name} prompt body"

        _ = _prompt_handler

    def _register_resource(
        resource_uri: str,
        resource_name: str,
        resource_description: str,
    ) -> None:
        @backend.resource(resource_uri, name=resource_name, description=resource_description)
        def _resource_handler() -> str:
            return f"{resource_name} resource body"

        _ = _resource_handler

    def _register_template(
        uri_template: str,
        template_name: str,
        template_description: str,
    ) -> None:
        @backend.resource(uri_template, name=template_name, description=template_description)
        def _template_handler(name: str) -> str:
            return f"{template_name}:{name}"

        _ = _template_handler

    for prompt in spec.prompts:
        _register_prompt(prompt.name, prompt.description)
    for resource in spec.resources:
        _register_resource(resource.uri, resource.name, resource.description)
    for template in spec.resource_templates:
        _register_template(template.uri_template, template.name, template.description)

    return backend


def build_aggregator_with_offerings_backend(
    *,
    config: AggregatorConfig | None = None,
    offerings: BackendOfferingsSpec | None = None,
    cache_tools: list[dict[str, Any]] | None = None,
) -> tuple[FastMCP, SessionWorkspaceMiddleware, MultiWorkspaceCoordinator, BackendOfferingsSpec]:
    spec = offerings or load_backend_offerings()
    effective_config = config or sample_aggregator_config(
        mcp_servers={spec.server_key: {"command": "echo", "args": ["ok"]}},
    )
    cache = RuntimeToolCache()
    if cache_tools is not None:
        cache.replace(cache_tools)
    server, _list_changed, coordinator = build_aggregator(ConfigHolder(effective_config), cache)
    server.mount(build_backend_offerings_server(spec), namespace=spec.server_key)
    middleware = _session_workspace_middleware(server)
    if middleware is None:
        raise RuntimeError("SessionWorkspaceMiddleware was not registered on aggregator")
    return server, middleware, coordinator, spec


def _session_workspace_middleware(server: FastMCP) -> SessionWorkspaceMiddleware | None:
    middlewares = getattr(server, "middleware", None) or []
    for middleware in middlewares:
        if isinstance(middleware, SessionWorkspaceMiddleware):
            return middleware
    return None


def make_offerings_list_context(method: str) -> MiddlewareContext[Any]:
    request_by_method = {
        "prompts/list": ListPromptsRequest(method="prompts/list"),
        "resources/list": ListResourcesRequest(method="resources/list"),
        "resources/templates/list": ListResourceTemplatesRequest(method="resources/templates/list"),
        "tools/list": ListToolsRequest(method="tools/list"),
    }
    message = request_by_method.get(method)
    if message is None:
        raise ValueError(f"unsupported method: {method}")
    context = MagicMock()
    context.method = method
    context.message = message
    context.fastmcp_context = None
    return context


ListHandler = Callable[
    [MiddlewareContext[Any], Callable[[MiddlewareContext[Any]], Awaitable[Any]]],
    Awaitable[Any],
]


def list_handler_for_method(middleware: SessionWorkspaceMiddleware, method: str) -> ListHandler:
    handlers = {
        "prompts/list": middleware.on_list_prompts,
        "resources/list": middleware.on_list_resources,
        "resources/templates/list": middleware.on_list_resource_templates,
        "tools/list": middleware.on_list_tools,
    }
    handler = handlers.get(method)
    if handler is None:
        raise ValueError(f"unsupported method: {method}")
    return cast(ListHandler, handler)


async def list_offerings_via_middleware(
    middleware: SessionWorkspaceMiddleware,
    server: FastMCP,
    *,
    method: str,
) -> Sequence[Any]:
    context = make_offerings_list_context(method)
    handler = list_handler_for_method(middleware, method)

    async def _call_next(_ctx: MiddlewareContext[Any]) -> Sequence[Any]:
        if method == "prompts/list":
            return await server._list_prompts()
        if method == "resources/list":
            return await server._list_resources()
        if method == "resources/templates/list":
            return await server._list_resource_templates()
        if method == "tools/list":
            return await server._list_tools()
        raise ValueError(f"unsupported method: {method}")

    result = await handler(context, _call_next)
    return cast(Sequence[Any], result)
