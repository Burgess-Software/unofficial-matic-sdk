from __future__ import annotations

import asyncio
import json
import stat
from dataclasses import replace
from pathlib import Path
from uuid import UUID

import pytest

from matic_sdk.client import MaticClient
from matic_sdk.commands import (
    CommandExecutor,
    CommandOutcomeUnknown,
    DirectJoystickUnsupported,
    JsonlAuditLog,
    UnverifiedCommandTransport,
)
from matic_sdk.config import MaticConfig, TlsConfig
from matic_sdk.models.control import (
    CleaningCommand,
    CleaningIntensity,
    CommandFamily,
    DeviceAction,
    DeviceCommand,
    ExplicitFloorCleaningMode,
    JoystickCommand,
    LifecycleAction,
    LifecycleCommand,
    MapEnvironmentAction,
    MapEnvironmentCommand,
    ObservedEffect,
    ObservedEffectStatus,
    RawMotorCommand,
    ScheduleAction,
    ScheduleCommand,
    SettingAction,
    SettingsCommand,
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
    UnsupportedCommandCodec,
    UnsupportedProtocolVersion,
    _VerifiedRawMotorCodec,
    encode_command,
)
from matic_sdk.protocol.grpc import GrpcProtocolError, GrpcResponse
from matic_sdk.protocol.wire import encode_bytes_field
from matic_sdk.safety import (
    MOTION_CONFIRMATION,
    UNSAFE_CONFIRMATION,
    MotionControlRequired,
    MotionControls,
    UnsafeControlRequired,
    UnsafeControls,
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
    assert available == {
        "cleaning.manual",
        "device.clear_calibration",
        "device.configure_shipping",
        "lifecycle.reboot",
        "lifecycle.shutdown",
        "lifecycle.update",
        "map.clear_rgb_weights",
        "schedule.generate_suggested",
        "settings.child_lock",
        "settings.pet_waste_avoidance",
        "settings.voice",
        "user.dock",
        "user.explore",
        "user.joystick",
        "user.pause",
        "user.re_explore",
        "user.redo_coverage",
        "user.resume",
        "user.resume_coverage",
        "user.stop",
        "user.stay_put",
        "user.trace_calibration",
        "wifi.scan",
    }
    wire_verified = {
        spec.key
        for spec in COMMAND_SPECS
        if spec.evidence_level is CodecEvidenceLevel.WIRE_VERIFIED
    }
    assert len(wire_verified) == 24
    assert wire_verified - available == {"raw_motors.setpoints"}
    live_verified = {
        spec.key for spec in COMMAND_SPECS if spec.live_delivery_verified
    }
    assert live_verified == {
        "settings.child_lock",
        "settings.pet_waste_avoidance",
        "settings.voice",
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


def test_protocol_gate_runs_before_codec_lookup() -> None:
    with pytest.raises(UnsupportedProtocolVersion, match="protocol 24"):
        COMMAND_REGISTRY.encode(
            UserCommand(UserAction.STOP),
            protocol_version=24,
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


def test_registry_refuses_codec_for_policy_disabled_wire_format() -> None:
    raw_spec = COMMAND_REGISTRY.spec_for("raw_motors.setpoints")
    with pytest.raises(ValueError, match="enabled WIRE_VERIFIED"):
        CommandRegistry(
            (raw_spec,),
            codecs={"raw_motors.setpoints": _VerifiedRawMotorCodec()},
        )


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
            "0800",
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
    expected_envelope = encode_bytes_field(1, target.encode()) + encode_bytes_field(
        2, bytes.fromhex(payload_hex)
    )
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
    # Keep the recovered wire format under test without registering a public
    # encoder before hardware-safe ranges are known.
    encoded = _VerifiedRawMotorCodec().encode(command)
    assert encoded == EncodedCommand(bytes.fromhex(expected_hex), "motor_command")


@pytest.mark.parametrize("value", [True, "1", float("nan"), float("inf"), 1e100])
def test_raw_motor_codec_rejects_non_float32_values(value: object) -> None:
    with pytest.raises(ValueError, match="float32"):
        _VerifiedRawMotorCodec().encode(
            RawMotorCommand(vacuum_rpm=value),  # type: ignore[arg-type]
        )


def test_raw_motor_command_remains_fail_closed_without_safe_ranges() -> None:
    with pytest.raises(UnsupportedCommandCodec):
        encode_command(RawMotorCommand(vacuum_rpm=1.0), protocol_version=25)


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
async def test_unsafe_command_needs_explicit_capability() -> None:
    transport = AcknowledgingTransport()
    executor = CommandExecutor(
        transport,
        protocol_version=25,
        tls_identity_verified=True,
    )
    command = RawMotorCommand(vacuum_rpm=100.0)
    with pytest.raises(UnsafeControlRequired):
        await executor.execute(command)
    assert transport.commands == []

    capability = UnsafeControls.arm(UNSAFE_CONFIRMATION)
    with pytest.raises(UnsupportedCommandCodec):
        await executor.execute(command, unsafe_controls=capability)
    assert transport.commands == []


@pytest.mark.asyncio
async def test_motion_command_needs_explicit_capability() -> None:
    transport = AcknowledgingTransport()
    executor = CommandExecutor(
        transport,
        protocol_version=25,
        tls_identity_verified=True,
    )
    command = UserCommand(UserAction.RESUME)

    with pytest.raises(MotionControlRequired):
        await executor.execute(command)
    assert transport.commands == []
    capability = MotionControls.arm(MOTION_CONFIRMATION)
    receipt = await executor.execute(command, motion_controls=capability)
    assert receipt.transport_acknowledged
    assert transport.commands == [
        EncodedCommand(bytes.fromhex("4801880100"), "user_command")
    ]


@pytest.mark.asyncio
async def test_trace_calibration_requires_motion_and_unsafe_capabilities() -> None:
    transport = AcknowledgingTransport()
    executor = CommandExecutor(
        transport,
        protocol_version=25,
        tls_identity_verified=True,
    )
    command = UserCommand(UserAction.TRACE_CALIBRATION, mission_id=42)
    motion = MotionControls.arm(MOTION_CONFIRMATION)
    unsafe = UnsafeControls.arm(UNSAFE_CONFIRMATION)

    with pytest.raises(MotionControlRequired):
        await executor.execute(command)
    with pytest.raises(UnsafeControlRequired):
        await executor.execute(command, motion_controls=motion)
    with pytest.raises(MotionControlRequired):
        await executor.execute(command, unsafe_controls=unsafe)
    assert transport.commands == []

    receipt = await executor.execute(
        command,
        motion_controls=motion,
        unsafe_controls=unsafe,
    )
    assert receipt.transport_acknowledged
    assert transport.commands == [
        EncodedCommand(
            bytes.fromhex("120b32091205152a0000001a00"),
            "user_command",
        )
    ]


@pytest.mark.asyncio
async def test_direct_joystick_cannot_bypass_teleop_watchdog() -> None:
    transport = NeverCalledTransport()
    executor = CommandExecutor(
        transport,
        protocol_version=25,
        tls_identity_verified=True,
    )
    capability = MotionControls.arm(MOTION_CONFIRMATION)

    with pytest.raises(DirectJoystickUnsupported, match="TeleopSession"):
        await executor.execute(
            JoystickCommand(0.1, 0.2),
            motion_controls=capability,
        )
    assert transport.calls == 0


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

    transport = NeverCalledTransport()
    executor = CommandExecutor(
        transport,
        protocol_version=25,
        tls_identity_verified=True,
        audit_log=audit,
    )
    command = WifiCommand(
        WifiAction.CONNECT,
        ssid="household-network",
        passphrase="network-password",
    )
    capability = UnsafeControls.arm(UNSAFE_CONFIRMATION)
    with pytest.raises(UnsupportedCommandCodec):
        await executor.execute(command, unsafe_controls=capability)

    assert stat.S_IMODE(audit_path.stat().st_mode) == 0o600
    contents = audit_path.read_text(encoding="utf-8")
    assert "bot-token-value" not in contents
    assert "household-network" not in contents
    assert "network-password" not in contents
    assert "protobuf bytes" not in contents
    records = [json.loads(line) for line in contents.splitlines()]
    assert records[0]["nested"]["safe"] == "kept"
    assert records[0]["nested"]["ssid"] == "[REDACTED]"
    assert records[-1]["error_type"] == "UnsupportedCommandCodec"
