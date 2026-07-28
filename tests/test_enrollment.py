from __future__ import annotations

# ruff: noqa: ASYNC109 -- fakes deliberately mirror Bleak/enrollment signatures.
import uuid
from dataclasses import dataclass
from types import SimpleNamespace
from typing import ClassVar

import pytest

import matic_sdk.enrollment as enrollment
from matic_sdk.credentials import BotToken, CredentialStore, serialize_token_request
from matic_sdk.enrollment import (
    TOKEN_CHARACTERISTIC_UUID,
    TOKEN_SERVICE_UUID,
    BleCandidate,
    EnrollmentError,
    acquire_bot_token,
    enroll,
    scan,
    select_candidate,
)
from matic_sdk.protocol.wire import encode_bytes_field


def synthetic_token(user_id: str) -> bytes:
    return encode_bytes_field(1, serialize_token_request(user_id)) + encode_bytes_field(
        2, b"synthetic-secret"
    )


@dataclass
class NativeDevice:
    address: str
    name: str | None


class FakeScanner:
    @staticmethod
    async def discover(*, timeout: float, return_adv: bool):
        assert timeout == 1.5
        assert return_adv is True
        strong = NativeDevice("AA:00", "matic-test")
        unrelated = NativeDevice("BB:00", "headphones")
        return {
            "strong": (
                strong,
                SimpleNamespace(
                    service_uuids=[TOKEN_SERVICE_UUID.upper()],
                    local_name=strong.name,
                    rssi=-40,
                ),
            ),
            "unrelated": (
                unrelated,
                SimpleNamespace(
                    service_uuids=[],
                    local_name=unrelated.name,
                    rssi=-10,
                ),
            ),
        }


async def test_scan_filters_and_normalizes_token_advertisers(monkeypatch) -> None:
    monkeypatch.setattr(enrollment, "_bleak", lambda: (object, FakeScanner))

    candidates = await scan(timeout=1.5)

    assert len(candidates) == 1
    assert candidates[0].address == "AA:00"
    assert candidates[0].service_uuids == (TOKEN_SERVICE_UUID,)
    assert candidates[0].exposes_token_service


class FakeService:
    def get_characteristic(self, uuid_value: str):
        return (
            "token-characteristic" if uuid_value == TOKEN_CHARACTERISTIC_UUID else None
        )


class FakeServices:
    def get_service(self, uuid_value: str):
        return FakeService() if uuid_value == TOKEN_SERVICE_UUID else None


class FakeClient:
    instances: ClassVar[list[FakeClient]] = []
    response: bytes = b""

    def __init__(self, target: object, **options: object) -> None:
        self.target = target
        self.options = options
        self.is_connected = True
        self.services = FakeServices()
        self.writes: list[tuple[object, bytes, bool]] = []
        type(self).instances.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def write_gatt_char(
        self, characteristic: object, value: bytes, *, response: bool
    ) -> None:
        self.writes.append((characteristic, value, response))

    async def read_gatt_char(self, characteristic: object) -> bytes:
        assert characteristic == "token-characteristic"
        return self.response


async def test_acquire_token_uses_acknowledged_write_then_read() -> None:
    user_id = str(uuid.UUID(int=4))
    FakeClient.instances.clear()
    FakeClient.response = synthetic_token(user_id)
    native = NativeDevice("AA:00", "matic-test")
    candidate = BleCandidate(
        native.address,
        native.name,
        (TOKEN_SERVICE_UUID,),
        -40,
        native,
    )

    token = await acquire_bot_token(
        candidate,
        user_id=user_id,
        settle_time=0,
        _client_factory=FakeClient,
    )

    assert isinstance(token, BotToken)
    assert token.user_id == user_id
    assert len(FakeClient.instances) == 1
    client = FakeClient.instances[0]
    assert client.options["pair"] is True
    assert client.writes == [
        ("token-characteristic", serialize_token_request(user_id), True)
    ]


async def test_enroll_persists_result_without_printing_secret(
    tmp_path, monkeypatch
) -> None:
    store = CredentialStore("test-device", root=tmp_path / "credentials")
    native = NativeDevice("AA:00", "matic-test")
    candidate = BleCandidate(
        native.address,
        native.name,
        (TOKEN_SERVICE_UUID,),
        -40,
        native,
    )

    async def fake_acquire(
        selected: BleCandidate,
        *,
        user_id: str,
        timeout: float,
        pair: bool,
    ) -> BotToken:
        assert selected is candidate
        assert timeout == 60.0
        assert pair is True
        return BotToken.decode(synthetic_token(user_id))

    monkeypatch.setattr(enrollment, "acquire_bot_token", fake_acquire)

    result = await enroll(store, candidate=candidate)

    assert result.device_alias == "test-device"
    assert store.load_token().user_id == result.client_id
    assert "synthetic-secret" not in repr(result)


def test_exact_name_never_falls_back_to_a_different_robot() -> None:
    native = NativeDevice("AA:00", "matic-nearby")
    candidates = (
        BleCandidate(
            native.address,
            native.name,
            (TOKEN_SERVICE_UUID,),
            -20,
            native,
        ),
    )

    with pytest.raises(EnrollmentError, match="requested BLE name"):
        select_candidate(candidates, name="matic-requested")
