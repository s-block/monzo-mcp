"""Typed, data-minimized Monzo tools exposed over MCP."""

from __future__ import annotations

from collections.abc import Awaitable, Callable  # noqa: TC003
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from mcp.server.elicitation import AcceptedElicitation
from mcp.server.fastmcp import FastMCP  # noqa: TC002
from mcp.server.fastmcp.exceptions import ToolError
from mcp.shared.exceptions import McpError
from mcp.types import ToolAnnotations
from pydantic import Field, ValidationError
from starlette.requests import Request

from monzo_mcp.client import (
    MonzoAuthenticationError,
    MonzoClient,
    MonzoClientError,
    MonzoHTTPError,
    MonzoPermissionError,
    MonzoRateLimitError,
    MonzoReauthenticationRequired,
    MonzoRequestValidationError,
    MonzoResponseDecodeError,
    MonzoResponseValidationError,
    MonzoTimeoutError,
    MonzoTransportError,
)
from monzo_mcp.mcp.broker import (
    CredentialBrokerAuthorizationError,
    CredentialBrokerError,
    CredentialBrokerReauthenticationRequiredError,
    CredentialBrokerUnavailableError,
)
from monzo_mcp.mcp.context import (
    AppContext,
    MonzoMCPContext,
    app_context,
)
from monzo_mcp.mcp.models import (
    AccountsResult,
    AccountSummary,
    BalanceResult,
    ConnectionStatus,
    PotsResult,
    PotSummary,
    TransactionListItem,
    TransactionResult,
    TransactionsResult,
    TransactionSummary,
    TransferConfirmation,
)
from monzo_mcp.mcp.settings import AccessTokenProviderMode

_READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=True,
)
_POT_MOVEMENT = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=True,
    idempotentHint=True,
    openWorldHint=True,
)
_ANNOTATION_WRITE = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=True,
    idempotentHint=True,
    openWorldHint=True,
)
_DEFAULT_TRANSACTION_LOOKBACK = timedelta(days=30)
_DEFAULT_TRANSACTION_LIMIT = 30
_MONZO_RECENT_TRANSACTION_LOOKBACK_DAYS = 89
_MONZO_VERIFICATION_REQUIRED_CODE = "forbidden.verification_required"

type Identifier = Annotated[
    str,
    Field(
        min_length=1,
        description="Explicit Monzo identifier; the server never selects one.",
    ),
]
type PaginationLimit = Annotated[
    int,
    Field(ge=1, le=100, description="Bounded page size between 1 and 100."),
]
type PositiveMinorAmount = Annotated[
    int,
    Field(
        gt=0,
        description="Positive integer minor currency units; 100 means £1.00 for GBP.",
    ),
]
type DedupeIdentifier = Annotated[
    str,
    Field(
        min_length=1,
        description=(
            "Stable unique ID for this intended transfer. Reuse it only when "
            "retrying the same transfer."
        ),
    ),
]


