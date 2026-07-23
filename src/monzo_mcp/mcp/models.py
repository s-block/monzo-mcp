"""Data-minimized Pydantic contracts exposed through MCP tools."""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from monzo_mcp.client import Merchant

if TYPE_CHECKING:
    from monzo_mcp.client import Account, Balance, Pot, Transaction


class MCPModel(BaseModel):
    """Strict immutable base for model-visible MCP input and output."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ConnectionStatus(MCPModel):
    """Non-secret status for the currently configured Monzo connection."""

    authenticated: bool


class AccountSummary(MCPModel):
    """Account fields required for explicit selection, excluding bank details."""

    id: str
    description: str
    created: datetime
    type: str | None = None
    closed: bool | None = None

    @classmethod
    def from_client(cls, account: Account) -> AccountSummary:
        return cls(
            id=account.id,
            description=account.description,
            created=account.created,
            type=account.type,
            closed=account.closed,
        )


class AccountsResult(MCPModel):
    """Bounded list of safe account summaries."""

    accounts: list[AccountSummary]


class BalanceResult(MCPModel):
    """Balance amounts in integer minor currency units for one account."""

    account_id: str
    balance: int
    total_balance: int
    currency: str
    spend_today: int
    balance_including_flexible_savings: int | None = None
    local_currency: str | None = None
    local_exchange_rate: int | float | None = None

    @classmethod
    def from_client(cls, *, account_id: str, balance: Balance) -> BalanceResult:
        return cls(
            account_id=account_id,
            balance=balance.balance,
            total_balance=balance.total_balance,
            currency=balance.currency,
            spend_today=balance.spend_today,
            balance_including_flexible_savings=(
                balance.balance_including_flexible_savings
            ),
            local_currency=balance.local_currency,
            local_exchange_rate=balance.local_exchange_rate,
        )


class PotSummary(MCPModel):
    """A pot without provider-specific fields."""

    id: str
    name: str
    balance: int
    currency: str
    created: datetime
    updated: datetime
    deleted: bool

    @classmethod
    def from_client(cls, pot: Pot) -> PotSummary:
        return cls(
            id=pot.id,
            name=pot.name,
            balance=pot.balance,
            currency=pot.currency,
            created=pot.created,
            updated=pot.updated,
            deleted=pot.deleted,
        )


class PotsResult(MCPModel):
    """Bounded list of pots for one account."""

    account_id: str
    pots: list[PotSummary]


class MerchantSummary(MCPModel):
    """Expanded merchant data with location and contact details removed."""

    id: str
    name: str
    category: str | None = None
    online: bool | None = None
    emoji: str | None = None

    @classmethod
    def from_client(cls, merchant: Merchant) -> MerchantSummary:
        return cls(
            id=merchant.id,
            name=merchant.name,
            category=merchant.category,
            online=merchant.online,
            emoji=merchant.emoji,
        )


class TransactionSummary(MCPModel):
    """Useful transaction fields without counterparty bank or provider extras."""

    id: str
    created: datetime
    amount: int
    currency: str
    description: str
    merchant_id: str | None = None
    merchant: MerchantSummary | None = None
    notes: str
    is_load: bool
    settled: datetime | None = None
    category: str | None = None
    decline_reason: str | None = None
    account_id: str | None = None

    @classmethod
    def from_client(cls, transaction: Transaction) -> TransactionSummary:
        merchant_id: str | None = None
        merchant: MerchantSummary | None = None
        if isinstance(transaction.merchant, str):
            merchant_id = transaction.merchant
        elif isinstance(transaction.merchant, Merchant):
            merchant_id = transaction.merchant.id
            merchant = MerchantSummary.from_client(transaction.merchant)
        return cls(
            id=transaction.id,
            created=transaction.created,
            amount=transaction.amount,
            currency=transaction.currency,
            description=transaction.description,
            merchant_id=merchant_id,
            merchant=merchant,
            notes=transaction.notes,
            is_load=transaction.is_load,
            settled=transaction.settled,
            category=transaction.category,
            decline_reason=transaction.decline_reason,
            account_id=transaction.account_id,
        )


class TransactionListItem(MCPModel):
    """Compact transaction fields suitable for bounded list responses."""

    id: str
    created: datetime
    amount: int
    currency: str
    description: str
    category: str | None = None
    merchant_name: str | None = None

    @classmethod
    def from_client(cls, transaction: Transaction) -> TransactionListItem:
        merchant_name: str | None = None
        merchant_category: str | None = None
        if isinstance(transaction.merchant, Merchant):
            merchant_name = transaction.merchant.name
            merchant_category = transaction.merchant.category
        return cls(
            id=transaction.id,
            created=transaction.created,
            amount=transaction.amount,
            currency=transaction.currency,
            description=transaction.description,
            category=transaction.category or merchant_category,
            merchant_name=merchant_name,
        )


class TransactionResult(MCPModel):
    """One data-minimized transaction."""

    transaction: TransactionSummary


class TransactionsResult(MCPModel):
    """One bounded transaction page with explicit query and cursor metadata."""

    account_id: str
    since: str
    before: datetime | None = None
    limit: int
    returned_count: int
    next_since: str | None = None
    transactions: list[TransactionListItem]


class TransferConfirmation(MCPModel):
    """Human confirmation required immediately before a pot movement."""

    confirm: bool = Field(
        description="Set to true only if the human approves this exact transfer."
    )
