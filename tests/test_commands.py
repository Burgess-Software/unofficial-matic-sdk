from __future__ import annotations

import asyncio
import json
import math
import stat
import struct
from base64 import b64decode
from dataclasses import replace
from pathlib import Path
from uuid import UUID

import pytest

from matic_sdk.client import MaticClient
from matic_sdk.commands import (
    CommandExecutor,
    CommandOutcomeUnknown,
    JsonlAuditLog,
    UnverifiedCommandTransport,
)
from matic_sdk.config import MaticConfig, TlsConfig
from matic_sdk.models.control import (
    CleaningCommand,
    CleaningFloor,
    CleaningIntensity,
    CommandFamily,
    CoverageAction,
    CoverageBehavior,
    CoverageCleaningMode,
    CoverageCommand,
    CoverageGoalCleaningMode,
    CoverageGoals,
    CoverageGoalSetting,
    CoverageGoalSpec,
    CoveragePlanGoal,
    CoverageSetting,
    DeviceAction,
    DeviceCommand,
    DrawnCircle,
    ExplicitFloorCleaningMode,
    JoystickCommand,
    JukeboxTrack,
    LifecycleAction,
    LifecycleCommand,
    MapEnvironmentAction,
    MapEnvironmentCommand,
    MissionPosture,
    NavigationCommand,
    NavigationMode,
    ObservedEffect,
    ObservedEffectStatus,
    RawMotorCommand,
    ReprioritizeAction,
    ReprioritizeCoverageCommand,
    ScheduleAction,
    ScheduleCommand,
    SettingAction,
    SettingsCommand,
    StainMode,
    TransportAcknowledgement,
    TransportAckStatus,
    UserAction,
    UserCommand,
    WifiAction,
    WifiCommand,
)
from matic_sdk.protocol.commands import (
    COMMAND_REGISTRY,
    COMMAND_SPECS,
    CodecEvidenceLevel,
    CommandRegistry,
    EncodedCommand,
    UnsupportedProtocolVersion,
    UnverifiedProtocolVersionWarning,
    _VerifiedNormalCoverageCodec,
    _VerifiedRawMotorCodec,
    _VerifiedReprioritizeCoverageCodec,
    _VerifiedStainCoverageCodec,
    encode_command,
)
from matic_sdk.protocol.grpc import GrpcProtocolError, GrpcResponse
from matic_sdk.protocol.wire import (
    bytes_values,
    encode_bytes_field,
    integer_values,
    parse_fields,
)
from matic_sdk.transport.commands import (
    HERMES_TARGET_HEADER,
    SEND_TO_CHANNEL_PATH,
    _encode_channel_request,
    _HermesCommandTransport,
)


class NeverCalledTransport:
    def __init__(self) -> None:
        self.calls = 0

    async def send_channel(
        self,
        command: EncodedCommand,
    ) -> TransportAcknowledgement:
        del command
        self.calls += 1
        raise AssertionError("transport must not be reached")


class SyntheticStopCodec:
    def encode(self, command: object) -> EncodedCommand:
        assert command == UserCommand(UserAction.STOP)
        return EncodedCommand(b"synthetic-stop", "synthetic-user-command")


class WrongPayloadStopCodec:
    def encode(self, command: object) -> EncodedCommand:
        assert command == UserCommand(UserAction.STOP)
        return EncodedCommand(b"wrong-stop", "user_command")


class AcknowledgingTransport:
    def __init__(self) -> None:
        self.commands: list[EncodedCommand] = []

    async def send_channel(
        self,
        command: EncodedCommand,
    ) -> TransportAcknowledgement:
        self.commands.append(command)
        return TransportAcknowledgement(
            TransportAckStatus.ACKNOWLEDGED,
            code="synthetic-ok",
        )


class FailingCompletionAudit:
    def record(self, event: str, **values: object) -> None:
        del values
        if event == "command.complete":
            raise OSError("synthetic disk full")


def synthetic_stop_registry() -> CommandRegistry:
    stop_spec = replace(
        COMMAND_REGISTRY.spec_for("user.stop"),
        evidence_level=CodecEvidenceLevel.WIRE_VERIFIED,
        known_hermes_target="synthetic-user-command",
        known_payload=b"synthetic-stop",
        evidence="synthetic unit-test fixture",
    )
    return CommandRegistry(
        (stop_spec,),
        codecs={"user.stop": SyntheticStopCodec()},
    )


def test_registry_documents_every_recovered_command_family() -> None:
    families = {spec.family for spec in COMMAND_SPECS}
    assert families == set(CommandFamily)
    assert len(COMMAND_SPECS) == 65
    assert all(spec.known_hermes_target for spec in COMMAND_SPECS)
    expected_keys = {
        "user.stop",
        "user.joystick",
        "navigation.navigate",
        "coverage.normal",
        "coverage.reprioritize",
        "cleaning.manual",
        "map.build_partition",
        "map.edit_rooms",
        "wifi.connect",
        "device.rename",
        "settings.child_lock",
        "schedule.add_or_modify",
        "media.recording_enable",
        "telemetry.support_ssh_permission",
        "raw_motors.setpoints",
        "lifecycle.update",
        "lifecycle.reboot",
        "lifecycle.shutdown",
    }
    assert expected_keys <= COMMAND_REGISTRY.specs.keys()


def test_default_registry_exposes_only_verified_codecs() -> None:
    available = {spec.key for spec in COMMAND_SPECS if spec.codec_available}
    assert available == set(COMMAND_REGISTRY.specs)
    wire_verified = {
        spec.key
        for spec in COMMAND_SPECS
        if spec.evidence_level is CodecEvidenceLevel.WIRE_VERIFIED
    }
    assert len(wire_verified) == 65
    assert wire_verified == available
    live_verified = {spec.key for spec in COMMAND_SPECS if spec.live_delivery_verified}
    assert live_verified == {
        "coverage.normal",
        "navigation.navigate",
        "settings.child_lock",
        "settings.auto_record_voice",
        "settings.jukebox",
        "settings.pet_waste_avoidance",
        "settings.voice",
        "user.dock",
        "user.joystick",
        "user.pause",
        "user.stay_put",
        "user.stop",
    }
    assert encode_command(UserCommand(UserAction.STOP), protocol_version=25) == (
        EncodedCommand(bytes.fromhex("7a040a022200"), "user_command")
    )
    assert encode_command(
        JoystickCommand(0.0, 0.0), protocol_version=25
    ) == EncodedCommand(bytes.fromhex("12021200"), "user_command")


def test_constant_user_payloads_match_official_or_native_fixtures() -> None:
    expected = {
        "user.stop": "7a040a022200",
        "user.stay_put": "820100",
        "user.pause": "4801880101",
        "user.resume": "4801880100",
        "user.dock": "12042a020800",
        "user.explore": "7a0a0a082a060a021a001002",
    }
    for key, payload_hex in expected.items():
        spec = COMMAND_REGISTRY.spec_for(key)
        assert spec.evidence_level is CodecEvidenceLevel.WIRE_VERIFIED
        assert spec.known_payload == bytes.fromhex(payload_hex)
        assert spec.known_hermes_target == "user_command"
        assert spec.codec_available

    joystick = COMMAND_REGISTRY.spec_for("user.joystick")
    assert joystick.known_hermes_target == "user_command"
    assert joystick.known_payload is None


def test_unverified_protocol_version_warns_and_encodes() -> None:
    with pytest.warns(UnverifiedProtocolVersionWarning, match="not 24"):
        encoded = COMMAND_REGISTRY.encode(
            UserCommand(UserAction.STOP),
            protocol_version=24,
        )
    assert encoded == EncodedCommand(bytes.fromhex("7a040a022200"), "user_command")


