"""Fail-closed command codec registry for Hermes protocol version 25.

Static analysis recovered command type names and exact payloads for a small
verified subset. Independent protocol reconstruction, official-client
evidence, and live testing established the surrounding ``ChannelRequest`` wire
shape and response semantics. The default registry exposes only commands whose
target and complete payload are proven; callers never get a guessing or
raw-payload escape hatch.
"""

from __future__ import annotations

import math
import struct
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Protocol
from uuid import UUID

from matic_sdk.models.control import (
    CleaningAction,
    CleaningCommand,
    CleaningIntensity,
    CommandFamily,
    CommandRisk,
    ControlCommand,
    CoverageCommand,
    DeviceAction,
    DeviceCommand,
    ExplicitFloorCleaningMode,
    JoystickCommand,
    LifecycleAction,
    LifecycleCommand,
    MapEnvironmentAction,
    MapEnvironmentCommand,
    MediaCommand,
    NavigationCommand,
    RawMotorCommand,
    ScheduleAction,
    ScheduleCommand,
    SettingAction,
    SettingsCommand,
    TelemetryCommand,
    UserAction,
    UserCommand,
    WifiAction,
    WifiCommand,
)
from matic_sdk.protocol.wire import (
    encode_bytes_field,
    encode_fixed32_field,
    encode_fixed64_field,
    encode_varint_field,
)

DEFAULT_PROTOCOL_VERSION = 25
SUPPORTED_PROTOCOL_VERSIONS = frozenset({DEFAULT_PROTOCOL_VERSION})
USER_COMMAND_HERMES_TARGET = "user_command"

_BINARY_SETTING_TARGETS = MappingProxyType(
    {
        SettingAction.CHILD_LOCK: "child_lock_enabled_command",
        SettingAction.PET_WASTE_AVOIDANCE: "petwaste_enabled_command",
        SettingAction.VOICE: "voice_enabled_command",
    }
)


class CommandProtocolError(RuntimeError):
    """Base class for command-protocol failures."""


class UnsupportedProtocolVersion(CommandProtocolError):
    """Raised before encoding when the robot protocol is not allowlisted."""

    def __init__(self, version: object) -> None:
        super().__init__(
            f"robot protocol {version!r} is not supported for commands; "
            f"supported versions: {sorted(SUPPORTED_PROTOCOL_VERSIONS)}"
        )
        self.version = version


class UnknownCommand(CommandProtocolError):
    """Raised when an intent has no documented registry entry."""


class UnsupportedCommandCodec(CommandProtocolError):
    """Raised when static types exist but the wire schema is unresolved."""

    def __init__(self, command_key: str) -> None:
        super().__init__(
            f"{command_key!r} has no evidence-backed wire codec; the protobuf "
            "payload or command-specific semantics remain unresolved"
        )
        self.command_key = command_key


class CodecEvidenceLevel(StrEnum):
    """How far reverse-engineering has established a command type."""

    STATIC_TYPE = "static_type"
    STATIC_FIELDS = "static_fields"
    PAYLOAD_VERIFIED = "payload_verified"
    WIRE_VERIFIED = "wire_verified"


@dataclass(frozen=True, slots=True)
class CommandSpec:
    """Documented command variant and its evidence/safety classification."""

    key: str
    family: CommandFamily
    risk: CommandRisk
    model_type: type[ControlCommand]
    native_type: str
    evidence_level: CodecEvidenceLevel = CodecEvidenceLevel.STATIC_TYPE
    known_fields: tuple[str, ...] = ()
    known_payload: bytes | None = None
    known_hermes_target: str | None = None
    requires_unsafe_controls: bool = False
    requires_motion_controls: bool = False
    codec_enabled: bool = True
    live_delivery_verified: bool = False
    evidence: str = "Matic Android 1.151.0 generated bindings/libmegazord.so"

    @property
    def codec_available(self) -> bool:
        """Whether the checked-in default registry may encode this command."""

        return (
            self.evidence_level is CodecEvidenceLevel.WIRE_VERIFIED
            and self.codec_enabled
        )


@dataclass(frozen=True, slots=True)
class EncodedCommand:
    """Evidence-backed input for the Hermes ``SendToChannel`` transport."""

    payload: bytes
    hermes_target: str


class CommandCodec(Protocol):
    """A codec backed by known protobuf tags and envelope structure."""

    def encode(self, command: ControlCommand) -> EncodedCommand:
        """Encode one checked command intent."""


def ensure_protocol_compatible(protocol_version: object) -> int:
    """Accept only the robot protocol version used to derive this surface."""

    if (
        isinstance(protocol_version, bool)
        or not isinstance(protocol_version, int)
        or protocol_version not in SUPPORTED_PROTOCOL_VERSIONS
    ):
        raise UnsupportedProtocolVersion(protocol_version)
    return protocol_version


def _spec(
    key: str,
    family: CommandFamily,
    risk: CommandRisk,
    model_type: type[ControlCommand],
    native_type: str,
    *,
    unsafe: bool = False,
    fields: tuple[str, ...] = (),
    payload: bytes | None = None,
    target: str | None = None,
    wire_verified: bool = False,
    codec_enabled: bool = True,
    motion: bool | None = None,
    live_verified: bool = False,
    evidence: str | None = None,
) -> CommandSpec:
    if live_verified and (not wire_verified or not codec_enabled):
        raise ValueError("live command verification requires an enabled wire codec")
    return CommandSpec(
        key=key,
        family=family,
        risk=risk,
        model_type=model_type,
        native_type=native_type,
        evidence_level=(
            CodecEvidenceLevel.WIRE_VERIFIED
            if wire_verified
            else (
                CodecEvidenceLevel.PAYLOAD_VERIFIED
                if payload is not None
                else (
                    CodecEvidenceLevel.STATIC_FIELDS
                    if fields
                    else CodecEvidenceLevel.STATIC_TYPE
                )
            )
        ),
        known_fields=fields,
        known_payload=payload,
        known_hermes_target=(
            target
            if target is not None
            else (
                USER_COMMAND_HERMES_TARGET
                if family is CommandFamily.USER
                else None
            )
        ),
        requires_unsafe_controls=unsafe,
        requires_motion_controls=(
            risk is CommandRisk.MOTION if motion is None else motion
        ),
        codec_enabled=codec_enabled,
        live_delivery_verified=live_verified,
        evidence=(
            evidence
            if evidence is not None
            else "Matic Android 1.151.0 generated bindings/libmegazord.so"
        ),
    )


