"""High-level fully asynchronous Monzo Developer API client."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Literal, Self, TypeVar
from urllib.parse import quote

import httpx
from pydantic import (
    AnyHttpUrl,
    AwareDatetime,
    ConfigDict,
    SecretStr,
    TypeAdapter,
    ValidationError,
)

from monzo_mcp.client.auth import (
    AccessTokenProvider,
    InMemoryTokenStore,
    OAuthAccessTokenProvider,
    TokenStore,
)
from monzo_mcp.client.exceptions import (
    MonzoConfigurationError,
    MonzoRequestValidationError,
)
from monzo_mcp.client.models import (
    Account,
    AuthorizationRequest,
    Balance,
    NonEmptyString,
    OAuthClientConfig,
    OAuthToken,
    PaginationLimit,
    PositiveMinorUnits,
    Pot,
    RetryPolicy,
    Transaction,
    Webhook,
    WhoAmI,
    _AccountsResponse,
    _EmptyResponse,
    _PotsResponse,
    _TransactionResponse,
    _TransactionsResponse,
    _WebhookResponse,
    _WebhooksResponse,
)
from monzo_mcp.client.transport import _AsyncTransport

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

_ValueT = TypeVar("_ValueT")
_NON_EMPTY_ADAPTER: TypeAdapter[str] = TypeAdapter(
    NonEmptyString, config=ConfigDict(strict=True)
)
_AMOUNT_ADAPTER: TypeAdapter[int] = TypeAdapter(PositiveMinorUnits)
_LIMIT_ADAPTER: TypeAdapter[int] = TypeAdapter(PaginationLimit)
_URL_ADAPTER: TypeAdapter[AnyHttpUrl] = TypeAdapter(
    AnyHttpUrl, config=ConfigDict(strict=True)
)
_DATETIME_ADAPTER: TypeAdapter[datetime] = TypeAdapter(
    AwareDatetime, config=ConfigDict(strict=True)
)
_DEFAULT_TIMEOUT = httpx.Timeout(connect=5.0, read=10.0, write=10.0, pool=5.0)


class MonzoClient:
    """A pooled, async, typed client for the public Monzo API."""

    def __init__(
        self,
        *,
        access_token: str | SecretStr | OAuthToken | None = None,
        access_token_provider: AccessTokenProvider | None = None,
        oauth: OAuthClientConfig | None = None,
        token_store: TokenStore | None = None,
        http_client: httpx.AsyncClient | None = None,
        api_base_url: str = "https://api.monzo.com",
        timeout: httpx.Timeout | float | None = None,
        limits: httpx.Limits | None = None,
        retry_policy: RetryPolicy | None = None,
        refresh_skew: timedelta = timedelta(seconds=30),
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """Configure a client without performing network or filesystem I/O."""
        credential_sources = sum(
            source is not None
            for source in (
                access_token,
                access_token_provider,
                token_store,
            )
        )
        if credential_sources > 1 or (
            access_token_provider is not None and oauth is not None
        ):
            raise MonzoConfigurationError("Pass exactly one access token source")
        if refresh_skew < timedelta(0):
            raise MonzoConfigurationError("refresh_skew must not be negative")

        initial_token: OAuthToken | None
        if isinstance(access_token, OAuthToken):
            initial_token = access_token
        elif access_token is not None:
            try:
                initial_token = OAuthToken.static(access_token)
            except ValidationError:
                raise MonzoConfigurationError(
                    "access_token must not be empty"
                ) from None
        else:
            initial_token = None

        base_url = self._base_url(api_base_url)
        owns_http_client = http_client is None
        if http_client is None:
            http_client = httpx.AsyncClient(
                timeout=timeout if timeout is not None else _DEFAULT_TIMEOUT,
                limits=limits
                or httpx.Limits(
                    max_connections=20,
                    max_keepalive_connections=10,
                    keepalive_expiry=30.0,
                ),
                headers={"User-Agent": "monzo-mcp/0.1.0"},
            )
        oauth_manager: OAuthAccessTokenProvider | None = None
        if access_token_provider is None:
            store = token_store or InMemoryTokenStore(initial_token)
            oauth_manager = OAuthAccessTokenProvider(
                http_client=http_client,
                token_store=store,
                oauth=oauth,
                api_base_url=base_url,
                refresh_skew=refresh_skew,
                clock=clock,
            )
            access_token_provider = oauth_manager
        self._oauth_manager = oauth_manager
        self._auth = access_token_provider
        self._transport = _AsyncTransport(
            http_client=http_client,
            auth=self._auth,
            api_base_url=base_url,
            owns_http_client=owns_http_client,
            retry_policy=retry_policy or RetryPolicy(),
        )

    async def __aenter__(self) -> Self:
        await self._transport.__aenter__()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Close resources owned by this client."""
        await self._transport.aclose()

    def create_authorization_request(
        self,
        *,
        state: str | None = None,
    ) -> AuthorizationRequest:
        """Create a Monzo browser authorization URL and CSRF state."""
        return self._require_oauth_manager().create_authorization_request(state=state)

    async def exchange_authorization_code(
        self,
        code: str,
        *,
        expected_state: str,
        returned_state: str,
    ) -> OAuthToken:
        """Validate callback state, exchange a code, and persist the token set."""
        return await self._require_oauth_manager().exchange_authorization_code(
            code,
            expected_state=expected_state,
            returned_state=returned_state,
        )

    async def refresh_access_token(self) -> OAuthToken:
        """Explicitly rotate the current OAuth token set."""
        return await self._require_oauth_manager().refresh_access_token()

    async def logout(self) -> None:
        """Invalidate the current access token, then clear the token store."""
        oauth_manager = self._require_oauth_manager()
        await self._transport.request(
            "POST",
            "/oauth2/logout",
            response_model=_EmptyResponse,
        )
        await oauth_manager.clear()

    async def who_am_i(self) -> WhoAmI:
        """Return the identity associated with the current token."""
        return await self._transport.request(
            "GET",
            "/ping/whoami",
            response_model=WhoAmI,
            retry_safe=True,
        )

    async def list_accounts(self, *, account_type: str | None = None) -> list[Account]:
        """List accounts accessible to the current user."""
        params = None
        if account_type is not None:
            params = {"account_type": self._non_empty(account_type, "account_type")}
        response = await self._transport.request(
            "GET",
            "/accounts",
            response_model=_AccountsResponse,
            params=params,
            retry_safe=True,
        )
        return response.accounts

    async def get_balance(self, account_id: str) -> Balance:
        """Get the current balance for one explicit account."""
        return await self._transport.request(
            "GET",
            "/balance",
            response_model=Balance,
            params={"account_id": self._non_empty(account_id, "account_id")},
            retry_safe=True,
        )

    async def list_pots(self, account_id: str) -> list[Pot]:
        """List pots belonging to one explicit account."""
        response = await self._transport.request(
            "GET",
            "/pots",
            response_model=_PotsResponse,
            params={"current_account_id": self._non_empty(account_id, "account_id")},
            retry_safe=True,
        )
        return response.pots

    async def deposit_into_pot(
        self,
        pot_id: str,
        *,
        source_account_id: str,
        amount: int,
        dedupe_id: str,
    ) -> Pot:
        """Move minor currency units into a pot using a stable dedupe ID."""
        return await self._transport.request(
            "PUT",
            f"/pots/{self._identifier(pot_id, 'pot_id')}/deposit",
            response_model=Pot,
            data={
                "source_account_id": self._non_empty(
                    source_account_id, "source_account_id"
                ),
                "amount": str(self._amount(amount)),
                "dedupe_id": self._non_empty(dedupe_id, "dedupe_id"),
            },
            retry_safe=True,
        )

    async def withdraw_from_pot(
        self,
        pot_id: str,
        *,
        destination_account_id: str,
        amount: int,
        dedupe_id: str,
    ) -> Pot:
        """Move minor currency units out of a pot using a stable dedupe ID."""
        return await self._transport.request(
            "PUT",
            f"/pots/{self._identifier(pot_id, 'pot_id')}/withdraw",
            response_model=Pot,
            data={
                "destination_account_id": self._non_empty(
                    destination_account_id, "destination_account_id"
                ),
                "amount": str(self._amount(amount)),
                "dedupe_id": self._non_empty(dedupe_id, "dedupe_id"),
            },
            retry_safe=True,
        )

    async def get_transaction(
        self,
        transaction_id: str,
        *,
        expand: Sequence[Literal["merchant"]] = (),
    ) -> Transaction:
        """Get one transaction, optionally expanding its merchant."""
        params = self._expand_params(expand)
        response = await self._transport.request(
            "GET",
            f"/transactions/{self._identifier(transaction_id, 'transaction_id')}",
            response_model=_TransactionResponse,
            params=params,
            retry_safe=True,
        )
        return response.transaction

    async def list_transactions(
        self,
        account_id: str,
        *,
        since: datetime | str | None = None,
        before: datetime | None = None,
        limit: int = 30,
        expand: Sequence[Literal["merchant"]] = (),
    ) -> list[Transaction]:
        """Return one bounded page of transactions for an explicit account."""
        params: list[tuple[str, str]] = [
            ("account_id", self._non_empty(account_id, "account_id")),
            ("limit", str(self._limit(limit))),
        ]
        if since is not None:
            params.append(("since", self._cursor(since, "since")))
        if before is not None:
            params.append(("before", self._timestamp(before, "before")))
        params.extend(self._expand_params(expand))
        response = await self._transport.request(
            "GET",
            "/transactions",
            response_model=_TransactionsResponse,
            params=params,
            retry_safe=True,
        )
        return response.transactions

    async def annotate_transaction(
        self,
        transaction_id: str,
        metadata: Mapping[str, str | None],
    ) -> Transaction:
        """Set annotations, using ``None`` to remove an existing metadata key."""
        if not metadata:
            raise MonzoRequestValidationError("metadata must not be empty")
        form: dict[str, str] = {}
        for key, value in metadata.items():
            safe_key = self._non_empty(key, "metadata key")
            if value is not None and not isinstance(value, str):
                raise MonzoRequestValidationError(
                    "metadata values must be strings or None"
                )
            form[f"metadata[{safe_key}]"] = value or ""
        response = await self._transport.request(
            "PATCH",
            f"/transactions/{self._identifier(transaction_id, 'transaction_id')}",
            response_model=_TransactionResponse,
            data=form,
        )
        return response.transaction

    async def register_webhook(self, account_id: str, url: str) -> Webhook:
        """Register an HTTPS webhook for one account."""
        safe_url = self._validate(url, _URL_ADAPTER, "url")
        response = await self._transport.request(
            "POST",
            "/webhooks",
            response_model=_WebhookResponse,
            data={
                "account_id": self._non_empty(account_id, "account_id"),
                "url": str(safe_url),
            },
        )
        return response.webhook

    async def list_webhooks(self, account_id: str) -> list[Webhook]:
        """List webhooks registered for one account."""
        response = await self._transport.request(
            "GET",
            "/webhooks",
            response_model=_WebhooksResponse,
            params={"account_id": self._non_empty(account_id, "account_id")},
            retry_safe=True,
        )
        return response.webhooks

    async def delete_webhook(self, webhook_id: str) -> None:
        """Delete one webhook by ID."""
        await self._transport.request(
            "DELETE",
            f"/webhooks/{self._identifier(webhook_id, 'webhook_id')}",
            response_model=_EmptyResponse,
            retry_safe=True,
        )

    @staticmethod
    def _validate(
        value: object,
        adapter: TypeAdapter[_ValueT],
        field: str,
    ) -> _ValueT:
        try:
            return adapter.validate_python(value)
        except ValidationError:
            raise MonzoRequestValidationError(f"Invalid {field}") from None

    @classmethod
    def _non_empty(cls, value: str, field: str) -> str:
        return cls._validate(value, _NON_EMPTY_ADAPTER, field)

    @classmethod
    def _identifier(cls, value: str, field: str) -> str:
        return quote(cls._non_empty(value, field), safe="")

    @classmethod
    def _amount(cls, value: int) -> int:
        return cls._validate(value, _AMOUNT_ADAPTER, "amount")

    @classmethod
    def _limit(cls, value: int) -> int:
        return cls._validate(value, _LIMIT_ADAPTER, "limit")

    @classmethod
    def _timestamp(cls, value: datetime, field: str) -> str:
        timestamp = cls._validate(value, _DATETIME_ADAPTER, field)
        return timestamp.isoformat()

    @classmethod
    def _cursor(cls, value: datetime | str, field: str) -> str:
        if isinstance(value, datetime):
            return cls._timestamp(value, field)
        return cls._non_empty(value, field)

    @classmethod
    def _expand_params(
        cls,
        expand: Sequence[Literal["merchant"]],
    ) -> list[tuple[str, str]]:
        if isinstance(expand, str):
            raise MonzoRequestValidationError("expand must be a sequence")
        params: list[tuple[str, str]] = []
        for item in expand:
            if item != "merchant":
                raise MonzoRequestValidationError("Only merchant can be expanded")
            params.append(("expand[]", item))
        return params

    @staticmethod
    def _base_url(value: str) -> str:
        try:
            url = httpx.URL(value)
        except (TypeError, ValueError):
            raise MonzoConfigurationError("api_base_url is invalid") from None
        if url.scheme not in {"http", "https"} or not url.host:
            raise MonzoConfigurationError("api_base_url must be an HTTP(S) URL")
        if url.query or url.fragment:
            raise MonzoConfigurationError(
                "api_base_url must not contain a query or fragment"
            )
        return str(url).rstrip("/")

    def _require_oauth_manager(self) -> OAuthAccessTokenProvider:
        if self._oauth_manager is None:
            raise MonzoConfigurationError(
                "OAuth token management is unavailable for this access token provider"
            )
        return self._oauth_manager
