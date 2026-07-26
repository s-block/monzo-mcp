from __future__ import annotations

import json
from typing import TYPE_CHECKING
from urllib.parse import parse_qs

import httpx

from monzo_mcp.mcp.settings import AccessTokenProviderMode
from tests.mcp.helpers import configured_server, connected_session
from tests.mcp.monzo_responses import (
    ACCOUNT,
    BALANCE,
    EXPANDED_TRANSACTION,
    POT,
    TRANSACTION,
)

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


async def test_read_tools_use_real_client_and_minimize_results(tmp_path: Path) -> None:
    calls: list[tuple[str, str, dict[str, list[str]]]] = []
    account = json.loads(ACCOUNT)
    account.update(
        {
            "account_number": "12345678",
            "sort_code": "040004",
        }
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer access-static"
        calls.append(
            (
                request.method,
                request.url.path,
                parse_qs(request.url.query.decode()),
            )
        )
        if request.url.path == "/ping/whoami":
            return httpx.Response(
                200,
                json={
                    "authenticated": True,
                    "client_id": "client-private",
                    "user_id": "user-private",
                },
            )
        if request.url.path == "/accounts":
            return httpx.Response(200, json={"accounts": [account]})
        if request.url.path == "/balance":
            return httpx.Response(200, content=BALANCE)
        if request.url.path == "/pots":
            return httpx.Response(200, content=b'{"pots":[' + POT + b"]}")
        if request.url.path == "/transactions/tx_1":
            return httpx.Response(
                200,
                content=b'{"transaction":' + EXPANDED_TRANSACTION + b"}",
            )
        if request.url.path == "/transactions":
            return httpx.Response(
                200,
                content=b'{"transactions":[' + TRANSACTION + b"]}",
            )
        return httpx.Response(404)

    server, factory, _ = await configured_server(tmp_path, handler=handler)
    try:
        async with connected_session(server) as session:
            status = await session.call_tool("monzo_connection_status")
            accounts = await session.call_tool(
                "monzo_list_accounts",
                {"account_type": "uk_retail"},
            )
            balance = await session.call_tool(
                "monzo_get_balance",
                {"account_id": "acc_1"},
            )
            pots = await session.call_tool(
                "monzo_list_pots",
                {"account_id": "acc_1"},
            )
            transaction = await session.call_tool(
                "monzo_get_transaction",
                {"transaction_id": "tx_1", "expand_merchant": True},
            )
            transactions = await session.call_tool(
                "monzo_list_transactions",
                {
                    "account_id": "acc_1",
                    "since": "tx_cursor",
                    "limit": 100,
                },
            )
    finally:
        await factory.aclose()

    assert factory.provider_factory.created == 6
    assert factory.provider_factory.provider.gets == 6
    assert status.structuredContent == {"authenticated": True}
    assert accounts.structuredContent is not None
    account_output = accounts.structuredContent["accounts"][0]
    assert account_output["id"] == "acc_1"
    assert "account_number" not in account_output
    assert "sort_code" not in account_output
    assert "owners" not in account_output
    assert balance.structuredContent is not None
    assert balance.structuredContent["balance"] == 12345
    assert pots.structuredContent is not None
    assert pots.structuredContent["pots"][0]["name"] == "Holiday"
    assert transaction.structuredContent is not None
    transaction_output = transaction.structuredContent["transaction"]
    assert transaction_output["merchant"]["name"] == "Cafe"
    assert "address" not in transaction_output["merchant"]
    assert "metadata" not in transaction_output
    assert "counterparty" not in transaction_output
    assert transactions.structuredContent is not None
    transaction_list_output = transactions.structuredContent["transactions"][0]
    assert transaction_list_output["description"] == "Coffee"
    assert transaction_list_output["category"] == "eating_out"
    assert "notes" not in transaction_list_output
    assert "settled" not in transaction_list_output
    assert "merchant_id" not in transaction_list_output
    assert transactions.structuredContent["since"] == "tx_cursor"
    assert transactions.structuredContent["limit"] == 100
    assert transactions.structuredContent["returned_count"] == 1
    assert transactions.structuredContent["next_since"] is None
    assert calls == [
        ("GET", "/ping/whoami", {}),
        ("GET", "/accounts", {"account_type": ["uk_retail"]}),
        ("GET", "/balance", {"account_id": ["acc_1"]}),
        ("GET", "/pots", {"current_account_id": ["acc_1"]}),
        ("GET", "/transactions/tx_1", {"expand[]": ["merchant"]}),
        (
            "GET",
            "/transactions",
            {
                "account_id": ["acc_1"],
                "limit": ["100"],
                "since": ["tx_cursor"],
            },
        ),
    ]


async def test_list_transactions_defaults_to_thirty_day_window(
    tmp_path: Path,
) -> None:
    calls: list[dict[str, list[str]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(parse_qs(request.url.query.decode()))
        return httpx.Response(
            200,
            content=b'{"transactions":[' + TRANSACTION + b"]}",
        )

    server, factory, _ = await configured_server(tmp_path, handler=handler)
    try:
        async with connected_session(server) as session:
            transactions = await session.call_tool(
                "monzo_list_transactions",
                {
                    "account_id": "acc_1",
                    "before": "2026-03-03T00:00:00Z",
                },
            )
    finally:
        await factory.aclose()

    assert transactions.isError is False
    assert calls == [
        {
            "account_id": ["acc_1"],
            "limit": ["30"],
            "since": ["2026-02-01T00:00:00+00:00"],
            "before": ["2026-03-03T00:00:00+00:00"],
        }
    ]
    assert transactions.structuredContent is not None
    assert transactions.structuredContent["since"] == "2026-02-01T00:00:00+00:00"
    assert transactions.structuredContent["before"] == "2026-03-03T00:00:00Z"
    assert transactions.structuredContent["limit"] == 30
    assert transactions.structuredContent["returned_count"] == 1


async def test_tool_validation_and_permission_errors_are_safe(
    tmp_path: Path,
) -> None:
    requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(
            403,
            json={"message": "approval required", "secret": "provider-sensitive"},
        )

    server, factory, _ = await configured_server(tmp_path, handler=handler)
    try:
        async with connected_session(server) as session:
            invalid = await session.call_tool(
                "monzo_list_transactions",
                {"account_id": "acc_1", "limit": 101},
            )
            oversized_account = await session.call_tool(
                "monzo_get_balance",
                {"account_id": "a" * 257},
            )
            oversized_cursor = await session.call_tool(
                "monzo_list_transactions",
                {"account_id": "acc_1", "since": "c" * 257},
            )
            denied = await session.call_tool(
                "monzo_get_balance",
                {"account_id": "acc_1"},
            )
    finally:
        await factory.aclose()

    assert invalid.isError is True
    assert oversized_account.isError is True
    assert oversized_cursor.isError is True
    assert requests == 1
    assert denied.isError is True
    serialized = denied.model_dump_json()
    assert "denied permission" in serialized
    assert "provider-sensitive" not in serialized


async def test_verification_required_explains_recent_transaction_window(
    tmp_path: Path,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            403,
            json={
                "code": "forbidden.verification_required",
                "message": "Verification required",
                "secret": "provider-sensitive",
            },
        )

    server, factory, _ = await configured_server(tmp_path, handler=handler)
    try:
        async with connected_session(server) as session:
            denied = await session.call_tool(
                "monzo_list_transactions",
                {
                    "account_id": "acc_1",
                    "since": "2026-01-01T00:00:00Z",
                },
            )
    finally:
        await factory.aclose()

    assert denied.isError is True
    serialized = denied.model_dump_json()
    assert "recent in-app verification" in serialized
    assert "within the last 89 days" in serialized
    assert "provider-sensitive" not in serialized


async def test_authentication_rate_limit_and_invalid_response_errors_are_safe(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    responses = [
        httpx.Response(
            401,
            json={"message": "token-sensitive-provider-message"},
        ),
        httpx.Response(
            429,
            headers={"Retry-After": "999"},
            json={"message": "rate-sensitive-provider-message"},
        ),
        httpx.Response(200, content=b"not-json-sensitive"),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return responses.pop(0)

    server, factory, _ = await configured_server(tmp_path, handler=handler)
    try:
        async with connected_session(server) as session:
            unauthorized = await session.call_tool(
                "monzo_get_balance",
                {"account_id": "acc_1"},
            )
            limited = await session.call_tool(
                "monzo_get_balance",
                {"account_id": "acc_1"},
            )
            invalid = await session.call_tool(
                "monzo_get_balance",
                {"account_id": "acc_1"},
            )
    finally:
        await factory.aclose()

    assert unauthorized.isError is True
    assert "run monzo-mcp auth login" in unauthorized.model_dump_json()
    assert limited.isError is True
    assert "999 seconds" in limited.model_dump_json()
    assert invalid.isError is True
    rendered = (
        unauthorized.model_dump_json()
        + limited.model_dump_json()
        + invalid.model_dump_json()
    )
    assert "provider-message" not in rendered
    assert "not-json-sensitive" not in rendered
    assert responses == []
    assert "security_event=monzo_authentication_failed" in caplog.messages
    assert (
        "security_event=monzo_rate_limited retry_after_supplied=True" in caplog.messages
    )
    assert "provider-message" not in caplog.text


async def test_broker_mode_authentication_error_tells_client_to_reconnect(
    tmp_path: Path,
) -> None:
    server, factory, _ = await configured_server(
        tmp_path,
        handler=lambda _request: httpx.Response(401),
        access_token_provider=AccessTokenProviderMode.BROKER,
    )
    try:
        async with connected_session(server) as session:
            unauthorized = await session.call_tool(
                "monzo_get_balance",
                {"account_id": "acc_1"},
            )
    finally:
        await factory.aclose()

    assert unauthorized.isError is True
    assert "reconnect it in your MCP client" in unauthorized.model_dump_json()