def register_tools(
    server: FastMCP[AppContext],
    *,
    enable_writes: bool,
) -> None:
    """Register the deliberate v1 tool surface on one server."""

    @server.tool(
        name="monzo_connection_status",
        title="Check Monzo connection",
        description=(
            "Verify that the configured credential can authenticate with Monzo. "
            "No token or provider identity is returned."
        ),
        annotations=_READ_ONLY,
    )
    async def connection_status(ctx: MonzoMCPContext) -> ConnectionStatus:
        identity = await _execute(ctx, lambda monzo: monzo.who_am_i())
        return ConnectionStatus(authenticated=identity.authenticated)

    @server.tool(
        name="monzo_list_accounts",
        title="List Monzo accounts",
        description=(
            "List data-minimized account summaries for explicit selection. "
            "Account numbers, sort codes, and owner records are excluded."
        ),
        annotations=_READ_ONLY,
    )
    async def list_accounts(
        ctx: MonzoMCPContext,
        account_type: Annotated[
            str | None,
            Field(
                description=(
                    "Optional Monzo account type such as uk_retail or uk_retail_joint."
                )
            ),
        ] = None,
    ) -> AccountsResult:
        accounts = await _execute(
            ctx,
            lambda monzo: monzo.list_accounts(account_type=account_type),
        )
        return AccountsResult(
            accounts=[AccountSummary.from_client(account) for account in accounts]
        )

    @server.tool(
        name="monzo_get_balance",
        title="Get Monzo balance",
        description=(
            "Get balance amounts in integer minor units for one explicit account."
        ),
        annotations=_READ_ONLY,
    )
    async def get_balance(
        account_id: Identifier,
        ctx: MonzoMCPContext,
    ) -> BalanceResult:
        balance = await _execute(
            ctx,
            lambda monzo: monzo.get_balance(account_id),
        )
        return BalanceResult.from_client(account_id=account_id, balance=balance)

    @server.tool(
        name="monzo_list_pots",
        title="List Monzo pots",
        description="List pots and their minor-unit balances for one explicit account.",
        annotations=_READ_ONLY,
    )
    async def list_pots(
        account_id: Identifier,
        ctx: MonzoMCPContext,
    ) -> PotsResult:
        pots = await _execute(
            ctx,
            lambda monzo: monzo.list_pots(account_id),
        )
        return PotsResult(
            account_id=account_id,
            pots=[PotSummary.from_client(pot) for pot in pots],
        )

    @server.tool(
        name="monzo_get_transaction",
        title="Get Monzo transaction",
        description=(
            "Get one data-minimized transaction. Counterparty bank details, precise "
            "merchant location, metadata, and provider extras are excluded."
        ),
        annotations=_READ_ONLY,
    )
    async def get_transaction(
        transaction_id: Identifier,
        ctx: MonzoMCPContext,
        expand_merchant: Annotated[
            bool,
            Field(description="Include safe expanded merchant details when available."),
        ] = False,
    ) -> TransactionResult:
        expand: tuple[Literal["merchant"], ...] = (
            ("merchant",) if expand_merchant else ()
        )
        transaction = await _execute(
            ctx,
            lambda monzo: monzo.get_transaction(
                transaction_id,
                expand=expand,
            ),
        )
        return TransactionResult(
            transaction=TransactionSummary.from_client(transaction)
        )

    @server.tool(
        name="monzo_list_transactions",
        title="List Monzo transactions",
        description=(
            "Return one compact, bounded transaction page for an explicit account. "
            "When since is omitted, the page defaults to the 30 days ending at before "
            "or the current UTC time. The default limit is 30; pass an explicit "
            "timestamp or transaction ID as since and a limit up to 100 to override "
            "the defaults or paginate. More than five minutes after Monzo "
            "authentication, transaction history is restricted to the last 90 days; "
            "use at most an 89-day lookback to avoid the moving boundary. Older "
            "history requires fresh Monzo in-app verification."
        ),
        annotations=_READ_ONLY,
    )
    async def list_transactions(
        account_id: Identifier,
        ctx: MonzoMCPContext,
        since: Annotated[
            str | None,
            Field(
                description=(
                    "Optional RFC3339 timestamp or transaction ID cursor. Omit to "
                    "default to a 30-day lookback. Use at most an 89-day lookback "
                    "unless the user has just completed Monzo in-app verification."
                )
            ),
        ] = None,
        before: Annotated[
            datetime | None,
            Field(description="Optional timezone-aware RFC3339 upper bound."),
        ] = None,
        limit: PaginationLimit = _DEFAULT_TRANSACTION_LIMIT,
        expand_merchant: Annotated[
            bool,
            Field(description="Include safe expanded merchant details when available."),
        ] = False,
    ) -> TransactionsResult:
        effective_since: datetime | str = (
            since
            if since is not None
            else (before or datetime.now(UTC)) - _DEFAULT_TRANSACTION_LOOKBACK
        )
        expand: tuple[Literal["merchant"], ...] = (
            ("merchant",) if expand_merchant else ()
        )
        transactions = await _execute(
            ctx,
            lambda monzo: monzo.list_transactions(
                account_id,
                since=effective_since,
                before=before,
                limit=limit,
                expand=expand,
            ),
        )
        return TransactionsResult(
            account_id=account_id,
            since=(
                effective_since.isoformat()
                if isinstance(effective_since, datetime)
                else effective_since
            ),
            before=before,
            limit=limit,
            returned_count=len(transactions),
            next_since=(
                transactions[-1].id
                if transactions and len(transactions) == limit
                else None
            ),
            transactions=[
                TransactionListItem.from_client(transaction)
                for transaction in transactions
            ],
        )

    if not enable_writes:
        return

    @server.tool(
        name="monzo_deposit_into_pot",
        title="Deposit into Monzo pot",
        description=(
            "Move positive integer minor units from an explicit account into an "
            "explicit pot after immediate human confirmation."
        ),
        annotations=_POT_MOVEMENT,
    )
    async def deposit_into_pot(
        pot_id: Identifier,
        source_account_id: Identifier,
        amount_minor: PositiveMinorAmount,
        dedupe_id: DedupeIdentifier,
        ctx: MonzoMCPContext,
    ) -> PotSummary:
        await _confirm_transfer(
            ctx,
            action="deposit into",
            pot_id=pot_id,
            account_id=source_account_id,
            amount_minor=amount_minor,
        )
        pot = await _execute(
            ctx,
            lambda monzo: monzo.deposit_into_pot(
                pot_id,
                source_account_id=source_account_id,
                amount=amount_minor,
                dedupe_id=dedupe_id,
            ),
        )
        return PotSummary.from_client(pot)

    @server.tool(
        name="monzo_withdraw_from_pot",
        title="Withdraw from Monzo pot",
        description=(
            "Move positive integer minor units from an explicit pot into an "
            "explicit account after immediate human confirmation."
        ),
        annotations=_POT_MOVEMENT,
    )
    async def withdraw_from_pot(
        pot_id: Identifier,
        destination_account_id: Identifier,
        amount_minor: PositiveMinorAmount,
        dedupe_id: DedupeIdentifier,
        ctx: MonzoMCPContext,
    ) -> PotSummary:
        await _confirm_transfer(
            ctx,
            action="withdraw from",
            pot_id=pot_id,
            account_id=destination_account_id,
            amount_minor=amount_minor,
        )
        pot = await _execute(
            ctx,
            lambda monzo: monzo.withdraw_from_pot(
                pot_id,
                destination_account_id=destination_account_id,
                amount=amount_minor,
                dedupe_id=dedupe_id,
            ),
        )
        return PotSummary.from_client(pot)

    @server.tool(
        name="monzo_annotate_transaction",
        title="Annotate Monzo transaction",
        description=(
            "Set or remove private application metadata on one explicit transaction. "
            "Use null to remove a key."
        ),
        annotations=_ANNOTATION_WRITE,
    )
    async def annotate_transaction(
        transaction_id: Identifier,
        metadata: Annotated[
            dict[str, str | None],
            Field(
                min_length=1,
                description="Metadata values to set; null removes an existing key.",
            ),
        ],
        ctx: MonzoMCPContext,
    ) -> TransactionResult:
        transaction = await _execute(
            ctx,
            lambda monzo: monzo.annotate_transaction(
                transaction_id,
                metadata,
            ),
        )
        return TransactionResult(
            transaction=TransactionSummary.from_client(transaction)
        )


