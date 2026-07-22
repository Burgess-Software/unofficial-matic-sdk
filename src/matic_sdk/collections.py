"""High-level concurrent subscriptions to Hermes collections."""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator, Callable
from typing import Any

from matic_sdk.protocol.collections import (
    DEFAULT_SUBSCRIPTION_CONFIG,
    KNOWN_TARGET_SET,
    KNOWN_TARGETS,
    RawCollectionEvent,
    SubscriptionConfig,
    decode_collection_response,
    initial_request,
    sequence_acknowledgement,
)
from matic_sdk.transport.h2 import GrpcStream, H2Transport

FETCH_COLLECTION_PATH = "/hermes.Hermes/FetchCollection"


class UnknownCollectionTarget(ValueError):
    """The target is not part of the live-verified read-only inventory."""


class CollectionSubscription(AsyncIterator[RawCollectionEvent]):
    """One auto-acknowledged bidirectional collection stream."""

    def __init__(
        self,
        target: str,
        stream: GrpcStream,
        *,
        on_close: Callable[[CollectionSubscription], Any] | None = None,
    ) -> None:
        self.target = target
        self._stream = stream
        self._on_close = on_close
        self._closed = False
        self.acknowledgements = 0
        self.messages = 0

    @classmethod
    async def open(
        cls,
        transport: H2Transport,
        target: str,
        *,
        config: SubscriptionConfig = DEFAULT_SUBSCRIPTION_CONFIG,
        fresh: bool = True,
        allow_unverified_target: bool = False,
        on_close: Callable[[CollectionSubscription], Any] | None = None,
    ) -> CollectionSubscription:
        if target not in KNOWN_TARGET_SET and not allow_unverified_target:
            raise UnknownCollectionTarget(
                f"{target!r} is not one of the 43 live-verified read-only targets"
            )
        stream = await transport.open_grpc_stream(
            FETCH_COLLECTION_PATH,
            metadata=(("hermes-target", target),),
            mutating=False,
        )
        try:
            await stream.send_message(
                initial_request(target, config=config, fresh=fresh),
                end_stream=False,
            )
        except BaseException:
            with contextlib.suppress(BaseException):
                await stream.cancel()
            raise
        return cls(target, stream, on_close=on_close)

    def __aiter__(self) -> CollectionSubscription:
        return self

    async def __anext__(self) -> RawCollectionEvent:
        if self._closed:
            raise StopAsyncIteration
        try:
            payload = await anext(self._stream)
        except StopAsyncIteration:
            await self.aclose(cancel_stream=False)
            raise
        except BaseException:
            with contextlib.suppress(BaseException):
                await self.aclose()
            raise
        try:
            event = decode_collection_response(self.target, payload)
            self.messages += 1
            if event.sequence_id is not None:
                await self._stream.send_message(
                    sequence_acknowledgement(event.sequence_id.encoded),
                    end_stream=False,
                )
                self.acknowledgements += 1
            return event
        except BaseException:
            with contextlib.suppress(BaseException):
                await self.aclose()
            raise

    async def aclose(self, *, cancel_stream: bool = True) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            if cancel_stream:
                await self._stream.cancel()
        finally:
            if self._on_close is not None:
                result = self._on_close(self)
                if hasattr(result, "__await__"):
                    await result

    async def __aenter__(self) -> CollectionSubscription:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()


class CollectionManager:
    """Collection facade exposed as ``client.collections``."""

    def __init__(self, opener: Callable[..., Any]) -> None:
        self._opener = opener

    @property
    def targets(self) -> tuple[str, ...]:
        return KNOWN_TARGETS

    async def subscribe(
        self,
        target: str,
        *,
        fresh: bool = True,
        config: SubscriptionConfig = DEFAULT_SUBSCRIPTION_CONFIG,
        allow_unverified_target: bool = False,
    ) -> CollectionSubscription:
        return await self._opener(
            target,
            fresh=fresh,
            subscription_config=config,
            allow_unverified_target=allow_unverified_target,
        )
