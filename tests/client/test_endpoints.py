"""Exact request and typed endpoint response tests."""

from datetime import UTC, datetime
from urllib.parse import parse_qs

import httpx
import pytest

from monzo_mcp.client import (
    InMemoryTokenStore,
    Merchant,
    MonzoClient,
    MonzoReauthenticationRequired,
    MonzoRequestValidationError,
    OAuthToken,
)
from tests.client.helpers import (
    ACCOUNT,
    BALANCE,
    EXPANDED_TRANSACTION,
    POT,
    TRANSACTION,
    WEBHOOK,
)


async def test_identity_accounts_balance_pots_and_logout() -> None:
    calls: list[tuple[str, str, dict[str, list[str]]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(
            (request.method, request.url.path, parse_qs(request.url.query.decode()))
        )
        assert request.headers["authorization"] == "Bearer access-static"
        if request.url.path == "/ping/whoami":
            return httpx.Response(
                200, json={"authenticated": True, "user_id": "user_1"}
            )
        if request.url.path == "/accounts":
            return httpx.Response(200, content=b'{"accounts":[' + ACCOUNT + b"]}")
        if request.url.path == "/balance":
            return httpx.Response(200, content=BALANCE)
        if request.url.path == "/pots":
            return httpx.Response(200, content=b'{"pots":[' + POT + b"]}")
        if request.url.path == "/oauth2/logout":
            return httpx.Response(204)
        return httpx.Response(404)

    store = InMemoryTokenStore(OAuthToken.static("access-static"))
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = MonzoClient(token_store=store, http_client=http)

    identity = await client.who_am_i()
    accounts = await client.list_accounts(account_type="uk_retail")
    balance = await client.get_balance("acc_1")
    pots = await client.list_pots("acc_1")
    await client.logout()

    assert identity.user_id == "user_1"
    assert accounts[0].owners[0].preferred_name == "Test User"
    assert balance.balance == 12345
    assert balance.model_extra == {"provider_future_field": True}
    assert pots[0].name == "Holiday"
    assert calls == [
        ("GET", "/ping/whoami", {}),
        ("GET", "/accounts", {"account_type": ["uk_retail"]}),
        ("GET", "/balance", {"account_id": ["acc_1"]}),
        ("GET", "/pots", {"current_account_id": ["acc_1"]}),
        ("POST", "/oauth2/logout", {}),
    ]
    assert await store.load() is None
    with pytest.raises(MonzoReauthenticationRequired):
        await client.who_am_i()
    await client.aclose()
    await http.aclose()


async def test_pot_transfers_send_integer_amount_and_stable_dedupe_id() -> None:
    calls: list[tuple[str, str, dict[str, list[str]]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(
            (request.method, request.url.path, parse_qs(request.content.decode()))
        )
        return httpx.Response(200, content=POT)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = MonzoClient(access_token="access-static", http_client=http)

    deposited = await client.deposit_into_pot(
        "pot_1",
        source_account_id="acc_1",
        amount=250,
        dedupe_id="operation-1",
    )
    withdrawn = await client.withdraw_from_pot(
        "pot_1",
        destination_account_id="acc_1",
        amount=100,
        dedupe_id="operation-2",
    )

    assert deposited.id == withdrawn.id == "pot_1"
    assert calls == [
        (
            "PUT",
            "/pots/pot_1/deposit",
            {
                "source_account_id": ["acc_1"],
                "amount": ["250"],
                "dedupe_id": ["operation-1"],
            },
        ),
        (
            "PUT",
            "/pots/pot_1/withdraw",
            {
                "destination_account_id": ["acc_1"],
                "amount": ["100"],
                "dedupe_id": ["operation-2"],
            },
        ),
    ]
    await client.aclose()
    await http.aclose()


async def test_transactions_encode_pagination_expansion_and_annotations() -> None:
    calls: list[tuple[str, str, dict[str, list[str]]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        encoded = (
            request.url.query.decode()
            if request.method == "GET"
            else request.content.decode()
        )
        calls.append(
            (
                request.method,
                request.url.path,
                parse_qs(encoded, keep_blank_values=True),
            )
        )
        if request.method == "GET" and request.url.path == "/transactions/tx_1":
            return httpx.Response(
                200,
                content=b'{"transaction":' + EXPANDED_TRANSACTION + b"}",
            )
        if request.method == "GET":
            return httpx.Response(
                200,
                content=b'{"transactions":[' + TRANSACTION + b"]}",
            )
        return httpx.Response(200, content=b'{"transaction":' + TRANSACTION + b"}")

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = MonzoClient(access_token="access-static", http_client=http)

    transaction = await client.get_transaction("tx_1", expand=("merchant",))
    page = await client.list_transactions(
        "acc_1",
        since="tx_cursor",
        before=datetime(2026, 3, 1, tzinfo=UTC),
        limit=100,
        expand=("merchant",),
    )
    annotated = await client.annotate_transaction(
        "tx_1", {"project": "holiday", "remove": None}
    )

    assert isinstance(transaction.merchant, Merchant)
    assert page[0].settled is None
    assert annotated.id == "tx_1"
    assert calls == [
        ("GET", "/transactions/tx_1", {"expand[]": ["merchant"]}),
        (
            "GET",
            "/transactions",
            {
                "account_id": ["acc_1"],
                "limit": ["100"],
                "since": ["tx_cursor"],
                "before": ["2026-03-01T00:00:00+00:00"],
                "expand[]": ["merchant"],
            },
        ),
        (
            "PATCH",
            "/transactions/tx_1",
            {"metadata[project]": ["holiday"], "metadata[remove]": [""]},
        ),
    ]
    await client.aclose()
    await http.aclose()


async def test_webhook_lifecycle_uses_expected_methods_and_paths() -> None:
    calls: list[tuple[str, str, dict[str, list[str]]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        encoded = (
            request.url.query.decode()
            if request.method == "GET"
            else request.content.decode()
        )
        calls.append((request.method, request.url.path, parse_qs(encoded)))
        if request.method == "POST":
            return httpx.Response(200, content=b'{"webhook":' + WEBHOOK + b"}")
        if request.method == "GET":
            return httpx.Response(200, content=b'{"webhooks":[' + WEBHOOK + b"]}")
        return httpx.Response(204)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = MonzoClient(access_token="access-static", http_client=http)

    webhook = await client.register_webhook("acc_1", "https://example.test/monzo")
    webhooks = await client.list_webhooks("acc_1")
    await client.delete_webhook("webhook_1")

    assert webhook == webhooks[0]
    assert calls == [
        (
            "POST",
            "/webhooks",
            {"account_id": ["acc_1"], "url": ["https://example.test/monzo"]},
        ),
        ("GET", "/webhooks", {"account_id": ["acc_1"]}),
        ("DELETE", "/webhooks/webhook_1", {}),
    ]
    await client.aclose()
    await http.aclose()


async def test_invalid_requests_fail_before_network_io() -> None:
    requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(500)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = MonzoClient(access_token="access-static", http_client=http)

    with pytest.raises(MonzoRequestValidationError):
        await client.deposit_into_pot(
            "pot_1",
            source_account_id="acc_1",
            amount=0,
            dedupe_id="operation",
        )
    with pytest.raises(MonzoRequestValidationError):
        await client.list_transactions("acc_1", limit=101)
    with pytest.raises(MonzoRequestValidationError):
        await client.list_transactions("acc_1", before=datetime(2026, 1, 1))
    with pytest.raises(MonzoRequestValidationError):
        await client.annotate_transaction("tx_1", {})
    with pytest.raises(MonzoRequestValidationError):
        await client.get_transaction("tx_1", expand="merchant")  # type: ignore[arg-type]

    assert requests == 0
    await client.aclose()
    await http.aclose()


async def test_collection_endpoints_accept_empty_lists() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/accounts":
            return httpx.Response(200, json={"accounts": []})
        if request.url.path == "/pots":
            return httpx.Response(200, json={"pots": []})
        if request.url.path == "/transactions":
            return httpx.Response(200, json={"transactions": []})
        return httpx.Response(200, json={"webhooks": []})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = MonzoClient(access_token="access-static", http_client=http)

    assert await client.list_accounts() == []
    assert await client.list_pots("acc_1") == []
    assert await client.list_transactions("acc_1") == []
    assert await client.list_webhooks("acc_1") == []

    await client.aclose()
    await http.aclose()
