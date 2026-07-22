"""Command-line interface for the unofficial Matic SDK."""

from __future__ import annotations

import asyncio
import hashlib
import json
import struct
from collections.abc import Awaitable, Iterable
from pathlib import Path
from typing import Annotated, Any, TypeVar

import typer

from matic_sdk import __version__
from matic_sdk.client import MaticClient
from matic_sdk.config import DEFAULT_HERMES_PORT, MaticConfig, TlsConfig
from matic_sdk.credentials import CredentialStore
from matic_sdk.discovery import BotInformation
from matic_sdk.discovery import probe as probe_endpoint
from matic_sdk.enrollment import enroll as enroll_device
from matic_sdk.protocol.collections import KNOWN_TARGETS, TARGET_GROUPS
from matic_sdk.protocol.commands import COMMAND_REGISTRY, DEFAULT_PROTOCOL_VERSION
from matic_sdk.telemetry import record_telemetry
from matic_sdk.transport.tls import create_ssl_context, verify_peer

app = typer.Typer(
    help="Unofficial tools for an owner-controlled Matic robot.",
    no_args_is_help=True,
    invoke_without_command=True,
    add_completion=False,
)
tls_app = typer.Typer(help="Inspect and configure local TLS identity.")
credential_app = typer.Typer(help="Manage owner-only local enrollment credentials.")
collection_app = typer.Typer(help="List, stream, and record Hermes collections.")
map_app = typer.Typer(help="Decode captured map collection events.")
voxel_app = typer.Typer(help="Export captured sparse colored voxels.")
media_app = typer.Typer(help="Extract retained WebP media from captures.")
control_app = typer.Typer(help="Inspect the fail-closed command surface.")
app.add_typer(tls_app, name="tls")
app.add_typer(credential_app, name="credentials")
app.add_typer(collection_app, name="collections")
app.add_typer(map_app, name="maps")
app.add_typer(voxel_app, name="voxels")
app.add_typer(media_app, name="media")
app.add_typer(control_app, name="control")

T = TypeVar("T")


def _run(awaitable: Awaitable[T]) -> T:
    return asyncio.run(awaitable)


def _terminal_safe(value: object) -> str:
    """Escape control characters before writing peer-influenced errors."""

    escaped: list[str] = []
    for character in str(value):
        if character.isprintable():
            escaped.append(character)
        else:
            codepoint = ord(character)
            escaped.append(
                f"\\x{codepoint:02x}" if codepoint <= 0xFF else f"\\u{codepoint:04x}"
            )
    return "".join(escaped)


def _abort(error: Exception) -> None:
    typer.echo(f"Error: {_terminal_safe(error)}", err=True)
    raise typer.Exit(1) from error


def _tls_config(
    *,
    certificate_sha256: str | None,
    ca_file: Path | None,
    insecure_read_only: bool,
) -> TlsConfig:
    selected = sum(
        (
            certificate_sha256 is not None,
            ca_file is not None,
            insecure_read_only,
        )
    )
    if selected > 1:
        raise typer.BadParameter(
            "choose only one of --certificate-sha256, --ca-file, or "
            "--insecure-read-only"
        )
    if certificate_sha256:
        return TlsConfig.pinned(certificate_sha256)
    if insecure_read_only:
        return TlsConfig.insecure_diagnostics()
    return TlsConfig(ca_file=ca_file)


def _config(
    *,
    host: str,
    port: int,
    authority: str | None,
    server_name: str | None,
    certificate_sha256: str | None,
    ca_file: Path | None,
    insecure_read_only: bool,
) -> MaticConfig:
    return MaticConfig(
        host=host,
        port=port,
        authority=authority,
        sni=server_name,
        tls=_tls_config(
            certificate_sha256=certificate_sha256,
            ca_file=ca_file,
            insecure_read_only=insecure_read_only,
        ),
    )


def _inputs(paths: Iterable[Path]) -> tuple[Path, ...]:
    resolved: list[Path] = []
    for path in paths:
        candidate = path.expanduser()
        if candidate.is_dir():
            resolved.extend(sorted(candidate.glob("*.pb")))
        elif candidate.is_file():
            resolved.append(candidate)
        else:
            raise FileNotFoundError(candidate)
    if not resolved:
        raise ValueError("no capture files were found")
    return tuple(resolved)


def _messages(paths: Iterable[Path]) -> Iterable[bytes]:
    for path in _inputs(paths):
        data = path.read_bytes()
        frames = _split_grpc_frames(data)
        yield from frames or (data,)


