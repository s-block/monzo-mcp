# Python client

The `monzo_mcp.client` package is a fully asynchronous, typed Monzo Developer
API client that can be used without running the MCP server.

## Installation

Install a local checkout:

```bash
uv add /absolute/path/to/monzo-mcp
```

or:

```bash
python -m pip install /absolute/path/to/monzo-mcp
```

Python 3.12 through 3.14 are supported.

## Static token example

For short-lived development with a Monzo API Playground token:

```python
from monzo_mcp.client import MonzoClient


async def read_balance(playground_token: str) -> int:
    async with MonzoClient(access_token=playground_token) as monzo:
        accounts = await monzo.list_accounts()
        balance = await monzo.get_balance(accounts[0].id)
        return balance.balance
```

Do not embed tokens in source code, command history, test fixtures, or logs.

## Credential sources

`MonzoClient` accepts exactly one access-token source:

| Source | Use |
| --- | --- |
| `access_token` | Static string, `SecretStr`, or `OAuthToken` |
| `token_store` plus optional `oauth` | Client-managed OAuth and refresh |
| `access_token_provider` | External request-time token ownership |

Passing more than one source, or an external provider together with `oauth`, is
rejected.

## Custom token store

OAuth persistence uses a small async protocol:

```python
from monzo_mcp.client import OAuthToken


class MyTokenStore:
    async def load(self) -> OAuthToken | None:
        ...

    async def save(self, token: OAuthToken) -> None:
        ...

    async def clear(self) -> None:
        ...
```

`save()` must atomically replace the complete token set. Monzo refresh tokens
are one-time credentials, so the store and surrounding application must not
allow concurrent refresh using the same token.

Construct the client with a confidential OAuth configuration:

```python
from pydantic import SecretStr

from monzo_mcp.client import MonzoClient, OAuthClientConfig


oauth = OAuthClientConfig(
    client_id="YOUR_MONZO_CLIENT_ID",
    client_secret=SecretStr(load_secret_securely()),
    redirect_uri="http://127.0.0.1:8765/oauth/callback",
)

async with MonzoClient(oauth=oauth, token_store=store) as monzo:
    request = monzo.create_authorization_request()
    # Send request.url to the human and retain request.state outside the model.
```

After receiving the callback:

```python
token = await monzo.exchange_authorization_code(
    callback_code,
    expected_state=request.state,
    returned_state=callback_state,
)
```

The client validates state, exchanges the code, and saves the returned token.

## Custom access-token provider

For an existing credential service:

```python
class MyAccessTokenProvider:
    async def get_access_token(self) -> str:
        return await credential_service.usable_access_token()

    async def refresh_after_rejection(
        self,
        rejected_access_token: str,
    ) -> str:
        return await credential_service.resolve_rejected_token(
            rejected_access_token
        )
```

Then:

```python
async with MonzoClient(
    access_token_provider=MyAccessTokenProvider(),
) as monzo:
    identity = await monzo.who_am_i()
```

The provider owns storage and refresh. `MonzoClient` calls
`refresh_after_rejection` at most once when Monzo rejects a request.

## Public operations

| Method | Result |
| --- | --- |
| `create_authorization_request()` | Authorization URL and state |
| `exchange_authorization_code()` | Persisted OAuth token |
| `refresh_access_token()` | Rotated and persisted OAuth token |
| `logout()` | Remote invalidation and local clear |
| `who_am_i()` | Token identity metadata |
| `list_accounts()` | Typed accounts |
| `get_balance(account_id)` | Typed balance |
| `list_pots(account_id)` | Typed pots |
| `deposit_into_pot(...)` | Updated pot |
| `withdraw_from_pot(...)` | Updated pot |
| `get_transaction(transaction_id, ...)` | Typed transaction |
| `list_transactions(account_id, ...)` | Bounded typed page |
| `annotate_transaction(transaction_id, metadata)` | Updated transaction |
| `register_webhook(account_id, url)` | Registered webhook |
| `list_webhooks(account_id)` | Typed webhooks |
| `delete_webhook(webhook_id)` | No result |

The Python client exposes more provider data than the MCP data-minimized
models. Treat all returned objects as sensitive application data.

## HTTP client ownership

By default, `MonzoClient` creates and closes its own pooled
`httpx.AsyncClient`. For a larger application, inject one:

```python
import httpx

from monzo_mcp.client import MonzoClient


async with httpx.AsyncClient() as http:
    async with MonzoClient(
        access_token=token,
        http_client=http,
    ) as monzo:
        accounts = await monzo.list_accounts()
```

Closing `MonzoClient` does not close an injected client.

## Exceptions

All public failures derive from `MonzoClientError`. Important subclasses
include:

- `MonzoConfigurationError`
- `MonzoRequestValidationError`
- `MonzoReauthenticationRequired`
- `MonzoAuthenticationError`
- `MonzoPermissionError`
- `MonzoRateLimitError`
- `MonzoTimeoutError`
- `MonzoTransportError`
- `MonzoResponseDecodeError`
- `MonzoResponseValidationError`
- `MonzoHTTPError`
- `MonzoTokenStoreError`
- `MonzoClosedError`

Exceptions are designed to be safe and typed, but applications should still
avoid logging arbitrary input, provider response bodies, or returned financial
models.

## Validation and retries

- Identifiers must be non-empty and are safely path-encoded.
- Money movements require positive integer minor units.
- Page limits are from 1 through 100.
- Timestamps must be timezone-aware.
- Registered webhook URLs must be valid HTTP(S) URLs; use HTTPS in deployment.
- Network timeouts are bounded.
- Cancellation is preserved.
- Safe reads and deduplicated pot movements can be retried according to the
  configured `RetryPolicy`.
- Authentication rejection permits one access-token-provider retry.
