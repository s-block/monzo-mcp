"""Credential-broker provider contract tests."""

from __future__ import annotations

import hashlib

import httpx
import pytest
from pydantic import SecretStr

from monzo_mcp.mcp.broker import (
    BrokerAccessTokenProvider,
    CredentialBrokerAuthorizationError,
    CredentialBrokerReauthenticationRequiredError,
    CredentialBrokerUnavailableError,
)
from monzo_mcp.mcp.settings import AccessTokenBrokerSettings

_BROKER_URL = "https://credential-broker.test/access-token"
_DELEGATION = "Bearer signed-delegation"


def _provider(
    handler: httpx.MockTransport,
) -> tuple[BrokerAccessTokenProvider, httpx.AsyncClient]:
    client = httpx.AsyncClient(transport=handler)
    return (
        BrokerAccessTokenProvider(
            http_client=client,
            settings=AccessTokenBrokerSettings(url=_BROKER_URL),
            delegation=SecretStr(_DELEGATION),
        ),
        client,
    )


async def test_rejected_token_is_sent_only_as_sha256_fingerprint() -> None:
    rejected_token = "rejected-secret-access-token"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == _DELEGATION
        assert rejected_token not in request.content.decode()
        assert request.content.decode() == (
            '{"rejected_access_token_sha256":"'
            f"{hashlib.sha256(rejected_token.encode()).hexdigest()}"
            '"}'
        )
        return httpx.Response(
            200,
            json={
                "access_token": "replacement-token",
                "token_type": "Bearer",
                "expires_at": "2026-07-24T18:00:00Z",
            },
        )

    provider, client = _provider(httpx.MockTransport(handler))
    try:
        token = await provider.refresh_after_rejection(rejected_token)
    finally:
        await client.aclose()

    assert token == "replacement-token"


@pytest.mark.parametrize(
    ("status_code", "expected_error"),
    [
        (401, CredentialBrokerAuthorizationError),
        (403, CredentialBrokerAuthorizationError),
        (409, CredentialBrokerReauthenticationRequiredError),
        (500, CredentialBrokerUnavailableError),
    ],
)
async def test_broker_statuses_map_to_safe_errors(
    status_code: int,
    expected_error: type[Exception],
) -> None:
    provider, client = _provider(
        httpx.MockTransport(
            lambda _request: httpx.Response(status_code, text="sensitive detail")
        )
    )
    try:
        with pytest.raises(expected_error) as raised:
            await provider.get_access_token()
    finally:
        await client.aclose()

    assert "sensitive detail" not in str(raised.value)


async def test_invalid_success_response_is_rejected() -> None:
    provider, client = _provider(
        httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                json={"access_token": "token", "token_type": "Basic"},
            )
        )
    )
    try:
        with pytest.raises(CredentialBrokerUnavailableError):
            await provider.get_access_token()
    finally:
        await client.aclose()
