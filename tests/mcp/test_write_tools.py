from __future__ import annotations

from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs

import httpx
from mcp.types import ElicitResult

from tests.client.helpers import POT, TRANSACTION
from tests.mcp.helpers import configured_server, connected_session

if TYPE_CHECKING:
    from pathlib import Path

    from mcp import ClientSession
    from mcp.shared.context import RequestContext
    from mcp.types import ElicitRequestParams, ErrorData


async def test_approved_pot_transfer_uses_exact_dedupe_id(
    tmp_path: Path,
) -> None:
    calls: list[dict[str, list[str]]] = []
    elicitations: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(parse_qs(request.content.decode()))
        return httpx.Response(200, content=POT)

    async def approve(
        context: RequestContext[ClientSession, Any],
        params: ElicitRequestParams,
    ) -> ElicitResult | ErrorData:
        del context
        elicitations.append(params.message)
        return ElicitResult(action="accept", content={"confirm": True})

    server, factory, _ = await configured_server(
        tmp_path,
        handler=handler,
        enable_writes=True,
    )
    try:
        async with connected_session(
            server,
            elicitation_callback=approve,
        ) as session:
            result = await session.call_tool(
                "monzo_deposit_into_pot",
                {
                    "pot_id": "pot_1",
                    "source_account_id": "acc_1",
                    "amount_minor": 250,
                    "dedupe_id": "stable-operation-1",
                },
            )
    finally:
        await factory.aclose()

    assert result.isError is not True
    assert len(elicitations) == 1
    assert "250 minor currency units" in elicitations[0]
    assert calls == [
        {
            "source_account_id": ["acc_1"],
            "amount": ["250"],
            "dedupe_id": ["stable-operation-1"],
        }
    ]


async def test_declined_or_unsupported_confirmation_never_calls_monzo(
    tmp_path: Path,
) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, content=POT)

    async def decline(
        context: RequestContext[ClientSession, Any],
        params: ElicitRequestParams,
    ) -> ElicitResult | ErrorData:
        del context, params
        return ElicitResult(action="decline")

    async def cancel(
        context: RequestContext[ClientSession, Any],
        params: ElicitRequestParams,
    ) -> ElicitResult | ErrorData:
        del context, params
        return ElicitResult(action="cancel")

    server, factory, _ = await configured_server(
        tmp_path,
        handler=handler,
        enable_writes=True,
    )
    try:
        async with connected_session(
            server,
            elicitation_callback=decline,
        ) as session:
            declined = await session.call_tool(
                "monzo_withdraw_from_pot",
                {
                    "pot_id": "pot_1",
                    "destination_account_id": "acc_1",
                    "amount_minor": 100,
                    "dedupe_id": "stable-operation-2",
                },
            )
        assert calls == 0

        async with connected_session(
            server,
            elicitation_callback=cancel,
        ) as session:
            cancelled = await session.call_tool(
                "monzo_withdraw_from_pot",
                {
                    "pot_id": "pot_1",
                    "destination_account_id": "acc_1",
                    "amount_minor": 100,
                    "dedupe_id": "stable-operation-2",
                },
            )
        assert calls == 0

        async with connected_session(server) as session:
            unsupported = await session.call_tool(
                "monzo_withdraw_from_pot",
                {
                    "pot_id": "pot_1",
                    "destination_account_id": "acc_1",
                    "amount_minor": 100,
                    "dedupe_id": "stable-operation-2",
                },
            )
    finally:
        await factory.aclose()

    assert declined.isError is True
    assert cancelled.isError is True
    assert unsupported.isError is True
    assert calls == 0


async def test_non_positive_transfer_fails_before_confirmation_or_request(
    tmp_path: Path,
) -> None:
    calls = 0
    elicitations = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        del request
        calls += 1
        return httpx.Response(200, content=POT)

    async def approve(
        context: RequestContext[ClientSession, Any],
        params: ElicitRequestParams,
    ) -> ElicitResult | ErrorData:
        nonlocal elicitations
        del context, params
        elicitations += 1
        return ElicitResult(action="accept", content={"confirm": True})

    server, factory, _ = await configured_server(
        tmp_path,
        handler=handler,
        enable_writes=True,
    )
    try:
        async with connected_session(
            server,
            elicitation_callback=approve,
        ) as session:
            result = await session.call_tool(
                "monzo_deposit_into_pot",
                {
                    "pot_id": "pot_1",
                    "source_account_id": "acc_1",
                    "amount_minor": 0,
                    "dedupe_id": "stable-operation-3",
                },
            )
    finally:
        await factory.aclose()

    assert result.isError is True
    assert elicitations == 0
    assert calls == 0


async def test_annotation_write_is_typed_and_data_minimized(tmp_path: Path) -> None:
    calls: list[dict[str, list[str]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(parse_qs(request.content.decode(), keep_blank_values=True))
        return httpx.Response(
            200,
            content=b'{"transaction":' + TRANSACTION + b"}",
        )

    server, factory, _ = await configured_server(
        tmp_path,
        handler=handler,
        enable_writes=True,
    )
    try:
        async with connected_session(server) as session:
            result = await session.call_tool(
                "monzo_annotate_transaction",
                {
                    "transaction_id": "tx_1",
                    "metadata": {"project": "holiday", "remove": None},
                },
            )
    finally:
        await factory.aclose()

    assert result.isError is not True
    assert result.structuredContent is not None
    assert "metadata" not in result.structuredContent["transaction"]
    assert calls == [
        {
            "metadata[project]": ["holiday"],
            "metadata[remove]": [""],
        }
    ]