def _split_grpc_frames(data: bytes) -> tuple[bytes, ...] | None:
    """Split complete uncompressed frames without importing the maps extra."""

    if len(data) < 5 or data[0] != 0:
        return None
    frames: list[bytes] = []
    offset = 0
    while offset < len(data):
        if offset + 5 > len(data) or data[offset] != 0:
            return None
        length = struct.unpack_from(">I", data, offset + 1)[0]
        start = offset + 5
        end = start + length
        if end > len(data):
            return None
        frames.append(data[start:end])
        offset = end
    return tuple(frames)


def _event_summary(event: Any) -> dict[str, object]:
    payload = event.payload
    return {
        "target": event.target,
        "operation": event.operation.value,
        "received_at": event.received_at.isoformat(),
        "sequence_no": event.sequence_id.sequence_no if event.sequence_id else None,
        "key_sha256": hashlib.sha256(event.key).hexdigest(),
        "payload_bytes": len(payload) if payload is not None else 0,
        "payload_sha256": hashlib.sha256(payload).hexdigest() if payload else None,
    }


def _info_dict(info: BotInformation) -> dict[str, object]:
    return {
        "serial_number": info.serial_number,
        "ipv4_address": info.ipv4_address,
        "ipv6_address": info.ipv6_address,
        "encrypted": info.encrypted,
        "requires_auth": info.requires_auth,
        "network_auth": info.network_auth,
        "hardware_revision": info.hardware_revision,
    }


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option("--version", help="Print the SDK version and exit."),
    ] = False,
) -> None:
    if version:
        typer.echo(__version__)
        raise typer.Exit


@app.command()
def enroll(
    device: Annotated[
        str,
        typer.Option("--device", help="Local alias used for this robot."),
    ],
    address: Annotated[
        str | None,
        typer.Option(help="Optional BLE address selected from the scan."),
    ] = None,
    name: Annotated[
        str | None,
        typer.Option(help="Optional exact BLE advertisement name."),
    ] = None,
    credential_root: Annotated[
        Path | None,
        typer.Option(help="Override the owner-only XDG credential directory."),
    ] = None,
    scan_timeout: Annotated[
        float,
        typer.Option(min=0.1, help="Seconds to scan for the token service."),
    ] = 10.0,
    connect_timeout: Annotated[
        float,
        typer.Option(min=1.0, help="Seconds allowed for pairing and enrollment."),
    ] = 60.0,
) -> None:
    """Pair over Linux Bluetooth and save a new BotToken privately."""

    try:
        store = CredentialStore(device, root=credential_root)
        result = _run(
            enroll_device(
                store,
                address=address,
                name=name,
                scan_timeout=scan_timeout,
                timeout=connect_timeout,
            )
        )
    except Exception as error:
        _abort(error)
    typer.echo(
        f"Enrolled {result.device_id!r}; credentials saved at {result.token_path}"
    )


@credential_app.command("import-token")
def import_token(
    device: Annotated[str, typer.Option(help="Local alias used for this robot.")],
    source: Annotated[
        Path,
        typer.Option(help="Existing owner-only serialized BotToken file."),
    ],
    credential_root: Annotated[
        Path | None,
        typer.Option(help="Override the owner-only XDG credential directory."),
    ] = None,
) -> None:
    """Import an existing mode-0600 BotToken without printing its contents."""

    try:
        store = CredentialStore(device, root=credential_root)
        store.import_token(source)
    except Exception as error:
        _abort(error)
    typer.echo(f"Imported credentials for {device!r} into {store.paths.directory}")


@credential_app.command("status")
def credential_status(
    device: Annotated[str, typer.Option(help="Local alias used for this robot.")],
    credential_root: Annotated[Path | None, typer.Option()] = None,
) -> None:
    """Show whether an alias is enrolled without reading or printing its secret."""

    try:
        store = CredentialStore(device, root=credential_root)
        enrolled = store.enrolled
        if enrolled:
            store.load_token()
    except Exception as error:
        _abort(error)
    typer.echo(
        json.dumps(
            {
                "device": device,
                "enrolled": enrolled,
                "directory": str(store.paths.directory),
            },
            indent=2,
        )
    )


