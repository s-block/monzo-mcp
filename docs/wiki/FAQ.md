# FAQ

## Why does a Docker MCP server use Streamable HTTP instead of stdio?

With stdio, the MCP client launches and owns the server subprocess. A
long-running Docker service is already an independent process with its own
network and lifecycle, so native Streamable HTTP is the direct MCP transport.
It also supports multiple client connections and request headers without a
stdio-to-HTTP bridge.

The project intentionally exposes no stdio transport.

## Is plain HTTP secure?

Only within loopback or a private, controlled network. The application still
authenticates MCP requests and validates Host and Origin, but it does not
encrypt transport. Use trusted TLS termination whenever traffic crosses a host
or trust boundary.

## Why are there two bearer credentials in broker mode?

They authenticate different relationships:

- `Authorization: Bearer <mcp-endpoint-token>` authenticates the caller to
  `monzo-mcp`.
- `X-MCP-Credential-Delegation: Bearer <delegation>` authorizes
  `monzo-mcp` to ask the private broker for the current user's usable Monzo
  access token.

The Monzo access token is then used only on the outbound Monzo API call. Reusing
one bearer for all three audiences would collapse trust boundaries and make
rotation, authorization, and leakage harder to contain.

## Does the model ever see a token?

It should not. Tokens and delegations are HTTP headers or internal provider
values, never tool arguments or tool results. The trusted MCP client or host is
responsible for injecting headers outside model control.

Financial tool results do enter model context.

## Why keep an MCP endpoint bearer if every tool also needs Monzo access?

A Monzo token proves access to Monzo, not authorization to connect to this MCP
service. Endpoint authentication blocks unauthenticated discovery and requests,
lets operators rotate MCP access independently, and prevents the server from
accepting an upstream credential in the wrong trust boundary.

## Is the endpoint bearer MCP OAuth?

No. It is static bearer authentication for a private service. The server does
not publish protected-resource metadata or authorization-server discovery.
Clients must support configuring a custom `Authorization` header.

## Can one container serve multiple Monzo users?

Yes, in explicit broker mode. Every request must carry a short-lived delegation
bound to the correct user and integration. The broker—not `monzo-mcp`—owns user
identity, OAuth storage, and refresh locking.

Local mode is one Monzo authorization per container.

## Does broker mode store access tokens?

No durable store or cross-request cache is opened. The server requests a usable
access token when a Monzo operation runs and retains it only for the
request-time client. The external broker necessarily stores or otherwise owns
the user's OAuth state.

## Why not pass the Monzo token directly from the MCP client?

The broker pattern keeps token refresh, rotated refresh-token persistence,
locking, and user authorization inside trusted application code. It also avoids
returning refreshed credentials through MCP protocol messages and avoids
placing provider tokens in model-visible inputs.

An application that already owns a usable token can implement an
`AccessTokenProvider` directly when using `aiomonzo`, but the packaged
multi-user server uses the explicit broker contract.

## Why is local mode the default?

It is the complete self-hosted path: one operator, one OAuth client, one
encrypted credential bundle, no additional identity service. Broker mode is
valuable only when a trusted host already has a real multi-user credential
architecture.

## Can local mode have multiple replicas?

Not against one credential bundle. The server holds an exclusive filesystem
lock, and Monzo refresh tokens are single-use. Run independent local
deployments with independent credentials, or use broker mode for coordinated
multi-user scaling.

## Why are writes disabled by default?

Banking writes have a higher consequence than reads. Explicit startup opt-in
keeps them absent from discovery unless the operator has reviewed client
grants, retention, and confirmation behavior.

Pot transfers also require immediate structured human confirmation and a
stable deduplication ID.

## Why does transaction history default to 30 days and 30 items?

It gives spending-analysis tasks useful recent context while bounding latency,
data exposure, and model context. Callers can override the time range and page
size up to 100 and can continue with `next_since`.

## Why can old transaction history fail?

Monzo restricts ordinary transaction-history access to the last 90 days more
than five minutes after authentication. Use a recent window or complete fresh
in-app verification before requesting older history.

## Why are some Monzo fields missing?

MCP results are deliberately data-minimized. Bank details, owner records,
counterparty bank details, precise merchant locations, raw metadata, and
unknown provider fields are excluded. The lower-level `aiomonzo` client exposes
typed provider models for trusted application code.

## Does the server support webhooks?

The `aiomonzo` client supports webhook API operations. The MCP server does not
expose webhook tools or run a webhook listener.

## Is this an official Monzo product?

No. It is an independent open-source project and is not affiliated with or
endorsed by Monzo.
