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
the response. Command and collection streams can share the HTTP/2 connection.

## Compatibility

Read decoders preserve unknown fields. Writes are stricter: the command
registry accepts only the explicitly supported protocol version. Unknown
command encodings or robot versions fail before network I/O.
`MaticConfig.command_protocol_version` has no default: a future caller must
provide a version observed from that robot before even a complete codec can run.

Five complete inner `UserCommand` protobuf bodies have been recovered from the
native conversion and Prost encoder paths. They are retained as offline golden
evidence, along with the exact `user_command` Hermes target. They do not become
sendable until the surrounding `ChannelRequest` and acknowledgement behavior
are independently established.

This project contains reconstructed protocol descriptions and synthetic test
vectors. It does not redistribute the Android application or native library.
