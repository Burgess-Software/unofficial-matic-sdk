# Capability status

The SDK uses four evidence labels:

- **Verified live:** observed from an authenticated robot or official client.
- **Verified offline:** decoded from real data, then covered by synthetic tests.
- **Static:** present in the official client binary, but not sent by this SDK.
- **Experimental:** implemented with incomplete compatibility or service evidence.

## Reads

The Hermes `FetchCollection` RPC, sequence acknowledgements, and 43 named
collection targets are verified live. Every known target is available through
the raw event interface. Friendly models are provided only where the wire shape
can be validated; unknown payloads remain losslessly accessible as bytes.
Apart from maps, voxels, retained media, and the collection envelope itself,
most real-time state payloads do not yet have friendly typed decoders.

The highest-value streams are pose, operating state, motor telemetry, active
mission/session, coverage progress, trajectory, maps, version/update state,
settings, schedules, and retained media.

## Controls

The official client exposes mission, navigation, coverage, cleaning, map,
network, settings, scheduling, media, diagnostics, update, reboot, and shutdown
command families. The Hermes RPC and typed client-side models are static
evidence. Each encoder in this SDK must carry its own verification level.

The inner payloads for Stop, StayPut, Pause, Resume, and Dock plus their
`user_command` target are recovered; the surrounding channel envelope is not.
Incomplete encodings fail closed rather than guessing protobuf field numbers.
Transport acknowledgement is reported separately from an independently
observed state change.

## Firmware boundary

The SDK does not contain robot firmware and provides no root shell, filesystem,
bootloader, signing key, UART, motor-controller image, or internal SLAM
database. An update command is a control-plane request, not firmware access.

## Remote access

The official client can tunnel Hermes through a portal WebSocket, but a current
reliable portal credential/backend path has not been established. Remote code
therefore remains experimental and must never silently fall back to an
unauthenticated path.
