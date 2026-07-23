"""Guarded command execution and redacted command auditing."""

from __future__ import annotations

import asyncio
import json
import os
import stat
import threading
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from matic_sdk.models.control import (
    CommandReceipt,
    ControlCommand,
    JoystickCommand,
    ObservedEffect,
    ObservedEffectStatus,
    SettingAction,
    SettingsCommand,
    TransportAcknowledgement,
    UserAction,
    UserCommand,
    utc_now,
)
from matic_sdk.protocol.commands import (
    COMMAND_REGISTRY,
    CommandRegistry,
    EncodedCommand,
    ensure_protocol_compatible,
)
from matic_sdk.safety import (
    MotionControls,
    UnsafeControls,
    require_motion_controls,
    require_unsafe_controls,
)

_REDACTED = "[REDACTED]"
_SENSITIVE_KEY_PARTS = (
    "authorization",
    "credential",
    "passphrase",
    "password",
    "payload",
    "secret",
    "ssid",
    "token",
    "wire",
)


class CommandExecutionError(RuntimeError):
    """Base class for errors at the guarded execution layer."""


class UnverifiedCommandTransport(CommandExecutionError):
    """Raised when a command is attempted without verified TLS identity."""


class CommandOutcomeUnknown(CommandExecutionError):
    """Transport failed after execution began; callers must not auto-retry."""


class DirectJoystickUnsupported(CommandExecutionError):
    """Joystick intents must use the watchdog-backed TeleopSession path."""


class CommandTransport(Protocol):
    """Minimal transport seam consumed by the guarded command executor."""

    async def send_channel(
        self,
        command: EncodedCommand,
    ) -> TransportAcknowledgement:
        """Send one encoded request without automatic retry."""


class CommandAuditLog(Protocol):
    """Synchronous append-only audit sink."""

    def record(self, event: str, **values: object) -> None:
        """Record a redacted event before returning."""


class NullAuditLog:
    """Explicit no-op audit sink for callers that do not configure a file."""

    def record(self, event: str, **values: object) -> None:
        del event, values


