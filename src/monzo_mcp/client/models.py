"""Pydantic models for the public Monzo Developer API."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Annotated

from pydantic import (
    AnyHttpUrl,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    StringConstraints,
    field_validator,
    model_validator,
)

type NonEmptyString = Annotated[str, StringConstraints(min_length=1)]
type CurrencyCode = Annotated[
    str,
    StringConstraints(min_length=3, max_length=3, pattern=r"^[A-Z]{3}$"),
]
type PositiveMinorUnits = Annotated[int, Field(strict=True, gt=0)]
type PaginationLimit = Annotated[int, Field(strict=True, ge=1, le=100)]


class MonzoModel(BaseModel):
    """Base model that validates known fields and preserves provider additions."""

    model_config = ConfigDict(extra="allow", frozen=True, strict=True)


class OAuthClientConfig(MonzoModel):
    """Confidential OAuth client credentials registered with Monzo."""

    client_id: NonEmptyString
    client_secret: SecretStr
    redirect_uri: AnyHttpUrl

    @field_validator("client_secret")
    @classmethod
    def client_secret_must_not_be_empty(cls, value: SecretStr) -> SecretStr:
        """Reject an empty confidential client secret."""
        if not value.get_secret_value():
            msg = "client_secret must not be empty"
            raise ValueError(msg)
        return value


class AuthorizationRequest(MonzoModel):
    """Monzo authorization URL and the state that must survive the callback."""

    url: AnyHttpUrl
    state: NonEmptyString


class OAuthToken(MonzoModel):
    """A token set held by a token store."""

    access_token: SecretStr
    token_type: NonEmptyString = "Bearer"  # noqa: S105
    refresh_token: SecretStr | None = None
    expires_at: AwareDatetime | None = None
    client_id: str | None = None
    user_id: str | None = None

    @field_validator("access_token")
    @classmethod
    def access_token_must_not_be_empty(cls, value: SecretStr) -> SecretStr:
        """Reject an empty access token."""
        if not value.get_secret_value():
            msg = "access_token must not be empty"
            raise ValueError(msg)
        return value

    @field_validator("refresh_token")
    @classmethod
    def refresh_token_must_not_be_empty(
        cls,
        value: SecretStr | None,
    ) -> SecretStr | None:
        """Treat an empty refresh token as invalid instead of silently unusable."""
        if value is not None and not value.get_secret_value():
            msg = "refresh_token must not be empty"
            raise ValueError(msg)
        return value

    @classmethod
    def static(cls, access_token: str | SecretStr) -> OAuthToken:
        """Build a non-expiring token set, such as an API-playground token."""
        token = (
            access_token
            if isinstance(access_token, SecretStr)
            else SecretStr(access_token)
        )
        return cls(access_token=token)

    def is_expiring(self, *, at: datetime, skew: timedelta) -> bool:
        """Return whether the token is expired or inside the refresh window."""
        return self.expires_at is not None and self.expires_at <= at + skew


class _OAuthTokenResponse(MonzoModel):
    """Wire response returned by either OAuth token grant."""

    access_token: SecretStr
    expires_in: Annotated[int, Field(strict=True, gt=0)]
    token_type: NonEmptyString
    refresh_token: SecretStr | None = None
    client_id: str | None = None
    user_id: str | None = None

    def to_token(self, *, received_at: datetime) -> OAuthToken:
        """Convert relative token expiry into an absolute timestamp."""
        return OAuthToken(
            access_token=self.access_token,
            token_type=self.token_type,
            refresh_token=self.refresh_token,
            expires_at=received_at + timedelta(seconds=self.expires_in),
            client_id=self.client_id,
            user_id=self.user_id,
        )


class WhoAmI(MonzoModel):
    """Identity associated with the current access token."""

    authenticated: bool
    client_id: str | None = None
    user_id: str | None = None


class MonzoErrorPayload(MonzoModel):
    """Safe structured fields returned for a Monzo API error."""

    code: str | None = None
    message: str | None = None
    error: str | None = None


class AccountOwner(MonzoModel):
    """An owner attached to a Monzo account."""

    user_id: NonEmptyString
    preferred_name: str | None = None
    preferred_first_name: str | None = None


class Account(MonzoModel):
    """A Monzo account owned by the authorized user."""

    id: NonEmptyString
    description: str
    created: AwareDatetime
    type: str | None = None
    closed: bool | None = None
    account_number: str | None = None
    sort_code: str | None = None
    owners: list[AccountOwner] = Field(default_factory=list)


class Balance(MonzoModel):
    """Balance amounts for a Monzo account, expressed in minor currency units."""

    balance: int
    total_balance: int
    currency: CurrencyCode
    spend_today: int
    balance_including_flexible_savings: int | None = None
    local_currency: str | None = None
    local_exchange_rate: int | float | None = None


class Pot(MonzoModel):
    """A Monzo pot associated with an account."""

    id: NonEmptyString
    name: str
    balance: int
    currency: CurrencyCode
    created: AwareDatetime
    updated: AwareDatetime
    deleted: bool
    style: str | None = None


class MerchantAddress(MonzoModel):
    """Address information for an expanded transaction merchant."""

    address: str | None = None
    city: str | None = None
    country: str | None = None
    latitude: int | float | None = None
    longitude: int | float | None = None
    postcode: str | None = None
    region: str | None = None


class Merchant(MonzoModel):
    """Expanded merchant information on a transaction."""

    id: NonEmptyString
    name: str
    address: MerchantAddress | None = None
    created: AwareDatetime | None = None
    group_id: str | None = None
    logo: str | None = None
    emoji: str | None = None
    category: str | None = None
    online: bool | None = None
    phone: str | None = None


class TransactionCounterparty(MonzoModel):
    """Counterparty information where Monzo includes it on a transaction."""

    name: str | None = None
    account_number: str | None = None
    sort_code: str | None = None
    user_id: str | None = None


class Transaction(MonzoModel):
    """A movement of funds into or out of a Monzo account."""

    id: NonEmptyString
    created: AwareDatetime
    amount: int
    currency: CurrencyCode
    description: str
    merchant: str | Merchant | None = None
    metadata: dict[str, str] = Field(default_factory=dict)
    notes: str = ""
    is_load: bool
    settled: AwareDatetime | None = None
    category: str | None = None
    decline_reason: str | None = None
    account_id: str | None = None
    counterparty: TransactionCounterparty | None = None

    @field_validator("settled", mode="before")
    @classmethod
    def empty_settled_is_none(cls, value: object) -> object:
        """Normalize Monzo's empty timestamp for an unsettled transaction."""
        if value == "":
            return None
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return value
        return value

    @field_validator("merchant", "counterparty", mode="before")
    @classmethod
    def empty_objects_are_none(cls, value: object) -> object:
        """Normalize empty optional embedded objects."""
        return None if value == {} or value == "" else value


