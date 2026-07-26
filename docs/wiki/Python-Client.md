# aiomonzo Python client

The MCP server uses
[`aiomonzo`](https://github.com/s-block/aiomonzo), the separately published,
fully asynchronous and typed client for the Monzo Developer API.

Applications that need Monzo API access without MCP should install `aiomonzo`
directly from PyPI:

```bash
uv add aiomonzo
```

or:

```bash
python -m pip install aiomonzo
```

For short-lived development with a Monzo API Playground token:

```python
from aiomonzo import MonzoClient


async def read_balance(playground_token: str) -> int:
    async with MonzoClient(access_token=playground_token) as monzo:
        accounts = await monzo.list_accounts()
        balance = await monzo.get_balance(accounts[0].id)
        return balance.balance
```

Do not embed tokens in source code, command history, test fixtures, or logs.
See the [`aiomonzo` documentation](https://github.com/s-block/aiomonzo/wiki)
for OAuth, token-provider, API, retry, model, and exception guidance.

The MCP server deliberately returns a smaller, data-minimized view of provider
data and does not expose OAuth, logout, annotation, or webhook operations as
model-callable tools.