@pytest.mark.parametrize("version", (None, 0, -1, True, "25"))
def test_invalid_protocol_version_is_rejected(version: object) -> None:
    with pytest.raises(UnsupportedProtocolVersion):
        COMMAND_REGISTRY.encode(
            UserCommand(UserAction.STOP),
            protocol_version=version,
        )


def test_wifi_passphrase_is_not_exposed_by_repr() -> None:
    command = WifiCommand(
        WifiAction.CONNECT,
        ssid="example-network",
        passphrase="do-not-print-this",
    )
    assert "do-not-print-this" not in repr(command)


def test_registry_refuses_codec_without_wire_verified_evidence() -> None:
    unverified_stop = replace(
        COMMAND_REGISTRY.spec_for("user.stop"),
        evidence_level=CodecEvidenceLevel.PAYLOAD_VERIFIED,
    )
    with pytest.raises(ValueError, match="WIRE_VERIFIED"):
        CommandRegistry(
            (unverified_stop,),
            codecs={"user.stop": SyntheticStopCodec()},
        )


def test_registry_refuses_wire_verified_spec_without_codec() -> None:
    with pytest.raises(ValueError, match="require registered codecs"):
        CommandRegistry((COMMAND_REGISTRY.spec_for("user.stop"),), codecs={})


def test_registry_rejects_codec_target_that_diverges_from_spec() -> None:
    stop_spec = COMMAND_REGISTRY.spec_for("user.stop")
    registry = CommandRegistry(
        (stop_spec,),
        codecs={"user.stop": SyntheticStopCodec()},
    )
    with pytest.raises(ValueError, match="target does not match"):
        registry.encode(UserCommand(UserAction.STOP), protocol_version=25)


def test_registry_rejects_fixed_payload_that_diverges_from_spec() -> None:
    stop_spec = COMMAND_REGISTRY.spec_for("user.stop")
    registry = CommandRegistry(
        (stop_spec,),
        codecs={"user.stop": WrongPayloadStopCodec()},
    )
    with pytest.raises(ValueError, match="payload does not match"):
        registry.encode(UserCommand(UserAction.STOP), protocol_version=25)


def test_stop_channel_request_matches_golden_wire_vector() -> None:
    command = encode_command(UserCommand(UserAction.STOP), protocol_version=25)
    assert _encode_channel_request(command).hex() == (
        "0a0c757365725f636f6d6d616e6412067a040a022200"
    )


def test_empty_command_message_uses_canonical_channel_envelope() -> None:
    command = EncodedCommand(b"", "wifi_scan_command")

    assert _encode_channel_request(command).hex() == (
        "0a11776966695f7363616e5f636f6d6d616e64"
    )


@pytest.mark.parametrize(
    ("action", "enabled", "target", "payload_hex"),
    [
        (
            SettingAction.CHILD_LOCK,
            True,
            "child_lock_enabled_command",
            "0801",
        ),
        (
            SettingAction.PET_WASTE_AVOIDANCE,
            False,
            "petwaste_enabled_command",
            "",
        ),
        (SettingAction.VOICE, True, "voice_enabled_command", "0801"),
    ],
)
def test_binary_setting_codecs_match_golden_wire_vectors(
    action: SettingAction,
    enabled: bool,
    target: str,
    payload_hex: str,
) -> None:
    encoded = encode_command(
        SettingsCommand(action, enabled),
        protocol_version=25,
    )
    assert encoded == EncodedCommand(bytes.fromhex(payload_hex), target)
    expected_envelope = encode_bytes_field(1, target.encode())
    if payload_hex:
        expected_envelope += encode_bytes_field(2, bytes.fromhex(payload_hex))
    assert _encode_channel_request(encoded) == expected_envelope


@pytest.mark.parametrize("value", [None, "true", 1])
def test_binary_setting_codecs_reject_non_boolean_values(value: object) -> None:
    with pytest.raises(ValueError, match="requires a boolean"):
        encode_command(
            SettingsCommand(SettingAction.CHILD_LOCK, value),  # type: ignore[arg-type]
            protocol_version=25,
        )


@pytest.mark.parametrize(
    ("command", "expected_hex"),
    [
        (JoystickCommand(0.0, 0.0), "12021200"),
        (JoystickCommand(1.0, -2.0), "120c120a0d0000803f15000000c0"),
        (JoystickCommand(0.5, 0.0), "120712050d0000003f"),
        (JoystickCommand(0.0, -0.5), "1207120515000000bf"),
    ],
)
def test_joystick_codec_matches_native_wire_vectors(
    command: JoystickCommand,
    expected_hex: str,
) -> None:
    assert encode_command(command, protocol_version=25) == EncodedCommand(
        bytes.fromhex(expected_hex),
        "user_command",
    )


@pytest.mark.parametrize(
    "command",
    [
        JoystickCommand(float("nan"), 0.0),
        JoystickCommand(0.0, float("inf")),
        JoystickCommand(True, 0.0),  # type: ignore[arg-type]
        JoystickCommand(10.0**100, 0.0),
    ],
)
def test_joystick_codec_rejects_values_outside_finite_float32(
    command: JoystickCommand,
) -> None:
    with pytest.raises(ValueError, match="joystick velocities"):
        encode_command(command, protocol_version=25)


@pytest.mark.parametrize(
    ("mode", "expected_hex"),
    [
        (
            NavigationMode.NAVIGATE,
            "121c221a0a110d000000c015000080bf220515000080bf1205152a000000",
        ),
        (
            NavigationMode.NAVIGATE_AND_WAIT,
            "122722250a110d000000c015000080bf220515000080bf1205152a000000"
            "22090a070a030884071000",
        ),
        (
            NavigationMode.NAVIGATE_AND_EXPLORE,
            "121c221a0a110d000000c015000080bf220515000080bf1205152a000000"
            "7a0a0a082a060a021a001002",
        ),
    ],
)
def test_navigation_codecs_match_canonical_coordinate_wire_vectors(
    mode: NavigationMode,
    expected_hex: str,
) -> None:
    command = NavigationCommand(
        mode,
        MissionPosture(
            mission_id=42,
            x_meters=1.0,
            y_meters=2.0,
            yaw_radians=0.0,
        ),
    )
    assert encode_command(command, protocol_version=25) == EncodedCommand(
        bytes.fromhex(expected_hex),
        "user_command",
    )


@pytest.mark.parametrize("mission_yaw", [0.37, 1.5036, -2.4])
def test_navigation_heading_uses_the_same_reflection_as_position(
    mission_yaw: float,
) -> None:
    encoded = encode_command(
        NavigationCommand(
            NavigationMode.NAVIGATE,
            MissionPosture(42, 1.0, 2.0, mission_yaw),
        ),
        protocol_version=25,
    )
    drive = _only_message(encoded.payload, 2)
    navigate_to = _only_message(drive, 4)
    posture = _only_message(navigate_to, 1)
    orientation = parse_fields(_only_message(posture, 4))
    cosine_bits = integer_values(orientation, 1)
    sine_bits = integer_values(orientation, 2)
    assert len(cosine_bits) == len(sine_bits) == 1
    cosine = struct.unpack("<f", struct.pack("<I", cosine_bits[0]))[0]
    sine = struct.unpack("<f", struct.pack("<I", sine_bits[0]))[0]
    encoded_yaw = math.atan2(sine, cosine)
    expected_yaw = (-mission_yaw - math.pi / 2 + math.pi) % (2 * math.pi) - math.pi
    assert math.isclose(encoded_yaw, expected_yaw, abs_tol=1e-6)


@pytest.mark.parametrize(
    "destination",
    [
        MissionPosture(True, 0.0, 0.0, 0.0),  # type: ignore[arg-type]
        MissionPosture(-1, 0.0, 0.0, 0.0),
        MissionPosture(1 << 32, 0.0, 0.0, 0.0),
        MissionPosture(1, float("nan"), 0.0, 0.0),
        MissionPosture(1, 0.0, float("inf"), 0.0),
        MissionPosture(1, 0.0, 0.0, 10.0**100),
    ],
)
def test_navigation_codecs_reject_invalid_destinations(
    destination: MissionPosture,
) -> None:
    with pytest.raises(ValueError):
        encode_command(
            NavigationCommand(NavigationMode.NAVIGATE, destination),
            protocol_version=25,
        )


