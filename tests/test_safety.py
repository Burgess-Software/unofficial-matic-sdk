from __future__ import annotations

import asyncio

import pytest

from matic_sdk.models.control import JoystickCommand
from matic_sdk.safety import (
    DEFAULT_INPUT_LEASE_SECONDS,
    DEFAULT_MAX_LINEAR_MPS,
    DEFAULT_TELEOP_RATE_HZ,
    HARD_MAX_ANGULAR_RAD_S,
    HARD_MAX_LINEAR_MPS,
    MAX_MOTION_CAPABILITY_SECONDS,
    MAX_UNSAFE_CAPABILITY_SECONDS,
    MOTION_CONFIRMATION,
    UNSAFE_CONFIRMATION,
    MotionControlRequired,
    MotionControls,
    TeleopDisconnectedError,
    TeleopLimitError,
    TeleopLimits,
    TeleopSession,
    UnsafeControlRequired,
    UnsafeControls,
    require_unsafe_controls,
)


def armed_motion() -> MotionControls:
    return MotionControls.arm(MOTION_CONFIRMATION)


def test_published_teleop_safety_defaults() -> None:
    assert DEFAULT_TELEOP_RATE_HZ == 20.0
    assert DEFAULT_INPUT_LEASE_SECONDS == 0.250
    assert DEFAULT_MAX_LINEAR_MPS == 0.3
    assert HARD_MAX_LINEAR_MPS == 0.77
    assert HARD_MAX_ANGULAR_RAD_S == 1.444


def test_soft_and_hard_velocity_limits_fail_closed() -> None:
    limits = TeleopLimits()
    limits.validate(0.3, 1.444)
    with pytest.raises(TeleopLimitError, match="armed limit"):
        limits.validate(0.3001, 0.0)
    with pytest.raises(TeleopLimitError, match="finite"):
        limits.validate(float("nan"), 0.0)
    with pytest.raises(TeleopLimitError, match="hard limit"):
        TeleopLimits(max_linear_mps=0.78)
    with pytest.raises(TeleopLimitError, match="hard limit"):
        TeleopLimits(max_angular_rad_s=1.445)


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


def test_motion_capability_requires_exact_phrase_and_expires() -> None:
    with pytest.raises(MotionControlRequired):
        MotionControls.arm("yes")

    now = [100.0]
    capability = MotionControls.arm(
        MOTION_CONFIRMATION,
        ttl_seconds=5.0,
        _clock=lambda: now[0],
    )
    capability.assert_active()
    now[0] = 105.0
    with pytest.raises(MotionControlRequired, match="expired"):
        capability.assert_active()


def test_capability_lifetimes_have_hard_upper_bounds() -> None:
    with pytest.raises(ValueError, match="motion capability maximum"):
        MotionControls.arm(
            MOTION_CONFIRMATION,
            ttl_seconds=MAX_MOTION_CAPABILITY_SECONDS + 0.001,
        )
    with pytest.raises(ValueError, match="unsafe capability maximum"):
        UnsafeControls.arm(
            UNSAFE_CONFIRMATION,
            ttl_seconds=MAX_UNSAFE_CAPABILITY_SECONDS + 0.001,
        )


def test_teleop_rate_must_be_fast_enough_for_bounded_lease() -> None:
    async def send_velocity(command: JoystickCommand) -> None:
        del command

    async def send_stop() -> None:
        return None

    with pytest.raises(TeleopLimitError, match="too low"):
        TeleopSession(
            send_velocity,
            send_stop,
            motion_controls=armed_motion(),
            rate_hz=10.0,
            lease_seconds=0.05,
        )
    with pytest.raises(TeleopLimitError, match="hard limit"):
        TeleopSession(
            send_velocity,
            send_stop,
            motion_controls=armed_motion(),
            lease_seconds=DEFAULT_INPUT_LEASE_SECONDS + 0.001,
        )


@pytest.mark.asyncio
async def test_session_publishes_latest_value_at_fixed_rate_and_stops_on_exit() -> None:
    velocities: list[JoystickCommand] = []
    stops = 0

    async def send_velocity(command: JoystickCommand) -> None:
        velocities.append(command)

    async def send_stop() -> None:
        nonlocal stops
        stops += 1

    async with TeleopSession(
        send_velocity, send_stop, motion_controls=armed_motion()
    ) as session:
        await session.set_velocity(0.2, -0.4)
        await asyncio.sleep(0.13)

    nonzero = [
        command
        for command in velocities
        if command.linear_mps != 0.0 or command.angular_rad_s != 0.0
    ]
    assert len(nonzero) >= 2
    assert all(command == JoystickCommand(0.2, -0.4) for command in nonzero)
    assert velocities[-1] == JoystickCommand(0.0, 0.0)
    assert stops == 1


