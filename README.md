# Unofficial Matic SDK

An async Python SDK and command-line client for an owner-controlled Matic robot.

> [!WARNING]
> This project is unofficial, unaffiliated with Matic Robots or Matician, and
> based on independent protocol research. It is alpha software for hardware
> you own. Commands can move the robot or change persistent settings.

The repository packages the useful, repeatable parts of the research into a
credential-safe library. It supports Bluetooth enrollment on Linux, direct
authenticated Hermes access on the local network, live collection streams,
map and sparse-voxel decoding, retained-media extraction, and a deliberately
gated command surface.

## Status

| Capability | Status |
| --- | --- |
| Linux Bluetooth enrollment | Protocol verified on BlueZ; packaged implementation is synthetic-tested |
| Local TLS, HTTP/2, gRPC and BotToken authentication | Verified |
| Read-only Hermes collections | 43 targets accepted by a live robot |
| RGB, integrated, semantic and coverage maps | Verified offline from live captures |
| Sparse colored surface voxels | Verified offline from live captures |
| Direct laptop-originated commands | Typed and guarded, but wire codecs fail closed pending proof |
| Portal-backed remote transport | Experimental and not currently reliable |
| Firmware, root, filesystem or SSH access | Not available |

See [how the protocol was recovered](docs/research-method.md),
[capability status](docs/status.md), [protocol notes](docs/protocol.md), the
[control safety model](docs/safety.md), and the explicitly
[experimental remote path](docs/remote.md) before using those APIs.

## Install for development

```bash
git clone git@github.com:Burgess-Software/unofficial-matic-sdk.git
cd unofficial-matic-sdk
uv sync --all-extras --group dev
uv run matic --help
```

The repository is private and no package is published. A license is
intentionally not granted at this stage.

## Credentials

Normal operation uses a per-client serialized `BotToken` obtained during an
explicit Bluetooth enrollment session. The SDK stores secrets in an
owner-only directory and refuses credential files readable by another user.
Bluetooth is not needed for subsequent local-network sessions.

Never place a token in a shell argument, `.env` file, issue, log, fixture, or
Git commit.

```bash
# Put the robot in Bluetooth pairing mode first. The SDK creates its own UUID.
uv run matic enroll --device living-room

# Or migrate a previously enrolled, owner-only token without exposing it.
uv run matic credentials import-token \
  --device living-room \
  --source /secure/path/to/bot-token.pb

# Fetch the TLS fingerprint without sending credentials, verify it separately,
# then use it for authenticated local access.
uv run matic tls fingerprint --host ROBOT_HOST
uv run matic status \
  --device living-room \
  --host ROBOT_HOST \
  --certificate-sha256 VERIFIED_SHA256
```

## Capture and decode a map

Raw map captures can reveal the layout and contents of a home. Keep them out of
the repository and delete or archive them securely when finished. The recorder
creates a new owner-only directory and refuses to overwrite an existing one.

```bash
uv run matic collections record private-map-capture \
  --device living-room \
  --host ROBOT_HOST \
  --certificate-sha256 VERIFIED_SHA256 \
  --target map_compressed_rgb \
  --duration 10

uv run matic maps decode private-map-capture \
  --output decoded-map \
  --target map_compressed_rgb

uv run matic voxels export private-map-capture \
  --output surface-map.ply
```

The `maps decode` command auto-selects well-known map targets. For ambiguous
`map_combined_coverage`, `map_semantics`, or `map_semantics_override` captures,
pass the exact `--target` explicitly.

## Python API

```python
import asyncio

from matic_sdk import MaticClient, MaticConfig, TlsConfig


async def main() -> None:
    config = MaticConfig(
        host="ROBOT_HOST",
        tls=TlsConfig.pinned("VERIFIED_CERTIFICATE_SHA256"),
    )
    async with await MaticClient.connect_from_store("living-room", config) as robot:
        async with await robot.collections.subscribe("latest_pose") as events:
            async for event in events:
                print(event)


asyncio.run(main())
```

Command intent APIs already enforce protocol, TLS, motion, and hazardous-action
guards. The default registry does not yet contain a proven command encoder, so
attempted sends fail before network I/O. There is no unrestricted public
raw-command escape hatch.

```python
# With no observed protocol configured, this fails before any command I/O.
await robot.commands.stop()
```

## Development

```bash
uv run ruff check .
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -p pytest_asyncio.plugin
uv run python -m build
```

Tests and documentation use synthetic protocol data only. Real household
captures, application binaries, robot identifiers, tokens, maps, recordings,
and packet captures are intentionally excluded.
