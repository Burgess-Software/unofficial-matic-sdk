from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from matic_sdk.client import MaticClient
from matic_sdk.config import MaticConfig, TlsConfig
from matic_sdk.protocol.collections import (
    RawCollectionEvent,
    decode_collection_response,
)
from matic_sdk.protocol.wire import encode_bytes_field
from matic_sdk.telemetry import TelemetrySession


def synthetic_event(target: str, payload: bytes) -> RawCollectionEvent:
    value = encode_bytes_field(5, encode_bytes_field(1, payload))
    return decode_collection_response(target, encode_bytes_field(2, value))


class FiniteSubscription(AsyncIterator[RawCollectionEvent]):
    def __init__(self, event: RawCollectionEvent) -> None:
        self._event = event
        self._sent = False
        self.closed = False

    def __aiter__(self) -> FiniteSubscription:
        return self

    async def __anext__(self) -> RawCollectionEvent:
        if self._sent:
            raise StopAsyncIteration
        self._sent = True
        return self._event

    async def aclose(self) -> None:
        self.closed = True


class FiniteClient:
    def __init__(self) -> None:
        self.subscriptions: list[FiniteSubscription] = []

    async def subscribe(self, target: str) -> FiniteSubscription:
        subscription = FiniteSubscription(synthetic_event(target, target.encode()))
        self.subscriptions.append(subscription)
        return subscription


@pytest.mark.asyncio
async def test_telemetry_stops_after_all_finite_streams_end() -> None:
    client = FiniteClient()
    session = TelemetrySession(client, ("latest_pose", "motor_status"))  # type: ignore[arg-type]

    updates = [update async for update in session]

    assert {update.event.target for update in updates} == {
        "latest_pose",
        "motor_status",
    }
    assert all(subscription.closed for subscription in client.subscriptions)


class ClosingSubscription:
    def __init__(self, *, error: BaseException | None = None) -> None:
        self.error = error
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True
        if self.error is not None:
            raise self.error


class ClosingTransport:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_client_closes_every_resource_after_subscription_error() -> None:
    transport = ClosingTransport()
    client = MaticClient(
        MaticConfig("robot.invalid", tls=TlsConfig.pinned("00" * 32)),
        transport,  # type: ignore[arg-type]
        credentials=None,
    )
    broken = ClosingSubscription(error=ConnectionError("synthetic close failure"))
    healthy = ClosingSubscription()
    client._subscriptions.update((broken, healthy))  # type: ignore[arg-type]

    with pytest.raises(ConnectionError, match="synthetic close failure"):
        await client.close()

    assert broken.closed
    assert healthy.closed
    assert transport.closed
