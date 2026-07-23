# Tools and data

The MCP surface is deliberately smaller than the lower-level Python client.
OAuth, token management, logout, and webhooks are human or application
operations and are not exposed to a model.

## Read tools

These tools are always registered.

### `monzo_connection_status`

Verifies the configured credential with Monzo.

Input: none.

Result:

```json
{
  "authenticated": true
}
```

No token, user ID, or OAuth client ID is returned.

### `monzo_list_accounts`

Lists accounts for explicit selection.

| Input | Type | Required | Notes |
| --- | --- | --- | --- |
| `account_type` | string or null | No | For example, `uk_retail` or `uk_retail_joint` |

Each account includes `id`, `description`, `created`, and provider-reported
`type` and `closed` values when available. Account numbers, sort codes, and
owner records are excluded.

### `monzo_get_balance`

Gets balances for one explicit account.

| Input | Type | Required |
| --- | --- | --- |
| `account_id` | non-empty string | Yes |

The result contains `balance`, `total_balance`, `spend_today`, currency, and
provider-supported optional local/flexible-savings values.

### `monzo_list_pots`

Lists pots for one explicit account.

| Input | Type | Required |
| --- | --- | --- |
| `account_id` | non-empty string | Yes |

Each pot includes its ID, name, balance, currency, timestamps, and deleted
state. Provider-specific decorative and internal fields are excluded.

### `monzo_get_transaction`

Gets one transaction.

| Input | Type | Required | Default |
| --- | --- | --- | --- |
| `transaction_id` | non-empty string | Yes | — |
| `expand_merchant` | boolean | No | `false` |

The result includes the transaction ID, timestamps, integer amount, currency,
description, safe merchant summary, notes, category, settlement state, decline
reason, and account ID where available.

Counterparty bank details, precise merchant location, raw metadata, and unknown
provider extras are excluded.

### `monzo_list_transactions`

Returns one compact, bounded transaction page for an explicit account.

| Input | Type | Required | Default |
| --- | --- | --- | --- |
| `account_id` | non-empty string | Yes | — |
| `since` | RFC3339 timestamp, transaction ID, or null | No | 30 days before the effective upper bound |
| `before` | timezone-aware RFC3339 timestamp or null | No | Current UTC time |
| `limit` | integer from 1 through 100 | No | `30` |
| `expand_merchant` | boolean | No | `false` |

Each list item contains only:

- `id`
- `created`
- `amount`
- `currency`
- `description`
- `category`
- `merchant_name`

The result also returns the effective `since`, `before`, requested `limit`,
`returned_count`, and `next_since`.

If a full page is returned, pass `next_since` as the next call's `since` cursor.
Do not assume that fewer than `limit` items means a permanent end if the
underlying account is changing concurrently.

Monzo allows full transaction history for five minutes after authentication,
then restricts ordinary access to the most recent 90 days. Use at most an
89-day explicit lookback to avoid the moving boundary, or complete fresh Monzo
in-app verification before requesting older history.

## Money values

All amounts are signed integer minor units:

| Value | GBP interpretation |
| --- | --- |
| `100` | £1.00 |
| `-679` | -£6.79 |
| `0` | £0.00 |

Use the accompanying ISO currency code. Do not assume every account is GBP or
convert integer values with floating-point arithmetic.

## Write tools

Write tools are absent unless:

```text
MONZO_MCP_ENABLE_WRITES=true
```

or `monzo-mcp serve --enable-writes` is supplied.

### `monzo_deposit_into_pot`

| Input | Type | Required |
| --- | --- | --- |
| `pot_id` | non-empty string | Yes |
| `source_account_id` | non-empty string | Yes |
| `amount_minor` | positive integer | Yes |
| `dedupe_id` | non-empty stable string | Yes |

### `monzo_withdraw_from_pot`

| Input | Type | Required |
| --- | --- | --- |
| `pot_id` | non-empty string | Yes |
| `destination_account_id` | non-empty string | Yes |
| `amount_minor` | positive integer | Yes |
| `dedupe_id` | non-empty stable string | Yes |

Both pot operations request immediate structured human confirmation showing the
amount and explicit IDs. If the MCP client cannot perform form elicitation, the
tool fails before calling Monzo. Reuse a `dedupe_id` only when retrying the same
intended transfer.

### `monzo_annotate_transaction`

| Input | Type | Required |
| --- | --- | --- |
| `transaction_id` | non-empty string | Yes |
| `metadata` | non-empty map of string to string or null | Yes |

A string sets a private application annotation. `null` removes the named key.
Although this does not move money, it mutates data and is therefore part of the
opt-in write surface.

## Tool annotations

Read tools declare themselves read-only, non-destructive, idempotent, and
open-world. Pot movements and annotations declare write/destructive semantics.
Pot operations are replay-safe only when the stable deduplication ID continues
to identify exactly the same intended transfer.

Annotations are safety hints to MCP clients. They do not replace endpoint
authentication, per-client grants, human confirmation, or model-independent
authorization policy.

## Data retention

The server does not persist tool results. The MCP client, gateway, model
provider, chat transcript, trace system, or tool-event store may retain them.
Review those systems before granting tools, and grant only the tools needed for
the intended task.