@dataclass(frozen=True, slots=True)
class _VerifiedUserCommandCodec:
    """Exact encoder for one independently verified no-argument variant."""

    action: UserAction
    payload: bytes

    def encode(self, command: ControlCommand) -> EncodedCommand:
        _require_user_command(command, self.action)
        return EncodedCommand(self.payload, USER_COMMAND_HERMES_TARGET)


def _require_user_command(
    command: ControlCommand,
    action: UserAction,
    *,
    allowed_fields: frozenset[str] = frozenset(),
) -> UserCommand:
    if not isinstance(command, UserCommand) or command.action is not action:
        raise TypeError(f"codec expects UserCommand({action.value})")
    values = {
        "until_localized": command.until_localized,
        "mission_id": command.mission_id,
        "coverage_session_id": command.coverage_session_id,
    }
    unexpected = sorted(
        name
        for name, value in values.items()
        if name not in allowed_fields and value is not None
    )
    if unexpected:
        joined = ", ".join(unexpected)
        raise ValueError(f"{command.command_key} does not accept: {joined}")
    return command


@dataclass(frozen=True, slots=True)
class _VerifiedReExploreCodec:
    """Exact encoder for both native ``ReExplore`` variants."""

    def encode(self, command: ControlCommand) -> EncodedCommand:
        user = _require_user_command(
            command,
            UserAction.RE_EXPLORE,
            allowed_fields=frozenset({"until_localized"}),
        )
        if not isinstance(user.until_localized, bool):
            raise ValueError("user.re_explore requires until_localized: bool")
        payload = bytes.fromhex(
            "7a0e0a0c2a0a0a02220010031a020a00"
            if user.until_localized
            else "7a0a0a082a060a0222001002"
        )
        return EncodedCommand(payload, USER_COMMAND_HERMES_TARGET)


@dataclass(frozen=True, slots=True)
class _VerifiedTraceCalibrationCodec:
    """Exact encoder for the mission-scoped ``TraceCalib8`` command."""

    def encode(self, command: ControlCommand) -> EncodedCommand:
        user = _require_user_command(
            command,
            UserAction.TRACE_CALIBRATION,
            allowed_fields=frozenset({"mission_id"}),
        )
        mission_id = user.mission_id
        if (
            isinstance(mission_id, bool)
            or not isinstance(mission_id, int)
            or not 0 <= mission_id <= 0xFFFFFFFF
        ):
            raise ValueError("user.trace_calibration requires a u32 mission_id")
        mission = (
            encode_fixed32_field(2, mission_id) if mission_id != 0 else b""
        )
        drive = encode_bytes_field(2, mission) + encode_bytes_field(3, b"")
        payload = encode_bytes_field(2, encode_bytes_field(6, drive))
        return EncodedCommand(payload, USER_COMMAND_HERMES_TARGET)


@dataclass(frozen=True, slots=True)
class _VerifiedCoverageSessionCodec:
    """Exact encoder for resuming or redoing an existing coverage session."""

    action: UserAction
    task_field: int

    def encode(self, command: ControlCommand) -> EncodedCommand:
        user = _require_user_command(
            command,
            self.action,
            allowed_fields=frozenset({"coverage_session_id"}),
        )
        session_id = user.coverage_session_id
        if not isinstance(session_id, UUID):
            raise ValueError(
                f"{user.command_key} requires coverage_session_id: UUID"
            )
        value = session_id.int
        high = value >> 64
        low = value & ((1 << 64) - 1)
        uuid_fields = b"".join(
            (
                encode_fixed64_field(1, high) if high != 0 else b"",
                encode_fixed64_field(2, low) if low != 0 else b"",
            )
        )
        session = encode_bytes_field(2, encode_bytes_field(2, uuid_fields))
        task = encode_bytes_field(self.task_field, session)
        return EncodedCommand(
            encode_bytes_field(15, task),
            USER_COMMAND_HERMES_TARGET,
        )


@dataclass(frozen=True, slots=True)
class _VerifiedManualCleanCodec:
    """Exact encoder for the native manual-cleaning command."""

    def encode(self, command: ControlCommand) -> EncodedCommand:
        if (
            not isinstance(command, CleaningCommand)
            or command.action is not CleaningAction.MANUAL
        ):
            raise TypeError("codec expects CleaningCommand(manual)")
        if not isinstance(command.intensity, CleaningIntensity) or not isinstance(
            command.mode, ExplicitFloorCleaningMode
        ):
            raise ValueError(
                "cleaning.manual requires exact cleaning mode and intensity enums"
            )
        intensity_values = {
            CleaningIntensity.BASE: 0,
            CleaningIntensity.MAX: 1,
        }
        mode_values = {
            ExplicitFloorCleaningMode.SWEEPING_CARPET: 0,
            ExplicitFloorCleaningMode.SWEEPING_HARDFLOOR: 1,
            ExplicitFloorCleaningMode.SWEEPING_TRANSITION: 2,
            ExplicitFloorCleaningMode.MOPPING_HARDFLOOR: 3,
        }
        intensity = intensity_values[command.intensity]
        mode = mode_values[command.mode]
        cleaning_spec = b"".join(
            (
                encode_varint_field(1, intensity) if intensity != 0 else b"",
                encode_varint_field(2, mode) if mode != 0 else b"",
            )
        )
        manual = encode_bytes_field(3, cleaning_spec)
        background = encode_bytes_field(1, manual)
        task = encode_bytes_field(1, background)
        return EncodedCommand(
            encode_bytes_field(15, task),
            USER_COMMAND_HERMES_TARGET,
        )


