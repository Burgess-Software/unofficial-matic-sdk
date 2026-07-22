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
- **Motion:** Resume, Dock, exploration, navigation, coverage, manual cleaning
  and joystick control. These require a short-lived `MotionControls` capability
  created with the exact documented clear-area confirmation.
- **Unsafe:** raw motors, network mutation, destructive map changes, updater,
  reboot, shutdown, support access, and persistent diagnostic configuration.
  These require a short-lived unsafe capability created with the exact
  documented warning confirmation.

Commands with an unknown outcome are not retried automatically. Audit records
contain the local request ID, command kind, timestamps, protocol version,
acknowledgement, and observed effect, but never authorization material.

## Teleoperation

The typed joystick session mirrors the observed official-client units and adds
a local watchdog. It will become sendable only after the channel envelope is
proven:

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
