# Control safety model

## Trust requirements

Commands require authenticated TLS with either the Matician trust chain and
robot identity checks or an explicitly confirmed per-device DER-certificate
SHA-256 pin.
The diagnostic insecure read mode cannot construct a command transport.
It also cannot carry authentication metadata; it is limited to unauthenticated
discovery.

## Risk classes

- **Stationary:** Stop, StayPut and Pause.
- **Motion:** Resume, Dock, exploration, navigation, coverage, manual cleaning,
  trace calibration, and joystick control. These require a short-lived
  `MotionControls` capability created with this exact clear-area confirmation:

  ```text
  I have cleared the area and will keep the robot in view.
  ```

- **Unsafe:** raw motors; network or device mutation; map edits; persistent
  settings and schedules; sensitive media, diagnostics, and support access;
  updater, reboot, and shutdown. These require a short-lived `UnsafeControls`
  capability created with this exact warning confirmation:

  ```text
  I understand these controls can damage the robot or its surroundings.
  ```

Capabilities authorize an informed, time-bounded attempt; they do not mean the
command was live-tested or that its physical effect is known. The
[command verification ledger](command-verification.md) records those facts
separately. Trace calibration requires both motion and unsafe capabilities.
Raw-motor encoding has no registered codec because hardware-safe ranges are not
proven. No motion-changing, raw-actuation, destructive, network-changing,
update, reboot, or shutdown command has been live-tested by this SDK.

Commands with an unknown outcome are not retried automatically. Audit records
contain the local request ID, command kind, timestamps, protocol version,
acknowledgement, and observed effect, but never authorization material.

## Teleoperation

The joystick protobuf body and channel envelope are exact. The SDK supports
joystick sending only through `MaticClient.teleop()`, which constructs an
executor-backed `TeleopSession`. Direct
`CommandExecutor.execute(JoystickCommand(...))` execution remains blocked so a
caller cannot bypass the watchdog, velocity limits, expiring input lease, or
emergency Stop sequence.

Create a short-lived motion capability, enter the returned session, and refresh
the desired velocity while the operator is actively controlling the robot:

```python
from matic_sdk import MotionControls
from matic_sdk.safety import MOTION_CONFIRMATION


async def drive(robot):
    motion = MotionControls.arm(MOTION_CONFIRMATION, ttl_seconds=30)
    try:
        async with robot.teleop(motion_controls=motion) as teleop:
            await teleop.set_velocity(0.05, 0.0)
            # Refresh set_velocity() while input remains active.
            await teleop.release()
    finally:
        motion.disarm()
```

Every published velocity still passes through the normal protocol-version, TLS,
motion-capability, codec, audit, and no-retry checks. Closing the client closes
its teleoperation sessions before closing the transport. The session design
uses:

- latest-value publishing at 20 Hz;
- default linear limit of 0.3 m/s;
- hard limits of 0.77 m/s and 1.444 rad/s;
- a 250 ms input lease;
- a per-send timeout no longer than that lease;
- a tick interval that cannot exceed the lease;
- explicit zero velocity on release;
- best-effort zero velocity followed by Stop on cancellation or disconnect.

The SDK path and its local dead-man behavior are covered by automated tests, but
joystick delivery and physical motion have not yet been live-validated against
the robot. A Hermes acknowledgement would prove RPC delivery, not physical
effect.

This SDK cannot prove that the robot implements its own dead-man timeout. The
operator must keep the robot in view and use a clear test area.