@tls_app.command("fingerprint")
def tls_fingerprint(
    host: Annotated[str, typer.Option(envvar="MATIC_HOST", help="Robot host or IP.")],
    port: Annotated[
        int,
        typer.Option(envvar="MATIC_PORT", min=1, max=65535),
    ] = DEFAULT_HERMES_PORT,
    server_name: Annotated[
        str | None,
        typer.Option("--server-name", envvar="MATIC_SERVER_NAME"),
    ] = None,
    timeout: Annotated[float, typer.Option(min=0.1)] = 10.0,
) -> None:
    """Fetch the unauthenticated TLS certificate fingerprint for TOFU review."""

    async def fetch() -> str:
        policy = TlsConfig.insecure_diagnostics()
        context = create_ssl_context(policy)
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(
                host,
                port,
                ssl=context,
                server_hostname=server_name or host,
            ),
            timeout=timeout,
        )
        del reader
        try:
            ssl_object = writer.get_extra_info("ssl_object")
            if ssl_object is None:
                raise RuntimeError("peer did not provide a TLS certificate")
            return verify_peer(ssl_object, policy).certificate_sha256
        finally:
            writer.close()
            await writer.wait_closed()

    try:
        fingerprint = _run(fetch())
    except Exception as error:
        _abort(error)
    typer.echo(fingerprint)
    typer.echo("Verify this fingerprint independently before trusting it.", err=True)


@app.command()
def probe(
    host: Annotated[str, typer.Option(envvar="MATIC_HOST", help="Robot host or IP.")],
    port: Annotated[
        int,
        typer.Option(envvar="MATIC_PORT", min=1, max=65535),
    ] = DEFAULT_HERMES_PORT,
    authority: Annotated[
        str | None,
        typer.Option(envvar="MATIC_AUTHORITY", help="HTTP/2 :authority override."),
    ] = None,
    server_name: Annotated[
        str | None,
        typer.Option("--server-name", envvar="MATIC_SERVER_NAME"),
    ] = None,
    certificate_sha256: Annotated[
        str | None,
        typer.Option("--certificate-sha256", envvar="MATIC_CERT_SHA256"),
    ] = None,
    ca_file: Annotated[Path | None, typer.Option(envvar="MATIC_CA_FILE")] = None,
    insecure_read_only: Annotated[
        bool,
        typer.Option(help="Skip identity verification for this unauthenticated probe."),
    ] = False,
) -> None:
    """Read the robot's unauthenticated Hermes identity response."""

    try:
        config = _config(
            host=host,
            port=port,
            authority=authority,
            server_name=server_name,
            certificate_sha256=certificate_sha256,
            ca_file=ca_file,
            insecure_read_only=insecure_read_only,
        )
        info = _run(probe_endpoint(config))
    except Exception as error:
        _abort(error)
    typer.echo(json.dumps(_info_dict(info), indent=2))


async def _connect(
    device: str,
    config: MaticConfig,
    credential_root: Path | None,
) -> MaticClient:
    return await MaticClient.connect_from_store(
        device,
        config,
        credential_root=str(credential_root) if credential_root else None,
    )


@app.command()
def status(
    device: Annotated[str, typer.Option(envvar="MATIC_DEVICE")],
    host: Annotated[str, typer.Option(envvar="MATIC_HOST")],
    port: Annotated[
        int,
        typer.Option(envvar="MATIC_PORT", min=1, max=65535),
    ] = DEFAULT_HERMES_PORT,
    authority: Annotated[str | None, typer.Option(envvar="MATIC_AUTHORITY")] = None,
    server_name: Annotated[
        str | None,
        typer.Option("--server-name", envvar="MATIC_SERVER_NAME"),
    ] = None,
    certificate_sha256: Annotated[
        str | None,
        typer.Option("--certificate-sha256", envvar="MATIC_CERT_SHA256"),
    ] = None,
    ca_file: Annotated[Path | None, typer.Option(envvar="MATIC_CA_FILE")] = None,
    credential_root: Annotated[Path | None, typer.Option()] = None,
) -> None:
    """Authenticate, handshake, and show the local robot identity."""

    async def run() -> BotInformation:
        config = _config(
            host=host,
            port=port,
            authority=authority,
            server_name=server_name,
            certificate_sha256=certificate_sha256,
            ca_file=ca_file,
            insecure_read_only=False,
        )
        async with await _connect(device, config, credential_root) as robot:
            return await robot.bot_info()

    try:
        info = _run(run())
    except Exception as error:
        _abort(error)
    typer.echo(json.dumps(_info_dict(info), indent=2))


@collection_app.command("list")
def list_collections() -> None:
    """List the 43 live-verified read-only collection targets."""

    for group, targets in TARGET_GROUPS.items():
        typer.echo(f"{group}:")
        for target in targets:
            typer.echo(f"  {target}")


