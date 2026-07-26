"""Validated process and HTTP settings for the Monzo MCP service."""

from __future__ import annotations

import os
from enum import StrEnum
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    ValidationError,
    field_validator,
)

from monzo_mcp.private_files import (
    PrivateTextFileError,
    read_private_text_file,
)

_DEFAULT_CREDENTIAL_DIR = Path("/credentials")
_DEFAULT_KEY_FILE = Path("/run/secrets/monzo-mcp.key")
_DEFAULT_ENDPOINT_TOKEN_FILE = Path("/run/secrets/monzo-mcp-endpoint-token")
# The container bind still requires endpoint authentication and exact allowed hosts.
_DEFAULT_HTTP_HOST = "0.0.0.0"  # noqa: S104  # nosec B104
_DEFAULT_HTTP_PORT = 8000
_DEFAULT_MAX_REQUEST_BODY_BYTES = 1_048_576
_MAX_MAX_REQUEST_BODY_BYTES = 16_777_216
_DEFAULT_MAX_CONCURRENT_REQUESTS = 100
_MAX_MAX_CONCURRENT_REQUESTS = 10_000
_DEFAULT_DELEGATION_HEADER_NAME = "X-MCP-Credential-Delegation"
_MINIMUM_TOKEN_BYTES = 32
_MAXIMUM_TOKEN_BYTES = 8192
_DEFAULT_BROKER_TIMEOUT_SECONDS = 5.0
_BROKER_ENVIRONMENT_NAMES = (
    "MONZO_MCP_TOKEN_BROKER_URL",
    "MONZO_MCP_DELEGATION_HEADER_NAME",
    "MONZO_MCP_TOKEN_BROKER_TIMEOUT_SECONDS",
)


class ServerConfigurationError(Exception):
    """The MCP service configuration is invalid."""


class AccessTokenProviderMode(StrEnum):
    """Select who owns and refreshes the upstream Monzo access token."""

    LOCAL = "local"
    BROKER = "broker"


class ServerSettings(BaseModel):
    """Validated non-secret behavior for one Monzo MCP process."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    enable_writes: bool = False
    access_token_provider: AccessTokenProviderMode = AccessTokenProviderMode.LOCAL

    @classmethod
    def from_environment(
        cls,
        *,
        enable_writes: bool | None = None,
        access_token_provider: AccessTokenProviderMode | None = None,
    ) -> ServerSettings:
        """Load explicit overrides, then documented environment defaults."""
        writes = (
            enable_writes
            if enable_writes is not None
            else _parse_boolean(os.environ.get("MONZO_MCP_ENABLE_WRITES", "false"))
        )
        provider = access_token_provider
        if provider is None:
            try:
                provider = AccessTokenProviderMode(
                    os.environ.get(
                        "MONZO_MCP_ACCESS_TOKEN_PROVIDER",
                        AccessTokenProviderMode.LOCAL.value,
                    )
                )
            except ValueError:
                raise ServerConfigurationError(
                    "MONZO_MCP_ACCESS_TOKEN_PROVIDER must be local or broker"
                ) from None
        try:
            return cls(
                enable_writes=writes,
                access_token_provider=provider,
            )
        except ValidationError:
            raise ServerConfigurationError("Invalid MCP server settings") from None


class CredentialSettings(BaseModel):
    """Paths for the encrypted credentials used by local mode and auth commands."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    credential_dir: Path = _DEFAULT_CREDENTIAL_DIR
    key_file: Path = _DEFAULT_KEY_FILE

    @field_validator("credential_dir", "key_file")
    @classmethod
    def path_must_be_absolute(cls, value: Path) -> Path:
        if not value.is_absolute():
            raise ValueError("credential paths must be absolute")
        return value

    @classmethod
    def from_environment(
        cls,
        *,
        credential_dir: Path | None = None,
        key_file: Path | None = None,
    ) -> CredentialSettings:
        """Load standalone credential paths."""
        try:
            return cls(
                credential_dir=credential_dir
                or Path(
                    os.environ.get(
                        "MONZO_MCP_CREDENTIAL_DIR",
                        str(_DEFAULT_CREDENTIAL_DIR),
                    )
                ),
                key_file=key_file
                or Path(
                    os.environ.get(
                        "MONZO_MCP_KEY_FILE",
                        str(_DEFAULT_KEY_FILE),
                    )
                ),
            )
        except ValidationError:
            raise ServerConfigurationError(
                "Invalid client credential settings"
            ) from None