class Webhook(MonzoModel):
    """A webhook registered for a Monzo account."""

    id: NonEmptyString
    account_id: NonEmptyString
    url: AnyHttpUrl


class RetryPolicy(MonzoModel):
    """Bounded retry configuration for requests that are safe to replay."""

    max_attempts: Annotated[int, Field(strict=True, ge=1, le=5)] = 3
    base_delay_seconds: Annotated[float, Field(ge=0)] = 0.25
    max_delay_seconds: Annotated[float, Field(ge=0)] = 5.0
    max_elapsed_seconds: Annotated[float, Field(ge=0)] = 10.0
    jitter_ratio: Annotated[float, Field(ge=0, le=1)] = 0.2

    @model_validator(mode="after")
    def delay_range_is_ordered(self) -> RetryPolicy:
        """Ensure exponential delays can be capped without growing backwards."""
        if self.max_delay_seconds < self.base_delay_seconds:
            msg = "max_delay_seconds must be at least base_delay_seconds"
            raise ValueError(msg)
        return self


class _AccountsResponse(MonzoModel):
    accounts: list[Account]


class _PotsResponse(MonzoModel):
    pots: list[Pot]


class _TransactionResponse(MonzoModel):
    transaction: Transaction


class _TransactionsResponse(MonzoModel):
    transactions: list[Transaction]


class _WebhookResponse(MonzoModel):
    webhook: Webhook


class _WebhooksResponse(MonzoModel):
    webhooks: list[Webhook]


class _EmptyResponse(MonzoModel):
    """Validate an intentionally empty successful response."""
