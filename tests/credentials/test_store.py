from __future__ import annotations

import base64
import json
import os
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from aiomonzo import OAuthClientConfig, OAuthToken
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import AnyHttpUrl, SecretStr

from monzo_mcp.credentials import (
    ClientCredentialStore,
    CredentialConfigurationError,
    CredentialLockedError,
)

if TYPE_CHECKING:
    from pathlib import Path


def _oauth() -> OAuthClientConfig:
    return OAuthClientConfig(
        client_id="client-safe-id",
        client_secret=SecretStr("client-sensitive-secret"),
        redirect_uri=AnyHttpUrl("http://127.0.0.1:8765/oauth/callback"),
    )


def _token(access: str = "access-sensitive") -> OAuthToken:
    return OAuthToken(
        access_token=SecretStr(access),
        refresh_token=SecretStr("refresh-sensitive"),
        expires_at=datetime.now(UTC) + timedelta(hours=6),
        client_id="client-safe-id",
        user_id="user-safe-id",
    )


def _paths(tmp_path: Path) -> tuple[Path, Path]:
    credential_dir = tmp_path / "credentials"
    key_file = tmp_path / "key"
    return credential_dir, key_file


@pytest.mark.asyncio
async def test_encrypted_round_trip_rotation_and_clear(tmp_path: Path) -> None:
    credential_dir, key_file = _paths(tmp_path)
    first_token = _token()

    async with ClientCredentialStore(
        credential_dir=credential_dir,
        key_file=key_file,
        create_key=True,
    ) as store:
        await store.save_oauth(_oauth())
        await store.save(first_token)
        status = await store.status()

    encrypted = (credential_dir / "credentials.enc").read_bytes()
    assert b"access-sensitive" not in encrypted
    assert b"refresh-sensitive" not in encrypted
    assert b"client-sensitive-secret" not in encrypted
    assert status.oauth_configured is True
    assert status.token_configured is True
    assert status.refreshable is True

    async with ClientCredentialStore(
        credential_dir=credential_dir,
        key_file=key_file,
    ) as reopened:
        loaded_oauth = await reopened.load_oauth()
        loaded_token = await reopened.load()
        assert loaded_oauth == _oauth()
        assert loaded_token == first_token

        replacement = _token("access-rotated")
        await reopened.save(replacement)
        assert await reopened.load() == replacement
        await reopened.clear()
        assert await reopened.load() is None
        assert await reopened.load_oauth() == _oauth()

    assert stat_mode(key_file) == 0o600
    assert stat_mode(credential_dir / "credentials.enc") == 0o600
    assert stat_mode(credential_dir) == 0o700


@pytest.mark.asyncio
async def test_every_encrypted_write_uses_a_fresh_nonce(tmp_path: Path) -> None:
    credential_dir, key_file = _paths(tmp_path)

    async with ClientCredentialStore(
        credential_dir=credential_dir,
        key_file=key_file,
        create_key=True,
    ) as store:
        await store.save_oauth(_oauth())
        first = json.loads((credential_dir / "credentials.enc").read_text())
        await store.save_oauth(_oauth())
        second = json.loads((credential_dir / "credentials.enc").read_text())

    assert first["nonce"] != second["nonce"]
    assert first["ciphertext"] != second["ciphertext"]


@pytest.mark.asyncio
async def test_lock_prevents_two_process_owners(tmp_path: Path) -> None:
    credential_dir, key_file = _paths(tmp_path)
    first = ClientCredentialStore(
        credential_dir=credential_dir,
        key_file=key_file,
        create_key=True,
    )
    second = ClientCredentialStore(
        credential_dir=credential_dir,
        key_file=key_file,
        allow_empty=True,
    )

    async with first:
        with pytest.raises(CredentialLockedError, match="already in use"):
            await second.__aenter__()

    await second.__aenter__()
    await second.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    ["tampered", "wrong-key", "unknown-version", "malformed"],
)
async def test_invalid_encrypted_data_fails_without_secrets(
    tmp_path: Path,
    failure: str,
) -> None:
    credential_dir, key_file = _paths(tmp_path)
    async with ClientCredentialStore(
        credential_dir=credential_dir,
        key_file=key_file,
        create_key=True,
    ) as store:
        await store.save_oauth(_oauth())
        await store.save(_token())

    credential_file = credential_dir / "credentials.enc"
    if failure == "tampered":
        envelope = json.loads(credential_file.read_text())
        ciphertext = bytearray(base64.urlsafe_b64decode(envelope["ciphertext"]))
        ciphertext[-1] ^= 1
        envelope["ciphertext"] = base64.urlsafe_b64encode(ciphertext).decode()
        credential_file.write_text(json.dumps(envelope))
    elif failure == "wrong-key":
        key_file.write_bytes(
            base64.urlsafe_b64encode(AESGCM.generate_key(bit_length=256)) + b"\n"
        )
    elif failure == "unknown-version":
        envelope = json.loads(credential_file.read_text())
        envelope["format_version"] = 2
        credential_file.write_text(json.dumps(envelope))
    else:
        credential_file.write_text("not-json")
    os.chmod(credential_file, 0o600)
    os.chmod(key_file, 0o600)

    with pytest.raises(CredentialConfigurationError) as captured:
        async with ClientCredentialStore(
            credential_dir=credential_dir,
            key_file=key_file,
        ):
            pytest.fail("invalid credentials unexpectedly opened")

    message = str(captured.value)
    assert "access-sensitive" not in message
    assert "refresh-sensitive" not in message
    assert "client-sensitive-secret" not in message


@pytest.mark.asyncio
async def test_symlink_and_insecure_permissions_are_rejected(tmp_path: Path) -> None:
    credential_dir, key_file = _paths(tmp_path)
    credential_dir.mkdir(mode=0o700)
    real_key = tmp_path / "real-key"
    real_key.write_bytes(
        base64.urlsafe_b64encode(AESGCM.generate_key(bit_length=256)) + b"\n"
    )
    os.chmod(real_key, 0o600)
    key_file.symlink_to(real_key)

    with pytest.raises(CredentialConfigurationError, match="regular file"):
        async with ClientCredentialStore(
            credential_dir=credential_dir,
            key_file=key_file,
        ):
            pytest.fail("symlinked key unexpectedly opened")

    key_file.unlink()
    key_file.write_bytes(real_key.read_bytes())
    os.chmod(key_file, 0o644)
    with pytest.raises(CredentialConfigurationError, match="group or others"):
        async with ClientCredentialStore(
            credential_dir=credential_dir,
            key_file=key_file,
        ):
            pytest.fail("public key unexpectedly opened")


@pytest.mark.asyncio
async def test_interrupted_atomic_write_preserves_previous_bundle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credential_dir, key_file = _paths(tmp_path)
    original = _token("access-original")

    async with ClientCredentialStore(
        credential_dir=credential_dir,
        key_file=key_file,
        create_key=True,
    ) as store:
        await store.save_oauth(_oauth())
        await store.save(original)

        def fail_replace(source: str, destination: Path) -> None:
            del source, destination
            raise OSError

        monkeypatch.setattr(os, "replace", fail_replace)
        with pytest.raises(
            CredentialConfigurationError,
            match="could not be saved",
        ):
            await store.save(_token("access-lost"))
        assert await store.load() == original

    monkeypatch.undo()
    async with ClientCredentialStore(
        credential_dir=credential_dir,
        key_file=key_file,
    ) as reopened:
        assert await reopened.load() == original


def stat_mode(path: Path) -> int:
    return path.stat().st_mode & 0o777