@collection_app.command()
def stream(
    target: Annotated[str, typer.Argument(help="Verified Hermes collection target.")],
    device: Annotated[str, typer.Option(envvar="MATIC_DEVICE")],
    host: Annotated[str, typer.Option(envvar="MATIC_HOST")],
    count: Annotated[int, typer.Option(min=1)] = 10,
    duration: Annotated[float, typer.Option(min=0.1)] = 10.0,
    port: Annotated[int, typer.Option(envvar="MATIC_PORT")] = DEFAULT_HERMES_PORT,
    authority: Annotated[str | None, typer.Option(envvar="MATIC_AUTHORITY")] = None,
    server_name: Annotated[
        str | None,
        typer.Option("--server-name", envvar="MATIC_SERVER_NAME"),
    ] = None,
    certificate_sha256: Annotated[
        str | None,
        typer.Option("--certificate-sha256", envvar="MATIC_CERT_SHA256"),
    ] = None,
    ca_file: Annotated[Path | None, typer.Option(envvar="MATIC_CA_FILE")] = None,
    credential_root: Annotated[Path | None, typer.Option()] = None,
) -> None:
    """Stream privacy-safe metadata; raw payloads are not printed."""

    if target not in KNOWN_TARGETS:
        raise typer.BadParameter(f"unknown target: {target}", param_hint="target")

    async def run() -> None:
        config = _config(
            host=host,
            port=port,
            authority=authority,
            server_name=server_name,
            certificate_sha256=certificate_sha256,
            ca_file=ca_file,
            insecure_read_only=False,
        )
        async with await _connect(device, config, credential_root) as robot:
            async with await robot.collections.subscribe(target) as events:
                deadline = asyncio.get_running_loop().time() + duration
                for _ in range(count):
                    remaining = deadline - asyncio.get_running_loop().time()
                    if remaining <= 0:
                        return
                    try:
                        event = await asyncio.wait_for(anext(events), remaining)
                    except TimeoutError:
                        return
                    typer.echo(json.dumps(_event_summary(event), separators=(",", ":")))

    try:
        _run(run())
    except Exception as error:
        _abort(error)


@collection_app.command()
def record(
    output: Annotated[Path, typer.Argument(help="New owner-only output directory.")],
    device: Annotated[str, typer.Option(envvar="MATIC_DEVICE")],
    host: Annotated[str, typer.Option(envvar="MATIC_HOST")],
    target: Annotated[
        list[str] | None,
        typer.Option("--target", help="Repeat for concurrent targets."),
    ] = None,
    duration: Annotated[float, typer.Option(min=0.1)] = 10.0,
    max_events: Annotated[int, typer.Option(min=1)] = 10_000,
    port: Annotated[int, typer.Option(envvar="MATIC_PORT")] = DEFAULT_HERMES_PORT,
    authority: Annotated[str | None, typer.Option(envvar="MATIC_AUTHORITY")] = None,
    server_name: Annotated[
        str | None,
        typer.Option("--server-name", envvar="MATIC_SERVER_NAME"),
    ] = None,
    certificate_sha256: Annotated[
        str | None,
        typer.Option("--certificate-sha256", envvar="MATIC_CERT_SHA256"),
    ] = None,
    ca_file: Annotated[Path | None, typer.Option(envvar="MATIC_CA_FILE")] = None,
    credential_root: Annotated[Path | None, typer.Option()] = None,
) -> None:
    """Record raw collection responses and a manifest with private permissions."""

    targets = tuple(target or ("latest_pose", "kabuki_state", "motor_status"))
    unknown = sorted(set(targets) - set(KNOWN_TARGETS))
    if unknown:
        raise typer.BadParameter(f"unknown targets: {', '.join(unknown)}")

    async def run() -> Path:
        config = _config(
            host=host,
            port=port,
            authority=authority,
            server_name=server_name,
            certificate_sha256=certificate_sha256,
            ca_file=ca_file,
            insecure_read_only=False,
        )
        async with await _connect(device, config, credential_root) as robot:
            return await record_telemetry(
                robot,
                output,
                targets=targets,
                duration=duration,
                max_events=max_events,
            )

    try:
        written = _run(run())
    except Exception as error:
        _abort(error)
    typer.echo(str(written))