@pytest.mark.asyncio
async def test_release_sends_zero_immediately() -> None:
    velocities: list[JoystickCommand] = []

    async def send_velocity(command: JoystickCommand) -> None:
        velocities.append(command)

    async def send_stop() -> None:
        return None

    async with TeleopSession(
        send_velocity, send_stop, motion_controls=armed_motion()
    ) as session:
        await session.set_velocity(0.1, 0.2)
        await asyncio.sleep(0.06)
        await session.release()
        assert velocities[-1] == JoystickCommand(0.0, 0.0)


@pytest.mark.asyncio
async def test_deadman_lease_replaces_stale_input_with_zero() -> None:
    velocities: list[JoystickCommand] = []

    async def send_velocity(command: JoystickCommand) -> None:
        velocities.append(command)

    async def send_stop() -> None:
        return None

    async with TeleopSession(
        send_velocity,
        send_stop,
        motion_controls=armed_motion(),
        rate_hz=100.0,
        lease_seconds=0.04,
    ) as session:
        await session.set_velocity(0.1, 0.0)
        await asyncio.sleep(0.075)
        assert JoystickCommand(0.1, 0.0) in velocities
        first_nonzero = velocities.index(JoystickCommand(0.1, 0.0))
        assert JoystickCommand(0.0, 0.0) in velocities[first_nonzero + 1 :]


@pytest.mark.asyncio
async def test_disconnect_attempts_zero_and_stop_and_surfaces_failure() -> None:
    attempts: list[JoystickCommand] = []
    stops = 0

    async def send_velocity(command: JoystickCommand) -> None:
        attempts.append(command)
        if len(attempts) == 2:
            raise ConnectionError("synthetic disconnect")

    async def send_stop() -> None:
        nonlocal stops
        stops += 1

    with pytest.raises(TeleopDisconnectedError):
        async with TeleopSession(
            send_velocity,
            send_stop,
            motion_controls=armed_motion(),
            rate_hz=100.0,
        ) as session:
            await session.set_velocity(0.1, 0.0)
            await asyncio.sleep(0.06)

    assert attempts[-1] == JoystickCommand(0.0, 0.0)
    assert stops == 1


@pytest.mark.asyncio
async def test_hung_send_times_out_and_attempts_emergency_stop() -> None:
    never = asyncio.Event()
    stops = 0

    async def send_velocity(command: JoystickCommand) -> None:
        del command
        await never.wait()

    async def send_stop() -> None:
        nonlocal stops
        stops += 1

    session = TeleopSession(
        send_velocity,
        send_stop,
        motion_controls=armed_motion(),
        rate_hz=100.0,
        lease_seconds=0.03,
        shutdown_timeout_seconds=0.05,
    )
    await session.__aenter__()

    with pytest.raises(TeleopDisconnectedError):
        await asyncio.wait_for(session.wait_closed(), timeout=0.5)
    with pytest.raises(TeleopDisconnectedError):
        await session.close()

    assert isinstance(session.failure, TimeoutError)
    assert stops == 1


@pytest.mark.asyncio
async def test_session_cannot_be_used_before_or_after_arming() -> None:
    async def send_velocity(command: JoystickCommand) -> None:
        del command

    async def send_stop() -> None:
        return None

    session = TeleopSession(
        send_velocity,
        send_stop,
        motion_controls=armed_motion(),
    )
    with pytest.raises(RuntimeError, match="not armed"):
        await session.set_velocity(0.0, 0.0)
    async with session:
        pass
    with pytest.raises(RuntimeError, match="not armed"):
        await session.set_velocity(0.0, 0.0)
    with pytest.raises(RuntimeError, match="cannot be reused"):
        async with session:
            pass


@pytest.mark.asyncio
async def test_closing_never_entered_session_sends_nothing() -> None:
    velocities: list[JoystickCommand] = []
    stops = 0

    async def send_velocity(command: JoystickCommand) -> None:
        velocities.append(command)

    async def send_stop() -> None:
        nonlocal stops
        stops += 1

    session = TeleopSession(
        send_velocity,
        send_stop,
        motion_controls=armed_motion(),
    )

    await session.close()
    await session.close()

    assert velocities == []
    assert stops == 0
    with pytest.raises(RuntimeError, match="cannot be reused"):
        async with session:
            pass