_OFFICIAL_STANDARD_VACUUM = b64decode(
    "erYGCrMGGrAGEgQSAgoAGgUVKgAAACrwBRJcMiIK"
    "FhIUEhIJVEAyPVHmPY8R0uwgOi/3oKoaCAgBEAAg"
    "ACgAOjYKFgoUEhIJqkqqqqqqqqoRqqqqqqqqqooS"
    "AgoAGhgaFhIUEhIJEUEREREREREREREREREREYES"
    "XDIiChYSFBISCe9NP5mwx/aREZc5e6+VCpGBGggI"
    "ARAAIAAoATo2ChYKFBISCapKqqqqqqqqEaqqqqqq"
    "qqqKEgIKABoYGhYSFBISCRFBERERERERERERERER"
    "ERGBElwyIgoWEhQSEgk3T/slO/8awxEdIG4p+wgk"
    "lxoICAEQACAAKAI6NgoWChQSEgmqSqqqqqqqqhGq"
    "qqqqqqqqihICCgAaGBoWEhQSEgkRQRERERERERER"
    "ERERERERgRJcMiIKFhIUEhIJ7ER+9T7qt30RA04G"
    "IBgwWqYaCAgBEAAgACgDOjYKFgoUEhIJqkqqqqqq"
    "qqoRqqqqqqqqqooSAgoAGhgaFhIUEhIJEUERERER"
    "EREREREREREREYESXDIiChYSFBISCf1IraEapU9Y"
    "ERt4dSaECMKdGggIARABIAAoADo2ChYKFBISCapK"
    "qqqqqqqqEaqqqqqqqqqKEgIKABoYGhYSFBISCRFB"
    "ERERERERERERERERERGBElwyIgoWEhQSEgloTx81"
    "5MxcyhGHicpgzolqlhoICAEQASAAKAE6NgoWChQS"
    "EgmqSqqqqqqqqhGqqqqqqqqqihICCgAaGBoWEhQS"
    "EgkRQRERERERERERERERERERgRJcMiIKFhIUEhIJ"
    "nEEjIPluMWoRNg1WxEKmCrYaCAgBEAEgACgCOjYK"
    "FgoUEhIJqkqqqqqqqqoRqqqqqqqqqooSAgoAGhga"
    "FhIUEhIJEUEREREREREREREREREREYESXDIiChYS"
    "FBISCZlIYs7tFb6HEa4/Cl1WuGuiGggIARABIAAo"
    "Azo2ChYKFBISCapKqqqqqqqqEaqqqqqqqqqKEgIK"
    "ABoYGhYSFBISCRFBERERERERERERERERERGBMhYS"
    "FBISCTZMu/4b2kTVEaVipiuDrFCYOhYKFBISCdJB"
    "KixDHtAGEaWF5brPjbSB"
)
_OFFICIAL_STANDARD_VACUUM_IDS = tuple(
    UUID(value)
    for value in (
        "8f3de651-3d32-4054-aaa0-f72f3a20ecd2",
        "91f6c7b0-993f-4def-8191-0a95af7b3997",
        "c31aff3b-25fb-4f37-9724-08fb296e201d",
        "7db7ea3e-f57e-44ec-a65a-301820064e03",
        "584fa51a-a1ad-48fd-9dc2-08842675781b",
        "ca5ccce4-351f-4f68-966a-89ce60ca8987",
        "6a316ef9-2023-419c-b60a-a642c4560d36",
        "87be15ed-ce62-4899-a26b-b8565d0a3fae",
        "d544da1b-febb-4c36-9850-ac832ba662a5",
        "06d01e43-2c2a-41d2-81b4-8dcfbae585a5",
    )
)


def test_normal_coverage_matches_official_android_encoder_byte_for_byte() -> None:
    command_ids = iter(_OFFICIAL_STANDARD_VACUUM_IDS)
    codec = _VerifiedNormalCoverageCodec(
        command_id_factory=lambda: next(command_ids),
    )
    command = CoverageCommand(
        CoverageAction.NORMAL,
        mission_id=42,
        partition_id=UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
        region_ids=(UUID("11111111-1111-4111-8111-111111111111"),),
        cleaning_mode=CoverageCleaningMode.VACUUM,
        coverage_setting=CoverageSetting.STANDARD,
    )

    assert codec.encode(command) == EncodedCommand(
        _OFFICIAL_STANDARD_VACUUM,
        "user_command",
    )


@pytest.mark.parametrize(
    "command",
    [
        CoverageCommand(CoverageAction.NORMAL, mission_id=-1),
        CoverageCommand(CoverageAction.NORMAL, mission_id=1 << 32),
        CoverageCommand(CoverageAction.NORMAL, mission_id=1),
        CoverageCommand(
            CoverageAction.NORMAL,
            mission_id=1,
            partition_id=UUID(int=1),
        ),
        CoverageCommand(
            CoverageAction.NORMAL,
            mission_id=1,
            partition_id=UUID(int=1),
            region_ids=("not-a-uuid",),  # type: ignore[arg-type]
        ),
        CoverageCommand(
            CoverageAction.NORMAL,
            mission_id=1,
            partition_id=UUID(int=1),
            region_ids=[UUID(int=2)],  # type: ignore[arg-type]
        ),
    ],
)
def test_normal_coverage_rejects_incomplete_or_invalid_targets(
    command: CoverageCommand,
) -> None:
    with pytest.raises(ValueError):
        encode_command(command, protocol_version=25)


def _only_message(message: bytes, field_number: int) -> bytes:
    values = bytes_values(parse_fields(message), field_number)
    assert len(values) == 1
    return values[0]


def _coverage_message(command: EncodedCommand) -> bytes:
    task = _only_message(command.payload, 15)
    background_task = _only_message(task, 1)
    return _only_message(background_task, 3)


def _decode_wrapped_uuid(message: bytes) -> UUID:
    fixed128 = _only_message(message, 2)
    fields = parse_fields(fixed128)
    high_values = integer_values(fields, 1)
    low_values = integer_values(fields, 2)
    high = high_values[0] if high_values else 0
    low = low_values[0] if low_values else 0
    return UUID(int=(high << 64) | low)


def _coverage_session_id(coverage: bytes) -> UUID:
    session = _only_message(coverage, 6)
    return _decode_wrapped_uuid(_only_message(session, 2))


def _coverage_command_id(coverage: bytes) -> UUID:
    command = _only_message(coverage, 7)
    return _decode_wrapped_uuid(_only_message(command, 1))


def _goal_id(goal: bytes) -> UUID:
    header = _only_message(goal, 6)
    round_key = _only_message(header, 1)
    return _decode_wrapped_uuid(_only_message(round_key, 2))


def _goal_spec(goal: bytes) -> tuple[int, int, int, int]:
    header = _only_message(goal, 6)
    spec = parse_fields(_only_message(header, 3))
    return tuple(integer_values(spec, number)[0] for number in (1, 2, 4, 5))


def _goal_region_id(goal: bytes) -> UUID:
    target = _only_message(goal, 7)
    region_selection = _only_message(target, 3)
    region = _only_message(region_selection, 3)
    return _decode_wrapped_uuid(_only_message(region, 2))


