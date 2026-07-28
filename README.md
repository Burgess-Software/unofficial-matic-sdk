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
- Export Matic's actual `32 x 32 x 24` sparse colored surface representation as
  a standard PLY point cloud.
- Recover retained WebP thumbnails and images embedded in captured collection
  responses.
- Stop, pause, tell the robot to stay put, or send it back to charge using
  typed Python APIs exercised against a real robot.
- Send direct joystick velocities or navigate to a mission pose through typed
  APIs exercised against a real robot.
- Run normal room coverage through a typed API exercised against a real robot.
- Reprioritize an active plan or clean a drawn dry-stain/wet-spill area through
  typed, wire-verified APIs.
- Send direct cleaning-mechanism setpoints through the wire-verified raw-motor
  codec; this path has not been exercised live and applies no device-specific
  range limits.

The [command verification ledger](https://github.com/Burgess-Software/unofficial-matic-sdk/blob/main/docs/command-verification.md) shows exactly
which controls were exercised live, which have offline wire proof, and which
remain unavailable.

## Quick start

You need Python 3.11 or newer, [`uv`](https://docs.astral.sh/uv/), and a robot
reachable from the same local network. Bluetooth enrollment currently requires
Linux with BlueZ; Bluetooth is not needed after enrollment.

```bash
git clone https://github.com/Burgess-Software/unofficial-matic-sdk.git
cd unofficial-matic-sdk
uv sync --all-extras --group dev
uv run matic --help
```

No package is currently published, and no license is granted at this stage.

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
robot identity metadata including its serial number and network addresses.
Treat that output as private even though it does not contain the BotToken.
Routine SDK use is now LAN-only.

## Read live robot data

List the 43 accepted collection targets:

```bash
uv run matic collections list
```

Stream event metadata without printing the raw latest-pose payload:

```bash
uv run matic collections stream latest_pose --count 20 --duration 30
```

Record raw responses from several targets into a new private directory:

```bash
uv run matic collections record captures/telemetry \
  --target latest_pose \
  --target kabuki_state \
  --target motor_status \
  --duration 30
```

The stream command does not print raw payloads, keys, or stable content hashes,
but its target, timing, sequence, and payload-size metadata can still be
sensitive. The record command saves raw data because it is needed for offline
decoding; capture directories can contain household and device information.

## Capture and decode maps

Record compressed map tiles:

```bash
uv run matic collections record captures/map \
  --target map_compressed_rgb \
  --duration 20
```

Assemble the tiles into correctly oriented PNGs:

```bash
uv run matic maps decode captures/map \
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
uv run matic voxels export captures/map --output surface-map.ply
```

Use `--all-depths` to include depths hidden by the official app's normal
surface rendering, or `--coordinate-mode app-native` to preserve the app's
native coordinate convention.

## Extract retained images

Capture the thumbnail collection and extract any validated WebP containers it
contains:

```bash
uv run matic collections record captures/thumbnails \
  --target coverage_session_thumbnails \
  --duration 10

uv run matic media extract captures/thumbnails --output extracted-images
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
version, and command-specific wire codecs.

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

### Send a motion command

Motion commands are direct method calls. For example, this program requests
Dock:

```python
import asyncio

from matic_sdk import MaticClient, MaticConfig, TlsConfig


async def request_dock() -> None:
    config = MaticConfig(
        host="ROBOT_HOST",
        command_protocol_version=25,
        tls=TlsConfig.pinned("VERIFIED_CERTIFICATE_SHA256"),
    )
    async with await MaticClient.connect_from_store(
        "living-room", config
    ) as robot:
        await robot.commands.dock()


asyncio.run(request_dock())
```

Dock has been exercised live through a ready-to-returning-to-charging state
transition. Other autonomous motion codecs retain their individual evidence
labels in the command ledger. Joystick delivery has been acknowledged live
through a docked-to-ready state transition.

### Drive with direct joystick commands

Each `joystick()` call sends one linear/angular velocity command:

```python
import asyncio

from matic_sdk import MaticClient, MaticConfig, TlsConfig


async def drive_briefly() -> None:
    config = MaticConfig(
        host="ROBOT_HOST",
        command_protocol_version=25,
        tls=TlsConfig.pinned("VERIFIED_CERTIFICATE_SHA256"),
    )
    async with await MaticClient.connect_from_store(
        "living-room", config
    ) as robot:
        await robot.commands.joystick(0.05, 0.0)
        await asyncio.sleep(0.5)
        await robot.commands.joystick(0.0, 0.0)


asyncio.run(drive_briefly())
```

The SDK does not repeat joystick commands in the background, expire stale
input, send zero velocity, or issue Stop automatically. Send every command your
control loop requires, including an explicit zero or `robot.commands.stop()`
when that is the behavior you want.

### Send cleaning-mechanism setpoints

The raw-motor codec exposes vacuum RPM and sweeper, mopper, cleaning-head, and
side-brush setpoints directly:

```python
async def set_cleaning_motors(
    robot,
    *,
    vacuum_rpm: float,
    sweeper_duty: float,
) -> None:
    await robot.commands.set_raw_motors(
        vacuum_rpm=vacuum_rpm,
        sweeper_duty=sweeper_duty,
    )
```

Each call sends one `motor_command`. The wire format is verified, but this path
has not been exercised live and the SDK applies no device-specific range
limits.

### Navigate or start coverage

These methods use mission-relative coordinates and map UUIDs. Choose the one
operation you intend to run; the calls are shown together only as an API
reference. `x_meters`, `y_meters`, and `yaw_radians` all use the same canonical
mission frame; a zero yaw points along positive X. The numeric mission, region,
and partition values below are placeholders, not coordinates for your robot.
The SDK does not yet provide a friendly decoder for `latest_pose`, so this is
an advanced API reference rather than a turnkey navigation example.

```python
import asyncio
from uuid import UUID

from matic_sdk import (
    DrawnCircle,
    MaticClient,
    MaticConfig,
    MissionPosture,
    StainMode,
    TlsConfig,
)


async def run_one_motion_command() -> None:
    config = MaticConfig(
        host="ROBOT_HOST",
        command_protocol_version=25,
        tls=TlsConfig.pinned("VERIFIED_CERTIFICATE_SHA256"),
    )
    async with await MaticClient.connect_from_store(
        "living-room", config
    ) as robot:
        destination = MissionPosture(
            mission_id=42,
            x_meters=1.0,
            y_meters=2.0,
            yaw_radians=0.0,
        )

        # Run exactly one intended operation. This example selects Navigate.
        await robot.commands.navigate(destination)

        # Alternatives:
        # await robot.commands.navigate_and_wait(destination)
        # await robot.commands.navigate_and_explore(destination)
        # await robot.commands.normal_coverage(
        #     mission_id=42,
        #     partition_id=UUID("PARTITION_UUID"),
        #     region_ids=[UUID("REGION_UUID")],
        # )
        # await robot.commands.stain_mode(
        #     mission_id=42,
        #     stain_mode=StainMode.DRY_STAIN,
        #     circles=[
        #         DrawnCircle(
        #             x_meters=1.0,
        #             y_meters=2.0,
        #             radius_meters=0.25,
        #         )
        #     ],
        # )


asyncio.run(run_one_motion_command())
```

Normal coverage supports vacuum, mop, or both plus Quick/Standard coverage.
Stain mode reproduces the official DryStain and WetSpill goal plans. As an SDK
safety constraint, it requires at least one circle with a positive finite
radius even though the recovered native serializer itself does not establish
those input checks.

### Reprioritize active coverage

`reprioritize_coverage()` implements the official Prioritize and Skip
transformations. The live-tested decoder joins the robot's active session and
coverage plan by mission ID, preserving the goal IDs and cleaning specs the
command must send back:

```python
from matic_sdk import ReprioritizeAction


async def prioritize_next_region(robot, selected_region_id) -> None:
    snapshot = await robot.reprioritization_snapshot(timeout=5.0)
    if snapshot is None:
        print("The robot does not have an active coverage session.")
        return

    if selected_region_id not in snapshot.region_ids:
        raise ValueError("selected_region_id is not in the active plan")

    await robot.commands.reprioritize_coverage(
        action=ReprioritizeAction.PRIORITIZE,
        mission_id=snapshot.mission_id,
        goals=snapshot.goals,
        current_region_id=snapshot.current_region_id,
        selected_region_id=selected_region_id,
        current_session_id=snapshot.current_session_id,
    )
```

Use `snapshot.region_ids` to show the currently scheduled rooms. Use
`ReprioritizeAction.SKIP` to remove the current region block; Skip does not need
`selected_region_id`. The read is safe while the robot is idle and returns
`None`. The official Add and Redo reprioritization helpers are not exposed yet.

### Change a supported preference

Persistent settings are direct calls too. This example requests child lock to
be enabled. Change the boolean only when you intend to change that setting:

```python
import asyncio

from matic_sdk import MaticClient, MaticConfig, TlsConfig
from matic_sdk.models.control import SettingAction


async def enable_child_lock() -> None:
    config = MaticConfig(
        host="ROBOT_HOST",
        command_protocol_version=25,
        tls=TlsConfig.pinned("VERIFIED_CERTIFICATE_SHA256"),
    )
    async with await MaticClient.connect_from_store(
        "living-room", config
    ) as robot:
        await robot.commands.set_binary_setting(
            SettingAction.CHILD_LOCK,
            True,
        )


asyncio.run(enable_child_lock())
```

Child lock, pet-waste avoidance, and voice are supported. Their live checks
wrote each setting's already-observed value, so delivery was verified without
claiming that a setting transition was tested.

See the [full command ledger](https://github.com/Burgess-Software/unofficial-matic-sdk/blob/main/docs/command-verification.md) and
[caller-responsibility notes](https://github.com/Burgess-Software/unofficial-matic-sdk/blob/main/docs/safety.md) before using additional codecs. Unknown,
partially reconstructed commands fail before network I/O.
The documented top-level API exposes no arbitrary-payload escape hatch, and an
ambiguous command outcome is never retried automatically.

All enabled typed commands are direct calls. The SDK does not require
confirmation phrases or capability objects; callers are responsible for using
commands safely and understanding their effects.

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
- Each joystick call sends exactly one command; the SDK does not provide a
  background resend loop, dead-man lease, automatic zero, or automatic Stop.
- Raw-motor setpoints are direct and wire-verified but have not been exercised
  live; the SDK applies no device-specific range limits.
- The portal-backed remote transport is experimental and not currently
  reliable; normal operation is local-network only.
- The SDK does not provide firmware images, root access, a filesystem, or an
  SSH shell. Update and support-permission command types do not change that.

## Documentation

- [Command verification ledger](https://github.com/Burgess-Software/unofficial-matic-sdk/blob/main/docs/command-verification.md)
- [Control behavior and caller responsibility](https://github.com/Burgess-Software/unofficial-matic-sdk/blob/main/docs/safety.md)
- [Protocol notes](https://github.com/Burgess-Software/unofficial-matic-sdk/blob/main/docs/protocol.md)
- [Research method](https://github.com/Burgess-Software/unofficial-matic-sdk/blob/main/docs/research-method.md)
- [SDK status](https://github.com/Burgess-Software/unofficial-matic-sdk/blob/main/docs/status.md)
- [Experimental remote transport](https://github.com/Burgess-Software/unofficial-matic-sdk/blob/main/docs/remote.md)

## Development

```bash
uv run ruff check .
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -p pytest_asyncio.plugin
uv run python -m build
uv run python tools/scan_repository.py
```

Keep real household captures, application binaries, robot identifiers, tokens,
maps, recordings, and packet captures out of Git.
