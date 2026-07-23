"""Encrypted, client-owned credential persistence for the local MCP host."""

from __future__ import annotations

import asyncio
import base64
import binascii
import fcntl
import os
import stat
import tempfile
from contextlib import suppress
from typing import IO, TYPE_CHECKING, Literal, Self

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import (
    AnyHttpUrl,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    ValidationError,
    field_validator,
)

from monzo_mcp.client import OAuthClientConfig, OAuthToken, TokenStore

if TYPE_CHECKING:
    from pathlib import Path

_ASSOCIATED_DATA = b"monzo-mcp.credentials.v1"
_CREDENTIAL_FILE = "credentials.enc"
_LOCK_FILE = ".credentials.lock"
_MAX_CREDENTIAL_BYTES = 1024 * 1024
_MAX_KEY_BYTES = 256
_KEY_BYTES = 32
_NONCE_BYTES = 12
_PRIVATE_MODE_MASK = stat.S_IRWXG | stat.S_IRWXO


class CredentialError(Exception):
    """Base class for safe credential-storage failures."""


class CredentialConfigurationError(CredentialError):
    """Credential paths, permissions, or encrypted data are invalid."""


class CredentialLockedError(CredentialError):
    """Another process already owns the credential directory."""


class CredentialStatus(BaseModel):
    """Non-secret credential metadata safe to show to a human."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    oauth_configured: bool
    token_configured: bool
    expires_at: AwareDatetime | None = None
    refreshable: bool = False


class _OAuthConfigWire(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    client_id: str = Field(min_length=1)
    client_secret: str = Field(min_length=1, repr=False)
    redirect_uri: AnyHttpUrl

    @classmethod
    def from_public(cls, oauth: OAuthClientConfig) -> _OAuthConfigWire:
        return cls(
            client_id=oauth.client_id,
            client_secret=oauth.client_secret.get_secret_value(),
            redirect_uri=oauth.redirect_uri,
        )

    def to_public(self) -> OAuthClientConfig:
        return OAuthClientConfig(
            client_id=self.client_id,
            client_secret=SecretStr(self.client_secret),
            redirect_uri=self.redirect_uri,
        )


class _OAuthTokenWire(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    access_token: str = Field(min_length=1, repr=False)
    token_type: str = Field(min_length=1)
    refresh_token: str | None = Field(default=None, min_length=1, repr=False)
    expires_at: AwareDatetime | None = None
    client_id: str | None = None
    user_id: str | None = None

    @classmethod
    def from_public(cls, token: OAuthToken) -> _OAuthTokenWire:
        refresh_token = (
            token.refresh_token.get_secret_value()
            if token.refresh_token is not None
            else None
        )
        return cls(
            access_token=token.access_token.get_secret_value(),
            token_type=token.token_type,
            refresh_token=refresh_token,
            expires_at=token.expires_at,
            client_id=token.client_id,
            user_id=token.user_id,
        )

    def to_public(self) -> OAuthToken:
        refresh_token = (
            SecretStr(self.refresh_token) if self.refresh_token is not None else None
        )
        return OAuthToken(
            access_token=SecretStr(self.access_token),
            token_type=self.token_type,
            refresh_token=refresh_token,
            expires_at=self.expires_at,
            client_id=self.client_id,
            user_id=self.user_id,
        )


class _CredentialPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    format_version: Literal[1] = 1
    oauth: _OAuthConfigWire | None = None
    token: _OAuthTokenWire | None = None


class _EncryptedEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    format_version: Literal[1] = 1
    nonce: str = Field(min_length=1)
    ciphertext: str = Field(min_length=1)

    @field_validator("nonce", "ciphertext")
    @classmethod
    def valid_base64(cls, value: str) -> str:
        try:
            base64.b64decode(value, altchars=b"-_", validate=True)
        except (binascii.Error, ValueError):
            msg = "value must use URL-safe base64"
            raise ValueError(msg) from None
        return value


class _CredentialDirectoryLock:
    """Non-blocking advisory lock held for one server or auth command."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._stream: IO[bytes] | None = None

    @property
    def acquired(self) -> bool:
        return self._stream is not None

    def acquire(self) -> None:
        if self._stream is not None:
            return
        flags = os.O_RDWR | os.O_CREAT
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(self._path, flags, 0o600)
        except OSError:
            raise CredentialConfigurationError(
                "Credential lock file could not be opened"
            ) from None
        stream = os.fdopen(descriptor, "r+b", buffering=0)
        try:
            _validate_private_regular_metadata(
                os.fstat(stream.fileno()),
                "Credential lock file",
            )
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            stream.close()
            raise CredentialLockedError(
                "Credential directory is already in use; stop the other process"
            ) from None
        except Exception:
            stream.close()
            raise
        self._stream = stream

    def release(self) -> None:
        stream = self._stream
        if stream is None:
            return
        self._stream = None
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        finally:
            stream.close()


