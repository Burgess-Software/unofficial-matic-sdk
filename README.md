# Unofficial Matic SDK

[![PyPI](https://img.shields.io/pypi/v/unofficial-matic-sdk.svg)](https://pypi.org/project/unofficial-matic-sdk/)
[![Python](https://img.shields.io/pypi/pyversions/unofficial-matic-sdk.svg)](https://pypi.org/project/unofficial-matic-sdk/)
[![CI](https://github.com/Burgess-Software/unofficial-matic-sdk/actions/workflows/ci.yml/badge.svg)](https://github.com/Burgess-Software/unofficial-matic-sdk/actions/workflows/ci.yml)

An async Python SDK and CLI for connecting directly to a Matic robot you own.
Pair once over Bluetooth, then read and control the robot through its
authenticated local-network service without a cloud relay.

> **Warning:** This project is unofficial, unaffiliated with Matic Robots, and
> based on independent protocol research. It is alpha software for hardware you
> own. Commands can move the robot or change persistent settings.

- [What you can do](#what-you-can-do)
- [Quick start](#quick-start)
- [Read live robot data](#read-live-robot-data)
- [Capture maps, voxels, and media](#capture-maps-voxels-and-media)
- [Use the Python API](#use-the-python-api)
- [Control the robot](#control-the-robot)
- [Documentation and examples](#documentation-and-examples)
- [Known boundaries](#known-boundaries)

## What you can do

| Area | SDK capabilities |
| --- | --- |
| Enrollment | Pair a Linux computer over Bluetooth and retain a private per-client `BotToken` |
| Live data | Decode 43 collection targets covering pose, operating state, motors, missions, schedules, history, media, settings, and maps |
| Motion | Stop, pause, stay put, dock, drive with direct joystick velocities, or navigate to a mission pose |
| Cleaning | Run mapped-room coverage, reprioritize an active plan, or clean a drawn dry-stain/wet-spill area |
| Maps | Build partitions; edit rooms, no-go/drive-only/stair zones, semantics, and sink-summon locations |
| Schedules and settings | Create schedules and change supported preferences through typed methods |
| Exports | Assemble RGB/coverage/semantic maps, export sparse colored voxels as PLY, and recover retained WebP media |

All 65 documented command intents have registered protocol-25 codecs. The
[verification ledger](https://github.com/Burgess-Software/unofficial-matic-sdk/blob/main/docs/command-verification.md)
distinguishes commands exercised on a real robot from formats proven only
through offline native serialization.

```text
Matic pairing mode ── Bluetooth once ──> private BotToken on this computer
this SDK           ── TLS + HTTP/2 over the LAN ──> robot Hermes service
```

## Quick start

You need Python 3.11 or newer. Bluetooth enrollment currently requires Linux
with BlueZ; Bluetooth is not needed after enrollment.

### 1. Install

```bash
python -m pip install --pre "unofficial-matic-sdk[all]"
matic --version
```

With [`uv`](https://docs.astral.sh/uv/):

```bash
uv tool install --python 3.13 --prerelease allow \
  "unofficial-matic-sdk[all]"
matic --version
```

### 2. Pair once over Bluetooth

In the Matic app, open **Settings → Connectivity → Add another user**, enable
pairing mode, and keep the computer's Bluetooth adapter close to the robot.
Then run:

```bash
matic enroll --device living-room
```

Complete the BlueZ passkey prompt with the six-digit code displayed by Matic.
`living-room` is only a local alias. The SDK creates its own client UUID and
saves the returned token in an owner-only credential directory without
overwriting an existing enrollment.

If you already have an owner-only serialized token:

```bash
matic credentials import-token \
  --device living-room \
  --source /secure/path/to/bot-token.pb
```

### 3. Identify the LAN endpoint and pin TLS

Find the robot's IP address or hostname in your router's DHCP/client list. The
SDK intentionally does not sweep the LAN.

```bash
export MATIC_DEVICE=living-room
export MATIC_HOST=ROBOT_IP_OR_HOSTNAME
```

Confirm the unauthenticated identity before sending credentials:

```bash
matic probe --host "$MATIC_HOST" --insecure-read-only
```

Compare the returned serial number with a trusted record or physical device
label. Then inspect and pin the certificate:

```bash
matic tls fingerprint --host "$MATIC_HOST"
export MATIC_CERT_SHA256=VERIFIED_64_CHARACTER_SHA256
matic status
```

The fingerprint is a trust-on-first-use observation. See
[Troubleshooting](https://github.com/Burgess-Software/unofficial-matic-sdk/blob/main/docs/troubleshooting.md#pinning-the-robot-certificate)
for the full verification boundary and certificate-change guidance.

### 4. Read the command protocol version

Reads work without selecting a command protocol. Decode the retained version
model:

```bash
matic collections stream current_version \
  --count 1 \
  --duration 10 \
  --decode \
  --json
```

Use its `protocol_version` in programs that send commands:

```bash
export MATIC_PROTOCOL_VERSION=25
```

Version 25 is the verified baseline. Another positive version is accepted with
an `UnverifiedProtocolVersionWarning` instead of blocking the write.

## Read live robot data

List all 43 accepted targets:

```bash
matic collections list
```

Stream privacy-preserving event metadata:

```bash
matic collections stream latest_pose --count 20 --duration 30
```

Include the registered friendly model as JSON Lines:

```bash
matic collections stream latest_pose \
  --count 20 \
  --duration 30 \
  --decode \
  --json
```

The decoded view omits raw protobuf and media bytes. Names, network/account
data, pairing codes, and stable media hashes are redacted unless
`--include-sensitive` is explicitly supplied.

Record synchronized raw responses into a new private directory:

```bash
matic collections record captures/telemetry \
  --target latest_pose \
  --target kabuki_state \
  --target motor_status \
  --duration 30
```

Decode the saved directory later; its manifest supplies each target:

```bash
matic collections decode captures/telemetry --json
```

For one response file, pass its target:

```bash
matic collections decode event.pb --target latest_pose --pretty
```

## Capture maps, voxels, and media

### Assemble map tiles

```bash
matic collections record captures/map \
  --target map_compressed_rgb \
  --duration 20

matic maps decode captures/map \
  --output decoded-map \
  --target map_compressed_rgb
```

The decoder supports RGB, integrated, combined-coverage, semantic, and
semantic-override captures and assembles them in the canonical app orientation.

<p align="center">
  <img width="720" alt="Decoded Matic map mosaic" src="https://github.com/user-attachments/assets/58efbb9e-4180-4c8d-b38f-ff300f1c86b7" />
</p>
<p align="center"><em>Decoded RGB map tiles assembled into one floor mosaic.</em></p>

### Export the sparse voxel surface

The compressed RGB collection also contains a `32 x 32 x 24` sparse colored
surface representation:

```bash
matic voxels export captures/map --output surface-map.ply
```

Use `--all-depths` for depths hidden by the app's normal surface rendering, or
`--coordinate-mode app-native` to preserve the native coordinate convention.

<p align="center">
  <img width="720" alt="Matic sparse voxel surface" src="https://github.com/user-attachments/assets/df910ffe-de7b-4bc0-bf21-1c3847c25a78" />
</p>
<p align="center"><em>The same capture exported as a colored PLY point cloud.</em></p>

### Extract retained media

```bash
matic collections record captures/thumbnails \
  --target coverage_session_thumbnails \
  --duration 10

matic media extract captures/thumbnails --output extracted-images
```

This recovers validated WebP containers already present in retained responses.
It does not start a recording or provide a live optical-camera stream.

## Use the Python API

Every known collection target decodes to a named immutable model. The in-memory
model also retains `raw_payload` and parsed `fields`, so newer protobuf fields
are not discarded.

```python
import asyncio
import os

from matic_sdk import MaticClient, MaticConfig, TlsConfig
from matic_sdk.models.collections import PoseCollectionModel


async def main() -> None:
    config = MaticConfig(
        host=os.environ["MATIC_HOST"],
        tls=TlsConfig.pinned(os.environ["MATIC_CERT_SHA256"]),
    )
    async with await MaticClient.connect_from_store(
        os.environ["MATIC_DEVICE"], config
    ) as robot:
        event = await robot.first("latest_pose")
        pose = event.decode()
        if isinstance(pose, PoseCollectionModel) and pose.pose is not None:
            print(pose.mission_id, pose.pose.translation, pose.observed_at)


asyncio.run(main())
```

For synchronized real-time control feedback:

```python
async with robot.telemetry() as updates:
    async for update in updates:
        print(update.model)
        latest_pose = update.latest_models.get("latest_pose")
```

The default telemetry set is `latest_pose`, `kabuki_state`, and `motor_status`.
See the runnable
[`decoded_telemetry.py`](https://github.com/Burgess-Software/unofficial-matic-sdk/blob/main/examples/decoded_telemetry.py)
example and the
[collection model reference](https://github.com/Burgess-Software/unofficial-matic-sdk/blob/main/docs/collections.md).

## Control the robot

Writes are direct typed method calls. This complete example sends Dock:

```python
import asyncio
import os

from matic_sdk import MaticClient, MaticConfig, TlsConfig


async def main() -> None:
    config = MaticConfig(
        host=os.environ["MATIC_HOST"],
        command_protocol_version=int(os.environ["MATIC_PROTOCOL_VERSION"]),
        tls=TlsConfig.pinned(os.environ["MATIC_CERT_SHA256"]),
    )
    async with await MaticClient.connect_from_store(
        os.environ["MATIC_DEVICE"], config
    ) as robot:
        receipt = await robot.commands.dock()
        print(receipt.transport.status.value)


asyncio.run(main())
```

Common methods include:

| Task | Methods |
| --- | --- |
| Stationary control | `stop()`, `pause()`, `stay_put()` |
| Motion | `dock()`, `joystick()`, `navigate()`, `navigate_and_wait()` |
| Cleaning | `normal_coverage()`, `stain_mode()`, `reprioritize_coverage()` |
| Maps | `build_partition()`, room edit methods, zone/semantic/sink-location methods |
| Schedules | `add_or_modify_schedule()`, `toggle_schedule()`, `remove_schedule()` |
| Settings | `set_binary_setting()` |
| Cleaning mechanisms | `set_raw_motors()` |

Each `joystick()` call sends one velocity command. The SDK does not repeat it,
expire it, send zero, or Stop automatically:

```python
await robot.commands.joystick(linear_mps=0.05, angular_rad_s=0.0)
await asyncio.sleep(0.5)
await robot.commands.joystick(linear_mps=0.0, angular_rad_s=0.0)
```

Advanced navigation, coverage, reprioritization, map-editing, scheduling,
settings, and raw-motor examples are in the
[SDK recipes](https://github.com/Burgess-Software/unofficial-matic-sdk/blob/main/docs/recipes.md).

## Documentation and examples

- [SDK recipes](https://github.com/Burgess-Software/unofficial-matic-sdk/blob/main/docs/recipes.md)
- [Troubleshooting first connection and captures](https://github.com/Burgess-Software/unofficial-matic-sdk/blob/main/docs/troubleshooting.md)
- [Collection model reference](https://github.com/Burgess-Software/unofficial-matic-sdk/blob/main/docs/collections.md)
- [Command verification ledger](https://github.com/Burgess-Software/unofficial-matic-sdk/blob/main/docs/command-verification.md)
- [Control behavior and caller responsibility](https://github.com/Burgess-Software/unofficial-matic-sdk/blob/main/docs/safety.md)
- [Protocol notes](https://github.com/Burgess-Software/unofficial-matic-sdk/blob/main/docs/protocol.md)
- [Research method](https://github.com/Burgess-Software/unofficial-matic-sdk/blob/main/docs/research-method.md)
- [Experimental remote transport](https://github.com/Burgess-Software/unofficial-matic-sdk/blob/main/docs/remote.md)

Runnable programs:

- [`stream_pose.py`](https://github.com/Burgess-Software/unofficial-matic-sdk/blob/main/examples/stream_pose.py)
- [`decoded_telemetry.py`](https://github.com/Burgess-Software/unofficial-matic-sdk/blob/main/examples/decoded_telemetry.py)
- [`dock.py`](https://github.com/Burgess-Software/unofficial-matic-sdk/blob/main/examples/dock.py)
- [`joystick.py`](https://github.com/Burgess-Software/unofficial-matic-sdk/blob/main/examples/joystick.py)
- [`start_coverage.py`](https://github.com/Burgess-Software/unofficial-matic-sdk/blob/main/examples/start_coverage.py)
- [`create_schedule.py`](https://github.com/Burgess-Software/unofficial-matic-sdk/blob/main/examples/create_schedule.py)
- [`decode_maps.py`](https://github.com/Burgess-Software/unofficial-matic-sdk/blob/main/examples/decode_maps.py)

## Safety and privacy

- BotTokens remain in owner-only files and are never printed by the SDK.
- TLS identity must be verified before authenticated commands are available.
- Map, media, schedule, pose, and collection captures can disclose details
  about a home. Keep them out of source control and public bug reports.
- Capture/export tools create private outputs and refuse accidental overwrite.
- The documented API has no arbitrary-payload command escape hatch and never
  retries an ambiguous command outcome automatically.

## Known boundaries

- Protocol 25 is the command-codec evidence baseline. Other positive versions
  warn and proceed with those codecs as a compatibility attempt.
- Bluetooth enrollment currently requires Linux and BlueZ.
- Some collection variants were observed only in an empty/default state, so
  optional model values remain `None` until that variant is emitted.
- Maps and voxels are decoded from captures; this is not a live map dashboard.
- Raw-motor commands are direct and wire-verified, have no device-specific
  range limits, and have not been exercised live.
- Normal operation is LAN-only; the portal-backed remote transport remains
  experimental.
- The SDK does not provide firmware images, root access, a filesystem, an SSH
  shell, or internal SLAM database access.

## Development

```bash
uv sync --all-extras --group dev
uv run ruff format --check .
uv run ruff check .
uv run mypy src/matic_sdk
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -p pytest_asyncio.plugin
uv run python -m build
uv run python tools/scan_repository.py
```

Keep real household captures, application binaries, robot identifiers, tokens,
maps, recordings, and packet captures out of Git.

## License

No license is granted at this stage.
