from __future__ import annotations

import os
import stat
import uuid

import pytest

from matic_sdk.client import MaticClient
from matic_sdk.config import InsecureTransportError, MaticConfig, TlsConfig
from matic_sdk.credentials import (
    BotToken,
    CredentialError,
    CredentialPermissionError,
    CredentialStore,
    serialize_token_request,
)
from matic_sdk.protocol.wire import encode_bytes_field


def synthetic_token(user_id: str, secret: bytes = b"x" * 16) -> bytes:
    return encode_bytes_field(1, serialize_token_request(user_id)) + encode_bytes_field(
        2, secret
    )


def test_bot_token_decodes_and_repr_redacts_secret() -> None:
    user_id = str(uuid.UUID(int=1))
    token = BotToken.decode(synthetic_token(user_id))
    assert token.user_id == user_id
    assert token.secret_length == 16
    assert "xxxxxxxx" not in repr(token)
    assert "serialized" not in repr(token)
    assert token.authorization_header().startswith("Bearer: ")


def test_credential_store_writes_owner_only_and_refuses_overwrite(tmp_path) -> None:
    store = CredentialStore("test-device", root=tmp_path / "data")
    client_id = store.load_or_create_client_id()
    assert str(uuid.UUID(client_id)) == client_id
    token = store.save_token(synthetic_token(client_id))
    assert token.user_id == client_id
    assert stat.S_IMODE(store.paths.directory.stat().st_mode) == 0o700
    assert stat.S_IMODE(store.paths.client_id.stat().st_mode) == 0o600
    assert stat.S_IMODE(store.paths.token.stat().st_mode) == 0o600
    assert store.load_token().serialized == token.serialized
    with pytest.raises(CredentialError, match="overwrite"):
        store.save_token(token.serialized)


def test_credential_store_rejects_public_token_file(tmp_path) -> None:
    store = CredentialStore("test-device", root=tmp_path / "data")
    client_id = store.load_or_create_client_id()
    store.save_token(synthetic_token(client_id))
    os.chmod(store.paths.token, 0o644)
    with pytest.raises(CredentialPermissionError, match="owner-only"):
        store.load_token()


def test_credential_device_id_cannot_escape_store(tmp_path) -> None:
    with pytest.raises(ValueError):
        CredentialStore("../escape", root=tmp_path)


@pytest.mark.asyncio
async def test_authenticated_client_rejects_insecure_tls_before_network_io() -> None:
    user_id = str(uuid.UUID(int=3))
    token = BotToken.decode(synthetic_token(user_id))
    config = MaticConfig("robot.invalid", tls=TlsConfig.insecure_diagnostics())

    with pytest.raises(InsecureTransportError, match="authenticated"):
        await MaticClient.connect(config, token=token)