@pytest.mark.parametrize(
    ("stain_mode", "goal_count", "final_command_id"),
    [
        (StainMode.DRY_STAIN, 20, 23),
        (StainMode.WET_SPILL, 12, 15),
    ],
)
def test_stain_coverage_matches_native_goal_plan_and_drawn_area_layout(
    stain_mode: StainMode,
    goal_count: int,
    final_command_id: int,
) -> None:
    command_ids = iter(UUID(int=value) for value in range(1, 24))
    codec = _VerifiedStainCoverageCodec(
        command_id_factory=lambda: next(command_ids),
    )
    encoded = codec.encode(
        CoverageCommand(
            CoverageAction.STAIN_MODE,
            mission_id=42,
            stain_mode=stain_mode,
            circles=(DrawnCircle(1.5, -2.0, 0.25),),
        )
    )

    coverage = _coverage_message(encoded)
    mission = parse_fields(_only_message(coverage, 3))
    assert integer_values(mission, 2) == (42,)
    assert _coverage_session_id(coverage) == UUID(int=1)
    assert _coverage_command_id(coverage) == UUID(int=final_command_id)

    commanded_goals = parse_fields(_only_message(coverage, 5))
    assert not bytes_values(commanded_goals, 1)
    goals = bytes_values(commanded_goals, 2)
    assert len(goals) == goal_count
    assert [_goal_id(goal) for goal in goals] == [
        UUID(int=value) for value in range(3, goal_count + 3)
    ]

    if stain_mode is StainMode.DRY_STAIN:
        assert _goal_spec(goals[0]) == (1, 0, 0, 0)
        assert _goal_spec(goals[7]) == (1, 1, 0, 3)
        assert _goal_spec(goals[8]) == (0, 0, 1, 0)
    else:
        assert _goal_spec(goals[0]) == (0, 0, 1, 0)
    assert _goal_spec(goals[-1]) == (0, 0, 1, 3)

    targets = [_only_message(goal, 7) for goal in goals]
    assert all(target == targets[0] for target in targets)
    target = targets[0]
    partition_target = _only_message(target, 1)
    assert _decode_wrapped_uuid(_only_message(partition_target, 1)) == UUID(int=2)
    selected_area = _only_message(target, 2)
    drawn_area = _only_message(selected_area, 3)
    circles = _only_message(drawn_area, 2)
    point_circle = _only_message(circles, 1)
    point = parse_fields(_only_message(point_circle, 1))
    assert integer_values(point, 1) == (0x40000000,)
    assert integer_values(point, 2) == (0xBFC00000,)
    assert integer_values(parse_fields(point_circle), 2) == (0x3E800000,)
    selected_regions = _only_message(target, 3)
    assert _only_message(selected_regions, 2) == b""


@pytest.mark.parametrize(
    "command",
    [
        CoverageCommand(CoverageAction.STAIN_MODE, mission_id=1),
        CoverageCommand(
            CoverageAction.STAIN_MODE,
            mission_id=1,
            stain_mode=StainMode.DRY_STAIN,
        ),
        CoverageCommand(
            CoverageAction.STAIN_MODE,
            mission_id=1,
            stain_mode=StainMode.DRY_STAIN,
            circles=(DrawnCircle(0.0, 0.0, 0.0),),
        ),
        CoverageCommand(
            CoverageAction.STAIN_MODE,
            mission_id=1,
            region_ids=(UUID(int=1),),
            stain_mode=StainMode.DRY_STAIN,
            circles=(DrawnCircle(0.0, 0.0, 0.1),),
        ),
        CoverageCommand(
            CoverageAction.STAIN_MODE,
            mission_id=1,
            ordered=True,
            stain_mode=StainMode.DRY_STAIN,
            circles=(DrawnCircle(0.0, 0.0, 0.1),),
        ),
        CoverageCommand(
            CoverageAction.STAIN_MODE,
            mission_id=1,
            stain_mode=StainMode.DRY_STAIN,
            circles=[DrawnCircle(0.0, 0.0, 0.1)],  # type: ignore[arg-type]
        ),
    ],
)
def test_stain_coverage_rejects_ambiguous_or_invalid_inputs(
    command: CoverageCommand,
) -> None:
    with pytest.raises(ValueError):
        encode_command(command, protocol_version=25)


def test_stain_coverage_repeats_circles_and_omits_zero_point_components() -> None:
    command_ids = iter(UUID(int=value) for value in range(1, 16))
    codec = _VerifiedStainCoverageCodec(
        command_id_factory=lambda: next(command_ids),
    )
    encoded = codec.encode(
        CoverageCommand(
            CoverageAction.STAIN_MODE,
            mission_id=42,
            stain_mode=StainMode.WET_SPILL,
            circles=(
                DrawnCircle(0.0, 0.0, 0.25),
                DrawnCircle(1.0, 0.0, 0.5),
            ),
        )
    )

    coverage = _coverage_message(encoded)
    goal = bytes_values(parse_fields(_only_message(coverage, 5)), 2)[0]
    target = _only_message(goal, 7)
    drawn_area = _only_message(_only_message(target, 2), 3)
    circles = bytes_values(parse_fields(_only_message(drawn_area, 2)), 1)
    assert len(circles) == 2
    assert _only_message(circles[0], 1) == b""
    second_point = parse_fields(_only_message(circles[1], 1))
    assert not integer_values(second_point, 1)
    assert integer_values(second_point, 2) == (0xBF800000,)


def _plan_goal(
    goal_id: int,
    region_id: UUID,
    behavior: CoverageBehavior,
) -> CoveragePlanGoal:
    return CoveragePlanGoal(
        goal_id=UUID(int=goal_id),
        partition_id=UUID(int=100),
        region_id=region_id,
        spec=CoverageGoalSpec(
            setting=CoverageGoalSetting.STANDARD,
            floor=CleaningFloor.HARD_FLOOR,
            cleaning_mode=CoverageGoalCleaningMode.VACUUM,
            behavior=behavior,
        ),
    )


def _reprioritize_plan(*, ordered: bool) -> tuple[CoverageGoals, UUID, UUID, UUID]:
    current = UUID(int=101)
    selected = UUID(int=102)
    remaining = UUID(int=103)
    plan = CoverageGoals(
        (
            _plan_goal(1, current, CoverageBehavior.INTERIOR),
            _plan_goal(2, current, CoverageBehavior.PERIMETER),
            _plan_goal(3, selected, CoverageBehavior.INTERIOR),
            _plan_goal(4, selected, CoverageBehavior.PERIMETER),
            _plan_goal(5, remaining, CoverageBehavior.INTERIOR),
            _plan_goal(6, remaining, CoverageBehavior.PERIMETER),
        ),
        ordered=ordered,
    )
    return plan, current, selected, remaining


def test_reprioritize_coverage_moves_current_block_after_selected_block() -> None:
    plan, current, selected, remaining = _reprioritize_plan(ordered=True)
    codec = _VerifiedReprioritizeCoverageCodec(
        command_id_factory=lambda: UUID(int=999),
    )
    encoded = codec.encode(
        ReprioritizeCoverageCommand(
            ReprioritizeAction.PRIORITIZE,
            mission_id=42,
            goals=plan,
            current_region_id=current,
            selected_region_id=selected,
            current_session_id=UUID(int=200),
        )
    )

    coverage = _coverage_message(encoded)
    commanded_goals = parse_fields(_only_message(coverage, 5))
    assert not bytes_values(commanded_goals, 2)
    goals = bytes_values(commanded_goals, 1)
    assert [_goal_id(goal).int for goal in goals] == [3, 4, 1, 2, 5, 6]
    assert [_goal_region_id(goal) for goal in goals] == [
        selected,
        selected,
        current,
        current,
        remaining,
        remaining,
    ]
    assert _goal_spec(goals[0]) == (1, 0, 0, 0)
    assert _coverage_session_id(coverage) == UUID(int=200)
    assert _coverage_command_id(coverage) == UUID(int=999)


