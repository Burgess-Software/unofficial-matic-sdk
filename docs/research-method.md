# How the protocol was recovered

This SDK is the cleaned, synthetic-testable result of independent work against
an owner-controlled robot and the official Android client. It does not contain
the application, its native libraries, packet captures, credentials, household
maps, or recordings.

## Obtaining the client binary

The official application was installed from Google Play on a paired Android
phone. Android permits ADB to read the installed APK paths even though the
release app's private data directory remains inaccessible:

```bash
adb shell pm path com.maticrobots.maticapp
adb pull <base-apk-path> research/base.apk
adb pull <arm64-split-path> research/split_config.arm64_v8a.apk
unzip -p research/split_config.arm64_v8a.apk \
  lib/arm64-v8a/libmegazord.so > research/libmegazord.so
```

The analyzed release was app version 1.151.0. The ARM64 split contained an
approximately 32 MB AArch64 ELF shared library named `libmegazord.so`.

That library is phone-side client code, not robot firmware. It was especially
useful because it was not stripped: the release still retained many Rust
symbol names, UniFFI metadata records, generated protobuf type names, panic and
log strings, and source-path fragments. “Not stripped” does not mean full
source code or rich debugger information was present. It means the usual
post-link removal of names and metadata had not erased many semantic landmarks.

## From names to code paths and wire types

The base APK was inspected with Android manifest/resource tooling. The native
library was inspected with `strings`, ELF symbol and section tools, demangling,
cross-reference searches, and targeted AArch64 disassembly. PID-filtered
`adb logcat` output from the running official app provided an independent live
view of selected connections and collection names.

A string by itself is weak evidence. The useful results came from following
references from a named public method through its concrete implementation:

- retained Rust and UniFFI names identify a high-level operation;
- call sites show which formatter, serializer, or network client consumes it;
- generated Prost encode/decode routines expose protobuf wire types and tags;
- constants loaded at a send call establish RPC paths or metadata values;
- live requests distinguish a structurally plausible guess from a protocol
  shape the robot actually accepts.

For example, the library retained the Hermes RPC names, the `hermes-target`
metadata key, and `BotToken::bearer_header`. Following the latter through its
Base64 call established that authentication is:

```text
authorization: Bearer: <standard padded Base64 of the serialized BotToken>
```

The colon after `Bearer` is part of the value. The encoded input is the complete
protobuf wrapper, not the opaque secret alone and not the SHA-256 digest shown
by the app's redacted debug formatter.

## Enrollment instead of extracting app-private state

Ordinary attempts to read the phone's saved credential correctly failed: the
release app was not debuggable, `run-as` was denied, Android backup contained no
useful private data, and stock ADB could not attach to or read the private app
directory. The research therefore used the app's normal enrollment mechanism
rather than rooting or modifying the phone.

Tracing `BluetoothConnection::get_token` recovered the service and
characteristic UUIDs checked into `matic_sdk.enrollment`, plus this operation
order:

1. find the advertiser while the robot is on its Bluetooth pairing screen;
2. pair using the normal displayed passkey flow;
3. discover the token GATT service and characteristic;
4. write a protobuf `TokenRequest` containing a persistent client UUID using an
   acknowledged write;
5. read the same characteristic;
6. parse the returned bytes as `BotToken`.

The robot's QR-scanning setup screen and Bluetooth pairing screen are distinct;
only the latter advertised the token service in the tested flow. A fresh,
computer-specific UUID produced a token accepted by LAN Hermes. The token was
written directly to an owner-only file without printing its secret. A valid
token, a byte-altered control, and a missing-token control were then compared:
only the valid token passed authentication.

Bluetooth is only the bootstrap. Normal subsequent sessions use the saved
per-client token over the local network.

## Reconstructing collections

Hermes uses TLS with ALPN `h2`, HTTP/2, and gRPC. `FetchCollection` is a
bidirectional stream rather than a unary get operation. A working subscription
requires both the `hermes-target` metadata and an `InitialRequest` body naming
the same collection. The request side remains open, and each returned sequence
identifier is acknowledged on that same stream.

The official client's fresh-subscription configuration established the values
implemented in `SubscriptionConfig`: mode 2, batch size 20, window size 1000,
and the fresh flag. This SDK preserves raw responses and unknown payload fields
instead of inventing schemas for them.

## Map and voxel reconstruction

Map values are collection records keyed by mission and 32 by 32 page
coordinates. Two details fixed an initially scrambled mosaic:

