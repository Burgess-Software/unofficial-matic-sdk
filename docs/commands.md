# Command inventory

This is the decoded official-client command inventory. Listing a command does
not imply that its protobuf codec or live behavior has been verified.

## Operator commands

- Stop, StayPut, Pause, Resume and Dock.
- Explore, re-explore, navigation, navigation-and-explore and session resume.
- Normal coverage, stain coverage, reprioritization and manual cleaning.
- Joystick linear/angular velocity control.

## Maps and environment

- Build or rebuild a room partition.
- Edit room boundaries and names.
- Add or edit no-go, drive-only and stair zones.
- Floor metadata, semantic overrides and sink-summon locations.
- Clear map persistence or RGB weights.

## Device administration

- Wi-Fi scan/connect/forget, robot rename and discoverability.
- Child lock, pet-waste avoidance, voice, automatic voice recording, Matter
  pairing and preview-release settings.
- Schedules, suggested schedules and sink-summon schedules.
- Recording state, rolling-buffer configuration, save and deletion confirmation.
- Telemetry uploader configuration and managed support-tunnel permission.
- Mop-roll replacement, calibration clear, update, reboot and shutdown.

## Mechanical control

The client has optional direct setpoints for vacuum RPM, sweeper duty, mopper
duty, cleaning-head position and side-brush duty. These bypass normal cleaning
intent. Their exact codec is registered directly, applies no device-specific
range limits, and has not been exercised live.

## Codec rule

Every recovered command has a `CommandSpec` describing its family, risk,
available channel evidence, and verification state. Compatibility is currently
verified against protocol 25. A decoded name or UniFFI enum discriminant is not
accepted as a protobuf field number. Unknown commands still raise an error
before opening a mutating stream. A different positive protocol version uses
the verified protocol-25 codec with an `UnverifiedProtocolVersionWarning`,
allowing compatible firmware to proceed without claiming it was verified.

## Verification state

The registry currently contains 65 intents. All 65 have exact protobuf formats
and Hermes targets, carry `wire_verified` evidence, and have registered codecs
for protocol 25. The raw-motor codec is included and applies no device-specific
range limits. See the
[complete command ledger](command-verification.md) for every key, target, risk
label, and live-test state.

Wire verification and live delivery are separate facts. A wire-verified format
means its inner protobuf, target, and `ChannelRequest` envelope are exact; it
does not claim that this SDK has sent the command or observed its effect. All
enabled typed commands are direct calls with no capability or confirmation
gate. Risk labels are informational. A
`robot.commands.joystick(linear_mps, angular_rad_s)` call sends exactly one
velocity command without a background watchdog or automatic Stop.

`ChannelRequest` field 1 contains the channel name and field 2 contains the
inner payload. For a valid empty protobuf command, field 2 is canonically
omitted rather than encoded as a zero-length bytes field. A successful unary
RPC returns one `ChannelResponse`; its bytes field may itself be absent/default
or populated.

Ten command intents have live delivery evidence. Six were delivered once on
2026-07-22: Stop, StayPut, Pause, child lock, pet-waste avoidance, and voice.
Stop used an initial one-shot
check. A separate bounded verifier required parked telemetry, pre-read all
setting values, wrote each setting's existing value, and sent the other five
commands once each. Neither path retried an ambiguous outcome. The robot
remained docked and the settings remained unchanged, so the evidence establishes
delivery without manufacturing a state-transition claim. On 2026-07-28, a
bounded joystick sequence produced 25 acknowledged velocity/zero/Stop sends
with no failures and a docked-to-ready state transition. A bounded
navigation run then exposed a yaw-frame bug; after correction, a second command
reached its requested pose within 0.012 m and 0.078 rad, followed by an
acknowledged Stop and ready state with no errors. An acknowledged Dock command
then transitioned ready to returning to charging in about eight seconds with
no errors. A bounded one-room Quick Vacuum run also verified normal coverage
delivery and the active plan/session decoder before Stop and Dock returned the
robot to charging. No raw-actuation, destructive, network-changing, update,
reboot, or shutdown command was live-tested.

The reusable verifier is
[`tools/live_verify_safe_commands.py`](../tools/live_verify_safe_commands.py).
It has no arbitrary channel or raw-payload option and performs a parked-state
preflight before its bounded checks. The SDK command methods themselves do not
perform this preflight.