@map_app.command("decode")
def decode_maps(
    inputs: Annotated[list[Path], typer.Argument(help="Capture files or directories.")],
    output: Annotated[Path, typer.Option(help="Directory for private PNG mosaics.")],
    target: Annotated[str, typer.Option()] = "auto",
    orientation: Annotated[str, typer.Option()] = "canonical",
    scale: Annotated[int, typer.Option(min=1)] = 1,
    y_up: Annotated[bool, typer.Option(help="Place increasing map Y upward.")] = False,
) -> None:
    """Decode collection responses and assemble correctly oriented map tiles."""

    from matic_sdk.maps import (
        SUPPORTED_MAP_TARGETS,
        MapCollectionState,
        build_mosaics,
        save_mosaics,
    )

    valid_targets = {"auto", *SUPPORTED_MAP_TARGETS}
    if target not in valid_targets:
        raise typer.BadParameter(f"unsupported map target: {target}")
    if orientation not in {"canonical", "native"}:
        raise typer.BadParameter("orientation must be canonical or native")
    try:
        state = MapCollectionState(target=target)  # type: ignore[arg-type]
        warnings: list[str] = []
        for message in _messages(inputs):
            warnings.extend(state.apply_message(message).warnings)
        mosaics = build_mosaics(
            state.tiles,
            scale=scale,
            orientation=orientation,  # type: ignore[arg-type]
            y_down=not y_up,
        )
        if not mosaics:
            detail = "; ".join(dict.fromkeys(warnings)) or "no map tiles decoded"
            raise ValueError(detail)
        paths = save_mosaics(mosaics, output)
    except Exception as error:
        _abort(error)
    for warning in dict.fromkeys(warnings):
        typer.echo(f"Warning: {warning}", err=True)
    for path in paths:
        typer.echo(str(path))


@voxel_app.command("export")
def export_voxels(
    inputs: Annotated[list[Path], typer.Argument(help="Compressed RGB captures.")],
    output: Annotated[Path, typer.Option(help="Destination binary PLY file.")],
    all_depths: Annotated[
        bool,
        typer.Option(help="Include depths hidden by the official visualizer."),
    ] = False,
    coordinate_mode: Annotated[str, typer.Option()] = "centered",
) -> None:
    """Decode the actual 32x32x24 sparse surface representation to PLY."""

    from matic_sdk.voxels import VoxelCollectionState, export_ply

    if coordinate_mode not in {"centered", "app-native"}:
        raise typer.BadParameter("coordinate mode must be centered or app-native")
    try:
        state = VoxelCollectionState()
        for message in _messages(inputs):
            state.apply_message(message)
        summary = export_ply(
            state.tiles,
            output,
            all_depths=all_depths,
            coordinate_mode=coordinate_mode,  # type: ignore[arg-type]
        )
    except Exception as error:
        _abort(error)
    typer.echo(
        json.dumps(
            {
                "path": str(output),
                "mission_id": summary.mission_id,
                "tiles": summary.tile_count,
                "surface_voxels": summary.surface_voxels,
                "visible_voxels": summary.visible_voxels,
                "exported_voxels": summary.exported_voxels,
                "coordinate_mode": summary.coordinate_mode,
            },
            indent=2,
        )
    )


@media_app.command("extract")
def extract_media(
    inputs: Annotated[list[Path], typer.Argument(help="Capture files or directories.")],
    output: Annotated[Path, typer.Option(help="Directory for extracted WebP files.")],
) -> None:
    """Find and save validated WebP containers embedded in captured responses."""

    from matic_sdk.media import extract_embedded_webps, save_embedded_webps

    try:
        images = tuple(
            image
            for message in _messages(inputs)
            for image in extract_embedded_webps(message)
        )
        if not images:
            raise ValueError("no validated WebP containers found")
        paths = save_embedded_webps(images, output)
    except Exception as error:
        _abort(error)
    typer.echo(f"Extracted {len(paths)} image(s).")
    for path in paths:
        typer.echo(str(path))


@control_app.command("list")
def list_controls() -> None:
    """List recovered command intents and whether a proven codec exists."""

    for spec in COMMAND_REGISTRY.specs.values():
        typer.echo(
            "\t".join(
                (
                    spec.key,
                    spec.family.value,
                    spec.risk.value,
                    spec.evidence_level.value,
                    "available" if spec.codec_available else "fail-closed",
                )
            )
        )


@control_app.command("status")
def control_status() -> None:
    """Report command protocol compatibility without sending anything."""

    specs = tuple(COMMAND_REGISTRY.specs.values())
    available = sum(spec.codec_available for spec in specs)
    typer.echo(
        json.dumps(
            {
                "observed_app_protocol_version": DEFAULT_PROTOCOL_VERSION,
                "sdk_default_command_protocol_version": None,
                "documented_intents": len(specs),
                "wire_verified_codecs": available,
                "stationary_stop_enabled": COMMAND_REGISTRY.spec_for(
                    "user.stop"
                ).codec_available,
                "motion_control_enabled": False,
                "remaining_commands": "fail-closed pending command-specific proof",
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    app()
