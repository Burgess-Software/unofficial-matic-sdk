# Capability status

Control evidence has two independent axes. The codec evidence levels are
`static_type`, `static_fields`, `payload_verified`, and `wire_verified`;
`wire_verified` is necessary but safety policy can still disable a codec. The separate
`live_delivery_verified` flag means this SDK sent one bounded request to an
authenticated robot and received the expected Hermes acknowledgement. Neither
label alone claims a physical effect.

Read-side features use the broader terms verified live, verified offline,
static, and experimental. Experimental means compatibility or service evidence
is still incomplete.

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

The registry documents 65 command intents and exact Hermes targets for each.
Thirty have exact wire formats and 29 have registered codecs. Thirty-five
remain fail-closed because their inner protobuf encoding is incomplete;
raw-motor encoding is exact but also fails closed because hardware-safe ranges
are unproven. The [command verification ledger](command-verification.md)
records every target, evidence level, live state, safety gate, and blocker.

On 2026-07-22 an initial one-shot check sent Stop once. A separate bounded
verifier sent StayPut, Pause, child lock, pet-waste avoidance, and voice once
each over a certificate-pinned, authenticated connection. It required parked
telemetry, pre-read all three settings, and wrote only their existing values.
Neither path retried an unknown outcome. Hermes acknowledged all six requests.
The robot remained docked and the settings retained their prior values, so
these checks verify delivery but do not claim an unobservable transition.

On 2026-07-28, a watchdog-backed joystick test first sent a short 0.05 m/s
sequence while docked, then sent 0.10 m/s for two seconds with input-lease
refreshes. The longer sequence produced 25 acknowledged velocity/zero/Stop
sends with no failures, and operating state changed from docked to ready.

The other 23 exact formats have offline wire evidence only. No raw-actuation,
destructive, network-changing, update, reboot, or shutdown command was
live-tested. Raw-motor encoding is not registered at all. Risk capabilities
control whether an informed caller may send an enabled exact codec; they do not
upgrade its evidence level. Joystick control is enabled only through
`MaticClient.teleop()`; direct executor use remains blocked.

## Firmware boundary

The SDK does not contain robot firmware and provides no root shell, filesystem,
bootloader, signing key, UART, motor-controller image, or internal SLAM
database. An update command is a control-plane request, not firmware access.

## Remote access

The official client can tunnel Hermes through a portal WebSocket, but a current
reliable portal credential/backend path has not been established. Remote code
therefore remains experimental and must never silently fall back to an
unauthenticated path.
