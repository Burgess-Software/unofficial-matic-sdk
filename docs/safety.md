# Control safety model

## Trust requirements

Commands require authenticated TLS with either the Matician trust chain and
robot identity checks or an explicitly confirmed per-device DER-certificate
SHA-256 pin. The diagnostic insecure read mode cannot construct a command
transport or carry authentication metadata; it is limited to unauthenticated
discovery.

These checks are cooperative API guardrails, not a sandbox for untrusted Python
code. `MaticClient` keeps its authenticated transport private, and the
documented top-level API has no arbitrary-payload method. Code running in the
same process can still import private implementation modules, read credentials
available to that process, or implement the protocol itself. Do not give
untrusted plugins access to the SDK process or BotToken.

## Risk classes and gates

The command ledger classifies commands by consequence:

- **Stationary:** Stop, StayPut, and Pause.
- **Motion:** Resume, Dock, exploration, navigation, coverage, manual cleaning,
  and joystick control.
- **Unsafe:** raw actuation; network or device mutation; map edits; persistent
  settings and schedules; sensitive media, diagnostics, and support access;
  updater, reboot, and shutdown.

Stationary and motion commands have no capability or confirmation gate. A
method call sends immediately once TLS identity, protocol version, and the
command's proven codec have been validated.

Enabled unsafe commands still require a short-lived `UnsafeControls`
capability created with this exact warning confirmation:

```text
I understand these controls can damage the robot or its surroundings.
```

Trace calibration is classified as raw actuation and therefore requires
`UnsafeControls`. Raw-motor encoding has no registered codec because
hardware-safe ranges are not proven.
No raw-actuation, destructive, network-changing, update, reboot, or shutdown
command has been live-tested by this SDK.

The [command verification ledger](command-verification.md) records codec proof
and live-delivery evidence separately from risk. Joystick, navigation, Dock,
and normal coverage have bounded live-delivery evidence. Navigation-and-wait,
navigation-and-explore, reprioritization, and stain mode do not.

Commands with an unknown outcome are not retried automatically. Audit records
created by an explicitly supplied `JsonlAuditLog` contain the local request ID,
command kind, timestamps, protocol version, acknowledgement, and observed
effect, but never authorization material. `MaticClient` currently uses the
no-op audit sink and does not write a log automatically.

## Direct joystick control

The joystick protobuf body and Hermes channel envelope are exact. Call
`robot.commands.joystick()` to send one velocity command:

```python
async def drive(robot):
    await robot.commands.joystick(0.05, 0.0)
    await robot.commands.joystick(0.0, 0.0)
```

The SDK does not add a background publishing loop, velocity clamp, expiring
input lease, dead-man timer, automatic zero command, or automatic Stop.
Finite-value and float32-range validation in the wire codec still applies.
The caller controls command timing and decides whether to send zero or Stop.

A bounded test harness on 2026-07-28 produced 25 acknowledged
velocity/zero/Stop sends with no failures and a docked-to-ready state
transition. The zero and Stop were explicit test-harness commands, not
automatic SDK behavior. This establishes live delivery and an observed robot
state effect; it does not establish precise distance, motor behavior, or a
robot-side stale-command timeout.
