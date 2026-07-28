# Control behavior and caller responsibility

## Direct command model

Every enabled typed command is a direct call. The SDK has no confirmation
phrases, capability objects, arming step, expiring permissions, command
watchdog, velocity clamp, automatic zero command, or automatic Stop.

The caller is responsible for deciding whether a command is appropriate and
for operating the robot safely. Risk labels in the command registry are
descriptive metadata only; they do not block or authorize execution.

The SDK still requires:

- authenticated TLS with a verified robot identity;
- an explicitly selected compatible protocol version;
- an exact, registered wire codec for the requested command; and
- valid values for the command's proven protobuf field types.

Those requirements prevent sending credentials to an unverified endpoint or
inventing unknown wire data. They are protocol and authentication boundaries,
not operator-safety interlocks.

All 65 currently documented protocol-25 commands have exact codecs. Unknown
commands and future protocol versions remain unavailable because the SDK will
not invent wire data. The exact raw-motor codec is registered and direct; the
SDK applies no device-specific range limits to those setpoints.

## Delivery behavior

Each command method sends one request. An ambiguous command outcome is not
retried automatically. Audit records created through an explicitly supplied
`JsonlAuditLog` contain the local request ID, command kind, timestamps,
protocol version, acknowledgement, and observed effect, but never
authentication material. `MaticClient` uses the no-op audit sink unless a
caller constructs a `CommandExecutor` with another sink.

## Direct joystick control

The joystick protobuf body and Hermes channel envelope are exact. Each call
sends one velocity command:

```python
async def drive(robot):
    await robot.commands.joystick(0.05, 0.0)
    await robot.commands.joystick(0.0, 0.0)
```

The SDK does not repeat the velocity in the background, expire stale input, or
send zero or Stop when the client closes. The caller controls command timing
and any desired stopping behavior. Finite-value and float32-range validation
in the wire codec still applies.

A bounded test harness on 2026-07-28 produced 25 acknowledged
velocity/zero/Stop sends with no failures and a docked-to-ready state
transition. The zero and Stop were explicit test-harness commands, not
automatic SDK behavior.

## Trust boundary

The diagnostic insecure read mode cannot construct a command transport or
carry authentication metadata. The normal command API requires verified TLS.

These are cooperative API boundaries, not a sandbox for untrusted Python code.
Code running in the same process can read credentials available to that
process, import private modules, or implement the protocol itself. Do not give
untrusted plugins access to the SDK process or BotToken.
