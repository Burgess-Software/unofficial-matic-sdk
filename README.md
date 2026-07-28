# Unofficial Matic SDK

An async Python SDK and CLI for connecting directly to a Matic robot you own.
Pair once over Bluetooth, then use the robot's authenticated local-network
service without a cloud relay.

> **Warning:** This project is unofficial, unaffiliated with Matic Robots, and
> based on independent protocol research. It is alpha software for hardware you
> own. Commands can move the robot or change persistent settings.

## What you can do

- Pair a Linux computer with Matic and store its per-client credential
  privately.
- Authenticate directly to the robot over encrypted local-network HTTP/2 and
  gRPC.
- Stream or record 43 robot collection surfaces covering pose, activity,
  device state, settings, schedules, history, media, and maps.
- Assemble captured RGB, integrated, coverage, and semantic map tiles into
  correctly oriented PNG mosaics.

  <img width="1024" height="928" alt="Decoded Matic map mosaic" src="https://github.com/user-attachments/assets/58efbb9e-4180-4c8d-b38f-ff300f1c86b7" />

- Export Matic's actual `32 x 32 x 24` sparse colored surface representation as
  a standard PLY point cloud.

  <img width="2416" height="1338" alt="Matic sparse voxel surface" src="https://github.com/user-attachments/assets/df910ffe-de7b-4bc0-bf21-1c3847c25a78" />

- Recover retained WebP thumbnails and images embedded in captured collection
  responses.
- Stop, pause, tell the robot to stay put, or send it back to charge using
  typed Python APIs exercised against a real robot.
- Send direct joystick velocities or navigate to a mission pose through typed
  APIs exercised against a real robot.
- Run normal room coverage through a typed API exercised against a real robot.
- Reprioritize an active plan or clean a drawn dry-stain/wet-spill area through
  typed, wire-verified APIs.
- Build room partitions; rename, merge, or split rooms; and manage no-go,
  drive-only, stair, semantics, and sink-summon map data.
- Create, modify, toggle, or remove regular and sink-summon schedules.
- Send direct cleaning-mechanism setpoints through the wire-verified raw-motor
  codec; this path has not been exercised live and applies no device-specific
  range limits.

