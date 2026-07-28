from __future__ import annotations

import pytest

from matic_sdk.models.control import (
    DeviceAction,
    DeviceCommand,
    JukeboxTrack,
    MapEnvironmentAction,
    MapEnvironmentCommand,
    MediaAction,
    MediaCommand,
    SettingAction,
    SettingsCommand,
    TelemetryAction,
    TelemetryCommand,
    WifiAction,
    WifiCommand,
)
from matic_sdk.protocol.commands import EncodedCommand, encode_command


@pytest.mark.parametrize(
    ("command", "target", "payload_hex"),
    [
        (
            MapEnvironmentCommand(MapEnvironmentAction.CANONICALIZE),
            "floor_command",
            "0a021200",
        ),
        (
            MapEnvironmentCommand(
                MapEnvironmentAction.CANONICALIZE,
                mission_id=42,
            ),
            "floor_command",
            "0a070a05152a000000",
        ),
        (
            MapEnvironmentCommand(
                MapEnvironmentAction.RENAME,
                mission_id=42,
                name="Upstairs",
            ),
            "floor_command",
            "1a110a05152a00000012085570737461697273",
        ),
        (
            MapEnvironmentCommand(MapEnvironmentAction.PERSISTENCE_CLEAR),
            "map_command",
            "1200",
        ),
        (
            MapEnvironmentCommand(
                MapEnvironmentAction.CLEAR_MAP,
                mission_id=42,
            ),
            "map_command",
            "2205152a000000",
        ),
        (
            MapEnvironmentCommand(MapEnvironmentAction.RESTORE_MAP),
            "map_command",
            "080432021200",
        ),
        (
            MapEnvironmentCommand(MapEnvironmentAction.UPLOAD_MAP_FOR_DEBUG),
            "map_command",
            "08053a021a00",
        ),
    ],
)
def test_floor_and_persistence_commands_match_native_goldens(
    command: MapEnvironmentCommand,
    target: str,
    payload_hex: str,
) -> None:
    assert encode_command(command, protocol_version=25) == EncodedCommand(
        bytes.fromhex(payload_hex),
        target,
    )


@pytest.mark.parametrize(
    ("command", "payload_hex"),
    [
        (
            WifiCommand(WifiAction.CONNECT, ssid="Net", passphrase="pw"),
            "080112034e65741a02707732034e6574",
        ),
        (
            WifiCommand(WifiAction.CONNECT, ssid="Net"),
            "080112034e657432034e6574",
        ),
        (
            WifiCommand(WifiAction.FORGET, ssid="Net"),
            "080312034e657432034e6574",
        ),
    ],
)
def test_wifi_update_commands_match_native_goldens(
    command: WifiCommand,
    payload_hex: str,
) -> None:
    assert encode_command(command, protocol_version=25) == EncodedCommand(
        bytes.fromhex(payload_hex),
        "wifi_update_command",
    )


@pytest.mark.parametrize(
    ("command", "target", "payload_hex"),
    [
        (
            DeviceCommand(DeviceAction.RENAME, new_name="Matic"),
            "new_bot_name",
            "0a054d61746963",
        ),
        (
            DeviceCommand(
                DeviceAction.DISCOVERABILITY,
                enabled=True,
                discoverable_seconds=60,
            ),
            "set_device_discoverable",
            "083c",
        ),
        (
            DeviceCommand(DeviceAction.DISCOVERABILITY, enabled=False),
            "set_device_discoverable",
            "1200",
        ),
        (
            DeviceCommand(DeviceAction.NEW_MOP_ROLL, enabled=True),
            "new_mop_roll_override_command",
            "0801",
        ),
        (
            DeviceCommand(DeviceAction.NEW_MOP_ROLL, enabled=False),
            "new_mop_roll_override_command",
            "",
        ),
    ],
)
def test_device_commands_match_native_goldens(
    command: DeviceCommand,
    target: str,
    payload_hex: str,
) -> None:
    assert encode_command(command, protocol_version=25) == EncodedCommand(
        bytes.fromhex(payload_hex),
        target,
    )


