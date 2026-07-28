"""Safety capabilities and the teleoperation dead-man loop."""

from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from typing import ClassVar, Final, Self
from uuid import uuid4

from matic_sdk.models.control import JoystickCommand

DEFAULT_MAX_LINEAR_MPS: Final = 0.3
HARD_MAX_LINEAR_MPS: Final = 0.77
HARD_MAX_ANGULAR_RAD_S: Final = 1.444
DEFAULT_TELEOP_RATE_HZ: Final = 20.0
DEFAULT_INPUT_LEASE_SECONDS: Final = 0.250
MAX_MOTION_CAPABILITY_SECONDS: Final = 60.0
MAX_UNSAFE_CAPABILITY_SECONDS: Final = 300.0
UNSAFE_CONFIRMATION: Final = (
    "I understand these controls can damage the robot or its surroundings."
)
MOTION_CONFIRMATION: Final = "I have cleared the area and will keep the robot in view."

_CAPABILITY_SENTINEL = object()


class SafetyError(RuntimeError):
    """Base class for control-safety errors."""


class UnsafeControlRequired(SafetyError):
    """Raised when a hazardous command lacks an active capability."""


class MotionControlRequired(SafetyError):
    """Raised when a movement command lacks an active capability."""


class TeleopDisconnectedError(SafetyError):
    """Raised after the teleoperation sender fails or disconnects."""


class TeleopLimitError(ValueError):
    """Raised when a requested or configured speed exceeds a safety cap."""


class _TimedCapability:
    """Internal common implementation for explicit, expiring capabilities."""

    __slots__ = ("_armed_until", "_clock", "_identifier", "_is_disarmed")
    confirmation: ClassVar[str]
    default_ttl_seconds: ClassVar[float]
    error_type: ClassVar[type[SafetyError]]
    label: ClassVar[str]
    max_ttl_seconds: ClassVar[float]

    def __init__(
        self,
        sentinel: object,
        *,
        armed_until: float,
        clock: Callable[[], float],
    ) -> None:
        if sentinel is not _CAPABILITY_SENTINEL:
            raise TypeError(f"use {type(self).__name__}.arm()")
        self._armed_until = armed_until
        self._clock = clock
        self._identifier = str(uuid4())
        self._is_disarmed = False

    @classmethod
    def arm(
        cls,
        confirmation: str,
        *,
        ttl_seconds: float | None = None,
        _clock: Callable[[], float] = time.monotonic,
    ) -> Self:
        """Create a temporary capability after an exact warning confirmation."""

        if confirmation != cls.confirmation:
            raise cls.error_type(
                f"{cls.label} controls require the exact documented confirmation"
            )
        if ttl_seconds is None:
            ttl_seconds = cls.default_ttl_seconds
        if (
            isinstance(ttl_seconds, bool)
            or not isinstance(ttl_seconds, (int, float))
            or not math.isfinite(ttl_seconds)
            or ttl_seconds <= 0
        ):
            raise ValueError("ttl_seconds must be finite and greater than zero")
        if ttl_seconds > cls.max_ttl_seconds:
            raise ValueError(
                f"ttl_seconds exceeds the {cls.label} capability maximum of "
                f"{cls.max_ttl_seconds:g} seconds"
            )
        return cls(
            _CAPABILITY_SENTINEL,
            armed_until=_clock() + ttl_seconds,
            clock=_clock,
        )

    @property
    def identifier(self) -> str:
        """Opaque audit identifier; it conveys no authority by itself."""

        return self._identifier

    @property
    def active(self) -> bool:
        return not self._is_disarmed and self._clock() < self._armed_until

    def assert_active(self) -> None:
        if not self.active:
            raise self.error_type(
                f"{self.label}-controls capability is absent or expired"
            )

    def disarm(self) -> None:
        self._is_disarmed = True

    def __enter__(self) -> Self:
        self.assert_active()
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.disarm()


class MotionControls(_TimedCapability):
    """Short-lived authority for commands that can move the robot."""

    confirmation = MOTION_CONFIRMATION
    default_ttl_seconds = MAX_MOTION_CAPABILITY_SECONDS
    error_type = MotionControlRequired
    label = "motion"
    max_ttl_seconds = MAX_MOTION_CAPABILITY_SECONDS


class UnsafeControls(_TimedCapability):
    """Short-lived authority for persistent or hazardous commands.

    This capability is separate from motion arming. A command that is both
    mobile and hazardous must receive both capabilities.
    """

    confirmation = UNSAFE_CONFIRMATION
    default_ttl_seconds = MAX_UNSAFE_CAPABILITY_SECONDS
    error_type = UnsafeControlRequired
    label = "unsafe"
    max_ttl_seconds = MAX_UNSAFE_CAPABILITY_SECONDS


def require_motion_controls(
    required: bool,
    capability: MotionControls | None,
) -> None:
    """Fail before encoding when a command can move the robot."""

    if not required:
        return
    if capability is None:
        raise MotionControlRequired(
            "this command requires an active MotionControls capability"
        )
    capability.assert_active()


