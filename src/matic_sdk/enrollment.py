"""Linux/BlueZ enrollment through Matic's BLE token service."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from matic_sdk.credentials import (
    BotToken,
    CredentialError,
    CredentialStore,
    serialize_token_request,
)

TOKEN_SERVICE_UUID = "5b14adcd-e995-9e80-c55a-b6c6fb6c612f"
TOKEN_CHARACTERISTIC_UUID = "84b52f26-d3b7-5ebe-ba52-ff38a447788d"


class EnrollmentError(RuntimeError):
    """BLE scanning, pairing, or token exchange failed."""


class BleSupportUnavailable(EnrollmentError):
    """The optional Linux BLE stack is unavailable."""


@dataclass(frozen=True, slots=True)
class BleCandidate:
    address: str
    name: str | None
    service_uuids: tuple[str, ...]
    rssi: int
    _native_device: object = field(repr=False, compare=False)

    @property
    def exposes_token_service(self) -> bool:
        return TOKEN_SERVICE_UUID in self.service_uuids


@dataclass(frozen=True, slots=True)
class EnrollmentResult:
    device_id: str
    client_id: str
    token_user_id: str | None
    token_bytes: int
    token_path: str


def _bleak() -> tuple[Any, Any]:
    if not sys.platform.startswith("linux"):
        raise BleSupportUnavailable("BLE enrollment currently requires Linux/BlueZ")
    try:
        from bleak import BleakClient, BleakScanner
    except ImportError as exc:
        raise BleSupportUnavailable(
            "BLE enrollment requires the optional 'ble' package extra"
        ) from exc
    return BleakClient, BleakScanner


async def scan(
    *,
    timeout: float = 10.0,  # noqa: ASYNC109
) -> tuple[BleCandidate, ...]:
    """Find advertisements for the token service or a Matic-named device."""

    if timeout <= 0:
        raise ValueError("scan timeout must be positive")
    _, scanner = _bleak()
    discovered = await scanner.discover(timeout=timeout, return_adv=True)
    candidates: list[BleCandidate] = []
    for native, advertisement in discovered.values():
        uuids = tuple(value.lower() for value in advertisement.service_uuids)
        name = advertisement.local_name or native.name
        if TOKEN_SERVICE_UUID in uuids or (name and name.lower().startswith("matic-")):
            candidates.append(
                BleCandidate(
                    address=native.address,
                    name=name,
                    service_uuids=uuids,
                    rssi=advertisement.rssi,
                    _native_device=native,
                )
            )
    return tuple(sorted(candidates, key=lambda item: item.rssi, reverse=True))


def select_candidate(
    candidates: tuple[BleCandidate, ...] | list[BleCandidate],
    *,
    address: str | None = None,
    name: str | None = None,
) -> BleCandidate:
    if address is not None:
        match = next(
            (
                candidate
                for candidate in candidates
                if candidate.address.casefold() == address.casefold()
            ),
            None,
        )
        if match is None:
            raise EnrollmentError("the requested BLE address was not discovered")
        return match
    service_matches = [item for item in candidates if item.exposes_token_service]
    if name is not None:
        match = next(
            (
                candidate
                for candidate in service_matches
                if candidate.name and candidate.name.casefold() == name.casefold()
            ),
            None,
        )
        if match is not None:
            return match
        raise EnrollmentError(
            "the requested BLE name did not expose the Matic token service"
        )
    if service_matches:
        return service_matches[0]
    raise EnrollmentError("no advertiser exposed the Matic token service")


def _find_characteristic(client: Any) -> Any | None:
    service = client.services.get_service(TOKEN_SERVICE_UUID)
    return service.get_characteristic(TOKEN_CHARACTERISTIC_UUID) if service else None


async def _exchange(client: Any, request: bytes, settle_time: float) -> bytes:
    characteristic = _find_characteristic(client)
    if characteristic is None:
        raise EnrollmentError("paired device does not expose the token characteristic")
    await client.write_gatt_char(characteristic, request, response=True)
    if settle_time:
        await asyncio.sleep(settle_time)
    return bytes(await client.read_gatt_char(characteristic))


async def acquire_bot_token(
    candidate: BleCandidate,
    *,
    user_id: str,
    timeout: float = 60.0,  # noqa: ASYNC109 - passed to Bleak's connection API
    settle_time: float = 0.25,
    pair: bool = True,
    _client_factory: Callable[..., Any] | None = None,
) -> BotToken:
    """Pair and perform the acknowledged request/read token exchange."""

    if timeout <= 0 or settle_time < 0:
        raise ValueError("timeout must be positive and settle_time non-negative")
    client_type, _ = _bleak() if _client_factory is None else (_client_factory, None)
    request = serialize_token_request(user_id)
    target = candidate._native_device
    async with client_type(target, pair=pair, timeout=timeout) as client:
        if not client.is_connected:
            raise EnrollmentError("BLE connection did not reach connected state")
        if _find_characteristic(client) is not None:
            serialized = await _exchange(client, request, settle_time)
            token = BotToken.decode(serialized)
            if token.user_id is not None and token.user_id != user_id:
                raise EnrollmentError(
                    "robot returned a token for a different client UUID"
                )
            return token

    # BlueZ can retain the pre-bond service view.  Reconnect once after Pair()
    # to force discovery with the newly established bond.
    async with client_type(target, timeout=timeout) as client:
        if not client.is_connected:
            raise EnrollmentError("BLE reconnection did not reach connected state")
        serialized = await _exchange(client, request, settle_time)
    token = BotToken.decode(serialized)
    if token.user_id is not None and token.user_id != user_id:
        raise EnrollmentError("robot returned a token for a different client UUID")
    return token


async def enroll(
    store: CredentialStore,
    *,
    candidate: BleCandidate | None = None,
    address: str | None = None,
    name: str | None = None,
    scan_timeout: float = 10.0,
    timeout: float = 60.0,  # noqa: ASYNC109 - public BLE operation timeout
    pair: bool = True,
) -> EnrollmentResult:
    """Enroll one SDK identity and persist its BotToken without overwriting."""

    if store.enrolled:
        raise CredentialError(
            f"device {store.device_id!r} is already enrolled; refusing to overwrite"
        )
    chosen = candidate
    if chosen is None:
        chosen = select_candidate(
            await scan(timeout=scan_timeout), address=address, name=name
        )
    client_id = store.load_or_create_client_id()
    token = await acquire_bot_token(
        chosen,
        user_id=client_id,
        timeout=timeout,
        pair=pair,
    )
    store.save_token(token.serialized)
    return EnrollmentResult(
        device_id=store.device_id,
        client_id=client_id,
        token_user_id=token.user_id,
        token_bytes=len(token.serialized),
        token_path=str(store.paths.token),
    )
