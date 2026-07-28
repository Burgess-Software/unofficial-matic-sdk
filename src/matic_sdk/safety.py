"""Explicit capability for destructive, persistent, or sensitive controls."""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from typing import Final, Self
from uuid import uuid4

MAX_UNSAFE_CAPABILITY_SECONDS: Final = 300.0
UNSAFE_CONFIRMATION: Final = (
    "I understand these controls can damage the robot or its surroundings."
)

_CAPABILITY_SENTINEL = object()


class SafetyError(RuntimeError):
    """Base class for control-safety errors."""


class UnsafeControlRequired(SafetyError):
    """Raised when a hazardous command lacks an active capability."""


class UnsafeControls:
    """Short-lived authority for destructive, persistent, or sensitive commands."""

    __slots__ = ("_armed_until", "_clock", "_identifier", "_is_disarmed")

    def __init__(
        self,
        sentinel: object,
        *,
        armed_until: float,
        clock: Callable[[], float],
    ) -> None:
        if sentinel is not _CAPABILITY_SENTINEL:
            raise TypeError("use UnsafeControls.arm()")
        self._armed_until = armed_until
        self._clock = clock
        self._identifier = str(uuid4())
        self._is_disarmed = False

    @classmethod
    def arm(
        cls,
        confirmation: str,
        *,
        ttl_seconds: float = MAX_UNSAFE_CAPABILITY_SECONDS,
        _clock: Callable[[], float] = time.monotonic,
    ) -> Self:
        """Create a temporary capability after the hazardous-control warning."""

        if confirmation != UNSAFE_CONFIRMATION:
            raise UnsafeControlRequired(
                "unsafe controls require the exact documented confirmation"
            )
        if (
            isinstance(ttl_seconds, bool)
            or not isinstance(ttl_seconds, (int, float))
            or not math.isfinite(ttl_seconds)
            or ttl_seconds <= 0
        ):
            raise ValueError("ttl_seconds must be finite and greater than zero")
        if ttl_seconds > MAX_UNSAFE_CAPABILITY_SECONDS:
            raise ValueError(
                "ttl_seconds exceeds the unsafe capability maximum of "
                f"{MAX_UNSAFE_CAPABILITY_SECONDS:g} seconds"
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
            raise UnsafeControlRequired(
                "unsafe-controls capability is absent or expired"
            )

    def disarm(self) -> None:
        self._is_disarmed = True

    def __enter__(self) -> Self:
        self.assert_active()
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.disarm()


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


__all__ = [
    "MAX_UNSAFE_CAPABILITY_SECONDS",
    "UNSAFE_CONFIRMATION",
    "SafetyError",
    "UnsafeControlRequired",
    "UnsafeControls",
    "require_unsafe_controls",
]
