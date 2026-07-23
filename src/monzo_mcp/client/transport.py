"""Reusable asynchronous HTTP transport for the Monzo API."""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Self, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from monzo_mcp.client.exceptions import (
    MonzoAuthenticationError,
    MonzoClosedError,
    MonzoHTTPError,
    MonzoResponseDecodeError,
    MonzoResponseValidationError,
    MonzoTimeoutError,
    MonzoTransportError,
    _exception_from_response,
    _retry_after_seconds,
)

if TYPE_CHECKING:
    from monzo_mcp.client.auth import AccessTokenProvider
    from monzo_mcp.client.models import RetryPolicy

_ModelT = TypeVar("_ModelT", bound=BaseModel)
_FormData = Mapping[str, str]
_QueryParams = Mapping[str, str] | Sequence[tuple[str, str]]
_RETRYABLE_STATUS_CODES = frozenset({408, 429, 500, 502, 503, 504})


class _AsyncTransport:
    """Send authenticated requests over one pooled HTTPX client."""

    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient,
        auth: AccessTokenProvider,
        api_base_url: str,
        owns_http_client: bool,
        retry_policy: RetryPolicy,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        random_source: Callable[[], float] = random.random,
    ) -> None:
        self._http_client = http_client
        self._auth = auth
        self._api_base_url = api_base_url.rstrip("/")
        self._owns_http_client = owns_http_client
        self._retry_policy = retry_policy
        self._sleep: Callable[[float], Awaitable[None]] = sleep
        self._random_source: Callable[[], float] = random_source
        self._closed = False

    async def __aenter__(self) -> Self:
        self._guard_open()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Close an owned HTTP client and reject further requests."""
        if self._closed:
            return
        self._closed = True
        if self._owns_http_client:
            await self._http_client.aclose()

    async def request(
        self,
        method: str,
        path: str,
        *,
        response_model: type[_ModelT],
        params: _QueryParams | None = None,
        data: _FormData | None = None,
        retry_safe: bool = False,
    ) -> _ModelT:
        """Send, authenticate, retry where safe, and validate one response."""
        self._guard_open()
        attempt = 1
        elapsed_delay = 0.0
        authentication_replayed = False

        while True:
            try:
                access_token = await self._auth.get_access_token()
                response = await self._send(
                    method,
                    path,
                    access_token=access_token,
                    params=params,
                    data=data,
                )
                if (
                    response.status_code == httpx.codes.UNAUTHORIZED
                    and not authentication_replayed
                    and self._is_invalid_token(response)
                ):
                    replacement = await self._auth.refresh_after_rejection(access_token)
                    authentication_replayed = True
                    response = await self._send(
                        method,
                        path,
                        access_token=replacement,
                        params=params,
                        data=data,
                    )
            except httpx.TimeoutException:
                error: MonzoHTTPError | MonzoTransportError = MonzoTimeoutError(
                    "Monzo API request timed out"
                )
                delay = self._exponential_delay(attempt)
                if not self._can_retry(
                    delay=delay,
                    attempt=attempt,
                    elapsed_delay=elapsed_delay,
                    retry_safe=retry_safe,
                ):
                    raise error from None
                elapsed_delay += delay
                attempt += 1
                await self._sleep(delay)
                continue
            except httpx.RequestError:
                error = MonzoTransportError("Monzo API request failed")
                delay = self._exponential_delay(attempt)
                if not self._can_retry(
                    delay=delay,
                    attempt=attempt,
                    elapsed_delay=elapsed_delay,
                    retry_safe=retry_safe,
                ):
                    raise error from None
                elapsed_delay += delay
                attempt += 1
                await self._sleep(delay)
                continue

            if response.is_error:
                http_error = _exception_from_response(response)
                delay = self._response_retry_delay(response, attempt=attempt)
                if not self._can_retry(
                    retry_safe=retry_safe,
                    attempt=attempt,
                    elapsed_delay=elapsed_delay,
                    delay=delay,
                ):
                    raise http_error
                elapsed_delay += delay
                attempt += 1
                await self._sleep(delay)
                continue

            return self._validate_response(response, response_model=response_model)

    async def _send(
        self,
        method: str,
        path: str,
        *,
        access_token: str,
        params: _QueryParams | None,
        data: _FormData | None,
    ) -> httpx.Response:
        return await self._http_client.request(
            method,
            f"{self._api_base_url}/{path.lstrip('/')}",
            params=self._encode_params(params),
            data=data,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {access_token}",
                "User-Agent": "monzo-mcp/0.1.0",
            },
        )

    def _response_retry_delay(
        self,
        response: httpx.Response,
        *,
        attempt: int,
    ) -> float:
        if response.status_code not in _RETRYABLE_STATUS_CODES:
            return self._retry_policy.max_elapsed_seconds + 1.0
        retry_after = _retry_after_seconds(response.headers.get("retry-after"))
        return (
            retry_after if retry_after is not None else self._exponential_delay(attempt)
        )

    def _can_retry(
        self,
        *,
        retry_safe: bool,
        attempt: int,
        elapsed_delay: float,
        delay: float,
    ) -> bool:
        policy = self._retry_policy
        return (
            retry_safe
            and attempt < policy.max_attempts
            and delay <= policy.max_delay_seconds
            and elapsed_delay + delay <= policy.max_elapsed_seconds
        )

    def _exponential_delay(self, attempt: int) -> float:
        policy = self._retry_policy
        base_delay = float(policy.base_delay_seconds)
        maximum = float(policy.max_delay_seconds)
        jitter = float(policy.jitter_ratio)
        base = min(base_delay * (2 ** (attempt - 1)), maximum)
        jittered = base + (base * jitter * self._random_source())
        return float(min(jittered, maximum))

    @staticmethod
    def _encode_params(params: _QueryParams | None) -> httpx.QueryParams | None:
        if params is None:
            return None
        items = params.items() if isinstance(params, Mapping) else params
        encoded = httpx.QueryParams()
        for key, value in items:
            encoded = encoded.add(key, value)
        return encoded

    @staticmethod
    def _is_invalid_token(response: httpx.Response) -> bool:
        error = _exception_from_response(response)
        return isinstance(error, MonzoAuthenticationError) and (
            error.oauth_error == "invalid_token"
            or error.code in {"invalid_token", "unauthorized.bad_token"}
        )

    @staticmethod
    def _validate_response(
        response: httpx.Response,
        *,
        response_model: type[_ModelT],
    ) -> _ModelT:
        model_name = response_model.__name__.removeprefix("_").removesuffix("Response")
        if not response.content.strip():
            try:
                return response_model.model_validate({})
            except ValidationError:
                raise MonzoResponseValidationError(model_name) from None
        try:
            return response_model.model_validate_json(response.content)
        except ValidationError as error:
            if any(item["type"] == "json_invalid" for item in error.errors()):
                raise MonzoResponseDecodeError(model_name) from None
            raise MonzoResponseValidationError(model_name) from None

    def _guard_open(self) -> None:
        if self._closed:
            raise MonzoClosedError("The Monzo client is closed")
