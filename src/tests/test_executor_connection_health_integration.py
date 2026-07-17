"""Live executor integration tests for connection-health catalog gating.

Requires a running Executor at ``http://localhost:4789`` with ``EXECUTOR_TOKEN`` in
the keyring (or env). Skipped by default; opt in with::

    uv run pytest src/tests/test_executor_connection_health_integration.py --run-integration -s

Tools and integrations are discovered at runtime from the executor API
(``GET /api/connections``, ``GET /api/tools``, schema fetch). Assertions verify:

  - integrations with at least one healthy connection → all catalog tools kept
  - integrations with connections but none healthy → all catalog tools filtered
  - integrations with catalog tools but no saved connection → all catalog tools filtered
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Literal

import httpx
import pytest

from cyt.executor.connection_health import (
    apply_health_snapshot,
    build_healthy_connections,
    clear_connection_health_cache,
    filter_catalog_by_health,
    refresh_connection_health_async,
)
from cyt.executor.http import (
    _fetch_full_catalog_async,
    clear_executor_catalog_cache,
    get_executor_catalog,
)
from cyt.launch.secrets import resolve_credential
from tests.test_credential_helpers import CI_CREDENTIAL_STUBS

_EXECUTOR_URL = "http://localhost:4789"
_EXECUTOR_TOKEN_VAR = "EXECUTOR_TOKEN"

FilterReason = Literal[
    "healthy",
    "exempt",
    "unhealthy",
    "no_connection",
    "missing_metadata",
]

_CONFIG: dict[str, Any] = {
    "pruning": {
        "inject_via": "hook",
        "tools": {
            "hook": {
                "tools_from": "executor",
                "executor_url": _EXECUTOR_URL,
                "executor_token_var": _EXECUTOR_TOKEN_VAR,
            },
        },
    },
}


@dataclass(frozen=True)
class IntegrationBucket:
    owner: str
    integration: str
    tool_names: tuple[str, ...]
    connection_status: str | None  # None when no saved connection


@dataclass(frozen=True)
class LiveHealthScenario:
    connections: list[dict[str, Any]]
    healthy_integrations: set[tuple[str, str]]
    catalog: list[dict[str, Any]]
    filtered: list[dict[str, Any]]
    buckets: tuple[IntegrationBucket, ...]


def setup_function() -> None:
    clear_executor_catalog_cache()
    clear_connection_health_cache()


def _live_executor_token() -> str | None:
    token, _source = resolve_credential(_EXECUTOR_TOKEN_VAR, allow_prompt=False)
    if not token or token == CI_CREDENTIAL_STUBS[_EXECUTOR_TOKEN_VAR]:
        return None
    return token


def _require_live_executor() -> str:
    token = _live_executor_token()
    if token is None:
        pytest.skip(f"{_EXECUTOR_TOKEN_VAR} is not configured for live executor tests")
    try:
        with httpx.Client(
            headers={"Authorization": f"Bearer {token}"},
            timeout=httpx.Timeout(10.0, connect=3.0),
        ) as client:
            response = client.get(f"{_EXECUTOR_URL}/api/connections")
            response.raise_for_status()
    except httpx.HTTPError as exc:
        pytest.skip(f"executor not reachable at {_EXECUTOR_URL}: {exc}")
    return token


def _tool_names(tools: list[dict[str, Any]]) -> set[str]:
    return {str(tool.get("name") or "") for tool in tools if tool.get("name")}


def _integration_pairs(tools: list[dict[str, Any]]) -> set[tuple[str, str]]:
    pairs: set[tuple[str, str]] = set()
    for tool in tools:
        integration = str(tool.get("integration") or "").strip()
        if not integration or integration == "executor":
            continue
        owner = str(tool.get("owner") or "").strip()
        if owner:
            pairs.add((owner, integration))
    return pairs


def _connection_integrations(connections: list[dict[str, Any]]) -> set[tuple[str, str]]:
    return {
        (str(conn.get("owner") or ""), str(conn.get("integration") or "")) for conn in connections
    }


def _connection_status(
    connections: list[dict[str, Any]],
    owner: str,
    integration: str,
) -> str | None:
    statuses: list[str] = []
    for conn in connections:
        if conn.get("owner") != owner or conn.get("integration") != integration:
            continue
        last_health = conn.get("lastHealth")
        if isinstance(last_health, dict):
            status = last_health.get("status")
            if status is not None:
                statuses.append(str(status))
    if not statuses:
        return None
    if any(status == "healthy" for status in statuses):
        return "healthy"
    return statuses[0]


def _tools_by_integration(catalog: list[dict[str, Any]]) -> dict[tuple[str, str], list[str]]:
    grouped: dict[tuple[str, str], list[str]] = defaultdict(list)
    for tool in catalog:
        owner = str(tool.get("owner") or "").strip()
        integration = str(tool.get("integration") or "").strip()
        name = str(tool.get("name") or "").strip()
        if not owner or not integration or integration == "executor" or not name:
            continue
        grouped[(owner, integration)].append(name)
    return dict(grouped)


def _integration_buckets(
    *,
    catalog: list[dict[str, Any]],
    connections: list[dict[str, Any]],
) -> tuple[IntegrationBucket, ...]:
    connection_integrations = _connection_integrations(connections)
    buckets: list[IntegrationBucket] = []
    for (owner, integration), tool_names in sorted(_tools_by_integration(catalog).items()):
        status = (
            _connection_status(connections, owner, integration)
            if (owner, integration) in connection_integrations
            else None
        )
        buckets.append(
            IntegrationBucket(
                owner=owner,
                integration=integration,
                tool_names=tuple(sorted(tool_names)),
                connection_status=status,
            ),
        )
    return tuple(buckets)


def _filter_reason(
    tool: dict[str, Any],
    *,
    connection_integrations: set[tuple[str, str]],
    kept: bool,
) -> FilterReason:
    if kept:
        integration = str(tool.get("integration") or "").strip()
        if integration == "executor" or tool.get("static") is True:
            return "exempt"
        return "healthy"

    integration = str(tool.get("integration") or "").strip()
    owner = str(tool.get("owner") or "").strip()
    if not owner or not integration:
        return "missing_metadata"
    if (owner, integration) not in connection_integrations:
        return "no_connection"
    return "unhealthy"


def _reason_label(reason: FilterReason) -> str:
    return {
        "healthy": "healthy connection",
        "exempt": "executor/static exempt",
        "unhealthy": "no healthy connection",
        "no_connection": "no saved connection",
        "missing_metadata": "missing owner/integration metadata",
    }[reason]


def _print_connection_health_lines(connections: list[dict[str, Any]]) -> None:
    print("\n=== executor connection health ===")
    for conn in sorted(
        connections,
        key=lambda item: (
            str(item.get("owner") or ""),
            str(item.get("integration") or ""),
            str(item.get("name") or ""),
        ),
    ):
        owner = conn.get("owner")
        integration = conn.get("integration")
        name = conn.get("name")
        last_health = conn.get("lastHealth")
        status = last_health.get("status") if isinstance(last_health, dict) else None
        print(f"  {owner}/{integration}/{name}: {status or 'unknown'}")


def _print_integration_outcomes(scenario: LiveHealthScenario) -> None:
    print(f"\nhealthy integrations ({len(scenario.healthy_integrations)}):")
    for owner, integration in sorted(scenario.healthy_integrations):
        print(f"  {owner}/{integration}")

    print("\n=== integrations from catalog ===")
    for bucket in scenario.buckets:
        pair = (bucket.owner, bucket.integration)
        if pair in scenario.healthy_integrations:
            outcome = "survive (healthy connection)"
        elif bucket.connection_status is None:
            outcome = "filtered (no saved connection)"
        else:
            outcome = f"filtered (connection status: {bucket.connection_status})"
        print(
            f"  {bucket.owner}/{bucket.integration}: {len(bucket.tool_names)} tools → {outcome}",
        )


def _print_survived_tools(filtered: list[dict[str, Any]]) -> None:
    survived_by_integration: dict[str, list[str]] = defaultdict(list)
    for tool in filtered:
        name = str(tool.get("name") or "")
        if not name:
            continue
        integration = str(tool.get("integration") or "unknown")
        survived_by_integration[integration].append(name)

    print(f"\n=== survived ({len(_tool_names(filtered))} tools) ===")
    for integration in sorted(survived_by_integration):
        names = sorted(survived_by_integration[integration])
        print(f"[{integration}] ({len(names)} tools)")
        for name in names:
            print(f"  + {name}")


def _print_filtered_tools(
    scenario: LiveHealthScenario,
    *,
    kept_names: set[str],
    connection_integrations: set[tuple[str, str]],
) -> None:
    catalog_names = _tool_names(scenario.catalog)
    filtered_out_names = sorted(catalog_names - kept_names)
    filtered_by_integration: dict[str, list[tuple[str, FilterReason]]] = defaultdict(list)
    for tool in scenario.catalog:
        name = str(tool.get("name") or "")
        if not name or name in kept_names:
            continue
        integration = str(tool.get("integration") or "unknown")
        reason = _filter_reason(
            tool,
            connection_integrations=connection_integrations,
            kept=False,
        )
        filtered_by_integration[integration].append((name, reason))

    print(f"\n=== filtered out ({len(filtered_out_names)} tools) ===")
    for integration in sorted(filtered_by_integration):
        entries = sorted(filtered_by_integration[integration])
        reason_label = _reason_label(entries[0][1])
        print(f"[{integration}] ({len(entries)} tools, {reason_label})")
        for name, entry_reason in entries:
            if entry_reason != entries[0][1]:
                print(f"  - {name}  ({_reason_label(entry_reason)})")
            else:
                print(f"  - {name}")
    print()


def _print_health_filter_report(scenario: LiveHealthScenario) -> None:
    kept_names = _tool_names(scenario.filtered)
    connection_integrations = _connection_integrations(scenario.connections)
    _print_connection_health_lines(scenario.connections)
    _print_integration_outcomes(scenario)
    _print_survived_tools(scenario.filtered)
    _print_filtered_tools(
        scenario,
        kept_names=kept_names,
        connection_integrations=connection_integrations,
    )


async def _live_health_filter_scenario(token: str) -> LiveHealthScenario:
    slug = "live-integration-test"
    health_snapshot, _delta = await refresh_connection_health_async(
        base_url=_EXECUTOR_URL,
        token=token,
        slug=slug,
    )
    healthy_connections = build_healthy_connections(health_snapshot.connections)
    assert healthy_connections == health_snapshot.healthy_connections
    healthy_integrations = {(key.owner, key.integration) for key in healthy_connections}

    apply_health_snapshot(slug, health_snapshot, update_flapping=False)
    catalog = await _fetch_full_catalog_async(
        base_url=_EXECUTOR_URL,
        token=token,
        slug=slug,
        config=_CONFIG,
    )
    filtered = filter_catalog_by_health(catalog, slug, config=_CONFIG)
    connections_list = list(health_snapshot.connections.values())
    buckets = _integration_buckets(catalog=catalog, connections=connections_list)
    return LiveHealthScenario(
        connections=connections_list,
        healthy_integrations=healthy_integrations,
        catalog=catalog,
        filtered=filtered,
        buckets=buckets,
    )


def _require_filterable_catalog(scenario: LiveHealthScenario) -> None:
    catalog_integrations = _integration_pairs(scenario.catalog)
    connection_integrations = _connection_integrations(scenario.connections)
    healthy_with_tools = catalog_integrations & scenario.healthy_integrations
    unhealthy_with_tools = (
        connection_integrations & catalog_integrations
    ) - scenario.healthy_integrations
    disconnected_with_tools = catalog_integrations - connection_integrations

    if not healthy_with_tools:
        pytest.skip("live executor has no healthy integrations with catalog tools")
    if not unhealthy_with_tools and not disconnected_with_tools:
        pytest.skip(
            "live executor has nothing to filter (need unhealthy or disconnected integrations)",
        )


def _assert_filter_matches_api_health(scenario: LiveHealthScenario) -> None:
    kept = _tool_names(scenario.filtered)
    catalog_integrations = _integration_pairs(scenario.catalog)
    connection_integrations = _connection_integrations(scenario.connections)

    for bucket in scenario.buckets:
        pair = (bucket.owner, bucket.integration)
        tool_names = set(bucket.tool_names)
        if pair in scenario.healthy_integrations:
            assert tool_names <= kept, (
                f"healthy integration {pair} should keep all tools; "
                f"missing={sorted(tool_names - kept)}"
            )
        elif pair in connection_integrations:
            assert tool_names.isdisjoint(kept), (
                f"unhealthy integration {pair} should filter all tools; "
                f"kept={sorted(tool_names & kept)}"
            )
        else:
            assert tool_names.isdisjoint(kept), (
                f"disconnected integration {pair} should filter all tools; "
                f"kept={sorted(tool_names & kept)}"
            )

    disconnected = catalog_integrations - connection_integrations
    kept_integrations = _integration_pairs(scenario.filtered)
    assert disconnected.isdisjoint(kept_integrations), (
        f"integrations without connections must be filtered out: {sorted(disconnected & kept_integrations)}"
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_live_connection_health_refresh_and_filter() -> None:
    """Probe live connections and filter catalog by integration health."""
    token = _require_live_executor()
    scenario = await _live_health_filter_scenario(token)

    assert scenario.catalog, "executor returned an empty tool catalog"
    assert all(tool.get("integration") for tool in scenario.catalog), (
        "tool list metadata (owner/integration) is required for health gating"
    )

    _require_filterable_catalog(scenario)
    _print_health_filter_report(scenario)
    _assert_filter_matches_api_health(scenario)


@pytest.mark.integration
def test_live_get_executor_catalog_applies_health_filter() -> None:
    """End-to-end: force refresh populates health cache and filters hook catalog."""
    token = _require_live_executor()
    scenario = asyncio.run(_live_health_filter_scenario(token))
    _require_filterable_catalog(scenario)

    catalog = get_executor_catalog(
        _CONFIG,
        allow_prompt=False,
        blocking=True,
        force=True,
    )
    assert catalog is not None
    assert catalog, "executor returned an empty filtered catalog"
    assert all(tool.get("integration") for tool in catalog)

    expected_kept = _tool_names(scenario.filtered)
    actual_kept = _tool_names(catalog)
    assert actual_kept == expected_kept, (
        "get_executor_catalog should match direct health filter; "
        f"extra={sorted(actual_kept - expected_kept)} "
        f"missing={sorted(expected_kept - actual_kept)}"
    )

    _print_health_filter_report(scenario)