def test_reprioritize_coverage_skip_removes_the_current_region_block() -> None:
    plan, current, selected, remaining = _reprioritize_plan(ordered=False)
    codec = _VerifiedReprioritizeCoverageCodec(
        command_id_factory=lambda: UUID(int=999),
    )
    encoded = codec.encode(
        ReprioritizeCoverageCommand(
            ReprioritizeAction.SKIP,
            mission_id=42,
            goals=plan,
            current_region_id=selected,
            current_session_id=UUID(int=200),
            selected_region_id=current,
        )
    )

    coverage = _coverage_message(encoded)
    commanded_goals = parse_fields(_only_message(coverage, 5))
    assert not bytes_values(commanded_goals, 1)
    goals = bytes_values(commanded_goals, 2)
    assert [_goal_id(goal).int for goal in goals] == [1, 2, 5, 6]
    assert [_goal_region_id(goal) for goal in goals] == [
        current,
        current,
        remaining,
        remaining,
    ]


def test_reprioritize_coverage_clones_mixed_partition_plan_unchanged() -> None:
    plan, current, selected, _ = _reprioritize_plan(ordered=False)
    mixed_goal = replace(plan.goals[-1], partition_id=UUID(int=999))
    codec = _VerifiedReprioritizeCoverageCodec(
        command_id_factory=lambda: UUID(int=1000),
    )
    encoded = codec.encode(
        ReprioritizeCoverageCommand(
            ReprioritizeAction.PRIORITIZE,
            mission_id=42,
            goals=replace(plan, goals=(*plan.goals[:-1], mixed_goal)),
            current_region_id=current,
            selected_region_id=selected,
            current_session_id=UUID(int=200),
        )
    )

    coverage = _coverage_message(encoded)
    commanded_goals = parse_fields(_only_message(coverage, 5))
    goals = bytes_values(commanded_goals, 2)
    assert [_goal_id(goal).int for goal in goals] == [1, 2, 3, 4, 5, 6]


@pytest.mark.parametrize("selected_region_id", [UUID(int=999), UUID(int=103)])
def test_reprioritize_coverage_missing_or_current_selection_moves_current_to_front(
    selected_region_id: UUID,
) -> None:
    plan, _, _, current = _reprioritize_plan(ordered=False)
    codec = _VerifiedReprioritizeCoverageCodec(
        command_id_factory=lambda: UUID(int=1000),
    )
    encoded = codec.encode(
        ReprioritizeCoverageCommand(
            ReprioritizeAction.PRIORITIZE,
            mission_id=42,
            goals=plan,
            current_region_id=current,
            selected_region_id=selected_region_id,
            current_session_id=UUID(int=200),
        )
    )

    coverage = _coverage_message(encoded)
    commanded_goals = parse_fields(_only_message(coverage, 5))
    goals = bytes_values(commanded_goals, 2)
    assert [_goal_id(goal).int for goal in goals] == [5, 6, 1, 2, 3, 4]


def test_reprioritize_coverage_absent_current_preserves_plan() -> None:
    plan, _, selected, _ = _reprioritize_plan(ordered=False)
    codec = _VerifiedReprioritizeCoverageCodec(
        command_id_factory=lambda: UUID(int=1000),
    )
    encoded = codec.encode(
        ReprioritizeCoverageCommand(
            ReprioritizeAction.PRIORITIZE,
            mission_id=42,
            goals=plan,
            current_region_id=UUID(int=999),
            selected_region_id=selected,
            current_session_id=UUID(int=200),
        )
    )

    coverage = _coverage_message(encoded)
    commanded_goals = parse_fields(_only_message(coverage, 5))
    goals = bytes_values(commanded_goals, 2)
    assert [_goal_id(goal).int for goal in goals] == [1, 2, 3, 4, 5, 6]


def test_reprioritize_coverage_empty_plan_is_encoded_unchanged() -> None:
    codec = _VerifiedReprioritizeCoverageCodec(
        command_id_factory=lambda: UUID(int=1000),
    )
    encoded = codec.encode(
        ReprioritizeCoverageCommand(
            ReprioritizeAction.SKIP,
            mission_id=42,
            goals=CoverageGoals((), ordered=True),
            current_region_id=UUID(int=101),
            current_session_id=UUID(int=200),
            selected_region_id=UUID(int=102),
        )
    )

    coverage = _coverage_message(encoded)
    assert _only_message(coverage, 5) == b""


@pytest.mark.parametrize(
    ("replacement", "message"),
    [
        (
            CoveragePlanGoal(
                goal_id=UUID(int=1),
                partition_id=UUID(int=100),
                region_id=UUID(int=103),
                spec=CoverageGoalSpec(
                    setting=CoverageGoalSetting.STANDARD,
                    floor=CleaningFloor.HARD_FLOOR,
                    cleaning_mode=CoverageGoalCleaningMode.VACUUM,
                    behavior=CoverageBehavior.INTERIOR,
                ),
            ),
            "unique",
        ),
        (
            CoveragePlanGoal(
                goal_id=UUID(int=99),
                partition_id=UUID(int=100),
                region_id=UUID(int=103),
                spec=CoverageGoalSpec(
                    setting=CoverageGoalSetting.STANDARD,
                    floor=CleaningFloor.CARPET,
                    cleaning_mode=CoverageGoalCleaningMode.MOP,
                    behavior=CoverageBehavior.INTERIOR,
                ),
            ),
            "mop carpet",
        ),
    ],
)
def test_reprioritize_coverage_rejects_impossible_retained_goals(
    replacement: CoveragePlanGoal,
    message: str,
) -> None:
    plan, current, selected, _ = _reprioritize_plan(ordered=False)
    command = ReprioritizeCoverageCommand(
        ReprioritizeAction.PRIORITIZE,
        mission_id=42,
        goals=replace(plan, goals=(*plan.goals, replacement)),
        current_region_id=current,
        selected_region_id=selected,
        current_session_id=UUID(int=200),
    )

    with pytest.raises(ValueError, match=message):
        encode_command(command, protocol_version=25)


@pytest.mark.parametrize(
    ("command", "expected_hex"),
    [
        (UserCommand(UserAction.RESUME), "4801880100"),
        (UserCommand(UserAction.DOCK), "12042a020800"),
        (UserCommand(UserAction.EXPLORE), "7a0a0a082a060a021a001002"),
        (
            UserCommand(UserAction.RE_EXPLORE, until_localized=False),
            "7a0a0a082a060a0222001002",
        ),
        (
            UserCommand(UserAction.RE_EXPLORE, until_localized=True),
            "7a0e0a0c2a0a0a02220010031a020a00",
        ),
        (
            UserCommand(UserAction.TRACE_CALIBRATION, mission_id=0),
            "1206320412001a00",
        ),
        (
            UserCommand(UserAction.TRACE_CALIBRATION, mission_id=42),
            "120b32091205152a0000001a00",
        ),
    ],
)
def test_recovered_user_codecs_match_native_wire_vectors(
    command: UserCommand,
    expected_hex: str,
) -> None:
    assert encode_command(command, protocol_version=25) == EncodedCommand(
        bytes.fromhex(expected_hex),
        "user_command",
    )


@pytest.mark.parametrize(
    ("action", "expected_hex"),
    [
        (
            UserAction.RESUME_COVERAGE,
            "7a1812161214121209776655443322110011ffeeddccbbaa9988",
        ),
        (
            UserAction.REDO_COVERAGE,
            "7a181a161214121209776655443322110011ffeeddccbbaa9988",
        ),
    ],
)
def test_coverage_session_codecs_preserve_uuid_halves(
    action: UserAction,
    expected_hex: str,
) -> None:
    command = UserCommand(
        action,
        coverage_session_id=UUID("00112233-4455-6677-8899-aabbccddeeff"),
    )
    assert encode_command(command, protocol_version=25).payload.hex() == expected_hex