class HttpServerSettings(BaseModel):
    """Validated settings for the private Streamable HTTP endpoint."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    host: str = _DEFAULT_HTTP_HOST
    port: int = Field(default=_DEFAULT_HTTP_PORT, ge=1, le=65535)
    endpoint_token: SecretStr = Field(repr=False)
    allowed_hosts: tuple[str, ...]
    allowed_origins: tuple[str, ...] = ()
    max_request_body_bytes: int = Field(
        default=_DEFAULT_MAX_REQUEST_BODY_BYTES,
        ge=1_024,
        le=_MAX_MAX_REQUEST_BODY_BYTES,
    )
    max_concurrent_requests: int = Field(
        default=_DEFAULT_MAX_CONCURRENT_REQUESTS,
        ge=1,
        le=_MAX_MAX_CONCURRENT_REQUESTS,
    )

    @field_validator("host")
    @classmethod
    def host_must_be_explicit(cls, value: str) -> str:
        if not value or value != value.strip() or "/" in value or " " in value:
            raise ValueError("HTTP bind host is invalid")
        return value

    @field_validator("endpoint_token")
    @classmethod
    def endpoint_token_must_be_strong(cls, value: SecretStr) -> SecretStr:
        byte_length = len(value.get_secret_value().encode())
        if byte_length < _MINIMUM_TOKEN_BYTES:
            raise ValueError("HTTP endpoint token must be at least 32 bytes")
        if byte_length > _MAXIMUM_TOKEN_BYTES:
            raise ValueError("HTTP endpoint token is too large")
        return value

    @field_validator("allowed_hosts")
    @classmethod
    def allowed_hosts_must_be_exact(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        if not values:
            raise ValueError("At least one HTTP Host value must be allowed")
        for value in values:
            if (
                not value
                or value != value.strip()
                or "/" in value
                or " " in value
                or "*" in value
                or "://" in value
            ):
                raise ValueError("HTTP allowed hosts must be exact host values")
        return values

    @field_validator("allowed_origins")
    @classmethod
    def allowed_origins_must_be_origins(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        for value in values:
            parsed = urlsplit(value)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.netloc
                or parsed.username is not None
                or parsed.password is not None
                or parsed.path not in {"", "/"}
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError("HTTP allowed origins must be exact origins")
        return values

    @classmethod
    def from_environment(
        cls,
        *,
        host: str | None = None,
        port: int | None = None,
        endpoint_token_file: Path | None = None,
        allowed_hosts: tuple[str, ...] | None = None,
        allowed_origins: tuple[str, ...] | None = None,
        endpoint_token: str | None = None,
        max_request_body_bytes: int | None = None,
        max_concurrent_requests: int | None = None,
    ) -> HttpServerSettings:
        """Load endpoint settings and read the required owner-only bearer token."""
        selected_host = host or os.environ.get(
            "MONZO_MCP_HTTP_HOST",
            _DEFAULT_HTTP_HOST,
        )
        selected_port = port
        if selected_port is None:
            try:
                selected_port = int(
                    os.environ.get(
                        "MONZO_MCP_HTTP_PORT",
                        str(_DEFAULT_HTTP_PORT),
                    )
                )
            except ValueError:
                raise ServerConfigurationError(
                    "Invalid MCP HTTP server settings"
                ) from None
        selected_hosts = (
            allowed_hosts
            if allowed_hosts is not None
            else _environment_list("MONZO_MCP_HTTP_ALLOWED_HOSTS")
        )
        if not selected_hosts:
            selected_hosts = _default_allowed_hosts(
                host=selected_host,
                port=selected_port,
            )
        selected_origins = (
            allowed_origins
            if allowed_origins is not None
            else _environment_list("MONZO_MCP_HTTP_ALLOWED_ORIGINS")
        )
        selected_max_request_body_bytes = _environment_integer(
            "MONZO_MCP_HTTP_MAX_REQUEST_BODY_BYTES",
            default=_DEFAULT_MAX_REQUEST_BODY_BYTES,
            override=max_request_body_bytes,
        )
        selected_max_concurrent_requests = _environment_integer(
            "MONZO_MCP_HTTP_MAX_CONCURRENT_REQUESTS",
            default=_DEFAULT_MAX_CONCURRENT_REQUESTS,
            override=max_concurrent_requests,
        )
        try:
            selected_endpoint_token = (
                endpoint_token
                if endpoint_token is not None
                else read_private_text_file(
                    endpoint_token_file
                    or Path(
                        os.environ.get(
                            "MONZO_MCP_ENDPOINT_TOKEN_FILE",
                            str(_DEFAULT_ENDPOINT_TOKEN_FILE),
                        )
                    ),
                    label="MCP endpoint token file",
                    max_bytes=_MAXIMUM_TOKEN_BYTES,
                )
            )
            return cls(
                host=selected_host,
                port=selected_port,
                endpoint_token=SecretStr(selected_endpoint_token),
                allowed_hosts=selected_hosts,
                allowed_origins=selected_origins,
                max_request_body_bytes=selected_max_request_body_bytes,
                max_concurrent_requests=selected_max_concurrent_requests,
            )
        except (PrivateTextFileError, ValidationError):
            raise ServerConfigurationError("Invalid MCP HTTP server settings") from None


class AccessTokenBrokerSettings(BaseModel):
    """Validated connection settings for a request-time access-token broker."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    url: str = Field(min_length=1, max_length=2048)
    delegation_header_name: str = Field(
        default=_DEFAULT_DELEGATION_HEADER_NAME,
        min_length=1,
        max_length=128,
    )
    timeout_seconds: float = Field(
        default=_DEFAULT_BROKER_TIMEOUT_SECONDS,
        gt=0,
        le=30,
    )

    @field_validator("url")
    @classmethod
    def broker_url_must_be_safe(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Access-token broker URL is invalid")
        return value.rstrip("/")

    @field_validator("delegation_header_name")
    @classmethod
    def delegation_header_name_must_be_safe(cls, value: str) -> str:
        if (
            not value
            or value != value.strip()
            or any(character.isspace() for character in value)
            or ":" in value
        ):
            raise ValueError("Delegation header name is invalid")
        return value

    @classmethod
    def from_environment(
        cls,
        *,
        url: str | None = None,
        delegation_header_name: str | None = None,
        timeout_seconds: float | None = None,
    ) -> AccessTokenBrokerSettings:
        """Load the required broker URL and optional transport controls."""
        selected_url = url or os.environ.get("MONZO_MCP_TOKEN_BROKER_URL", "")
        selected_header = delegation_header_name or os.environ.get(
            "MONZO_MCP_DELEGATION_HEADER_NAME",
            _DEFAULT_DELEGATION_HEADER_NAME,
        )
        selected_timeout = timeout_seconds
        if selected_timeout is None:
            try:
                selected_timeout = float(
                    os.environ.get(
                        "MONZO_MCP_TOKEN_BROKER_TIMEOUT_SECONDS",
                        str(_DEFAULT_BROKER_TIMEOUT_SECONDS),
                    )
                )
            except ValueError:
                raise ServerConfigurationError(
                    "Invalid access-token broker settings"
                ) from None
        try:
            return cls(
                url=selected_url,
                delegation_header_name=selected_header,
                timeout_seconds=selected_timeout,
            )
        except ValidationError:
            raise ServerConfigurationError(
                "Invalid access-token broker settings"
            ) from None


type AccessTokenProviderSettings = CredentialSettings | AccessTokenBrokerSettings


def access_token_provider_settings_from_environment(
    server_settings: ServerSettings,
    *,
    credential_dir: Path | None = None,
    key_file: Path | None = None,
    broker_url: str | None = None,
    delegation_header_name: str | None = None,
    broker_timeout_seconds: float | None = None,
) -> AccessTokenProviderSettings:
    """Load only the settings owned by the explicitly selected provider."""
    if server_settings.access_token_provider is AccessTokenProviderMode.BROKER:
        return AccessTokenBrokerSettings.from_environment(
            url=broker_url,
            delegation_header_name=delegation_header_name,
            timeout_seconds=broker_timeout_seconds,
        )
    if _broker_configuration_supplied(
        broker_url=broker_url,
        delegation_header_name=delegation_header_name,
        broker_timeout_seconds=broker_timeout_seconds,
    ):
        raise ServerConfigurationError(
            "Broker settings require MONZO_MCP_ACCESS_TOKEN_PROVIDER=broker"
        )
    return CredentialSettings.from_environment(
        credential_dir=credential_dir,
        key_file=key_file,
    )


def _parse_boolean(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ServerConfigurationError("MONZO_MCP_ENABLE_WRITES must be true or false")


def _environment_list(name: str) -> tuple[str, ...]:
    return tuple(
        item.strip() for item in os.environ.get(name, "").split(",") if item.strip()
    )


def _environment_integer(
    name: str,
    *,
    default: int,
    override: int | None,
) -> int:
    if override is not None:
        return override
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        raise ServerConfigurationError("Invalid MCP HTTP server settings") from None


def _broker_configuration_supplied(
    *,
    broker_url: str | None,
    delegation_header_name: str | None,
    broker_timeout_seconds: float | None,
) -> bool:
    return (
        broker_url is not None
        or delegation_header_name is not None
        or broker_timeout_seconds is not None
        or any(name in os.environ for name in _BROKER_ENVIRONMENT_NAMES)
    )


def _default_allowed_hosts(*, host: str, port: int) -> tuple[str, ...]:
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ServerConfigurationError(
            "MONZO_MCP_HTTP_ALLOWED_HOSTS is required for a container bind"
        )
    return (
        "127.0.0.1",
        f"127.0.0.1:{port}",
        "localhost",
        f"localhost:{port}",
    )
