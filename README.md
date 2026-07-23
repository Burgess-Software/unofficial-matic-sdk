# Unofficial Matic SDK

An async Python SDK and CLI for connecting directly to a Matic robot you own.
Pair once over Bluetooth, then use the robot's authenticated local-network
service without a cloud relay.

> [!WARNING]
> This project is unofficial, unaffiliated with Matic Robots, and based on
> independent protocol research. It is alpha software for hardware you own.
> Commands can move the robot or change persistent settings.

## What you can do

- Pair a Linux computer with Matic and store its per-client credential
  privately.
- Authenticate directly to the robot over encrypted local-network HTTP/2 and
  gRPC.
- Stream or record 43 robot collection surfaces covering pose, activity,
  device state, settings, schedules, history, media, and maps.
- Assemble captured RGB, integrated, coverage, and semantic map tiles into
  correctly oriented PNG mosaics.
  <img width="1024" height="928" alt="image" src="https://github.com/user-attachments/assets/58efbb9e-4180-4c8d-b38f-ff300f1c86b7" />

- Export Matic's actual `32 x 32 x 24` sparse colored surface representation as
  a standard PLY point cloud.
  <img width="2416" height="1338" alt="image" src="https://github.com/user-attachments/assets/df910ffe-de7b-4bc0-bf21-1c3847c25a78" />

- Recover retained WebP thumbnails and images embedded in captured collection
  responses.
- Stop, pause, or tell the robot to stay put using typed Python APIs that have
  been delivered to a real robot.
- Use explicitly armed, fail-closed APIs for additional controls whose target
  and protobuf encoding have been recovered.

The [command verification ledger](docs/command-verification.md) shows exactly
which controls were exercised live, which have offline wire proof, and which
remain unavailable.

## Quick start