@pytest.mark.parametrize(
    ("session_id", "expected_hex"),
    [
        (UUID(int=0), "7a06120412021200"),
        (UUID(int=1), "7a0f120d120b1209110100000000000000"),
        (
            UUID(int=1 << 64),
            "7a0f120d120b1209090100000000000000",
        ),
    ],
)
def test_coverage_session_codec_omits_default_uuid_halves(
    session_id: UUID,
    expected_hex: str,
) -> None:
    command = UserCommand(
        UserAction.RESUME_COVERAGE,
        coverage_session_id=session_id,
    )
    assert encode_command(command, protocol_version=25).payload.hex() == expected_hex


@pytest.mark.parametrize(
    "command",
    [
        UserCommand(UserAction.STOP, until_localized=False),
        UserCommand(UserAction.RESUME, mission_id=0),
        UserCommand(UserAction.EXPLORE, coverage_session_id=UUID(int=0)),
    ],
)
def test_no_argument_user_codecs_reject_unrelated_fields(
    command: UserCommand,
) -> None:
    with pytest.raises(ValueError, match="does not accept"):
        encode_command(command, protocol_version=25)


@pytest.mark.parametrize("value", [None, 0, 1, "true"])
def test_re_explore_requires_an_actual_boolean(value: object) -> None:
    with pytest.raises(ValueError, match="until_localized: bool"):
        encode_command(
            UserCommand(  # type: ignore[arg-type]
                UserAction.RE_EXPLORE,
                until_localized=value,
            ),
            protocol_version=25,
        )


@pytest.mark.parametrize("mission_id", [None, True, -1, 1 << 32, 1.5])
def test_trace_calibration_requires_a_u32(mission_id: object) -> None:
    with pytest.raises(ValueError, match="u32 mission_id"):
        encode_command(
            UserCommand(  # type: ignore[arg-type]
                UserAction.TRACE_CALIBRATION,
                mission_id=mission_id,
            ),
            protocol_version=25,
        )


@pytest.mark.parametrize("session_id", [None, "not-a-uuid", 1])
def test_coverage_session_requires_uuid(session_id: object) -> None:
    with pytest.raises(ValueError, match="coverage_session_id: UUID"):
        encode_command(
            UserCommand(  # type: ignore[arg-type]
                UserAction.RESUME_COVERAGE,
                coverage_session_id=session_id,
            ),
            protocol_version=25,
        )


@pytest.mark.parametrize(
    ("intensity", "mode", "expected_hex"),
    [
        (
            CleaningIntensity.BASE,
            ExplicitFloorCleaningMode.SWEEPING_CARPET,
            "7a060a040a021a00",
        ),
        (
            CleaningIntensity.BASE,
            ExplicitFloorCleaningMode.SWEEPING_HARDFLOOR,
            "7a080a060a041a021001",
        ),
        (
            CleaningIntensity.BASE,
            ExplicitFloorCleaningMode.SWEEPING_TRANSITION,
            "7a080a060a041a021002",
        ),
        (
            CleaningIntensity.BASE,
            ExplicitFloorCleaningMode.MOPPING_HARDFLOOR,
            "7a080a060a041a021003",
        ),
        (
            CleaningIntensity.MAX,
            ExplicitFloorCleaningMode.SWEEPING_CARPET,
            "7a080a060a041a020801",
        ),
        (
            CleaningIntensity.MAX,
            ExplicitFloorCleaningMode.SWEEPING_HARDFLOOR,
            "7a0a0a080a061a0408011001",
        ),
        (
            CleaningIntensity.MAX,
            ExplicitFloorCleaningMode.SWEEPING_TRANSITION,
            "7a0a0a080a061a0408011002",
        ),
        (
            CleaningIntensity.MAX,
            ExplicitFloorCleaningMode.MOPPING_HARDFLOOR,
            "7a0a0a080a061a0408011003",
        ),
    ],
)
def test_manual_clean_codec_matches_every_native_enum_pair(
    intensity: CleaningIntensity,
    mode: ExplicitFloorCleaningMode,
    expected_hex: str,
) -> None:
    command = CleaningCommand(mode=mode, intensity=intensity)
    assert encode_command(command, protocol_version=25).payload.hex() == expected_hex


@pytest.mark.parametrize(
    "command",
    [
        CleaningCommand(  # type: ignore[arg-type]
            mode="sweeping_carpet",
            intensity=CleaningIntensity.BASE,
        ),
        CleaningCommand(  # type: ignore[arg-type]
            mode=ExplicitFloorCleaningMode.SWEEPING_CARPET,
            intensity="base",
        ),
    ],
)
def test_manual_clean_rejects_plain_strings(command: CleaningCommand) -> None:
    with pytest.raises(ValueError, match="exact cleaning mode"):
        encode_command(command, protocol_version=25)


@pytest.mark.parametrize(
    ("command", "expected_hex"),
    [
        (RawMotorCommand(), ""),
        (RawMotorCommand(vacuum_rpm=1.0), "150000803f"),
        (RawMotorCommand(sweeper_duty=-2.0), "1d000000c0"),
        (RawMotorCommand(mopper_duty=0.5), "250000003f"),
        (RawMotorCommand(head_position=-0.5), "35000000bf"),
        (RawMotorCommand(side_brush_duty=3.0), "3d00004040"),
        (RawMotorCommand(vacuum_rpm=0.0), "1500000000"),
        (RawMotorCommand(vacuum_rpm=-0.0), "1500000080"),
        (
            RawMotorCommand(
                vacuum_rpm=1.0,
                sweeper_duty=-2.0,
                mopper_duty=0.5,
                head_position=-0.5,
                side_brush_duty=3.0,
            ),
            "150000803f1d000000c0250000003f35000000bf3d00004040",
        ),
    ],
)
def test_raw_motor_codec_matches_native_fixed32_fields(
    command: RawMotorCommand,
    expected_hex: str,
) -> None:
    encoded = _VerifiedRawMotorCodec().encode(command)
    assert encoded == EncodedCommand(bytes.fromhex(expected_hex), "motor_command")


@pytest.mark.parametrize("value", [True, "1", float("nan"), float("inf"), 1e100])
def test_raw_motor_codec_rejects_non_float32_values(value: object) -> None:
    with pytest.raises(ValueError, match="float32"):
        _VerifiedRawMotorCodec().encode(
            RawMotorCommand(vacuum_rpm=value),  # type: ignore[arg-type]
        )


def test_raw_motor_command_encodes_through_default_registry() -> None:
    assert encode_command(
        RawMotorCommand(vacuum_rpm=1.0),
        protocol_version=25,
    ) == EncodedCommand(bytes.fromhex("150000803f"), "motor_command")


@pytest.mark.parametrize(
    ("command", "target", "payload_hex"),
    [
        (
            MapEnvironmentCommand(MapEnvironmentAction.CLEAR_RGB_WEIGHTS),
            "clear_rgb_weights_command",
            "",
        ),
        (WifiCommand(WifiAction.SCAN), "wifi_scan_command", ""),
        (
            DeviceCommand(DeviceAction.CLEAR_CALIBRATION),
            "clear_online_calib_command",
            "",
        ),
        (
            ScheduleCommand(ScheduleAction.GENERATE_SUGGESTED),
            "generate_suggested_schedule",
            "",
        ),
        (
            LifecycleCommand(LifecycleAction.UPDATE),
            "update_command",
            "0a00",
        ),
        (
            LifecycleCommand(LifecycleAction.REBOOT),
            "reboot_command",
            "0a00",
        ),
        (
            LifecycleCommand(LifecycleAction.SHUTDOWN),
            "reboot_command",
            "1200",
        ),
    ],
)
def test_exact_admin_and_lifecycle_codecs(
    command: object,
    target: str,
    payload_hex: str,
) -> None:
    encoded = encode_command(command, protocol_version=25)  # type: ignore[arg-type]
    assert encoded == EncodedCommand(bytes.fromhex(payload_hex), target)