@dataclass(frozen=True, slots=True)
class _VerifiedBinarySettingCodec:
    """Exact encoder for one native ``bool`` setting command."""

    action: SettingAction
    target: str

    def encode(self, command: ControlCommand) -> EncodedCommand:
        if (
            not isinstance(command, SettingsCommand)
            or command.action is not self.action
        ):
            raise TypeError(f"codec expects SettingsCommand({self.action.value})")
        if not isinstance(command.value, bool):
            raise ValueError(f"{command.command_key} requires a boolean value")
        # Native command records contain one bool. Prost encodes that as
        # protobuf field 1, wire type 0.
        return EncodedCommand(bytes((0x08, int(command.value))), self.target)


@dataclass(frozen=True, slots=True)
class _VerifiedJoystickCodec:
    """Exact encoder for robot-relative linear and angular velocity."""

    def encode(self, command: ControlCommand) -> EncodedCommand:
        if not isinstance(command, JoystickCommand):
            raise TypeError("codec expects JoystickCommand")
        values = (command.linear_mps, command.angular_rad_s)
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            for value in values
        ):
            raise ValueError("joystick velocities must be finite numbers")
        try:
            drive = b"".join(
                (
                    b"\x0d" + struct.pack("<f", float(command.linear_mps))
                    if command.linear_mps != 0
                    else b"",
                    b"\x15" + struct.pack("<f", float(command.angular_rad_s))
                    if command.angular_rad_s != 0
                    else b"",
                )
            )
        except (OverflowError, struct.error) as error:
            raise ValueError("joystick velocities must fit float32") from error
        joystick_control = encode_bytes_field(2, drive)
        payload = encode_bytes_field(2, joystick_control)
        return EncodedCommand(payload, USER_COMMAND_HERMES_TARGET)


