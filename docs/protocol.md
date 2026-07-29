# Protocol overview

## Enrollment and authentication

Enrollment is an explicit Bluetooth GATT exchange performed while the robot is
in pairing mode. A persistent client UUID is sent in a reconstructed token
request; the resulting serialized `BotToken` is stored privately.

Local operation uses TLS with ALPN `h2` on the robot's Hermes endpoint. The
serialized token is padded standard Base64 in this metadata value:

```text
authorization: Bearer: <base64 serialized BotToken>
```

The value after `Bearer:` is a protobuf message, not the opaque secret alone.
Authenticated clients refuse the diagnostic insecure mode so a local
man-in-the-middle cannot capture this reusable credential. Insecure mode is
limited to the unauthenticated identity probe.

## Hermes RPCs

Known RPC paths are:

- `/hermes.Hermes/Handshake`
- `/hermes.Hermes/FetchCollection`
- `/hermes.Hermes/SendToChannel`

Collections are bidirectional streams. The client sends an initial request,
keeps the HTTP/2 request side open, and acknowledges returned sequence IDs.

`SendToChannel` accepts a client stream and returns one response. The official
client normally sends one `ChannelRequest`, half-closes that stream, and awaits
the response. `ChannelRequest` carries `string channel_name = 1` and
`bytes value = 2`; the channel name is duplicated in `hermes-target` metadata.
The native response type is `ChannelResponse { bytes value = 1; }`, whose
default empty value serializes as an empty protobuf message. Command and
collection streams can share the HTTP/2 connection.

## Compatibility

Read decoders preserve unknown fields. Unknown command encodings still fail
before network I/O. The command registry requires an explicitly selected
positive protocol version, but it does not reject a write solely because that
number differs from 25. Instead, it emits
`UnverifiedProtocolVersionWarning` and reuses the verified protocol-25
protobuf codec. This permits protobuf-compatible firmware to proceed while
making the unverified compatibility visible to logs and test suites.
`MaticConfig.command_protocol_version` has no default, so callers still provide
the version observed from their robot.

The registry documents 65 intents and exact Hermes targets. All 65 inner
command encodings have been reconstructed completely, retained as synthetic
golden evidence, and registered as codecs. This includes raw-motor setpoints;
the SDK adds no device-specific range limits. `wire_verified` describes the
protocol-25 evidence baseline. Every enabled codec is callable directly on
another positive version after the warning; risk labels are informational and
add no capability gate.

An empty inner protobuf is a valid command body for the commands whose schema
has no fields. Because protobuf omits default values, `ChannelRequest.value`
field 2 is absent in that case; the target in field 1 and `hermes-target`
metadata still identify the command. This canonical omission is not treated as
a missing payload.

Twelve codecs have the separate SDK live-delivery flag. The remaining 53 exact
formats, including raw motors, have offline wire proof only.
Joystick sending is available directly through `robot.commands.joystick()`.
Each call sends once; the SDK adds no background watchdog, automatic zero, or
automatic Stop. See the [per-command ledger](command-verification.md) for the
exact boundary.

This project contains reconstructed protocol descriptions and synthetic test
vectors. It does not redistribute the Android application or native library.