@pytest.mark.parametrize("retain_user_data", [False, True])
def test_configure_shipping_preserves_explicit_false(
    retain_user_data: bool,
) -> None:
    command = DeviceCommand(
        DeviceAction.CONFIGURE_SHIPPING,
        retain_user_data=retain_user_data,
    )
    assert encode_command(command, protocol_version=25) == EncodedCommand(
        bytes((0x08, int(retain_user_data))),
        "configure_shipping_command",
    )


@pytest.mark.parametrize(
    "command",
    [
        MapEnvironmentCommand(
            MapEnvironmentAction.CLEAR_RGB_WEIGHTS,
            mission_id=0,
        ),
        WifiCommand(WifiAction.SCAN, ssid="unexpected"),
        DeviceCommand(DeviceAction.CLEAR_CALIBRATION, enabled=False),
        ScheduleCommand(
            ScheduleAction.GENERATE_SUGGESTED,
            definition={"unexpected": True},
        ),
        DeviceCommand(
            DeviceAction.CONFIGURE_SHIPPING,
            enabled=False,
            retain_user_data=True,
        ),
    ],
)
def test_no_argument_or_variant_specific_codecs_reject_extra_fields(
    command: object,
) -> None:
    with pytest.raises(ValueError):
        encode_command(command, protocol_version=25)  # type: ignore[arg-type]


class FakeCommandH2:
    def __init__(self, messages: tuple[bytes, ...] = (b"",)) -> None:
        self.messages = messages
        self.calls: list[tuple[str, bytes, object, bool]] = []

    async def unary(
        self,
        path: str,
        payload: bytes,
        *,
        metadata: object,
        mutating: bool,
    ) -> GrpcResponse:
        self.calls.append((path, payload, metadata, mutating))
        return GrpcResponse(self.messages, (), (("grpc-status", "0"),))


