from __future__ import annotations

import os
import stat
import uuid
from datetime import UTC, datetime
from importlib.metadata import version as package_version
from types import SimpleNamespace

from typer.testing import CliRunner

from matic_sdk import __version__
from matic_sdk.cli import _event_summary, _terminal_safe, app
from matic_sdk.credentials import serialize_token_request
from matic_sdk.protocol.collections import KNOWN_TARGETS
from matic_sdk.protocol.commands import COMMAND_REGISTRY
from matic_sdk.protocol.wire import encode_bytes_field

runner = CliRunner()


def test_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == __version__
    assert package_version("unofficial-matic-sdk") == __version__


def test_terminal_output_escapes_control_characters() -> None:
    assert _terminal_safe("before\x1b]52;payload\x07after") == (
        "before\\x1b]52;payload\\x07after"
    )


def test_collection_summary_omits_stable_key_and_payload_hashes() -> None:
    payload = b"dictionary-guessable-state"
    event = SimpleNamespace(
        target="latest_pose",
        operation=SimpleNamespace(value="upsert"),
        received_at=datetime(2026, 1, 1, tzinfo=UTC),
        sequence_id=SimpleNamespace(sequence_no=7),
        key=b"stable-device-key",
        payload=payload,
    )

    summary = _event_summary(event)

    assert summary == {
        "target": "latest_pose",
        "operation": "upsert",
        "received_at": "2026-01-01T00:00:00+00:00",
        "sequence_no": 7,
        "payload_bytes": len(payload),
    }
    assert all("sha256" not in key for key in summary)


def test_map_command_without_extra_returns_install_hint(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setattr("matic_sdk.cli.find_spec", lambda _name: None)

    result = runner.invoke(
        app,
        [
            "maps",
            "decode",
            str(tmp_path / "capture"),
            "--output",
            str(tmp_path / "out"),
        ],
    )

    assert result.exit_code != 0
    assert "unofficial-matic-sdk[maps]" in result.output
    assert "Traceback" not in result.output


def test_collection_inventory_is_exposed() -> None:
    result = runner.invoke(app, ["collections", "list"])
    assert result.exit_code == 0
    assert all(target in result.stdout for target in KNOWN_TARGETS)


def test_control_status_reports_complete_wire_codec_inventory() -> None:
    result = runner.invoke(app, ["control", "status"])
    assert result.exit_code == 0
    assert '"wire_verified_commands": 65' in result.stdout
    assert '"registered_codecs": 65' in result.stdout
    assert '"live_delivery_verified_commands": 10' in result.stdout
    assert '"stationary_stop_enabled": true' in result.stdout
    assert '"motion_codecs_available": 16' in result.stdout
    assert '"direct_joystick_enabled": true' in result.stdout
    assert '"remaining_fail_closed_commands": 0' in result.stdout


def test_control_inventory_is_complete() -> None:
    result = runner.invoke(app, ["control", "list"])
    assert result.exit_code == 0
    assert len(result.stdout.strip().splitlines()) == len(COMMAND_REGISTRY.specs)
    assert "user.stop" in result.stdout
    assert "fail-closed" not in result.stdout


def test_unknown_collection_is_rejected_before_network_io() -> None:
    result = runner.invoke(
        app,
        [
            "collections",
            "stream",
            "not-a-target",
            "--device",
            "test",
            "--host",
            "robot.invalid",
        ],
    )
    assert result.exit_code != 0
    assert "unknown target" in result.output


def test_existing_token_import_never_prints_secret(tmp_path) -> None:
    client_id = str(uuid.UUID(int=2))
    secret = b"synthetic-secret"
    serialized = encode_bytes_field(
        1, serialize_token_request(client_id)
    ) + encode_bytes_field(2, secret)
    source = tmp_path / "existing.pb"
    source.write_bytes(serialized)
    os.chmod(source, 0o600)
    root = tmp_path / "store"

    result = runner.invoke(
        app,
        [
            "credentials",
            "import-token",
            "--device",
            "test-device",
            "--source",
            str(source),
            "--credential-root",
            str(root),
        ],
    )

    assert result.exit_code == 0
    assert "synthetic-secret" not in result.output
    saved = root / "devices" / "test-device" / "bot-token.pb"
    assert saved.read_bytes() == serialized
    assert stat.S_IMODE(saved.stat().st_mode) == 0o600


def test_media_extract_fails_without_creating_output_for_zero_results(tmp_path) -> None:
    source = tmp_path / "capture.dat"
    source.write_bytes(b"no image data")
    output = tmp_path / "media"

    result = runner.invoke(
        app,
        ["media", "extract", str(source), "--output", str(output)],
    )

    assert result.exit_code != 0
    assert "no validated WebP" in result.output
    assert not output.exists()