@pytest.mark.parametrize(
    ("command", "target", "payload_hex"),
    [
        (
            SettingsCommand(SettingAction.AUTO_RECORD_VOICE, False),
            "auto_record_voice_enabled_command",
            "",
        ),
        (
            SettingsCommand(SettingAction.AUTO_RECORD_VOICE, True),
            "auto_record_voice_enabled_command",
            "0801",
        ),
        (
            SettingsCommand(SettingAction.MATTER_PAIRING, True),
            "matter_pairing_command",
            "0801",
        ),
        (
            SettingsCommand(SettingAction.PREVIEW_RELEASE, True),
            "request_preview_release_command",
            "0801",
        ),
        (
            SettingsCommand(SettingAction.JUKEBOX, None),
            "jukebox_command",
            "",
        ),
        (
            SettingsCommand(
                SettingAction.JUKEBOX,
                JukeboxTrack.OH_HANUKKAH,
            ),
            "jukebox_command",
            "0800",
        ),
        (
            SettingsCommand(
                SettingAction.JUKEBOX,
                JukeboxTrack.DECK_THE_HALLS,
            ),
            "jukebox_command",
            "0801",
        ),
        (
            SettingsCommand(
                SettingAction.JUKEBOX,
                JukeboxTrack.JINGLE_BELLS,
            ),
            "jukebox_command",
            "0802",
        ),
    ],
)
def test_additional_setting_commands_match_native_goldens(
    command: SettingsCommand,
    target: str,
    payload_hex: str,
) -> None:
    assert encode_command(command, protocol_version=25) == EncodedCommand(
        bytes.fromhex(payload_hex),
        target,
    )


@pytest.mark.parametrize(
    ("command", "target", "payload_hex"),
    [
        (
            MediaCommand(MediaAction.RECORDING_ENABLE, enabled=True),
            "recording_command",
            "22020801",
        ),
        (
            MediaCommand(MediaAction.RECORDING_ENABLE, enabled=False),
            "recording_command",
            "2200",
        ),
        (
            MediaCommand(
                MediaAction.ROLLING_BUFFER_CONFIG,
                enabled=True,
                confirm_for_each=False,
            ),
            "toggle_rolling_recordings",
            "0a00",
        ),
        (
            MediaCommand(
                MediaAction.ROLLING_BUFFER_CONFIG,
                enabled=True,
                confirm_for_each=True,
            ),
            "toggle_rolling_recordings",
            "1200",
        ),
        (
            MediaCommand(
                MediaAction.ROLLING_BUFFER_CONFIG,
                enabled=False,
            ),
            "toggle_rolling_recordings",
            "1a00",
        ),
        (
            MediaCommand(MediaAction.FLUSH_ROLLING_BUFFER),
            "recording_command",
            "1a00",
        ),
        (
            MediaCommand(MediaAction.CONFIRM_SAVE, recording_id=42),
            "recording_upload_confirmation",
            "08001202082a",
        ),
        (
            MediaCommand(MediaAction.CONFIRM_DELETE, recording_id=42),
            "recording_upload_confirmation",
            "08011202082a",
        ),
    ],
)
def test_media_commands_match_native_goldens(
    command: MediaCommand,
    target: str,
    payload_hex: str,
) -> None:
    assert encode_command(command, protocol_version=25) == EncodedCommand(
        bytes.fromhex(payload_hex),
        target,
    )


@pytest.mark.parametrize(
    ("command", "target", "payload_hex"),
    [
        (
            TelemetryCommand(TelemetryAction.UPLOADER_CONFIG, enabled=False),
            "uploader_config_command",
            "0800",
        ),
        (
            TelemetryCommand(TelemetryAction.UPLOADER_CONFIG, enabled=True),
            "uploader_config_command",
            "0801",
        ),
        (
            TelemetryCommand(
                TelemetryAction.SUPPORT_SSH_PERMISSION,
                enabled=False,
            ),
            "user_tunnel_ssh_permission_command",
            "",
        ),
        (
            TelemetryCommand(
                TelemetryAction.SUPPORT_SSH_PERMISSION,
                enabled=True,
            ),
            "user_tunnel_ssh_permission_command",
            "0801",
        ),
        (
            TelemetryCommand(
                TelemetryAction.PUSH_NOTIFICATION_SUBSCRIPTION,
                device_id="dev",
                app_bundle="app",
            ),
            "subscribe_push_notifications",
            "0a036465761a036170702001",
        ),
    ],
)
def test_telemetry_commands_match_native_goldens(
    command: TelemetryCommand,
    target: str,
    payload_hex: str,
) -> None:
    assert encode_command(command, protocol_version=25) == EncodedCommand(
        bytes.fromhex(payload_hex),
        target,
    )