def _float32_bits(value: object, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be a finite float32")
    try:
        numeric = float(value)
        if not math.isfinite(numeric):
            raise ValueError(f"{field_name} must be a finite float32")
        packed = struct.pack("<f", numeric)
    except (OverflowError, struct.error) as error:
        raise ValueError(f"{field_name} must fit float32") from error
    return struct.unpack("<I", packed)[0]


@dataclass(frozen=True, slots=True)
class _VerifiedRawMotorCodec:
    """Exact top-level fixed32 encoder for cleaning-mechanism setpoints."""

    def encode(self, command: ControlCommand) -> EncodedCommand:
        if not isinstance(command, RawMotorCommand):
            raise TypeError("codec expects RawMotorCommand")
        fields = (
            (2, "vacuum_rpm", command.vacuum_rpm),
            (3, "sweeper_duty", command.sweeper_duty),
            (4, "mopper_duty", command.mopper_duty),
            (6, "head_position", command.head_position),
            (7, "side_brush_duty", command.side_brush_duty),
        )
        payload = b"".join(
            encode_fixed32_field(
                number,
                _float32_bits(value, field_name=name),
            )
            for number, name, value in fields
            if value is not None
        )
        return EncodedCommand(payload, "motor_command")


@dataclass(frozen=True, slots=True)
class _VerifiedEmptyMapCodec:
    action: MapEnvironmentAction
    target: str

    def encode(self, command: ControlCommand) -> EncodedCommand:
        if (
            not isinstance(command, MapEnvironmentCommand)
            or command.action is not self.action
        ):
            raise TypeError(
                f"codec expects MapEnvironmentCommand({self.action.value})"
            )
        if command.mission_id is not None or command.change_set:
            raise ValueError(f"{command.command_key} does not accept arguments")
        return EncodedCommand(b"", self.target)


@dataclass(frozen=True, slots=True)
class _VerifiedWifiScanCodec:
    def encode(self, command: ControlCommand) -> EncodedCommand:
        if (
            not isinstance(command, WifiCommand)
            or command.action is not WifiAction.SCAN
        ):
            raise TypeError("codec expects WifiCommand(scan)")
        if command.ssid is not None or command.passphrase is not None:
            raise ValueError("wifi.scan does not accept ssid or passphrase")
        return EncodedCommand(b"", "wifi_scan_command")


@dataclass(frozen=True, slots=True)
class _VerifiedDeviceEmptyCodec:
    action: DeviceAction
    target: str

    def encode(self, command: ControlCommand) -> EncodedCommand:
        if not isinstance(command, DeviceCommand) or command.action is not self.action:
            raise TypeError(f"codec expects DeviceCommand({self.action.value})")
        if any(
            value is not None
            for value in (
                command.new_name,
                command.enabled,
                command.discoverable_seconds,
                command.retain_user_data,
            )
        ):
            raise ValueError(f"{command.command_key} does not accept arguments")
        return EncodedCommand(b"", self.target)


@dataclass(frozen=True, slots=True)
class _VerifiedConfigureShippingCodec:
    def encode(self, command: ControlCommand) -> EncodedCommand:
        if (
            not isinstance(command, DeviceCommand)
            or command.action is not DeviceAction.CONFIGURE_SHIPPING
        ):
            raise TypeError("codec expects DeviceCommand(configure_shipping)")
        if any(
            value is not None
            for value in (
                command.new_name,
                command.enabled,
                command.discoverable_seconds,
            )
        ):
            raise ValueError(
                "device.configure_shipping accepts only retain_user_data"
            )
        if not isinstance(command.retain_user_data, bool):
            raise ValueError(
                "device.configure_shipping requires retain_user_data: bool"
            )
        return EncodedCommand(
            bytes((0x08, int(command.retain_user_data))),
            "configure_shipping_command",
        )


@dataclass(frozen=True, slots=True)
class _VerifiedGenerateSuggestedScheduleCodec:
    def encode(self, command: ControlCommand) -> EncodedCommand:
        if (
            not isinstance(command, ScheduleCommand)
            or command.action is not ScheduleAction.GENERATE_SUGGESTED
        ):
            raise TypeError("codec expects ScheduleCommand(generate_suggested)")
        if command.key is not None or command.definition:
            raise ValueError("schedule.generate_suggested does not accept arguments")
        return EncodedCommand(b"", "generate_suggested_schedule")


@dataclass(frozen=True, slots=True)
class _VerifiedLifecycleCodec:
    action: LifecycleAction
    payload: bytes

    def encode(self, command: ControlCommand) -> EncodedCommand:
        if (
            not isinstance(command, LifecycleCommand)
            or command.action is not self.action
        ):
            raise TypeError(f"codec expects LifecycleCommand({self.action.value})")
        return EncodedCommand(self.payload, "reboot_command" if self.action in {
            LifecycleAction.REBOOT,
            LifecycleAction.SHUTDOWN,
        } else "update_command")


# This inventory is intentionally explicit. It remains useful documentation
# even though only a small, exact subset has callable codecs.
COMMAND_SPECS: tuple[CommandSpec, ...] = (
    _spec(
        "user.stop",
        CommandFamily.USER,
        CommandRisk.STATIONARY,
        UserCommand,
        "UserCommand.Stop",
        payload=bytes.fromhex("7a040a022200"),
        wire_verified=True,
        live_verified=True,
        evidence=(
            "Matic Android 1.151.0 native symbols and official-client logs; "
            "SDK live delivery verified 2026-07-22"
        ),
    ),
    _spec(
        "user.stay_put",
        CommandFamily.USER,
        CommandRisk.STATIONARY,
        UserCommand,
        "UserCommand.StayPut",
        payload=bytes.fromhex("820100"),
        wire_verified=True,
        live_verified=True,
        evidence=(
            "Matic Android 1.151.0 native UserCommand::to_proto output; SDK "
            "live delivery acknowledged while robot remained docked 2026-07-22"
        ),
    ),
    _spec(
        "user.pause",
        CommandFamily.USER,
        CommandRisk.STATIONARY,
        UserCommand,
        "UserCommand.Pause",
        payload=bytes.fromhex("4801880101"),
        wire_verified=True,
        live_verified=True,
        evidence=(
            "Official Android offline encoder fixture and native type mapping; "
            "SDK live delivery acknowledged while robot remained docked "
            "2026-07-22"
        ),
    ),
    _spec(
        "user.resume",
        CommandFamily.USER,
        CommandRisk.MOTION,
        UserCommand,
        "UserCommand.Resume",
        payload=bytes.fromhex("4801880100"),
        wire_verified=True,
        evidence=(
            "Official Android offline encoder fixture and native target; "
            "motion-capable and not live-tested"
        ),
    ),
    _spec(
        "user.dock",
        CommandFamily.USER,
        CommandRisk.MOTION,
        UserCommand,
        "UserCommand.Dock",
        payload=bytes.fromhex("12042a020800"),
        wire_verified=True,
        evidence=(
            "Official Android offline encoder fixture and native target; "
            "motion-capable and not live-tested"
        ),
    ),
    _spec(
        "user.explore",
        CommandFamily.USER,
        CommandRisk.MOTION,
        UserCommand,
        "UserCommand.Explore",
        payload=bytes.fromhex("7a0a0a082a060a021a001002"),
        wire_verified=True,
        evidence=(
            "Matic Android 1.151.0 native encoder disassembly; motion-capable "
            "and not live-tested"
        ),
    ),
    _spec(
        "user.re_explore",
        CommandFamily.USER,
        CommandRisk.MOTION,
        UserCommand,
        "UserCommand.ReExplore",
        fields=("untilLocalized: bool",),
        wire_verified=True,
        evidence=(
            "Matic Android 1.151.0 native encoder disassembly for both bool "
            "variants; motion-capable and not live-tested"
        ),
    ),
    _spec(
        "user.redo_coverage",
        CommandFamily.USER,
        CommandRisk.MOTION,
        UserCommand,
        "UserCommand.RedoCoverage",
        fields=("coverageSessionId: UUID",),
        wire_verified=True,
        evidence=(
            "Matic Android 1.151.0 native UUID and task-variant encoder paths; "
            "motion-capable and not live-tested"
        ),
    ),
    _spec(
        "user.resume_coverage",
        CommandFamily.USER,
        CommandRisk.MOTION,
        UserCommand,
        "UserCommand.ResumeCoverage",
        fields=("coverageSessionId: UUID",),
        wire_verified=True,
        evidence=(
            "Matic Android 1.151.0 native UUID and task-variant encoder paths; "
            "motion-capable and not live-tested"
        ),
    ),
    _spec(
        "user.trace_calibration",
        CommandFamily.USER,
        CommandRisk.RAW_ACTUATION,
        UserCommand,
        "UserCommand.TraceCalib8",
        unsafe=True,
        motion=True,
        fields=("missionId: u32",),
        wire_verified=True,
        evidence=(
            "Matic Android 1.151.0 native nested fixed32 encoder path; raw "
            "actuation and not live-tested"
        ),
    ),
    _spec(
        "user.joystick",
        CommandFamily.USER,
        CommandRisk.MOTION,
        JoystickCommand,
        "UserCommand.Joystick(JoystickControl)",
        fields=("linearMetersPerSecond: float32", "angularRadiansPerSecond: float32"),
        wire_verified=True,
        evidence=(
            "Matic Android 1.151.0 native UserCommand::to_proto disassembly "
            "proves both nested field-2 envelopes and float32 fields 1 and 2; "
            "not live-tested"
        ),
    ),
    _spec(
        "navigation.navigate",
        CommandFamily.NAVIGATION,
        CommandRisk.MOTION,
        NavigationCommand,
        "NavigationCommand.Navigate",
        target=USER_COMMAND_HERMES_TARGET,
    ),
    _spec(
        "navigation.navigate_and_wait",
        CommandFamily.NAVIGATION,
        CommandRisk.MOTION,
        NavigationCommand,
        "NavigationCommand.NavigateAndWait",
        target=USER_COMMAND_HERMES_TARGET,
    ),
    _spec(
        "navigation.navigate_and_explore",
        CommandFamily.NAVIGATION,
        CommandRisk.MOTION,
        NavigationCommand,
        "UserCommand.NavigationAndExplore",
        target=USER_COMMAND_HERMES_TARGET,
    ),
    _spec(
        "coverage.normal",
        CommandFamily.COVERAGE,
        CommandRisk.MOTION,
        CoverageCommand,
        "NormalCoverageCommand",
        target=USER_COMMAND_HERMES_TARGET,
    ),
    _spec(
        "coverage.reprioritize",
        CommandFamily.COVERAGE,
        CommandRisk.MOTION,
        CoverageCommand,
        "ReprioritizeCoverageCommand",
        target=USER_COMMAND_HERMES_TARGET,
    ),
    _spec(
        "coverage.stain_mode",
        CommandFamily.COVERAGE,
        CommandRisk.MOTION,
        CoverageCommand,
        "StainModeCoverageCommand",
        target=USER_COMMAND_HERMES_TARGET,
    ),
    _spec(
        "cleaning.manual",
        CommandFamily.CLEANING,
        CommandRisk.MOTION,
        CleaningCommand,
        "UserCommand.ManualClean",
        target=USER_COMMAND_HERMES_TARGET,
        fields=(
            "mode: ExplicitFloorCleaningMode",
            "intensity: CleaningIntensity",
        ),
        wire_verified=True,
        evidence=(
            "Matic Android 1.151.0 native enum values and nested encoder path; "
            "motion-capable and not live-tested"
        ),
    ),
    _spec(
        "raw_motors.setpoints",
        CommandFamily.RAW_MOTORS,
        CommandRisk.RAW_ACTUATION,
        RawMotorCommand,
        "CleaningMotorCommand",
        unsafe=True,
        target="motor_command",
        fields=(
            "vacuumRpm: Option<float32>",
            "sweeperDuty: Option<float32>",
            "mopperDuty: Option<float32>",
            "headPosition: Option<float32>",
            "sideBrushDuty: Option<float32>",
        ),
        wire_verified=True,
        codec_enabled=False,
        evidence=(
            "Matic Android 1.151.0 native Option<float32> encoder fields and "
            "exact target; disabled because hardware-safe ranges are unproven"
        ),
    ),
    _spec(
        "map.build_partition",
        CommandFamily.MAP_ENVIRONMENT,
        CommandRisk.PERSISTENT,
        MapEnvironmentCommand,
        "BuildPartitionCommand",
        unsafe=True,
        target="build_regions",
    ),
    _spec(
        "map.edit_rooms",
        CommandFamily.MAP_ENVIRONMENT,
        CommandRisk.PERSISTENT,
        MapEnvironmentCommand,
        "EditRoomsCommand",
        unsafe=True,
        target="rename_area_command",
    ),
    _spec(
        "map.edit_no_go_zone",
        CommandFamily.MAP_ENVIRONMENT,
        CommandRisk.PERSISTENT,
        MapEnvironmentCommand,
        "NoGoZoneEdit",
        unsafe=True,
        target="nogo_command",
    ),
    _spec(
        "map.edit_drive_only_zone",
        CommandFamily.MAP_ENVIRONMENT,
        CommandRisk.PERSISTENT,
        MapEnvironmentCommand,
        "DriveOnlyZoneEdit",
        unsafe=True,
        target="nogo_command",
    ),
    _spec(
        "map.edit_stairs",
        CommandFamily.MAP_ENVIRONMENT,
        CommandRisk.PERSISTENT,
        MapEnvironmentCommand,
        "StairEdit",
        unsafe=True,
        target="stair_command",
    ),
    _spec(
        "map.edit_semantics_override",
        CommandFamily.MAP_ENVIRONMENT,
        CommandRisk.PERSISTENT,
        MapEnvironmentCommand,
        "SemanticsOverrideCommand",
        unsafe=True,
        target="semantics_override",
    ),
    _spec(
        "map.edit_sink_summon_location",
        CommandFamily.MAP_ENVIRONMENT,
        CommandRisk.PERSISTENT,
        MapEnvironmentCommand,
        "EditSinkSummonLocationCommand",
        unsafe=True,
        target="edit_sink_summon_location",
    ),
    _spec(
        "map.canonicalize",
        CommandFamily.MAP_ENVIRONMENT,
        CommandRisk.PERSISTENT,
        MapEnvironmentCommand,
        "FloorCommand.Canonicalize",
        unsafe=True,
        target="floor_command",
    ),
    _spec(
        "map.rename",
        CommandFamily.MAP_ENVIRONMENT,
        CommandRisk.PERSISTENT,
        MapEnvironmentCommand,
        "FloorCommand.Rename",
        unsafe=True,
        target="floor_command",
    ),
    _spec(
        "map.persistence_clear",
        CommandFamily.MAP_ENVIRONMENT,
        CommandRisk.DESTRUCTIVE,
        MapEnvironmentCommand,
        "PersistenceCommand.Clear",
        unsafe=True,
        target="map_command",
    ),
    _spec(
        "map.clear_map",
        CommandFamily.MAP_ENVIRONMENT,
        CommandRisk.DESTRUCTIVE,
        MapEnvironmentCommand,
        "PersistenceCommand.ClearMap",
        unsafe=True,
        target="map_command",
    ),
    _spec(
        "map.restore_map",
        CommandFamily.MAP_ENVIRONMENT,
        CommandRisk.PERSISTENT,
        MapEnvironmentCommand,
        "PersistenceCommand.RestoreMap",
        unsafe=True,
        target="map_command",
    ),
    _spec(
        "map.upload_map_for_debug",
        CommandFamily.MAP_ENVIRONMENT,
        CommandRisk.SENSITIVE,
        MapEnvironmentCommand,
        "PersistenceCommand.UploadMapForDebug",
        unsafe=True,
        target="map_command",
    ),
    _spec(
        "map.clear_rgb_weights",
        CommandFamily.MAP_ENVIRONMENT,
        CommandRisk.DESTRUCTIVE,
        MapEnvironmentCommand,
        "ClearRgbWeightsCommand",
        unsafe=True,
        payload=b"",
        target="clear_rgb_weights_command",
        wire_verified=True,
        evidence=(
            "Matic Android 1.151.0 no-field ClearRgbWeightsCommand binding and "
            "exact native target; destructive and not live-tested"
        ),
    ),
    _spec(
        "wifi.scan",
        CommandFamily.WIFI,
        CommandRisk.SENSITIVE,
        WifiCommand,
        "WifiScanCommand",
        unsafe=True,
        payload=b"",
        target="wifi_scan_command",
        wire_verified=True,
        evidence=(
            "Matic Android 1.151.0 no-field WifiScanCommand binding and exact "
            "native target; command response is WifiNetworkList; not live-tested"
        ),
    ),
    _spec(
        "wifi.connect",
        CommandFamily.WIFI,
        CommandRisk.PERSISTENT,
        WifiCommand,
        "WifiUpdateCommand.Connect",
        unsafe=True,
        fields=("ssid: String", "passphrase: Option<String>"),
        target="wifi_update_command",
    ),
    _spec(
        "wifi.forget",
        CommandFamily.WIFI,
        CommandRisk.DESTRUCTIVE,
        WifiCommand,
        "WifiUpdateCommand.Forget",
        unsafe=True,
        fields=("ssid: String",),
        target="wifi_update_command",
    ),
    _spec(
        "device.rename",
        CommandFamily.DEVICE,
        CommandRisk.PERSISTENT,
        DeviceCommand,
        "NewBotNameRequest",
        unsafe=True,
        fields=("newName: String",),
        target="new_bot_name",
    ),
    _spec(
        "device.discoverability",
        CommandFamily.DEVICE,
        CommandRisk.SENSITIVE,
        DeviceCommand,
        "DiscoverableRequest",
        unsafe=True,
        fields=("Enable(durationSeconds: u64) | Disable",),
        target="set_device_discoverable",
    ),
    _spec(
        "device.new_mop_roll",
        CommandFamily.DEVICE,
        CommandRisk.PERSISTENT,
        DeviceCommand,
        "NewMopRollCommand",
        unsafe=True,
        fields=("enabled: bool",),
        target="new_mop_roll_override_command",
    ),
    _spec(
        "device.clear_calibration",
        CommandFamily.DEVICE,
        CommandRisk.DESTRUCTIVE,
        DeviceCommand,
        "ClearCalibrationCommand",
        unsafe=True,
        payload=b"",
        target="clear_online_calib_command",
        wire_verified=True,
        evidence=(
            "Matic Android 1.151.0 no-field ClearCalibrationCommand binding and "
            "exact native target; destructive and not live-tested"
        ),
    ),
    _spec(
        "device.configure_shipping",
        CommandFamily.DEVICE,
        CommandRisk.DESTRUCTIVE,
        DeviceCommand,
        "ConfigureShippingCommand",
        unsafe=True,
        fields=("retainUserData: bool",),
        target="configure_shipping_command",
        wire_verified=True,
        evidence=(
            "Matic Android 1.151.0 explicit field-1 bool encoder and exact "
            "native target; destructive and not live-tested"
        ),
    ),
    _spec(
        "settings.child_lock",
        CommandFamily.SETTINGS,
        CommandRisk.PERSISTENT,
        SettingsCommand,
        "ChildLockEnableCommand",
        unsafe=True,
        target=_BINARY_SETTING_TARGETS[SettingAction.CHILD_LOCK],
        wire_verified=True,
        live_verified=True,
        evidence=(
            "Official Android bool command binding and target; independent "
            "golden payload fixture; idempotent SDK live write acknowledged and "
            "state preserved 2026-07-22"
        ),
    ),
    _spec(
        "settings.pet_waste_avoidance",
        CommandFamily.SETTINGS,
        CommandRisk.PERSISTENT,
        SettingsCommand,
        "PetWasteEnableCommand",
        unsafe=True,
        target=_BINARY_SETTING_TARGETS[SettingAction.PET_WASTE_AVOIDANCE],
        wire_verified=True,
        live_verified=True,
        evidence=(
            "Official Android bool command binding and target; independent "
            "golden payload fixture; idempotent SDK live write acknowledged and "
            "state preserved 2026-07-22"
        ),
    ),
    _spec(
        "settings.voice",
        CommandFamily.SETTINGS,
        CommandRisk.PERSISTENT,
        SettingsCommand,
        "VoiceEnableCommand",
        unsafe=True,
        target=_BINARY_SETTING_TARGETS[SettingAction.VOICE],
        wire_verified=True,
        live_verified=True,
        evidence=(
            "Official Android bool command binding and target; independent "
            "golden payload fixture; idempotent SDK live write acknowledged and "
            "state preserved 2026-07-22"
        ),
    ),
    _spec(
        "settings.auto_record_voice",
        CommandFamily.SETTINGS,
        CommandRisk.SENSITIVE,
        SettingsCommand,
        "AutoRecordVoiceEnableCommand",
        unsafe=True,
        fields=("enabled: bool",),
        target="auto_record_voice_enabled_command",
    ),
    _spec(
        "settings.matter_pairing",
        CommandFamily.SETTINGS,
        CommandRisk.SENSITIVE,
        SettingsCommand,
        "MatterPairingEnableCommand",
        unsafe=True,
        fields=("enabled: bool",),
        target="matter_pairing_command",
    ),
    _spec(
        "settings.preview_release",
        CommandFamily.SETTINGS,
        CommandRisk.PERSISTENT,
        SettingsCommand,
        "UserRequestedPreviewEnableCommand",
        unsafe=True,
        fields=("enabled: bool",),
        target="request_preview_release_command",
    ),
    _spec(
        "settings.jukebox",
        CommandFamily.SETTINGS,
        CommandRisk.PERSISTENT,
        SettingsCommand,
        "JukeboxState",
        unsafe=True,
        fields=(
            "track: Option<OhHanukkah | DeckTheHalls | JingleBells>",
        ),
        target="jukebox_command",
    ),
    _spec(
        "schedule.add_or_modify",
        CommandFamily.SCHEDULES,
        CommandRisk.PERSISTENT,
        ScheduleCommand,
        "EditScheduleCommand.AddOrModify",
        unsafe=True,
        fields=("event: AddOrModifyScheduleEvent",),
        target="edit_schedule",
    ),
    _spec(
        "schedule.remove",
        CommandFamily.SCHEDULES,
        CommandRisk.DESTRUCTIVE,
        ScheduleCommand,
        "EditScheduleCommand.Remove",
        unsafe=True,
        fields=("key: ScheduleEventKey(missionId: u32, eventId: UUID)",),
        target="edit_schedule",
    ),
    _spec(
        "schedule.toggle",
        CommandFamily.SCHEDULES,
        CommandRisk.PERSISTENT,
        ScheduleCommand,
        "EditScheduleCommand.Toggle",
        unsafe=True,
        fields=("key: ScheduleEventKey(missionId: u32, eventId: UUID)",),
        target="edit_schedule",
    ),
    _spec(
        "schedule.generate_suggested",
        CommandFamily.SCHEDULES,
        CommandRisk.PERSISTENT,
        ScheduleCommand,
        "GenerateSuggestedScheduleCommand",
        unsafe=True,
        payload=b"",
        target="generate_suggested_schedule",
        wire_verified=True,
        evidence=(
            "Matic Android 1.151.0 no-field GenerateSuggestedScheduleCommand "
            "binding and exact native target; persistent and not live-tested"
        ),
    ),
    _spec(
        "schedule.sink_summon_add_or_modify",
        CommandFamily.SCHEDULES,
        CommandRisk.PERSISTENT,
        ScheduleCommand,
        "EditSinkSummonScheduleCommand.AddOrModify",
        unsafe=True,
        fields=("event: SinkSummonScheduleEvent",),
        target="edit_sink_summon_schedule",
    ),
    _spec(
        "schedule.sink_summon_remove",
        CommandFamily.SCHEDULES,
        CommandRisk.DESTRUCTIVE,
        ScheduleCommand,
        "EditSinkSummonScheduleCommand.Remove",
        unsafe=True,
        fields=("key: SinkSummonScheduleEventKey(missionId: u32, eventId: UUID)",),
        target="edit_sink_summon_schedule",
    ),
    _spec(
        "media.recording_enable",
        CommandFamily.MEDIA,
        CommandRisk.SENSITIVE,
        MediaCommand,
        "RecordingCommand.Enabled",
        unsafe=True,
        fields=("enabled: bool",),
        target="recording_command",
    ),
    _spec(
        "media.rolling_buffer_config",
        CommandFamily.MEDIA,
        CommandRisk.SENSITIVE,
        MediaCommand,
        "RollingRecordingConfigKind",
        unsafe=True,
        fields=("Enabled(confirmForEach: bool) | Disabled",),
        target="toggle_rolling_recordings",
    ),
    _spec(
        "media.flush_rolling_buffer",
        CommandFamily.MEDIA,
        CommandRisk.SENSITIVE,
        MediaCommand,
        "RecordingCommand.FlushRollingBuffer",
        unsafe=True,
        fields=("no arguments (nested RecordingCommand variant)",),
        target="recording_command",
    ),
    _spec(
        "media.confirm_save",
        CommandFamily.MEDIA,
        CommandRisk.SENSITIVE,
        MediaCommand,
        "ConfirmRecordingCommand save",
        unsafe=True,
        fields=("id: u64", "action: Save"),
        target="recording_upload_confirmation",
    ),
    _spec(
        "media.confirm_delete",
        CommandFamily.MEDIA,
        CommandRisk.DESTRUCTIVE,
        MediaCommand,
        "ConfirmRecordingCommand delete",
        unsafe=True,
        fields=("id: u64", "action: Delete"),
        target="recording_upload_confirmation",
    ),
    _spec(
        "telemetry.uploader_config",
        CommandFamily.TELEMETRY,
        CommandRisk.SENSITIVE,
        TelemetryCommand,
        "UploaderConfigCommand",
        unsafe=True,
        fields=("optIn: bool",),
        target="uploader_config_command",
    ),
    _spec(
        "telemetry.support_ssh_permission",
        CommandFamily.TELEMETRY,
        CommandRisk.SENSITIVE,
        TelemetryCommand,
        "UserTunnelSshPermissionCommand",
        unsafe=True,
        fields=("enabled: bool",),
        target="user_tunnel_ssh_permission_command",
    ),
    _spec(
        "telemetry.push_notification_subscription",
        CommandFamily.TELEMETRY,
        CommandRisk.SENSITIVE,
        TelemetryCommand,
        "PushNotificationSubscriptionCommand",
        unsafe=True,
        fields=("deviceId: String", "appBundle: String"),
        target="subscribe_push_notifications",
    ),
    _spec(
        "lifecycle.update",
        CommandFamily.LIFECYCLE,
        CommandRisk.DESTRUCTIVE,
        LifecycleCommand,
        "UpdateBotCommand",
        unsafe=True,
        payload=bytes.fromhex("0a00"),
        target="update_command",
        wire_verified=True,
        evidence=(
            "Matic Android 1.151.0 UpdateBotCommand variant encoder and exact "
            "native target; destructive and not live-tested"
        ),
    ),
    _spec(
        "lifecycle.reboot",
        CommandFamily.LIFECYCLE,
        CommandRisk.DESTRUCTIVE,
        LifecycleCommand,
        "ShutdownCommand.Reboot",
        unsafe=True,
        payload=bytes.fromhex("0a00"),
        target="reboot_command",
        wire_verified=True,
        evidence=(
            "Matic Android 1.151.0 ShutdownCommand.Reboot variant encoder and "
            "exact native target; destructive and not live-tested"
        ),
    ),
    _spec(
        "lifecycle.shutdown",
        CommandFamily.LIFECYCLE,
        CommandRisk.DESTRUCTIVE,
        LifecycleCommand,
        "ShutdownCommand.ShutDown",
        unsafe=True,
        payload=bytes.fromhex("1200"),
        target="reboot_command",
        wire_verified=True,
        evidence=(
            "Matic Android 1.151.0 ShutdownCommand.ShutDown variant encoder and "
            "exact native target; destructive and not live-tested"
        ),
    ),
)


class CommandRegistry:
    """Immutable command metadata plus opt-in, evidence-backed codecs."""

    def __init__(
        self,
        specs: tuple[CommandSpec, ...] = COMMAND_SPECS,
        *,
        codecs: Mapping[str, CommandCodec] | None = None,
    ) -> None:
        by_key = {spec.key: spec for spec in specs}
        if len(by_key) != len(specs):
            raise ValueError("command registry contains duplicate keys")
        codec_map = dict(codecs or {})
        unknown_codecs = codec_map.keys() - by_key.keys()
        if unknown_codecs:
            raise ValueError(
                f"codecs supplied for unknown commands: {sorted(unknown_codecs)}"
            )
        unavailable_codecs = {
            key for key in codec_map if not by_key[key].codec_available
        }
        if unavailable_codecs:
            raise ValueError(
                "codecs require enabled WIRE_VERIFIED evidence: "
                f"{sorted(unavailable_codecs)}"
            )
        missing_codecs = {
            spec.key
            for spec in specs
            if spec.codec_available and spec.key not in codec_map
        }
        if missing_codecs:
            raise ValueError(
                "WIRE_VERIFIED specs require registered codecs: "
                f"{sorted(missing_codecs)}"
            )
        invalid_live_evidence = {
            spec.key
            for spec in specs
            if spec.live_delivery_verified
            and spec.evidence_level is not CodecEvidenceLevel.WIRE_VERIFIED
        }
        if invalid_live_evidence:
            raise ValueError(
                "live command verification requires WIRE_VERIFIED evidence: "
                f"{sorted(invalid_live_evidence)}"
            )
        self._specs = MappingProxyType(by_key)
        self._codecs = MappingProxyType(codec_map)

    @property
    def specs(self) -> Mapping[str, CommandSpec]:
        return self._specs

    def spec_for(self, command: ControlCommand | str) -> CommandSpec:
        key = command if isinstance(command, str) else command.command_key
        try:
            spec = self._specs[key]
        except KeyError as error:
            raise UnknownCommand(f"undocumented command: {key!r}") from error
        if not isinstance(command, str) and not isinstance(command, spec.model_type):
            raise UnknownCommand(
                f"{key!r} expects {spec.model_type.__name__}, got "
                f"{type(command).__name__}"
            )
        return spec

    def encode(
        self,
        command: ControlCommand,
        *,
        protocol_version: object,
    ) -> EncodedCommand:
        ensure_protocol_compatible(protocol_version)
        spec = self.spec_for(command)
        codec = self._codecs.get(spec.key)
        if codec is None:
            raise UnsupportedCommandCodec(spec.key)
        encoded = codec.encode(command)
        if not isinstance(encoded, EncodedCommand):
            raise TypeError("command codec must return EncodedCommand")
        if not isinstance(encoded.payload, bytes):
            raise TypeError("command codec payload must be bytes")
        if not encoded.hermes_target:
            raise ValueError("command codec produced an empty hermes target")
        if encoded.hermes_target != spec.known_hermes_target:
            raise ValueError(
                f"{spec.key!r} codec target does not match its verified target"
            )
        if spec.known_payload is not None and encoded.payload != spec.known_payload:
            raise ValueError(
                f"{spec.key!r} codec payload does not match its verified payload"
            )
        return encoded


COMMAND_REGISTRY = CommandRegistry(
    codecs={
        "user.stop": _VerifiedUserCommandCodec(
            UserAction.STOP,
            bytes.fromhex("7a040a022200"),
        ),
        "user.stay_put": _VerifiedUserCommandCodec(
            UserAction.STAY_PUT,
            bytes.fromhex("820100"),
        ),
        "user.pause": _VerifiedUserCommandCodec(
            UserAction.PAUSE,
            bytes.fromhex("4801880101"),
        ),
        "user.resume": _VerifiedUserCommandCodec(
            UserAction.RESUME,
            bytes.fromhex("4801880100"),
        ),
        "user.dock": _VerifiedUserCommandCodec(
            UserAction.DOCK,
            bytes.fromhex("12042a020800"),
        ),
        "user.explore": _VerifiedUserCommandCodec(
            UserAction.EXPLORE,
            bytes.fromhex("7a0a0a082a060a021a001002"),
        ),
        "user.re_explore": _VerifiedReExploreCodec(),
        "user.redo_coverage": _VerifiedCoverageSessionCodec(
            UserAction.REDO_COVERAGE,
            3,
        ),
        "user.resume_coverage": _VerifiedCoverageSessionCodec(
            UserAction.RESUME_COVERAGE,
            2,
        ),
        "user.trace_calibration": _VerifiedTraceCalibrationCodec(),
        "user.joystick": _VerifiedJoystickCodec(),
        "cleaning.manual": _VerifiedManualCleanCodec(),
        "map.clear_rgb_weights": _VerifiedEmptyMapCodec(
            MapEnvironmentAction.CLEAR_RGB_WEIGHTS,
            "clear_rgb_weights_command",
        ),
        "wifi.scan": _VerifiedWifiScanCodec(),
        "device.clear_calibration": _VerifiedDeviceEmptyCodec(
            DeviceAction.CLEAR_CALIBRATION,
            "clear_online_calib_command",
        ),
        "device.configure_shipping": _VerifiedConfigureShippingCodec(),
        **{
            f"settings.{action.value}": _VerifiedBinarySettingCodec(action, target)
            for action, target in _BINARY_SETTING_TARGETS.items()
        },
        "schedule.generate_suggested": _VerifiedGenerateSuggestedScheduleCodec(),
        "lifecycle.update": _VerifiedLifecycleCodec(
            LifecycleAction.UPDATE,
            bytes.fromhex("0a00"),
        ),
        "lifecycle.reboot": _VerifiedLifecycleCodec(
            LifecycleAction.REBOOT,
            bytes.fromhex("0a00"),
        ),
        "lifecycle.shutdown": _VerifiedLifecycleCodec(
            LifecycleAction.SHUTDOWN,
            bytes.fromhex("1200"),
        ),
    }
)


def encode_command(
    command: ControlCommand,
    *,
    protocol_version: object,
) -> EncodedCommand:
    """Encode through the fail-closed default registry."""

    return COMMAND_REGISTRY.encode(command, protocol_version=protocol_version)


__all__ = [
    "COMMAND_REGISTRY",
    "COMMAND_SPECS",
    "DEFAULT_PROTOCOL_VERSION",
    "SUPPORTED_PROTOCOL_VERSIONS",
    "USER_COMMAND_HERMES_TARGET",
    "CodecEvidenceLevel",
    "CommandCodec",
    "CommandProtocolError",
    "CommandRegistry",
    "CommandSpec",
    "EncodedCommand",
    "UnknownCommand",
    "UnsupportedCommandCodec",
    "UnsupportedProtocolVersion",
    "encode_command",
    "ensure_protocol_compatible",
]