class JsonlAuditLog:
    """Append-only JSONL command log kept at mode ``0600``.

    Command arguments are intentionally excluded by :class:`CommandExecutor`.
    Recursive key-based redaction provides a second boundary for direct audit
    calls and future metadata additions.
    """

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)

    def record(self, event: str, **values: object) -> None:
        record = {
            "timestamp": utc_now().isoformat(),
            "event": event,
            **values,
        }
        redacted = _redact(record)
        line = (
            json.dumps(redacted, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        flags = os.O_APPEND | os.O_CREAT | os.O_WRONLY
        flags |= getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        with self._lock:
            descriptor = os.open(self.path, flags, 0o600)
            try:
                file_stat = os.fstat(descriptor)
                if not stat.S_ISREG(file_stat.st_mode):
                    raise OSError("audit path is not a regular file")
                os.fchmod(descriptor, 0o600)
                view = memoryview(line)
                while view:
                    written = os.write(descriptor, view)
                    view = view[written:]
            finally:
                os.close(descriptor)


def _redact(value: object, *, key: str = "") -> object:
    lowered_key = key.casefold()
    if any(part in lowered_key for part in _SENSITIVE_KEY_PARTS):
        return _REDACTED
    if isinstance(value, Mapping):
        return {
            str(item_key): _redact(item, key=str(item_key))
            for item_key, item in value.items()
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_redact(item) for item in value]
    if isinstance(value, bytes):
        return _REDACTED
    if isinstance(value, datetime):
        return value.isoformat()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return type(value).__name__


ObservationCallback = Callable[
    [ControlCommand, TransportAcknowledgement],
    Awaitable[ObservedEffect | None],
]


class CommandExecutor:
    """Apply protocol, TLS, risk, codec, audit, and no-retry boundaries."""

    def __init__(
        self,
        transport: CommandTransport,
        *,
        protocol_version: object = None,
        tls_identity_verified: bool,
        registry: CommandRegistry = COMMAND_REGISTRY,
        audit_log: CommandAuditLog | None = None,
    ) -> None:
        self._transport = transport
        self._protocol_version = protocol_version
        self._tls_identity_verified = tls_identity_verified
        self._registry = registry
        self._audit = audit_log if audit_log is not None else NullAuditLog()

    @property
    def protocol_version(self) -> object:
        return self._protocol_version

    async def execute(
        self,
        command: ControlCommand,
        *,
        motion_controls: MotionControls | None = None,
        unsafe_controls: UnsafeControls | None = None,
        observe: ObservationCallback | None = None,
    ) -> CommandReceipt:
        """Send exactly once and return delivery/effect results separately."""

        spec = self._registry.spec_for(command)
        command_id = str(uuid4())
        issued_at = utc_now()
        self._audit.record(
            "command.attempt",
            command_id=command_id,
            command=spec.key,
            family=spec.family.value,
            risk=spec.risk.value,
            protocol_version=self._protocol_version,
        )
        try:
            protocol_version = ensure_protocol_compatible(self._protocol_version)
            if not self._tls_identity_verified:
                raise UnverifiedCommandTransport(
                    "commands require verified robot TLS identity"
                )
            require_motion_controls(
                spec.requires_motion_controls,
                motion_controls,
            )
            require_unsafe_controls(
                spec.requires_unsafe_controls,
                unsafe_controls,
            )
            if isinstance(command, JoystickCommand):
                raise DirectJoystickUnsupported(
                    "JoystickCommand cannot be sent directly; use TeleopSession "
                    "so hard limits, input leases, and emergency Stop are enforced"
                )
            encoded = self._registry.encode(
                command,
                protocol_version=protocol_version,
            )
            try:
                acknowledgement = await self._transport.send_channel(encoded)
            except asyncio.CancelledError as error:
                raise CommandOutcomeUnknown(
                    "command execution was cancelled after transport began; the "
                    "outcome is unknown and the command must not be retried"
                ) from error
            except Exception as error:
                raise CommandOutcomeUnknown(
                    "command transport failed; outcome is unknown and the command "
                    "must not be retried automatically"
                ) from error
            if not isinstance(acknowledgement, TransportAcknowledgement):
                raise TypeError(
                    "command transport must return TransportAcknowledgement"
                )

            observed_effect: ObservedEffect | None = None
            if observe is not None:
                try:
                    observed_effect = await observe(command, acknowledgement)
                except Exception as error:
                    observed_effect = ObservedEffect(
                        status=ObservedEffectStatus.NOT_CHECKED,
                        detail=f"observer failed: {type(error).__name__}",
                    )

            receipt = CommandReceipt(
                command_key=spec.key,
                protocol_version=protocol_version,
                transport=acknowledgement,
                observed_effect=observed_effect,
                command_id=command_id,
                issued_at=issued_at,
            )
            try:
                self._audit.record(
                    "command.complete",
                    command_id=command_id,
                    command=spec.key,
                    transport_status=acknowledgement.status.value,
                    observed_effect=(
                        observed_effect.status.value
                        if observed_effect is not None
                        else ObservedEffectStatus.NOT_CHECKED.value
                    ),
                )
            except Exception:
                # A local audit sink cannot turn a delivered command into an
                # apparent failure that an operator might retry.
                pass
            return receipt
        except BaseException as error:
            try:
                self._audit.record(
                    "command.failed",
                    command_id=command_id,
                    command=spec.key,
                    error_type=type(error).__name__,
                )
            except Exception:
                # Preserve the real pre-send or unknown-outcome exception.
                pass
            raise

    async def stop(self) -> CommandReceipt:
        """Convenience wrapper for the stationary Stop intent."""

        return await self.execute(UserCommand(UserAction.STOP))

    async def stay_put(self) -> CommandReceipt:
        """Ask the robot to remain stationary at its current location."""

        return await self.execute(UserCommand(UserAction.STAY_PUT))

    async def pause(self) -> CommandReceipt:
        return await self.execute(UserCommand(UserAction.PAUSE))

    async def resume(self, *, motion_controls: MotionControls) -> CommandReceipt:
        return await self.execute(
            UserCommand(UserAction.RESUME),
            motion_controls=motion_controls,
        )

    async def dock(self, *, motion_controls: MotionControls) -> CommandReceipt:
        return await self.execute(
            UserCommand(UserAction.DOCK),
            motion_controls=motion_controls,
        )

    async def set_binary_setting(
        self,
        action: SettingAction,
        enabled: bool,
        *,
        unsafe_controls: UnsafeControls,
    ) -> CommandReceipt:
        """Set an exact, allowlisted boolean preference."""

        return await self.execute(
            SettingsCommand(action, enabled),
            unsafe_controls=unsafe_controls,
        )


__all__ = [
    "CommandAuditLog",
    "CommandExecutionError",
    "CommandExecutor",
    "CommandOutcomeUnknown",
    "CommandTransport",
    "DirectJoystickUnsupported",
    "JsonlAuditLog",
    "NullAuditLog",
    "ObservationCallback",
    "UnverifiedCommandTransport",
]
