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
intent and always belong to the unsafe capability.

## Codec rule

Every recovered command has a `CommandSpec` describing its family, risk,
available channel evidence, and verification state. Compatibility is currently
gated globally to protocol 25. A decoded name or UniFFI enum discriminant is
not accepted as a protobuf field number.
Commands without proven wire encoders raise an error before opening a mutating
stream.

## Recovered inner payloads

Static tracing through `UserCommand::to_proto` and Prost encoding established
the complete inner protobuf bodies for Stop, StayPut, Pause, Resume, and Dock.
Their `CommandSpec` entries expose those bytes as `known_payload` with the
`payload_verified` evidence level for offline inspection and golden tests.
The UserCommand send path also establishes the exact Hermes target
`user_command`.

This is deliberately not a callable codec. The containing `ChannelRequest` and
acknowledgement semantics remain unresolved. The default registry therefore
still has zero sendable commands.
