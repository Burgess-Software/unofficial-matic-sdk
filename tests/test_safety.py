from __future__ import annotations

import pytest

from matic_sdk.safety import (
    MAX_UNSAFE_CAPABILITY_SECONDS,
    UNSAFE_CONFIRMATION,
    UnsafeControlRequired,
    UnsafeControls,
    require_unsafe_controls,
)


def test_unsafe_capability_requires_exact_phrase_and_expires() -> None:
    with pytest.raises(UnsafeControlRequired):
        UnsafeControls.arm("yes")

    now = [100.0]
    capability = UnsafeControls.arm(
        UNSAFE_CONFIRMATION,
        ttl_seconds=5.0,
        _clock=lambda: now[0],
    )
    require_unsafe_controls(True, capability)
    now[0] = 105.0
    with pytest.raises(UnsafeControlRequired, match="expired"):
        require_unsafe_controls(True, capability)


@pytest.mark.parametrize("ttl_seconds", [True, 0, -1, float("inf"), float("nan")])
def test_unsafe_capability_rejects_invalid_lifetimes(
    ttl_seconds: float | int | bool,
) -> None:
    with pytest.raises(ValueError):
        UnsafeControls.arm(
            UNSAFE_CONFIRMATION,
            ttl_seconds=ttl_seconds,
        )


def test_unsafe_capability_lifetime_has_a_hard_upper_bound() -> None:
    with pytest.raises(ValueError, match="unsafe capability maximum"):
        UnsafeControls.arm(
            UNSAFE_CONFIRMATION,
            ttl_seconds=MAX_UNSAFE_CAPABILITY_SECONDS + 0.001,
        )


def test_unsafe_capability_disarms_on_context_exit() -> None:
    capability = UnsafeControls.arm(UNSAFE_CONFIRMATION)

    with capability:
        assert capability.active

    assert not capability.active
    with pytest.raises(UnsafeControlRequired):
        capability.assert_active()