- page coordinates use protobuf `sint32` and therefore require ZigZag decoding;
- tile planes are stored X-major and must be transposed into ordinary image
  row order.

The native renderer supplied the optional app-facing whole-map transform. The
SDK keeps a canonical orientation as its default and exposes the native
orientation explicitly.

Compressed RGB tiles also contain a `1 x 32 x 32 x 24` bit field. Its 3,072
bytes describe sparse surface occupancy, followed by one RGB triplet per set
bit. RGB consumption includes the lower depth layers that the official viewer
hides; skipping those bytes would misalign every later color. The exported PLY
uses 1.5 cm cells. This is an app-facing colored surface representation, not a
claim of access to the robot's full SLAM volume, TSDF, ESDF, or triangle mesh.

## Command evidence and safety boundary

Static tracing recovered 65 command intents and their Hermes targets. Continued
tracing through concrete conversion and generated Prost encoding paths, plus
independent reconstruction and byte-level golden fixtures, produced 30 exact
wire formats, 29 of which have enabled registry codecs. The public UniFFI
variant numbers were not protobuf tags; the trace
followed their lift into Rust enum discriminants, concrete protocol types, and
generated encoders.

Independent reconstruction established `ChannelRequest` as a channel-name
field plus encoded value, with `hermes-target` carrying the same channel name.
Official-client behavior corroborates one half-closed request and one
`ChannelResponse` on success. A valid empty command protobuf has no inner bytes;
its `ChannelRequest` therefore omits the default bytes field. This is distinct
from an unknown or guessed payload.

Exact wire evidence is necessary but not always sufficient to register an
encoder, and it is not live delivery evidence. Raw-motor encoding remains
disabled because hardware-safe ranges are not proven. Commands with incomplete
or policy-disabled encodings fail before opening a mutating stream. Motion
commands have no capability gate. Enabled hazardous commands retain the
short-lived `UnsafeControls` gate; trace calibration is in that category.
Direct joystick calls use the exact encoder while retaining the executor's TLS,
protocol, codec, audit, and no-retry boundaries. Each call sends once; there is
no SDK-managed watchdog or automatic Stop. A bounded live test on 2026-07-28
produced 25 acknowledged sends with no failures and a docked-to-ready state
transition.

The SDK implementations of Stop, StayPut, Pause, child lock, pet-waste
avoidance, and voice were exercised against an owner-authorized robot on
2026-07-22. Stop used an initial one-shot check. A separate verifier required
parked telemetry, pre-read the three settings, then sent StayPut, Pause, and
each same-value setting write once. Neither path retried an ambiguous result. A
pinned TLS identity, authenticated handshake, one Hermes response per command,
and gRPC status 0 established delivery. The robot stayed docked and each
setting preserved its value, so no physical or setting transition was claimed.
On 2026-07-28, a bounded joystick sequence produced 25 acknowledged
velocity/zero/Stop sends with no failures and a docked-to-ready state
transition. A first bounded navigation run reached its requested position but
revealed that canonical heading had not crossed the same reflected coordinate
transform as translation. Correcting the wire unit vector from
`(cos(yaw), sin(yaw))` to `(-sin(yaw), -cos(yaw))` produced a second live run
that reached the requested pose within 0.012 m and 0.078 rad. Stop was
acknowledged, state returned to ready, and no robot errors appeared. A
subsequent Dock command was acknowledged and transitioned ready to returning
to charging in about eight seconds with no errors. A bounded one-room Quick
Vacuum run then exposed a transient coverage-plan state with goals but no
selected candidate. Treating that state as non-actionable until the next event
allowed the friendly decoder to join the live mission, session, eight goals,
and current region before Stop and Dock returned the robot to charging. No
raw-actuation, destructive, network-changing, update, reboot, or shutdown
command was live-tested.

## Firmware boundary

Authenticated Hermes access reveals state that the robot deliberately exposes
to its client. It does not provide a shell, root access, a filesystem, process
list, boot image, kernel, motor-controller image, signing keys, or internal
SLAM database. Update, reboot, shutdown, and support-tunnel types visible in the
phone client are control-plane interfaces; merely seeing them does not grant
firmware access.

## Evidence policy in this repository

Reusable claims are separated into four levels:

- live verified against an owner-authorized robot;
- offline verified against real captures and then represented by synthetic
  tests;
- statically established in the official client;
- experimental, where a backend or compatibility assumption remains open.

Real binaries and captures stay outside the repository. Tests build synthetic
protobufs, maps, voxels, media containers, credentials, and transport responses.
