# Security

This server handles bank account data and credentials. Its security model
depends on preserving separate trust boundaries for the MCP endpoint, Monzo,
and—when enabled—the external credential broker.

## Credential boundaries

```text
MCP client
  | Authorization: Bearer <MCP endpoint token>
  v
monzo-mcp
  | Authorization: Bearer <Monzo access token>
  v
Monzo Developer API
```

In broker mode there is one additional, separate path:

```text
MCP client or trusted host
  | X-MCP-Credential-Delegation: Bearer <short-lived delegation>
  v
monzo-mcp
  | Authorization: Bearer <same delegation>
  v
private credential broker
```

These credentials have different issuers, audiences, lifetimes, and storage
rules. Never reuse one as another.

## MCP endpoint authentication

Every path except `/healthz` requires exactly one:

```http
Authorization: Bearer <mcp-endpoint-token>
```

The bearer:

- is read from an owner-only file;
- must be at least 32 bytes;
- is compared in constant time;
- is removed from the ASGI request before MCP processing; and
- is never used to call Monzo or the broker.

This is a static shared bearer, not a complete implementation of the MCP OAuth
authorization specification. It is suitable for a private deployment whose
clients can securely receive and store a custom header. For an internet-facing,
third-party service, put a standards-compliant identity and authorization layer
at trusted ingress or add one as an explicitly designed server capability.

## Local credential storage

Local mode stores the OAuth client and token set in a versioned AES-256-GCM
envelope. Its protections include:

- a separate 256-bit key file;
- authenticated associated data;
- a fresh nonce for each write;
- owner-only file and directory checks;
- symlink rejection;
- bounded file reads;
- atomic replacement and directory synchronization;
- a non-blocking process-lifetime filesystem lock;
- a process-wide async refresh lock; and
- removal of decrypted in-memory store state at shutdown.

Encryption protects a copied credential file from disclosure without its key.
It does not protect against an attacker who can read both mounted files,
inspect the running process, control the container host, or replace the image.
Protect the host, image supply chain, backups, and runtime with the same care as
the credentials.

Never run multiple local-mode processes against one bundle. Monzo refresh
tokens are single-use, and concurrent refresh can permanently invalidate the
recoverable token chain.

## Broker-mode security

Broker mode is stateless only within `monzo-mcp`. The external broker is a
high-value credential service and must provide:

- authenticated, short-lived delegations;
- user and credential authorization derived only from validated identity;
- a fixed or allowlisted Monzo integration, never an arbitrary caller URL;
- encrypted token and OAuth-client storage;
- per-credential refresh locking;
- atomic replacement of rotated tokens;
- safe `401`, `403`, and reauthorization responses;
- replay controls appropriate to delegation lifetime;
- private service networking or HTTPS; and
- logs, traces, and metrics with credentials removed.

The delegation must be injected as an HTTP header by trusted host code. It must
not be a model-visible tool argument or be derived from model output.

The MCP server returns only a SHA-256 fingerprint when asking the broker to
resolve a rejected token. A fingerprint is still correlation data; the broker
should use it only inside the refresh decision and should not emit it broadly.

## HTTP transport controls

The service uses native Streamable HTTP and implements the main transport
security requirements:

- exact browser `Origin` validation;
- DNS rebinding protection;
- exact `Host` allowlisting;
- authentication on MCP requests;
- loopback-only host publication in examples;
- no access log;
- no server banner;
- no proxy-header trust; and
- no automatic outbound redirects.

The container itself serves plain HTTP. Use it only on loopback or a private
network. Terminate TLS at trusted ingress when traffic crosses a host or trust
boundary.

Allowed hosts are not an authorization mechanism. Origin validation protects
browser scenarios but does not authenticate non-browser clients. Keep endpoint
authentication and network policy in place.

## Tool and data safety

The MCP surface:

- excludes tokens, OAuth secrets, authorization codes, account numbers, sort
  codes, owner records, counterparty bank details, precise merchant locations,
  raw metadata, and unknown provider fields;
- requires explicit account, pot, and transaction IDs;
- bounds transaction pages to 100 records;
- defaults transaction history to 30 days and 30 records;
- leaves write tools unregistered by default;
- requires positive integer minor units for movements;
- requires a stable deduplication ID; and
- fails closed unless the client completes immediate structured confirmation
  for a pot movement.

Data minimization reduces exposure; it does not make financial data harmless.
Descriptions, merchant names, categories, notes, balances, and timestamps can
still be sensitive.

## Model and client boundary

The model never needs a credential. Headers are injected by the MCP client or
trusted host outside tool arguments.

Tool results do enter model context. Before connecting:

- understand the MCP client's chat, run, tool-event, and trace retention;
- understand the model provider's data controls;
- grant only required tools;
- keep write tools disabled unless needed;
- avoid copying real tool results into issues or public logs; and
- revoke access when the integration is no longer in use.

## Deployment checklist

- [ ] Endpoint bearer and encryption key were generated independently.
- [ ] Secret files and directories are owner-only and not symlinks.
- [ ] The root filesystem is read-only.
- [ ] The process is non-root with capabilities dropped.
- [ ] The port is published only on loopback or trusted ingress.
- [ ] Allowed hosts exactly match real client host headers.
- [ ] Browser origins are empty or narrowly allowlisted.
- [ ] TLS protects traffic outside a private host/network.
- [ ] Reverse proxies do not log authorization or delegation headers.
- [ ] Local mode has exactly one process per credential bundle.
- [ ] Broker mode validates user, audience, integration, and expiry.
- [ ] Broker refresh is locked and atomically persisted.
- [ ] MCP clients have explicit, minimal tool grants.
- [ ] Write tools remain disabled unless their risk is accepted.
- [ ] Backups keep the encrypted bundle and key separately protected.
- [ ] Image versions are pinned and updates are vulnerability-scanned.

## Reporting a vulnerability

Do not place exploitable details, credentials, or real financial data in a
public issue. Prefer GitHub's private vulnerability-reporting channel when it
is available for the repository. Include a minimal synthetic reproduction,
affected version, impact, and suggested remediation.
