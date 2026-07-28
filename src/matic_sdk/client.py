"""Authenticated async client for local Matic Hermes services."""

from __future__ import annotations

import asyncio
import weakref
from collections.abc import Iterable
from typing import TYPE_CHECKING, Self

from matic_sdk.collections import (
    CollectionManager,
    CollectionSubscription,
)
from matic_sdk.commands import CommandExecutor
from matic_sdk.config import InsecureTransportError, MaticConfig
from matic_sdk.credentials import BotToken, CredentialStore
from matic_sdk.discovery import BotInformation, get_bot_info
from matic_sdk.protocol.collections import (
    DEFAULT_SUBSCRIPTION_CONFIG,
    RawCollectionEvent,
    SubscriptionConfig,
)
from matic_sdk.safety import (
    DEFAULT_INPUT_LEASE_SECONDS,
    DEFAULT_MAX_LINEAR_MPS,
    DEFAULT_TELEOP_RATE_HZ,
    HARD_MAX_ANGULAR_RAD_S,
    MotionControls,
    TeleopSession,
)
from matic_sdk.telemetry import DEFAULT_CONTROL_FEEDBACK_TARGETS, TelemetrySession
from matic_sdk.transport.commands import _HermesCommandTransport
from matic_sdk.transport.h2 import H2Transport

if TYPE_CHECKING:
    from matic_sdk.coverage import ReprioritizationSnapshot

HANDSHAKE_PATH = "/hermes.Hermes/Handshake"


class MaticClient:
    """One authenticated, multiplexed connection to an owner-controlled robot."""

    def __init__(
        self,
        config: MaticConfig,
        transport: H2Transport,
        *,
        credentials: CredentialStore | None,
    ) -> None:
        self.config = config
        self.transport = transport
        self.credentials = credentials
        self.collections = CollectionManager(self.subscribe)
        self.commands = CommandExecutor(
            _HermesCommandTransport(transport),
            protocol_version=config.command_protocol_version,
            tls_identity_verified=config.tls.verified,
        )
        self._subscriptions: set[CollectionSubscription] = set()
        self._teleop_sessions: weakref.WeakSet[TeleopSession] = weakref.WeakSet()
        self._closed = False

    @classmethod
    async def connect(
        cls,
        config: MaticConfig,
        *,
        credentials: CredentialStore | None = None,
        token: BotToken | None = None,
        handshake: bool = True,
    ) -> Self:
        """Load credentials, connect, and verify the authenticated handshake."""

        if not config.tls.verified:
            raise InsecureTransportError(
                "authenticated connections require verified TLS; insecure mode "
                "is limited to unauthenticated discovery"
            )
        if token is not None and credentials is not None:
            raise ValueError("pass either a BotToken or CredentialStore, not both")
        if token is None:
            if credentials is None:
                raise ValueError("authenticated connection requires credentials")
            token = credentials.load_token()
        transport = H2Transport(
            config,
            default_metadata=(("authorization", token.authorization_header()),),
        )
        await transport.connect()
        client = cls(config, transport, credentials=credentials)
        try:
            if handshake:
                await client.handshake()
        except BaseException:
            await client.close()
            raise
        return client

    @classmethod
    async def connect_from_store(
        cls,
        device_id: str,
        config: MaticConfig,
        *,
        credential_root: str | None = None,
    ) -> Self:
        from pathlib import Path

        root = (
            Path(credential_root).expanduser()  # noqa: ASYNC240
            if credential_root
            else None
        )
        return await cls.connect(
            config,
            credentials=CredentialStore(device_id, root=root),
        )

    async def handshake(self) -> None:
        response = await self.transport.unary(HANDSHAKE_PATH)
        response.raise_for_status()

    async def bot_info(self) -> BotInformation:
        return await get_bot_info(self.transport)

    async def subscribe(
        self,
        target: str,
        *,
        fresh: bool = True,
        subscription_config: SubscriptionConfig = DEFAULT_SUBSCRIPTION_CONFIG,
        allow_unverified_target: bool = False,
    ) -> CollectionSubscription:
        if self._closed:
            raise RuntimeError("MaticClient is closed")

        def remove(subscription: CollectionSubscription) -> None:
            self._subscriptions.discard(subscription)

        subscription = await CollectionSubscription.open(
            self.transport,
            target,
            fresh=fresh,
            config=subscription_config,
            allow_unverified_target=allow_unverified_target,
            on_close=remove,
        )
        self._subscriptions.add(subscription)
        return subscription

    async def first(
        self,
        target: str,
        *,
        timeout: float | None = None,  # noqa: ASYNC109 - public timeout setting
    ) -> RawCollectionEvent:
        subscription = await self.subscribe(target)
        try:
            return await asyncio.wait_for(
                anext(subscription),
                timeout=timeout or self.config.operation_timeout,
            )
        finally:
            await subscription.aclose()

    def telemetry(
        self,
        targets: Iterable[str] = DEFAULT_CONTROL_FEEDBACK_TARGETS,
    ) -> TelemetrySession:
        return TelemetrySession(self, targets)

    async def reprioritization_snapshot(
        self,
        *,
        timeout: float | None = None,  # noqa: ASYNC109 - public timeout setting
    ) -> ReprioritizationSnapshot | None:
        """Read a coherent active plan suitable for reprioritization."""

        from matic_sdk.coverage import fetch_reprioritization_snapshot

        return await fetch_reprioritization_snapshot(self, timeout=timeout)

    def teleop(
        self,
        *,
        motion_controls: MotionControls,
        max_linear_mps: float = DEFAULT_MAX_LINEAR_MPS,
        max_angular_rad_s: float = HARD_MAX_ANGULAR_RAD_S,
        rate_hz: float = DEFAULT_TELEOP_RATE_HZ,
        lease_seconds: float = DEFAULT_INPUT_LEASE_SECONDS,
        shutdown_timeout_seconds: float = 1.0,
    ) -> TeleopSession:
        """Create a watchdog-backed joystick session on this connection."""

        if self._closed:
            raise RuntimeError("MaticClient is closed")
        session = self.commands._teleop_session(
            motion_controls=motion_controls,
            max_linear_mps=max_linear_mps,
            max_angular_rad_s=max_angular_rad_s,
            rate_hz=rate_hz,
            lease_seconds=lease_seconds,
            shutdown_timeout_seconds=shutdown_timeout_seconds,
        )
        self._teleop_sessions.add(session)
        return session

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        errors: list[BaseException] = []
        for session in tuple(self._teleop_sessions):
            try:
                await session.close()
            except BaseException as error:
                errors.append(error)
        for subscription in tuple(self._subscriptions):
            try:
                await subscription.aclose()
            except BaseException as error:
                errors.append(error)
        try:
            await self.transport.close()
        except BaseException as error:
            errors.append(error)
        self._teleop_sessions.clear()
        self._subscriptions.clear()
        if len(errors) == 1:
            raise errors[0]
        if errors:
            raise BaseExceptionGroup("Matic client cleanup failed", errors)

    async def __aenter__(self) -> MaticClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()