class ClientCredentialStore(TokenStore):
    """Async token store backed by an encrypted MCP-host-owned directory."""

    def __init__(
        self,
        *,
        credential_dir: Path,
        key_file: Path,
        create_key: bool = False,
        allow_empty: bool = False,
    ) -> None:
        self._credential_dir = _absolute_path(credential_dir, "credential directory")
        self._key_file = _absolute_path(key_file, "credential key file")
        self._create_key = create_key
        self._allow_empty = allow_empty
        self._lock = _CredentialDirectoryLock(self._credential_dir.joinpath(_LOCK_FILE))
        self._write_lock = asyncio.Lock()
        self._payload: _CredentialPayload | None = None
        self._key: bytes | None = None

    async def __aenter__(self) -> Self:
        await asyncio.to_thread(self._open)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Forget decrypted state and release the process-level lock."""
        self._payload = None
        self._key = None
        await asyncio.to_thread(self._lock.release)

    async def load(self) -> OAuthToken | None:
        """Return the cached token loaded from encrypted client storage."""
        payload = self._require_open()
        return payload.token.to_public() if payload.token is not None else None

    async def save(self, token: OAuthToken) -> None:
        """Atomically encrypt and persist a replacement token set."""
        async with self._write_lock:
            current = self._require_open()
            replacement = current.model_copy(
                update={"token": _OAuthTokenWire.from_public(token)}
            )
            await self._persist(replacement)

    async def clear(self) -> None:
        """Clear the user token while retaining confidential OAuth configuration."""
        async with self._write_lock:
            current = self._require_open()
            await self._persist(current.model_copy(update={"token": None}))

    async def load_oauth(self) -> OAuthClientConfig | None:
        """Return confidential OAuth configuration from the encrypted bundle."""
        payload = self._require_open()
        return payload.oauth.to_public() if payload.oauth is not None else None

    async def save_oauth(self, oauth: OAuthClientConfig) -> None:
        """Atomically persist confidential OAuth configuration."""
        async with self._write_lock:
            current = self._require_open()
            replacement = current.model_copy(
                update={"oauth": _OAuthConfigWire.from_public(oauth)}
            )
            await self._persist(replacement)

    async def status(self) -> CredentialStatus:
        """Return only credential metadata that is safe for terminal output."""
        payload = self._require_open()
        token = payload.token
        return CredentialStatus(
            oauth_configured=payload.oauth is not None,
            token_configured=token is not None,
            expires_at=token.expires_at if token is not None else None,
            refreshable=token is not None and token.refresh_token is not None,
        )

    def _open(self) -> None:
        if self._payload is not None or self._lock.acquired:
            return
        _prepare_directory(self._credential_dir)
        self._lock.acquire()
        try:
            credential_path = self._credential_dir.joinpath(_CREDENTIAL_FILE)
            if self._create_key and not self._key_file.exists():
                _create_key_file(self._key_file)
            if not self._key_file.exists():
                if self._allow_empty and not credential_path.exists():
                    self._payload = _CredentialPayload()
                    self._key = None
                    return
                raise CredentialConfigurationError(
                    "Credential key file is missing; run human login first"
                )
            key = _read_key_file(self._key_file)
            if credential_path.exists():
                payload = _read_payload(credential_path, key)
            else:
                payload = _CredentialPayload()
            self._key = key
            self._payload = payload
        except Exception:
            self._lock.release()
            raise

    async def _persist(self, payload: _CredentialPayload) -> None:
        key = self._key
        if key is None:
            raise CredentialConfigurationError(
                "Credential key is unavailable; run human login first"
            )
        await asyncio.to_thread(
            _write_payload,
            self._credential_dir.joinpath(_CREDENTIAL_FILE),
            key,
            payload,
        )
        self._payload = payload

    def _require_open(self) -> _CredentialPayload:
        if self._payload is None or not self._lock.acquired:
            raise CredentialConfigurationError("Credential store is not open")
        return self._payload


def _absolute_path(path: Path, label: str) -> Path:
    if not path.is_absolute():
        raise CredentialConfigurationError(f"{label.capitalize()} must be absolute")
    return path


def _prepare_directory(path: Path) -> None:
    try:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        metadata = path.lstat()
    except OSError:
        raise CredentialConfigurationError(
            "Credential directory could not be prepared"
        ) from None
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise CredentialConfigurationError(
            "Credential directory must be a real directory"
        )
    if metadata.st_mode & _PRIVATE_MODE_MASK:
        raise CredentialConfigurationError(
            "Credential directory must not be accessible by group or others"
        )


def _require_private_regular_file(path: Path, label: str) -> os.stat_result:
    try:
        metadata = path.lstat()
    except OSError:
        raise CredentialConfigurationError(f"{label} could not be inspected") from None
    if stat.S_ISLNK(metadata.st_mode):
        raise CredentialConfigurationError(f"{label} must be a regular file")
    _validate_private_regular_metadata(metadata, label)
    return metadata


def _validate_private_regular_metadata(
    metadata: os.stat_result,
    label: str,
) -> None:
    if not stat.S_ISREG(metadata.st_mode):
        raise CredentialConfigurationError(f"{label} must be a regular file")
    if metadata.st_mode & _PRIVATE_MODE_MASK:
        raise CredentialConfigurationError(
            f"{label} must not be accessible by group or others"
        )


def _create_key_file(path: Path) -> None:
    parent = path.parent
    _prepare_directory(parent)
    encoded = base64.urlsafe_b64encode(AESGCM.generate_key(bit_length=256)) + b"\n"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        _fsync_directory(parent)
    except FileExistsError:
        return
    except OSError:
        raise CredentialConfigurationError(
            "Credential key file could not be created"
        ) from None


def _read_key_file(path: Path) -> bytes:
    _require_private_regular_file(path, "Credential key file")
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as stream:
            metadata = os.fstat(stream.fileno())
            _validate_private_regular_metadata(metadata, "Credential key file")
            if metadata.st_size > _MAX_KEY_BYTES:
                raise ValueError
            encoded = stream.read(_MAX_KEY_BYTES + 1).strip()
        key = base64.b64decode(encoded, altchars=b"-_", validate=True)
    except (CredentialConfigurationError, OSError, binascii.Error, ValueError):
        raise CredentialConfigurationError("Credential key file is invalid") from None
    if len(key) != _KEY_BYTES:
        raise CredentialConfigurationError("Credential key file is invalid")
    return key


def _read_payload(path: Path, key: bytes) -> _CredentialPayload:
    _require_private_regular_file(path, "Encrypted credential file")
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as stream:
            metadata = os.fstat(stream.fileno())
            _validate_private_regular_metadata(metadata, "Encrypted credential file")
            if metadata.st_size > _MAX_CREDENTIAL_BYTES:
                raise ValueError
            data = stream.read(_MAX_CREDENTIAL_BYTES + 1)
        envelope = _EncryptedEnvelope.model_validate_json(data)
        nonce = base64.urlsafe_b64decode(envelope.nonce)
        ciphertext = base64.urlsafe_b64decode(envelope.ciphertext)
        if len(nonce) != _NONCE_BYTES:
            raise ValueError
        plaintext = AESGCM(key).decrypt(nonce, ciphertext, _ASSOCIATED_DATA)
        return _CredentialPayload.model_validate_json(plaintext)
    except (
        CredentialConfigurationError,
        InvalidTag,
        OSError,
        ValidationError,
        ValueError,
        binascii.Error,
    ):
        raise CredentialConfigurationError(
            "Encrypted credential bundle is invalid or uses a different key"
        ) from None


def _write_payload(path: Path, key: bytes, payload: _CredentialPayload) -> None:
    nonce = os.urandom(_NONCE_BYTES)
    plaintext = payload.model_dump_json().encode()
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, _ASSOCIATED_DATA)
    envelope = _EncryptedEnvelope(
        nonce=base64.urlsafe_b64encode(nonce).decode(),
        ciphertext=base64.urlsafe_b64encode(ciphertext).decode(),
    )
    serialized = envelope.model_dump_json().encode()

    descriptor: int | None = None
    temporary_path: str | None = None
    try:
        descriptor, temporary_path = tempfile.mkstemp(
            prefix=".credentials.",
            suffix=".tmp",
            dir=path.parent,
        )
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = None
            stream.write(serialized)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
        temporary_path = None
        _fsync_directory(path.parent)
    except OSError:
        raise CredentialConfigurationError(
            "Encrypted credential bundle could not be saved"
        ) from None
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary_path is not None:
            with suppress(FileNotFoundError):
                os.unlink(temporary_path)


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