def require_unsafe_controls(
    required: bool,
    capability: UnsafeControls | None,
) -> None:
    """Fail before encoding when a command needs an unsafe capability."""

    if not required:
        return
    if capability is None:
        raise UnsafeControlRequired(
            "this command requires an active UnsafeControls capability"
        )
    capability.assert_active()


@dataclass(frozen=True, slots=True)
class TeleopLimits:
    """Configured soft limits, each bounded by the library hard limit."""

    max_linear_mps: float = DEFAULT_MAX_LINEAR_MPS
    max_angular_rad_s: float = HARD_MAX_ANGULAR_RAD_S

    def __post_init__(self) -> None:
        _validate_positive_limit(
            "max_linear_mps",
            self.max_linear_mps,
            HARD_MAX_LINEAR_MPS,
        )
        _validate_positive_limit(
            "max_angular_rad_s",
            self.max_angular_rad_s,
            HARD_MAX_ANGULAR_RAD_S,
        )

    def validate(self, linear_mps: float, angular_rad_s: float) -> None:
        _validate_velocity("linear_mps", linear_mps, self.max_linear_mps)
        _validate_velocity("angular_rad_s", angular_rad_s, self.max_angular_rad_s)


def _validate_positive_limit(name: str, value: float, hard_limit: float) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TeleopLimitError(f"{name} must be a number")
    if not math.isfinite(value) or value <= 0:
        raise TeleopLimitError(f"{name} must be finite and greater than zero")
    if value > hard_limit:
        raise TeleopLimitError(f"{name} exceeds the hard limit of {hard_limit}")


def _validate_velocity(name: str, value: float, limit: float) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TeleopLimitError(f"{name} must be a number")
    if not math.isfinite(value):
        raise TeleopLimitError(f"{name} must be finite")
    if abs(value) > limit:
        raise TeleopLimitError(f"{name} exceeds the armed limit of {limit}")


VelocitySender = Callable[[JoystickCommand], Awaitable[object]]
StopSender = Callable[[], Awaitable[object]]