You need Python 3.11 or newer, [`uv`](https://docs.astral.sh/uv/), and a robot
reachable from the same local network. Bluetooth enrollment currently requires
Linux with BlueZ; Bluetooth is not needed after enrollment.

```bash
git clone git@github.com:Burgess-Software/unofficial-matic-sdk.git
cd unofficial-matic-sdk
uv sync --all-extras --group dev
uv run matic --help
```

This repository is currently private, no package is published, and no license
is granted at this stage.

### Pair once over Bluetooth

1. In the Matic app, open **Settings → Connectivity → Add another user** and
   enable pairing mode.
2. Keep the Linux Bluetooth adapter close to the robot.
3. Start enrollment and complete the BlueZ passkey prompt using the six-digit
   code displayed by Matic.

```bash
uv run matic enroll --device living-room
```

`living-room` is only a local alias. The SDK creates its own client UUID and
saves the returned `BotToken` in an owner-only credential directory. It refuses
to overwrite an existing enrollment.

If you already have an owner-only serialized token, import it without exposing
the secret in a command argument:

```bash
uv run matic credentials import-token \
  --device living-room \
  --source /secure/path/to/bot-token.pb
```

Never put a token in an environment variable, shell argument, `.env` file,
issue, log, fixture, or Git commit.

### Verify the local connection

Set a local alias and the robot's LAN hostname or IP:

```bash
export MATIC_DEVICE=living-room
export MATIC_HOST=ROBOT_HOST
```

Fetch the certificate fingerprint without sending credentials, verify it
independently, and then pin the verified value:

```bash
uv run matic tls fingerprint
export MATIC_CERT_SHA256=VERIFIED_64_CHARACTER_SHA256
uv run matic status
```

The status command authenticates, performs the Hermes handshake, and prints
non-secret robot identity information. Routine SDK use is now LAN-only.

## Read live robot data

List the 43 accepted collection targets:

```bash
uv run matic collections list
```

Stream privacy-safe event metadata for the latest robot pose:

```bash
uv run matic collections stream latest_pose --count 20 --duration 30
```

Record raw responses from several targets into a new private directory:

```bash
uv run matic collections record telemetry-capture \
  --target latest_pose \
  --target kabuki_state \
  --target motor_status \
  --duration 30
```

The stream command does not print raw payloads. The record command does save
them, because they are needed for offline decoding; capture directories can
contain sensitive household and device information.

## Capture and decode maps

Record compressed map tiles:

```bash
uv run matic collections record map-capture \
  --target map_compressed_rgb \
  --duration 20
```

Assemble the tiles into correctly oriented PNGs:

```bash
uv run matic maps decode map-capture \
  --output decoded-map \
  --target map_compressed_rgb
```

The decoder also supports integrated, combined-coverage, semantic, and
semantic-override captures. For an ambiguous capture, pass its exact
`--target`. Use fresh capture and output paths; existing output files are never
overwritten.

## Export the sparse voxel surface

The compressed RGB map includes a sparse depth bit field rather than only a
flat image. Export it as a colored binary PLY point cloud:

```bash
uv run matic voxels export map-capture --output surface-map.ply
```

Use `--all-depths` to include depths hidden by the official app's normal
surface rendering, or `--coordinate-mode app-native` to preserve the app's
native coordinate convention.

## Extract retained images

Capture the thumbnail collection and extract any validated WebP containers it
contains:

```bash
uv run matic collections record thumbnail-capture \
  --target coverage_session_thumbnails \
  --duration 10

uv run matic media extract thumbnail-capture --output extracted-images
```

This recovers images already present in retained collection responses. It does
not start a recording or provide a live optical-camera stream.

## Use the Python API

### Subscribe to a live collection

```python
import asyncio

from matic_sdk import MaticClient, MaticConfig, TlsConfig


async def watch_pose() -> None:
    config = MaticConfig(
        host="ROBOT_HOST",
        tls=TlsConfig.pinned("VERIFIED_CERTIFICATE_SHA256"),
    )
    async with await MaticClient.connect_from_store(
        "living-room", config
    ) as robot:
        async with await robot.collections.subscribe("latest_pose") as events:
            async for event in events:
                print(event.operation.value, len(event.payload or b""))


asyncio.run(watch_pose())
```

Collection envelopes are typed and automatically acknowledged. Most
application payloads are intentionally exposed as raw bytes until their exact
schema has been proven.

## Control the robot

The CLI can inspect command support with `matic control list` and
`matic control status`, but it deliberately has no generic command sender.
Writes use typed Python APIs, verified TLS, an explicitly selected protocol
version, and command-specific safety gates.

### Stop, pause, or stay put

```python
import asyncio

from matic_sdk import MaticClient, MaticConfig, TlsConfig


async def stop_robot() -> None:
    config = MaticConfig(
        host="ROBOT_HOST",
        command_protocol_version=25,
        tls=TlsConfig.pinned("VERIFIED_CERTIFICATE_SHA256"),
    )
    async with await MaticClient.connect_from_store(
        "living-room", config
    ) as robot:
        receipt = await robot.commands.stop()
        assert receipt.transport_acknowledged


asyncio.run(stop_robot())
```

`robot.commands.pause()` and `robot.commands.stay_put()` use the same stationary
path. Stop, Pause, and StayPut have each been acknowledged by a real robot while
it remained docked.

### Intentionally enable a motion command

Motion-capable commands require a short-lived `MotionControls` capability. For
example, this program requests Dock while keeping the capability valid for no
more than 30 seconds. Keep the robot in view when you run it:

```python
import asyncio

from matic_sdk import MaticClient, MaticConfig, MotionControls, TlsConfig
from matic_sdk.safety import MOTION_CONFIRMATION


async def request_dock() -> None:
    config = MaticConfig(
        host="ROBOT_HOST",
        command_protocol_version=25,
        tls=TlsConfig.pinned("VERIFIED_CERTIFICATE_SHA256"),
    )
    async with await MaticClient.connect_from_store(
        "living-room", config
    ) as robot:
        motion = MotionControls.arm(MOTION_CONFIRMATION, ttl_seconds=30)
        try:
            await robot.commands.dock(motion_controls=motion)
        finally:
            motion.disarm()


asyncio.run(request_dock())
```

Dock and the other motion-changing codecs have exact offline wire evidence but
have not been exercised live by this SDK.

### Change a supported preference

Persistent settings require a separate `UnsafeControls` capability. Running
this example requests child lock to be enabled. Change the boolean only when
you intend to change that setting:

```python
import asyncio

from matic_sdk import MaticClient, MaticConfig, TlsConfig, UnsafeControls
from matic_sdk.models.control import SettingAction
from matic_sdk.safety import UNSAFE_CONFIRMATION


async def enable_child_lock() -> None:
    config = MaticConfig(
        host="ROBOT_HOST",
        command_protocol_version=25,
        tls=TlsConfig.pinned("VERIFIED_CERTIFICATE_SHA256"),
    )
    async with await MaticClient.connect_from_store(
        "living-room", config
    ) as robot:
        unsafe = UnsafeControls.arm(UNSAFE_CONFIRMATION, ttl_seconds=30)
        try:
            await robot.commands.set_binary_setting(
                SettingAction.CHILD_LOCK,
                True,
                unsafe_controls=unsafe,
            )
        finally:
            unsafe.disarm()


asyncio.run(enable_child_lock())
```

Child lock, pet-waste avoidance, and voice are supported. Their live checks
wrote each setting's already-observed value, so delivery was verified without
claiming that a setting transition was tested.

See the [full command ledger](docs/command-verification.md) and
[safety model](docs/safety.md) before using additional codecs. Unknown,
partially reconstructed, or policy-disabled commands fail before network I/O.
There is no public arbitrary-payload escape hatch, and an ambiguous command
outcome is never retried automatically.

## Safety and privacy

- BotTokens remain in owner-only files and are never printed by the SDK.
- TLS identity must be verified before authenticated commands are available.
- Map, media, schedule, pose, and raw collection captures can disclose details
  about a home. Keep them out of source control and public bug reports.
- Capture and decoder tools create new private outputs and refuse accidental
  overwrite.
- Tests and repository fixtures contain synthetic protocol data only.

## Current limits

- Command writes currently require an explicitly selected protocol version 25.
- Bluetooth enrollment currently requires Linux and BlueZ.
- Most collection payloads have a safe raw-byte interface but not yet a
  friendly high-level model.
- Maps and voxels are decoded from captures; this is not a live map dashboard.
- Direct joystick execution is blocked pending watchdog-path validation, and
  raw-motor sending is disabled until hardware-safe ranges are known.
- The portal-backed remote transport is experimental and not currently
  reliable; normal operation is local-network only.
- The SDK does not provide firmware images, root access, a filesystem, or an
  SSH shell. Update and support-permission command types do not change that.

## Documentation

- [Command verification ledger](docs/command-verification.md)
- [Control safety model](docs/safety.md)
- [Protocol notes](docs/protocol.md)
- [Research method](docs/research-method.md)
- [Capability details](docs/status.md)
- [Experimental remote transport](docs/remote.md)

## Development

```bash
uv run ruff check .
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -p pytest_asyncio.plugin
uv run python -m build
uv run python tools/scan_repository.py
```

Keep real household captures, application binaries, robot identifiers, tokens,
maps, recordings, and packet captures out of Git.