@pytest.mark.asyncio
async def test_hermes_transport_sends_one_verified_stop_request() -> None:
    h2 = FakeCommandH2()
    transport = _HermesCommandTransport(h2)  # type: ignore[arg-type]
    command = encode_command(UserCommand(UserAction.STOP), protocol_version=25)

    acknowledgement = await transport.send_channel(command)

    assert acknowledgement.status is TransportAckStatus.ACKNOWLEDGED
    assert acknowledgement.code == "grpc-status-0"
    assert h2.calls == [
        (
            SEND_TO_CHANNEL_PATH,
            bytes.fromhex("0a0c757365725f636f6d6d616e6412067a040a022200"),
            ((HERMES_TARGET_HEADER, "user_command"),),
            True,
        )
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("messages", [(), (b"", b"")])
async def test_hermes_transport_rejects_invalid_response_shape(
    messages: tuple[bytes, ...],
) -> None:
    transport = _HermesCommandTransport(FakeCommandH2(messages))  # type: ignore[arg-type]
    command = encode_command(UserCommand(UserAction.STOP), protocol_version=25)

    with pytest.raises(GrpcProtocolError):
        await transport.send_channel(command)


@pytest.mark.asyncio
async def test_hermes_transport_accepts_nonempty_response_value() -> None:
    response_value = b"synthetic-response-value"
    transport = _HermesCommandTransport(  # type: ignore[arg-type]
        FakeCommandH2((encode_bytes_field(1, response_value),))
    )
    command = encode_command(UserCommand(UserAction.STOP), protocol_version=25)

    acknowledgement = await transport.send_channel(command)

    assert acknowledgement.status is TransportAckStatus.ACKNOWLEDGED
    assert "synthetic-response-value" not in repr(acknowledgement)


@pytest.mark.asyncio
async def test_hermes_transport_rejects_malformed_channel_response() -> None:
    transport = _HermesCommandTransport(  # type: ignore[arg-type]
        FakeCommandH2((b"\x0a\x05bad",))
    )
    command = encode_command(UserCommand(UserAction.STOP), protocol_version=25)

    with pytest.raises(ValueError, match="truncated"):
        await transport.send_channel(command)


@pytest.mark.asyncio
async def test_unverified_tls_blocks_before_codec_or_transport() -> None:
    transport = NeverCalledTransport()
    executor = CommandExecutor(
        transport,
        protocol_version=25,
        tls_identity_verified=False,
    )
    with pytest.raises(UnverifiedCommandTransport):
        await executor.execute(UserCommand(UserAction.STOP))
    assert transport.calls == 0


@pytest.mark.asyncio
async def test_client_exposes_fail_closed_command_executor() -> None:
    config = MaticConfig(
        "robot.invalid",
        tls=TlsConfig.pinned("00" * 32),
    )
    client = MaticClient(config, NeverCalledTransport(), credentials=None)  # type: ignore[arg-type]

    with pytest.raises(UnsupportedProtocolVersion):
        await client.commands.stop()


@pytest.mark.asyncio
async def test_client_sends_wire_verified_stop_through_hermes() -> None:
    config = MaticConfig(
        "robot.invalid",
        command_protocol_version=25,
        tls=TlsConfig.pinned("00" * 32),
    )
    h2 = FakeCommandH2()
    client = MaticClient(config, h2, credentials=None)  # type: ignore[arg-type]

    receipt = await client.commands.stop()

    assert receipt.transport_acknowledged
    assert len(h2.calls) == 1


@pytest.mark.asyncio
async def test_raw_motor_command_sends_directly() -> None:
    transport = AcknowledgingTransport()
    executor = CommandExecutor(
        transport,
        protocol_version=25,
        tls_identity_verified=True,
    )
    receipt = await executor.execute(RawMotorCommand(vacuum_rpm=100.0))

    assert receipt.transport_acknowledged
    assert transport.commands == [
        EncodedCommand(bytes.fromhex("150000c842"), "motor_command")
    ]


@pytest.mark.asyncio
async def test_raw_motor_convenience_method_sends_directly() -> None:
    transport = AcknowledgingTransport()
    executor = CommandExecutor(
        transport,
        protocol_version=25,
        tls_identity_verified=True,
    )

    receipt = await executor.set_raw_motors(
        sweeper_duty=0.5,
        side_brush_duty=-0.25,
    )

    assert receipt.transport_acknowledged
    assert transport.commands == [
        EncodedCommand(
            bytes.fromhex("1d0000003f3d000080be"),
            "motor_command",
        )
    ]


@pytest.mark.asyncio
async def test_motion_command_sends_directly() -> None:
    transport = AcknowledgingTransport()
    executor = CommandExecutor(
        transport,
        protocol_version=25,
        tls_identity_verified=True,
    )
    command = UserCommand(UserAction.RESUME)

    receipt = await executor.execute(command)

    assert receipt.transport_acknowledged
    assert transport.commands == [
        EncodedCommand(bytes.fromhex("4801880100"), "user_command")
    ]


@pytest.mark.asyncio
async def test_motion_convenience_methods_route_typed_commands_once() -> None:
    transport = AcknowledgingTransport()
    executor = CommandExecutor(
        transport,
        protocol_version=25,
        tls_identity_verified=True,
    )
    destination = MissionPosture(42, 1.0, 2.0, 0.0)
    plan, current, selected, _ = _reprioritize_plan(ordered=False)

    receipts = [
        await executor.resume(),
        await executor.dock(),
        await executor.joystick(0.05, -0.1),
        await executor.navigate(destination),
        await executor.navigate_and_wait(destination),
        await executor.navigate_and_explore(destination),
        await executor.normal_coverage(
            mission_id=42,
            partition_id=UUID(int=100),
            region_ids=(current,),
        ),
        await executor.reprioritize_coverage(
            action=ReprioritizeAction.PRIORITIZE,
            mission_id=42,
            goals=plan,
            current_region_id=current,
            selected_region_id=selected,
            current_session_id=UUID(int=200),
        ),
        await executor.stain_mode(
            mission_id=42,
            stain_mode=StainMode.WET_SPILL,
            circles=(DrawnCircle(1.0, 2.0, 0.25),),
        ),
    ]

    assert [receipt.command_key for receipt in receipts] == [
        "user.resume",
        "user.dock",
        "user.joystick",
        "navigation.navigate",
        "navigation.navigate_and_wait",
        "navigation.navigate_and_explore",
        "coverage.normal",
        "coverage.reprioritize",
        "coverage.stain_mode",
    ]
    assert len(transport.commands) == len(receipts)
    assert all(
        command.hermes_target == "user_command" for command in transport.commands
    )


@pytest.mark.asyncio
async def test_audio_setting_convenience_methods_route_typed_commands_once() -> None:
    transport = AcknowledgingTransport()
    executor = CommandExecutor(
        transport,
        protocol_version=25,
        tls_identity_verified=True,
    )

    receipts = [
        await executor.set_voice_enabled(True),
        await executor.set_auto_record_voice_enabled(True),
        await executor.set_jukebox_track(JukeboxTrack.JINGLE_BELLS),
        await executor.stop_jukebox(),
    ]

    assert [receipt.command_key for receipt in receipts] == [
        "settings.voice",
        "settings.auto_record_voice",
        "settings.jukebox",
        "settings.jukebox",
    ]
    assert transport.commands == [
        EncodedCommand(b"\x08\x01", "voice_enabled_command"),
        EncodedCommand(b"\x08\x01", "auto_record_voice_enabled_command"),
        EncodedCommand(b"\x08\x02", "jukebox_command"),
        EncodedCommand(b"", "jukebox_command"),
    ]


@pytest.mark.asyncio
async def test_trace_calibration_sends_directly() -> None:
    transport = AcknowledgingTransport()
    executor = CommandExecutor(
        transport,
        protocol_version=25,
        tls_identity_verified=True,
    )
    command = UserCommand(UserAction.TRACE_CALIBRATION, mission_id=42)

    receipt = await executor.execute(command)
    assert receipt.transport_acknowledged
    assert transport.commands == [
        EncodedCommand(
            bytes.fromhex("120b32091205152a0000001a00"),
            "user_command",
        )
    ]


@pytest.mark.asyncio
async def test_direct_joystick_sends_once() -> None:
    transport = AcknowledgingTransport()
    executor = CommandExecutor(
        transport,
        protocol_version=25,
        tls_identity_verified=True,
    )

    receipt = await executor.execute(JoystickCommand(0.1, 0.2))

    assert receipt.transport_acknowledged
    assert transport.commands == [
        encode_command(JoystickCommand(0.1, 0.2), protocol_version=25)
    ]


@pytest.mark.asyncio
async def test_client_joystick_sends_one_command_without_watchdog_followups() -> None:
    config = MaticConfig(
        "robot.invalid",
        command_protocol_version=25,
        tls=TlsConfig.pinned("00" * 32),
    )
    h2 = FakeCommandH2()
    client = MaticClient(config, h2, credentials=None)  # type: ignore[arg-type]
    joystick = JoystickCommand(0.05, -0.1)
    expected_request = _encode_channel_request(
        encode_command(joystick, protocol_version=25)
    )

    receipt = await client.commands.joystick(
        joystick.linear_mps,
        joystick.angular_rad_s,
    )

    assert receipt.transport_acknowledged
    assert len(h2.calls) == 1
    assert h2.calls[0][1] == expected_request
    assert all(call[0] == SEND_TO_CHANNEL_PATH for call in h2.calls)
    assert all(
        call[2] == ((HERMES_TARGET_HEADER, "user_command"),) for call in h2.calls
    )
    assert all(call[3] is True for call in h2.calls)


@pytest.mark.asyncio
async def test_receipt_separates_transport_ack_from_observed_effect() -> None:
    transport = AcknowledgingTransport()
    executor = CommandExecutor(
        transport,
        protocol_version=25,
        tls_identity_verified=True,
        registry=synthetic_stop_registry(),
    )

    async def observe(
        command: object,
        acknowledgement: TransportAcknowledgement,
    ) -> ObservedEffect:
        assert command == UserCommand(UserAction.STOP)
        assert acknowledgement.status is TransportAckStatus.ACKNOWLEDGED
        return ObservedEffect(
            ObservedEffectStatus.OBSERVED,
            collection="kabuki_state",
        )

    receipt = await executor.execute(UserCommand(UserAction.STOP), observe=observe)
    assert receipt.transport_acknowledged
    assert receipt.effect_observed
    assert receipt.transport.code == "synthetic-ok"
    assert receipt.observed_effect is not None
    assert receipt.observed_effect.collection == "kabuki_state"
    assert transport.commands == [
        EncodedCommand(b"synthetic-stop", "synthetic-user-command")
    ]


@pytest.mark.asyncio
async def test_post_send_audit_failure_does_not_mask_acknowledged_command() -> None:
    transport = AcknowledgingTransport()
    executor = CommandExecutor(
        transport,
        protocol_version=25,
        tls_identity_verified=True,
        registry=synthetic_stop_registry(),
        audit_log=FailingCompletionAudit(),
    )

    receipt = await executor.stop()

    assert receipt.transport_acknowledged
    assert len(transport.commands) == 1


@pytest.mark.asyncio
async def test_transport_exception_is_unknown_and_never_retried() -> None:
    class BrokenTransport:
        def __init__(self) -> None:
            self.calls = 0

        async def send_channel(
            self,
            command: EncodedCommand,
        ) -> TransportAcknowledgement:
            del command
            self.calls += 1
            raise ConnectionError("synthetic disconnect")

    transport = BrokenTransport()
    executor = CommandExecutor(
        transport,
        protocol_version=25,
        tls_identity_verified=True,
        registry=synthetic_stop_registry(),
    )
    with pytest.raises(CommandOutcomeUnknown, match="must not be retried"):
        await executor.stop()
    assert transport.calls == 1


@pytest.mark.asyncio
async def test_transport_cancellation_surfaces_unknown_outcome() -> None:
    class CancelledTransport:
        async def send_channel(
            self,
            command: EncodedCommand,
        ) -> TransportAcknowledgement:
            del command
            raise asyncio.CancelledError

    executor = CommandExecutor(
        CancelledTransport(),
        protocol_version=25,
        tls_identity_verified=True,
        registry=synthetic_stop_registry(),
    )

    with pytest.raises(CommandOutcomeUnknown, match="cancelled") as raised:
        await executor.stop()

    assert isinstance(raised.value.__cause__, asyncio.CancelledError)


@pytest.mark.asyncio
async def test_jsonl_audit_is_mode_0600_and_contains_no_command_secrets(
    tmp_path: Path,
) -> None:
    audit_path = tmp_path / "private" / "commands.jsonl"
    audit = JsonlAuditLog(audit_path)
    audit.record(
        "direct-test",
        token="bot-token-value",
        nested={
            "ssid": "household-network",
            "passphrase": "network-password",
            "safe": "kept",
        },
        payload=b"protobuf bytes",
    )

    transport = AcknowledgingTransport()
    executor = CommandExecutor(
        transport,
        protocol_version=24,
        tls_identity_verified=True,
        audit_log=audit,
    )
    command = WifiCommand(
        WifiAction.CONNECT,
        ssid="household-network",
        passphrase="network-password",
    )
    with pytest.warns(
        UnverifiedProtocolVersionWarning,
        match="not 24",
    ) as version_warnings:
        receipt = await executor.execute(command)

    assert len(version_warnings) == 1
    assert stat.S_IMODE(audit_path.stat().st_mode) == 0o600
    contents = audit_path.read_text(encoding="utf-8")
    assert "bot-token-value" not in contents
    assert "household-network" not in contents
    assert "network-password" not in contents
    assert "protobuf bytes" not in contents
    records = [json.loads(line) for line in contents.splitlines()]
    assert records[0]["nested"]["safe"] == "kept"
    assert records[0]["nested"]["ssid"] == "[REDACTED]"
    assert records[-1]["event"] == "command.complete"
    assert receipt.transport_acknowledged