class TeleopSession:
    """An explicitly armed 20 Hz latest-value teleoperation session.

    Non-zero input expires after a 250 ms lease by default.  ``release()``
    transmits zero immediately.  Leaving the context or losing the sender
    makes a best-effort zero transmission followed by a Stop command.
    """

    def __init__(
        self,
        send_velocity: VelocitySender,
        send_stop: StopSender,
        *,
        motion_controls: MotionControls,
        max_linear_mps: float = DEFAULT_MAX_LINEAR_MPS,
        max_angular_rad_s: float = HARD_MAX_ANGULAR_RAD_S,
        rate_hz: float = DEFAULT_TELEOP_RATE_HZ,
        lease_seconds: float = DEFAULT_INPUT_LEASE_SECONDS,
        shutdown_timeout_seconds: float = 1.0,
        _clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._send_velocity_callback = send_velocity
        self._send_stop_callback = send_stop
        self._motion_controls = motion_controls
        self._limits = TeleopLimits(max_linear_mps, max_angular_rad_s)
        _validate_positive_limit("rate_hz", rate_hz, 200.0)
        _validate_positive_limit(
            "lease_seconds",
            lease_seconds,
            DEFAULT_INPUT_LEASE_SECONDS,
        )
        _validate_positive_limit(
            "shutdown_timeout_seconds",
            shutdown_timeout_seconds,
            10.0,
        )
        self._rate_hz = float(rate_hz)
        self._lease_seconds = float(lease_seconds)
        if 1.0 / self._rate_hz > self._lease_seconds:
            raise TeleopLimitError(
                "rate_hz is too low to enforce the configured input lease"
            )
        self._shutdown_timeout_seconds = float(shutdown_timeout_seconds)
        self._clock = _clock
        self._latest = JoystickCommand(0.0, 0.0)
        self._last_input_at: float | None = None
        self._entered = False
        self._active = False
        self._closed = False
        self._failure: BaseException | None = None
        self._worker: asyncio.Task[None] | None = None
        self._state_lock = asyncio.Lock()
        self._send_lock = asyncio.Lock()
        self._shutdown_lock = asyncio.Lock()
        self._shutdown_attempted = False
        self._closed_event = asyncio.Event()

    @property
    def limits(self) -> TeleopLimits:
        return self._limits

    @property
    def failure(self) -> BaseException | None:
        return self._failure

    async def __aenter__(self) -> TeleopSession:
        if self._active or self._closed:
            raise RuntimeError("teleoperation sessions cannot be reused")
        self._motion_controls.assert_active()
        self._entered = True
        self._active = True
        self._worker = asyncio.create_task(
            self._run(),
            name="matic-teleop-deadman",
        )
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object,
    ) -> None:
        try:
            await self.close()
        except TeleopDisconnectedError:
            if exc is None:
                raise

    async def set_velocity(self, linear_mps: float, angular_rad_s: float) -> None:
        """Replace the latest input; the sender publishes it on the next tick."""

        self._ensure_available()
        self._limits.validate(linear_mps, angular_rad_s)
        async with self._state_lock:
            self._ensure_available()
            self._latest = JoystickCommand(float(linear_mps), float(angular_rad_s))
            self._last_input_at = self._clock()

    async def release(self) -> None:
        """Clear the lease and transmit a zero velocity immediately."""

        self._ensure_available()
        try:
            await self._send_zero()
        except asyncio.CancelledError:
            await self._shielded_shutdown()
            raise
        except Exception as error:
            await self._record_disconnect(error)
            raise TeleopDisconnectedError(
                "teleoperation sender disconnected"
            ) from error

    async def close(self) -> None:
        """Stop the worker and perform the best-effort zero/Stop sequence."""

        if self._closed:
            if self._failure is not None:
                raise TeleopDisconnectedError(
                    "teleoperation sender disconnected"
                ) from self._failure
            return
        if not self._entered:
            self._closed = True
            self._closed_event.set()
            return
        self._active = False
        worker = self._worker
        if worker is not None and worker is not asyncio.current_task():
            worker.cancel()
            with suppress(asyncio.CancelledError):
                await worker
        await self._shielded_shutdown()
        self._closed = True
        self._closed_event.set()
        if self._failure is not None:
            raise TeleopDisconnectedError(
                "teleoperation sender disconnected"
            ) from self._failure

    async def wait_closed(self) -> None:
        """Wait for a background disconnect and surface its cause."""

        await self._closed_event.wait()
        if self._failure is not None:
            raise TeleopDisconnectedError(
                "teleoperation sender disconnected"
            ) from self._failure

    def _ensure_available(self) -> None:
        if not self._active or self._closed:
            raise RuntimeError("teleoperation is not armed")
        self._motion_controls.assert_active()
        if self._failure is not None:
            raise TeleopDisconnectedError(
                "teleoperation sender disconnected"
            ) from self._failure

    async def _run(self) -> None:
        interval = 1.0 / self._rate_hz
        try:
            while self._active:
                started_at = self._clock()
                try:
                    await self._send_tick(started_at)
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    await self._record_disconnect(error)
                    return
                elapsed = self._clock() - started_at
                await asyncio.sleep(max(0.0, interval - elapsed))
        except asyncio.CancelledError:
            raise
        finally:
            if self._failure is not None:
                self._closed = True
                self._closed_event.set()

    async def _command_for_tick(self, now: float) -> JoystickCommand:
        async with self._state_lock:
            if (
                self._last_input_at is not None
                and now - self._last_input_at >= self._lease_seconds
            ):
                self._latest = JoystickCommand(0.0, 0.0)
                self._last_input_at = None
            return self._latest

    async def _send_tick(self, now: float) -> None:
        self._motion_controls.assert_active()
        async with self._send_lock:
            command = await self._command_for_tick(now)
            await self._send_velocity(command)

    async def _send_velocity(self, command: JoystickCommand) -> None:
        await asyncio.wait_for(
            self._send_velocity_callback(command),
            timeout=self._lease_seconds,
        )

    async def _send_zero(self) -> None:
        zero = JoystickCommand(0.0, 0.0)
        async with self._send_lock:
            async with self._state_lock:
                self._latest = zero
                self._last_input_at = None
            await self._send_velocity(zero)

    async def _record_disconnect(self, error: BaseException) -> None:
        if self._failure is None:
            self._failure = error
        self._active = False
        await self._shielded_shutdown()

    async def _shielded_shutdown(self) -> None:
        shutdown = asyncio.create_task(
            self._best_effort_shutdown(),
            name="matic-teleop-emergency-stop",
        )
        await asyncio.shield(shutdown)

    async def _best_effort_shutdown(self) -> None:
        async with self._shutdown_lock:
            if self._shutdown_attempted:
                return
            self._shutdown_attempted = True
            for operation in (self._send_zero, self._send_stop_callback):
                with suppress(Exception):
                    await asyncio.wait_for(
                        operation(),
                        timeout=self._shutdown_timeout_seconds,
                    )


__all__ = [
    "DEFAULT_INPUT_LEASE_SECONDS",
    "DEFAULT_MAX_LINEAR_MPS",
    "DEFAULT_TELEOP_RATE_HZ",
    "HARD_MAX_ANGULAR_RAD_S",
    "HARD_MAX_LINEAR_MPS",
    "MOTION_CONFIRMATION",
    "UNSAFE_CONFIRMATION",
    "MotionControlRequired",
    "MotionControls",
    "SafetyError",
    "TeleopDisconnectedError",
    "TeleopLimitError",
    "TeleopLimits",
    "TeleopSession",
    "UnsafeControlRequired",
    "UnsafeControls",
    "require_motion_controls",
    "require_unsafe_controls",
]
