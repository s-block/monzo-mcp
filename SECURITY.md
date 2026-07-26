# Security Policy

`monzo-mcp` handles authentication material and sensitive financial data. Please
report suspected vulnerabilities privately and use synthetic data in reports.

## Supported versions

Security fixes are currently provided for the `main` branch. After the first
release, fixes will also be provided for the latest published release. Older
releases should be upgraded before a report is considered resolved.

| Version | Supported |
| --- | --- |
| Current `main` branch | Yes |
| Published releases | None yet |
| Older releases | No |

## Reporting a vulnerability

Use
[GitHub private vulnerability reporting](https://github.com/s-block/monzo-mcp/security/advisories/new).
If the private form is unavailable, open a
[private-contact request](https://github.com/s-block/monzo-mcp/issues/new?template=private_contact.yml)
containing no vulnerability details or sensitive data.
Do not open a public issue containing exploitable details, credentials, account
data, transaction data, or screenshots of real financial information.

Include:

- the affected version or commit;
- a minimal reproduction using synthetic credentials and data;
- the expected and observed behavior;
- the security impact and required preconditions; and
- any suggested remediation or disclosure deadline.

We aim to acknowledge a complete report within three business days, provide an
initial assessment within seven business days, and coordinate disclosure after
a fix is available. These are response targets rather than contractual service
levels.

## Research guidelines

Please:

- test only systems and accounts you own or are explicitly authorized to use;
- do not contact, enumerate, or disrupt Monzo users or Monzo production systems;
- do not perform denial-of-service testing against public or third-party
  services;
- stop if testing exposes credentials or another person's financial data;
- retain only the minimum evidence needed for the report; and
- allow a reasonable remediation period before public disclosure.

Good-faith research that follows this policy will not be treated as malicious
activity by this project. This statement cannot authorize testing of Monzo,
GitHub, an MCP host, a model provider, or any other third-party system.
