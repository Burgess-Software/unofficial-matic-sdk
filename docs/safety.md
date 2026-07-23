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

The joystick protobuf body and channel envelope are exact. Direct
`JoystickCommand` execution is nevertheless blocked so callers cannot bypass
the watchdog-backed `TeleopSession`. Live delivery remains disabled until that
path and its dead-man behavior have been validated. The session design uses:

- latest-value publishing at 20 Hz;
- default linear limit of 0.3 m/s;
- hard limits of 0.77 m/s and 1.444 rad/s;
- a 250 ms input lease;
- a per-send timeout no longer than that lease;
- a tick interval that cannot exceed the lease;
- explicit zero velocity on release;
- best-effort zero velocity followed by Stop on cancellation or disconnect.

This SDK cannot prove that the robot implements its own dead-man timeout. The
operator must keep the robot in view and use a clear test area.