async def _confirm_transfer(
    ctx: MonzoMCPContext,
    *,
    action: str,
    pot_id: str,
    account_id: str,
    amount_minor: int,
) -> None:
    message = (
        f"Approve moving {amount_minor} minor currency units to {action} pot "
        f"{pot_id} using account {account_id}?"
    )
    try:
        result = await ctx.elicit(message, TransferConfirmation)
    except (McpError, ValidationError):
        raise ToolError(
            "This transfer requires a client that supports human form confirmation"
        ) from None
    if not isinstance(result, AcceptedElicitation) or not result.data.confirm:
        raise ToolError("The Monzo pot transfer was not approved")


async def _execute[ResultT](
    ctx: MonzoMCPContext,
    operation: Callable[[MonzoClient], Awaitable[ResultT]],
) -> ResultT:
    context = app_context(ctx)
    try:
        request = ctx.request_context.request
        provider = context.access_token_provider_factory.create(
            request if isinstance(request, Request) else None
        )
        async with MonzoClient(
            access_token_provider=provider,
            http_client=context.http_client,
            api_base_url=context.monzo_api_base_url,
        ) as monzo:
            return await operation(monzo)
    except CredentialBrokerReauthenticationRequiredError:
        raise ToolError(
            "Monzo authorization is required; reconnect Monzo in your MCP client"
        ) from None
    except CredentialBrokerAuthorizationError:
        raise ToolError("The MCP credential delegation was rejected") from None
    except CredentialBrokerUnavailableError:
        raise ToolError("The credential broker is temporarily unavailable") from None
    except CredentialBrokerError:
        raise ToolError("A usable Monzo credential is unavailable") from None
    except MonzoReauthenticationRequired:
        raise ToolError(_reauthentication_message(context, rejected=False)) from None
    except MonzoAuthenticationError:
        raise ToolError(_reauthentication_message(context, rejected=True)) from None
    except MonzoPermissionError as error:
        if error.code == _MONZO_VERIFICATION_REQUIRED_CODE:
            raise ToolError(
                "Monzo requires recent in-app verification for this request. "
                "Retry transaction history with a since value within the last "
                f"{_MONZO_RECENT_TRANSACTION_LOOKBACK_DAYS} days, or complete "
                "fresh Monzo verification before requesting older history."
            ) from None
        raise ToolError("Monzo denied permission for this operation") from None
    except MonzoRateLimitError as error:
        retry = (
            f" Retry after {error.retry_after:g} seconds."
            if error.retry_after is not None
            else ""
        )
        raise ToolError(f"Monzo rate limited the request.{retry}") from None
    except MonzoRequestValidationError as error:
        raise ToolError(str(error)) from None
    except MonzoTimeoutError:
        raise ToolError("The Monzo request timed out") from None
    except MonzoTransportError:
        raise ToolError("The Monzo service could not be reached") from None
    except (MonzoResponseDecodeError, MonzoResponseValidationError):
        raise ToolError("Monzo returned an unexpected response") from None
    except MonzoHTTPError as error:
        raise ToolError(
            f"Monzo rejected the request with HTTP {error.status_code}"
        ) from None
    except MonzoClientError:
        raise ToolError("The Monzo request could not be completed") from None


def _reauthentication_message(
    context: AppContext,
    *,
    rejected: bool,
) -> str:
    if context.access_token_provider_mode is AccessTokenProviderMode.BROKER:
        if rejected:
            return "Monzo rejected the authorization; reconnect it in your MCP client"
        return "Monzo authorization is required; reconnect Monzo in your MCP client"
    reason = (
        "Monzo rejected the authorization"
        if rejected
        else "Monzo authorization is required"
    )
    return (
        f"{reason}; stop the service, run monzo-mcp auth login, approve the "
        "connection in Monzo, and restart the service"
    )