The [command verification ledger](https://github.com/Burgess-Software/unofficial-matic-sdk/blob/main/docs/command-verification.md) shows exactly
which controls were exercised live and which currently have offline wire proof
only. All 65 documented protocol-25 commands have registered codecs.

## Quick start

You need Python 3.11 or newer and a robot reachable from the same local network.
Bluetooth enrollment currently requires Linux with BlueZ; Bluetooth is not
needed after enrollment.

```bash
python -m pip install --pre "unofficial-matic-sdk[all]"
matic --help
```

With [`uv`](https://docs.astral.sh/uv/), use
`uv tool install --python 3.13 --prerelease allow "unofficial-matic-sdk[all]"`
for the CLI, or `uv add --prerelease allow "unofficial-matic-sdk[all]"` inside
a Python project.

No license is granted at this stage.

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

Every known target decodes to a named, immutable model. The raw payload and
parsed protobuf fields remain attached so an unrecognized field from newer
firmware is not discarded. The Python example below shows `event.decode()`.

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
from matic_sdk.models.collections import PoseCollectionModel


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
                model = event.decode()
                if isinstance(model, PoseCollectionModel):
                    print(model.mission_id, model.pose, model.observed_at)


asyncio.run(watch_pose())
```

Collection envelopes are typed and automatically acknowledged. All 43 known
targets have friendly models, while `raw_payload` and parsed `fields` keep the
decoder lossless across firmware additions. See the
[collection model reference](https://github.com/Burgess-Software/unofficial-matic-sdk/blob/main/docs/collections.md).

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
Subscribe to `latest_pose` to obtain the robot's current mission ID and
mission-relative coordinates before constructing a destination.

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
Stain mode reproduces the official DryStain and WetSpill goal plans. The typed
model rejects empty or non-positive drawn circles so it cannot serialize
malformed geometry.

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

### Edit rooms and map zones

Map commands use the same mission coordinates and UUIDs returned by map
collections. The convenience methods cover partition building, room
rename/merge/split, no-go and drive-only zones, stairs, semantic overrides, and
the sink-summon location:

```python
from uuid import UUID

from matic_sdk import DrawnCircle, RoomLabel


async def edit_one_map_item(robot) -> None:
    await robot.commands.rename_room(
        mission_id=42,
        partition_id=UUID("10213243-5465-7687-98a9-babcbddcedfe"),
        region_id=UUID("00112233-4455-6677-8899-aabbccddeeff"),
        label=RoomLabel.KITCHEN,
    )

    # Other typed operations include:
    # await robot.commands.build_partition(mission_id=42, overwrite=False)
    # await robot.commands.add_no_go_zones(
    #     mission_id=42,
    #     circles=[DrawnCircle(1.0, 2.0, 0.4)],
    # )
```

These persistent commands have exact offline native-serializer wire proof but
have not been exercised against a live robot through this SDK.

### Create a cleaning schedule

Regular schedules support mapped rooms or custom drawn areas, Sunday through
Saturday, vacuum/mop/both modes, Quick/Standard/legacy Deep vacuum settings,
ordered goals, names, and enabled/disabled/suggested states:

```python
from uuid import UUID, uuid4

from matic_sdk import (
    CoverageCleaningMode,
    ScheduleCoverageSetting,
    ScheduleEvent,
    ScheduleEventKey,
    ScheduleTime,
    StandardScheduleTarget,
    Weekday,
)


async def add_weekday_schedule(robot) -> None:
    key = ScheduleEventKey(mission_id=42, event_id=uuid4())
    event = ScheduleEvent(
        name="Weekday morning",
        weekdays=(
            Weekday.MONDAY,
            Weekday.TUESDAY,
            Weekday.WEDNESDAY,
            Weekday.THURSDAY,
            Weekday.FRIDAY,
        ),
        time=ScheduleTime(
            seconds_since_midnight=8 * 60 * 60,
            timezone_id="America/Chicago",
            utc_offset_seconds=-5 * 60 * 60,
        ),
        target=StandardScheduleTarget(
            (UUID("00112233-4455-6677-8899-aabbccddeeff"),)
        ),
        partition_id=UUID("10213243-5465-7687-98a9-babcbddcedfe"),
        cleaning_mode=CoverageCleaningMode.BOTH,
        vacuum_setting=ScheduleCoverageSetting.STANDARD,
    )
    await robot.commands.add_or_modify_schedule(key=key, event=event)
```

Use `toggle_schedule(key)` or `remove_schedule(key)` for an existing event.
Sink-summon schedules use `SinkSummonScheduleEvent` and
`add_or_modify_sink_summon_schedule()`. Schedule writes have exact offline wire
proof but have not been exercised live.

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

- Command writes require an explicitly selected positive protocol version.
  Version 25 is verified; other versions use the protocol-25 protobuf codec and
  emit an `UnverifiedProtocolVersionWarning` instead of blocking the write.
- Bluetooth enrollment currently requires Linux and BlueZ.
- All 43 known collection targets have friendly models. A few variants were
  observed only in their empty or default state, so their optional values
  remain `None` until the robot emits that variant; raw fields are preserved.
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
- [Collection model reference](https://github.com/Burgess-Software/unofficial-matic-sdk/blob/main/docs/collections.md)
- [Research method](https://github.com/Burgess-Software/unofficial-matic-sdk/blob/main/docs/research-method.md)
- [SDK status](https://github.com/Burgess-Software/unofficial-matic-sdk/blob/main/docs/status.md)
- [Experimental remote transport](https://github.com/Burgess-Software/unofficial-matic-sdk/blob/main/docs/remote.md)

## Development

```bash
uv sync --all-extras --group dev
uv run ruff check .
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -p pytest_asyncio.plugin
uv run python -m build
uv run python tools/scan_repository.py
```

Keep real household captures, application binaries, robot identifiers, tokens,
maps, recordings, and packet captures out of Git.
