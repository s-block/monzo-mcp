"""Process-scoped access-token provider factory for standalone local mode."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aiomonzo import AccessTokenProvider, OAuthAccessTokenProvider
    from starlette.requests import Request


@dataclass(frozen=True, slots=True)
class LocalAccessTokenProviderFactory:
    """Return one shared provider so all requests share its refresh lock."""

    provider: OAuthAccessTokenProvider

    def create(self, request: Request | None) -> AccessTokenProvider:
        """Return the process provider without reading request authorization."""
        del request
        return self.provider
