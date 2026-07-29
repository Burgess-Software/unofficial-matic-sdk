"""Evidence-backed command codec registry for Hermes protocol version 25.

Static analysis recovered command type names, and offline execution of the
official native serializers established exact payloads for all 65 documented
protocol-25 intents. Independent protocol reconstruction, official-client
evidence, and live testing established the surrounding ``ChannelRequest`` wire
shape and response semantics. The default registry exposes only commands whose
target and complete payload are proven. Protocol versions other than the
observed version 25 are allowed with a warning so callers can test compatible
firmware without silently treating that compatibility as verified.
"""

from __future__ import annotations

import math
import struct
import warnings
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Protocol
from uuid import UUID, uuid4

from matic_sdk.models.control import (
    AddZones,
    CleaningAction,
    CleaningCommand,
    CleaningFloor,
    CleaningIntensity,
    CommandFamily,
    CommandRisk,
    ControlCommand,
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
    CustomScheduleTarget,
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
    MapPoint,
    MediaAction,
    MediaCommand,
    MergeRooms,
    NavigationCommand,
    NavigationMode,
    RawMotorCommand,
    RemoveZones,
    RenameRoom,
    ReprioritizeAction,
    ReprioritizeCoverageCommand,
    RoomLabel,
    ScheduleAction,
    ScheduleCommand,
    ScheduleCoverageSetting,
    ScheduleDuration,
    ScheduleEnabledState,
    ScheduleEvent,
    ScheduleEventKey,
    ScheduleTime,
    SemanticsOverride,
    SemanticsOverrideKind,
    SettingAction,
    SettingsCommand,
    SinkSummonLocation,
    SinkSummonScheduleEvent,
    SplitRoom,
    StainMode,
    StandardScheduleTarget,
    TelemetryAction,
    TelemetryCommand,
    UserAction,
    UserCommand,
    Weekday,
    WifiAction,
    WifiCommand,
)
from matic_sdk.protocol.wire import (
    encode_bytes_field,
    encode_fixed32_field,
    encode_fixed64_field,
    encode_varint,
    encode_varint_field,
)

DEFAULT_PROTOCOL_VERSION = 25
SUPPORTED_PROTOCOL_VERSIONS = frozenset({DEFAULT_PROTOCOL_VERSION})
USER_COMMAND_HERMES_TARGET = "user_command"
_NATIVE_SERIALIZER_EVIDENCE = (
    "Matic Android 1.151.0 generated binding and exact Hermes target; "
    "official libmegazord.so ARM64 serializer executed offline against "
    "synthetic values to produce checked golden wire vectors; not live-tested"
)

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
    """Raised when a protocol version is missing or is not a positive integer."""

    def __init__(self, version: object) -> None:
        super().__init__(
            f"robot protocol {version!r} is invalid for commands; "
            "select a positive integer protocol version"
        )
        self.version = version


class UnverifiedProtocolVersionWarning(RuntimeWarning):
    """A command codec is being reused on an unverified protocol version."""


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
    """Documented command variant and its evidence/risk classification."""

    key: str
    family: CommandFamily
    risk: CommandRisk
    model_type: type[ControlCommand]
    native_type: str
    evidence_level: CodecEvidenceLevel = CodecEvidenceLevel.STATIC_TYPE
    known_fields: tuple[str, ...] = ()
    known_payload: bytes | None = None
    known_hermes_target: str | None = None
    live_delivery_verified: bool = False
    evidence: str = "Matic Android 1.151.0 generated bindings/libmegazord.so"

    @property
    def codec_available(self) -> bool:
        """Whether the checked-in default registry may encode this command."""

        return self.evidence_level is CodecEvidenceLevel.WIRE_VERIFIED


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
    """Validate the selected version and warn outside the evidence baseline."""

    if (
        isinstance(protocol_version, bool)
        or not isinstance(protocol_version, int)
        or protocol_version < 1
    ):
        raise UnsupportedProtocolVersion(protocol_version)
    if protocol_version not in SUPPORTED_PROTOCOL_VERSIONS:
        warnings.warn(
            f"command codecs were verified for robot protocol "
            f"{DEFAULT_PROTOCOL_VERSION}, not {protocol_version}; sending with "
            "the protocol-25 wire format because protobuf-compatible firmware "
            "may still accept it",
            UnverifiedProtocolVersionWarning,
            stacklevel=2,
        )
    return protocol_version


def _spec(
    key: str,
    family: CommandFamily,
    risk: CommandRisk,
    model_type: type[ControlCommand],
    native_type: str,
    *,
    fields: tuple[str, ...] = (),
    payload: bytes | None = None,
    target: str | None = None,
    wire_verified: bool = False,
    live_verified: bool = False,
    evidence: str | None = None,
) -> CommandSpec:
    if live_verified and not wire_verified:
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
            else (USER_COMMAND_HERMES_TARGET if family is CommandFamily.USER else None)
        ),
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
        mission = encode_fixed32_field(2, mission_id) if mission_id != 0 else b""
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
            raise ValueError(f"{user.command_key} requires coverage_session_id: UUID")
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
    """Exact encoder for one native scalar-bool setting command."""

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
        # These settings use a non-optional prost bool: false is the protobuf
        # default and is therefore omitted by the official native serializer.
        payload = b"\x08\x01" if command.value else b""
        return EncodedCommand(payload, self.target)


@dataclass(frozen=True, slots=True)
class _VerifiedJukeboxCodec:
    """Exact encoder for the optional seasonal track enum."""

    def encode(self, command: ControlCommand) -> EncodedCommand:
        if (
            not isinstance(command, SettingsCommand)
            or command.action is not SettingAction.JUKEBOX
        ):
            raise TypeError("codec expects SettingsCommand(jukebox)")
        if command.value is None:
            payload = b""
        elif isinstance(command.value, JukeboxTrack):
            values = {
                JukeboxTrack.OH_HANUKKAH: 0,
                JukeboxTrack.DECK_THE_HALLS: 1,
                JukeboxTrack.JINGLE_BELLS: 2,
            }
            # The optional enum is explicitly present, including enum value 0.
            payload = encode_varint_field(1, values[command.value])
        else:
            raise ValueError("settings.jukebox requires JukeboxTrack or None")
        return EncodedCommand(payload, "jukebox_command")


@dataclass(frozen=True, slots=True)
class _VerifiedWifiUpdateCodec:
    """Exact compatibility encoder for connect and forget Wi-Fi commands."""

    action: WifiAction

    def encode(self, command: ControlCommand) -> EncodedCommand:
        if not isinstance(command, WifiCommand) or command.action is not self.action:
            raise TypeError(f"codec expects WifiCommand({self.action.value})")
        if not isinstance(command.ssid, str):
            raise ValueError(f"{command.command_key} requires ssid: str")

        # The Android client deliberately populates both the current SSID field
        # (2) and its compatibility copy (6).
        kind = 1 if self.action is WifiAction.CONNECT else 3
        payload = encode_varint_field(1, kind) + encode_bytes_field(
            2, command.ssid.encode()
        )
        if self.action is WifiAction.CONNECT:
            if command.passphrase is not None and not isinstance(
                command.passphrase, str
            ):
                raise ValueError("wifi.connect requires passphrase: str | None")
            if command.passphrase is not None:
                payload += encode_bytes_field(3, command.passphrase.encode())
        elif command.passphrase is not None:
            raise ValueError("wifi.forget does not accept passphrase")
        payload += encode_bytes_field(6, command.ssid.encode())
        return EncodedCommand(payload, "wifi_update_command")


def _require_device_action(
    command: ControlCommand,
    action: DeviceAction,
) -> DeviceCommand:
    if not isinstance(command, DeviceCommand) or command.action is not action:
        raise TypeError(f"codec expects DeviceCommand({action.value})")
    return command


@dataclass(frozen=True, slots=True)
class _VerifiedDeviceRenameCodec:
    """Exact field-1 string encoder for the robot display name."""

    def encode(self, command: ControlCommand) -> EncodedCommand:
        device = _require_device_action(command, DeviceAction.RENAME)
        if not isinstance(device.new_name, str):
            raise ValueError("device.rename requires new_name: str")
        if any(
            value is not None
            for value in (
                device.enabled,
                device.discoverable_seconds,
                device.retain_user_data,
            )
        ):
            raise ValueError("device.rename accepts only new_name")
        return EncodedCommand(
            encode_bytes_field(1, device.new_name.encode()),
            "new_bot_name",
        )


@dataclass(frozen=True, slots=True)
class _VerifiedDiscoverabilityCodec:
    """Exact oneof encoder for timed enable and disable requests."""

    def encode(self, command: ControlCommand) -> EncodedCommand:
        device = _require_device_action(command, DeviceAction.DISCOVERABILITY)
        if device.new_name is not None or device.retain_user_data is not None:
            raise ValueError(
                "device.discoverability accepts only enabled and discoverable_seconds"
            )
        if not isinstance(device.enabled, bool):
            raise ValueError("device.discoverability requires enabled: bool")
        if device.enabled:
            seconds = device.discoverable_seconds
            if (
                isinstance(seconds, bool)
                or not isinstance(seconds, int)
                or not 0 <= seconds <= 0xFFFFFFFFFFFFFFFF
            ):
                raise ValueError(
                    "enabled device.discoverability requires discoverable_seconds: u64"
                )
            payload = encode_varint_field(1, seconds)
        else:
            if device.discoverable_seconds is not None:
                raise ValueError(
                    "disabled device.discoverability does not accept "
                    "discoverable_seconds"
                )
            payload = encode_bytes_field(2, b"")
        return EncodedCommand(payload, "set_device_discoverable")


@dataclass(frozen=True, slots=True)
class _VerifiedDeviceBooleanCodec:
    """Exact scalar-bool encoder for a device command."""

    action: DeviceAction
    target: str

    def encode(self, command: ControlCommand) -> EncodedCommand:
        device = _require_device_action(command, self.action)
        if not isinstance(device.enabled, bool):
            raise ValueError(f"{device.command_key} requires enabled: bool")
        if any(
            value is not None
            for value in (
                device.new_name,
                device.discoverable_seconds,
                device.retain_user_data,
            )
        ):
            raise ValueError(f"{device.command_key} accepts only enabled")
        payload = b"\x08\x01" if device.enabled else b""
        return EncodedCommand(payload, self.target)


def _require_telemetry_action(
    command: ControlCommand,
    action: TelemetryAction,
) -> TelemetryCommand:
    if not isinstance(command, TelemetryCommand) or command.action is not action:
        raise TypeError(f"codec expects TelemetryCommand({action.value})")
    return command


@dataclass(frozen=True, slots=True)
class _VerifiedUploaderConfigCodec:
    """Exact explicitly-present bool encoder for telemetry opt-in."""

    def encode(self, command: ControlCommand) -> EncodedCommand:
        telemetry = _require_telemetry_action(
            command,
            TelemetryAction.UPLOADER_CONFIG,
        )
        if not isinstance(telemetry.enabled, bool):
            raise ValueError("telemetry.uploader_config requires enabled: bool")
        if telemetry.device_id is not None or telemetry.app_bundle is not None:
            raise ValueError("telemetry.uploader_config accepts only enabled")
        return EncodedCommand(
            bytes((0x08, int(telemetry.enabled))),
            "uploader_config_command",
        )


@dataclass(frozen=True, slots=True)
class _VerifiedTelemetryBooleanCodec:
    """Exact scalar-bool encoder for a telemetry permission."""

    action: TelemetryAction
    target: str

    def encode(self, command: ControlCommand) -> EncodedCommand:
        telemetry = _require_telemetry_action(command, self.action)
        if not isinstance(telemetry.enabled, bool):
            raise ValueError(f"{telemetry.command_key} requires enabled: bool")
        if telemetry.device_id is not None or telemetry.app_bundle is not None:
            raise ValueError(f"{telemetry.command_key} accepts only enabled")
        payload = b"\x08\x01" if telemetry.enabled else b""
        return EncodedCommand(payload, self.target)


@dataclass(frozen=True, slots=True)
class _VerifiedPushNotificationCodec:
    """Exact four-field compatibility encoder used by the Android app."""

    def encode(self, command: ControlCommand) -> EncodedCommand:
        telemetry = _require_telemetry_action(
            command,
            TelemetryAction.PUSH_NOTIFICATION_SUBSCRIPTION,
        )
        if telemetry.enabled is not None:
            raise ValueError(
                "telemetry.push_notification_subscription does not accept enabled"
            )
        if not isinstance(telemetry.device_id, str):
            raise ValueError(
                "telemetry.push_notification_subscription requires device_id: str"
            )
        if not isinstance(telemetry.app_bundle, str):
            raise ValueError(
                "telemetry.push_notification_subscription requires app_bundle: str"
            )
        payload = b"".join(
            (
                encode_bytes_field(1, telemetry.device_id.encode()),
                encode_bytes_field(3, telemetry.app_bundle.encode()),
                encode_varint_field(4, 1),
            )
        )
        return EncodedCommand(payload, "subscribe_push_notifications")


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


def _float32_value(value: object, *, field_name: str) -> float:
    bits = _float32_bits(value, field_name=field_name)
    return struct.unpack("<f", struct.pack("<I", bits))[0]


def _encode_float32_field(field_number: int, value: float) -> bytes:
    if value == 0.0:
        return b""
    return encode_fixed32_field(
        field_number,
        _float32_bits(value, field_name=f"protobuf field {field_number}"),
    )


@dataclass(frozen=True, slots=True)
class _VerifiedNavigationCodec:
    """Exact mission-relative coordinate navigation encoder."""

    mode: NavigationMode

    def encode(self, command: ControlCommand) -> EncodedCommand:
        if not isinstance(command, NavigationCommand) or command.mode is not self.mode:
            raise TypeError(f"codec expects NavigationCommand({self.mode.value})")
        destination = command.destination
        mission_id = destination.mission_id
        if (
            isinstance(mission_id, bool)
            or not isinstance(mission_id, int)
            or not 0 <= mission_id <= 0xFFFFFFFF
        ):
            raise ValueError(f"{command.command_key} requires a u32 mission_id")

        x_meters = _float32_value(
            destination.x_meters,
            field_name="destination.x_meters",
        )
        y_meters = _float32_value(
            destination.y_meters,
            field_name="destination.y_meters",
        )
        yaw_radians = _float32_value(
            destination.yaw_radians,
            field_name="destination.yaw_radians",
        )
        # Mission coordinates are the reflected canonical frame used by the
        # SDK: native_x = -mission_y and native_y = -mission_x. Directions
        # must cross that same reflection, so a mission-frame heading
        # (cos(yaw), sin(yaw)) becomes (-sin(yaw), -cos(yaw)) on the wire.
        orientation = b"".join(
            (
                _encode_float32_field(1, -math.sin(yaw_radians)),
                _encode_float32_field(2, -math.cos(yaw_radians)),
            )
        )
        posture = b"".join(
            (
                _encode_float32_field(1, -y_meters),
                _encode_float32_field(2, -x_meters),
                encode_bytes_field(4, orientation),
            )
        )
        mission = encode_fixed32_field(2, mission_id) if mission_id != 0 else b""
        navigate_to = encode_bytes_field(1, posture) + encode_bytes_field(2, mission)
        if self.mode is NavigationMode.NAVIGATE_AND_WAIT:
            # The official variant installs a fixed 900-second wait condition:
            # field4 -> field1 -> field1(duration), plus an explicitly present
            # field2 whose value is zero.
            wait_condition = encode_bytes_field(
                1,
                encode_bytes_field(1, encode_varint_field(1, 900))
                + encode_varint_field(2, 0),
            )
            navigate_to += encode_bytes_field(4, wait_condition)

        drive = encode_bytes_field(4, navigate_to)
        payload = encode_bytes_field(2, drive)
        if self.mode is NavigationMode.NAVIGATE_AND_EXPLORE:
            # The app combines NavigateTo with the same Explore background task
            # emitted by UserCommand.Explore.
            payload += bytes.fromhex("7a0a0a082a060a021a001002")
        return EncodedCommand(payload, USER_COMMAND_HERMES_TARGET)


def _wrapped_uuid(value: UUID) -> bytes:
    high = value.int >> 64
    low = value.int & ((1 << 64) - 1)
    fixed128 = b"".join(
        (
            encode_fixed64_field(1, high) if high != 0 else b"",
            encode_fixed64_field(2, low) if low != 0 else b"",
        )
    )
    return encode_bytes_field(2, fixed128)


def _validate_coverage_mission_id(mission_id: object, *, command_key: str) -> int:
    if (
        isinstance(mission_id, bool)
        or not isinstance(mission_id, int)
        or not 0 <= mission_id <= 0xFFFFFFFF
    ):
        raise ValueError(f"{command_key} requires a u32 mission_id")
    return mission_id


def _encode_coverage_spec(
    *,
    setting: int,
    floor: int,
    cleaning_mode: int,
    behavior: int,
) -> bytes:
    """Encode four present proto2-style scalar options, including zeroes."""

    return b"".join(
        (
            encode_varint_field(1, setting),
            encode_varint_field(2, floor),
            encode_varint_field(4, cleaning_mode),
            encode_varint_field(5, behavior),
        )
    )


def _encode_coverage_goal_header(*, goal_id: UUID, spec: bytes) -> bytes:
    round_key = encode_bytes_field(2, _wrapped_uuid(goal_id))
    return encode_bytes_field(
        6,
        encode_bytes_field(1, round_key) + encode_bytes_field(3, spec),
    )


def _encode_region_target(*, partition_id: UUID, region_id: UUID) -> bytes:
    return b"".join(
        (
            encode_bytes_field(
                1,
                encode_bytes_field(1, _wrapped_uuid(partition_id)),
            ),
            encode_bytes_field(2, encode_bytes_field(1, b"")),
            encode_bytes_field(
                3,
                encode_bytes_field(
                    3,
                    encode_bytes_field(2, _wrapped_uuid(region_id)),
                ),
            ),
        )
    )


def _encode_drawn_area_target(
    *,
    partition_id: UUID,
    circles: tuple[DrawnCircle, ...],
) -> bytes:
    encoded_circles = []
    for index, circle in enumerate(circles):
        x_meters = _float32_value(
            circle.x_meters,
            field_name=f"circles[{index}].x_meters",
        )
        y_meters = _float32_value(
            circle.y_meters,
            field_name=f"circles[{index}].y_meters",
        )
        radius_meters = _float32_value(
            circle.radius_meters,
            field_name=f"circles[{index}].radius_meters",
        )
        if radius_meters <= 0.0:
            raise ValueError("stain circle radii must be greater than zero")
        point = _encode_float32_field(1, -y_meters) + _encode_float32_field(
            2, -x_meters
        )
        point_circle = encode_bytes_field(1, point) + encode_fixed32_field(
            2,
            _float32_bits(
                radius_meters,
                field_name=f"circles[{index}].radius_meters",
            ),
        )
        encoded_circles.append(encode_bytes_field(1, point_circle))

    circles_message = b"".join(encoded_circles)
    drawn_area = encode_bytes_field(2, circles_message)
    return b"".join(
        (
            encode_bytes_field(
                1,
                encode_bytes_field(1, _wrapped_uuid(partition_id)),
            ),
            encode_bytes_field(2, encode_bytes_field(3, drawn_area)),
            encode_bytes_field(3, encode_bytes_field(2, b"")),
        )
    )


def _encode_coverage_envelope(
    *,
    mission_id: int,
    goals: bytes,
    session_id: UUID,
    command_id: UUID,
) -> EncodedCommand:
    coverage = b"".join(
        (
            encode_bytes_field(
                2,
                encode_bytes_field(2, encode_bytes_field(1, b"")),
            ),
            encode_bytes_field(
                3,
                encode_fixed32_field(2, mission_id) if mission_id != 0 else b"",
            ),
            encode_bytes_field(5, goals),
            encode_bytes_field(
                6,
                encode_bytes_field(2, _wrapped_uuid(session_id)),
            ),
            encode_bytes_field(
                7,
                encode_bytes_field(1, _wrapped_uuid(command_id)),
            ),
        )
    )
    payload = encode_bytes_field(
        15,
        encode_bytes_field(1, encode_bytes_field(3, coverage)),
    )
    return EncodedCommand(payload, USER_COMMAND_HERMES_TARGET)


@dataclass(frozen=True, slots=True)
class _VerifiedNormalCoverageCodec:
    """Exact normal room-coverage encoder."""

    command_id_factory: Callable[[], UUID] = uuid4

    def encode(self, command: ControlCommand) -> EncodedCommand:
        if (
            not isinstance(command, CoverageCommand)
            or command.action is not CoverageAction.NORMAL
        ):
            raise TypeError("codec expects CoverageCommand(normal)")
        mission_id = _validate_coverage_mission_id(
            command.mission_id,
            command_key=command.command_key,
        )
        if not isinstance(command.partition_id, UUID):
            raise ValueError("coverage.normal requires partition_id: UUID")
        if not isinstance(command.region_ids, tuple):
            raise ValueError("coverage.normal region_ids must be a tuple")
        if not command.region_ids:
            raise ValueError("coverage.normal requires at least one region_id")
        if any(not isinstance(region_id, UUID) for region_id in command.region_ids):
            raise ValueError("coverage.normal region_ids must contain UUID values")
        if not isinstance(command.cleaning_mode, CoverageCleaningMode):
            raise ValueError("coverage.normal requires an exact cleaning mode")
        if not isinstance(command.coverage_setting, CoverageSetting):
            raise ValueError("coverage.normal requires an exact coverage setting")
        if not isinstance(command.ordered, bool):
            raise ValueError("coverage.normal ordered must be a boolean")
        if not isinstance(command.circles, tuple):
            raise ValueError("coverage.normal circles must be a tuple")
        if command.stain_mode is not None or command.circles:
            raise ValueError("coverage.normal does not accept stain-mode fields")

        setting = {
            CoverageSetting.STANDARD: 1,
            CoverageSetting.QUICK: 2,
        }[command.coverage_setting]
        specs: list[bytes] = []
        if command.cleaning_mode in {
            CoverageCleaningMode.VACUUM,
            CoverageCleaningMode.BOTH,
        }:
            for floor in (0, 1):
                for behavior in range(4):
                    specs.append(
                        _encode_coverage_spec(
                            setting=setting,
                            floor=floor,
                            cleaning_mode=0,
                            behavior=behavior,
                        )
                    )
        if command.cleaning_mode in {
            CoverageCleaningMode.MOP,
            CoverageCleaningMode.BOTH,
        }:
            for behavior in range(4):
                specs.append(
                    _encode_coverage_spec(
                        setting=setting,
                        floor=0,
                        cleaning_mode=1,
                        behavior=behavior,
                    )
                )

        goal_field = 1 if command.ordered else 2
        goals = b"".join(
            encode_bytes_field(
                goal_field,
                self._goal(
                    partition_id=command.partition_id,
                    region_id=region_id,
                    spec=spec,
                ),
            )
            for region_id in command.region_ids
            for spec in specs
        )
        return _encode_coverage_envelope(
            mission_id=mission_id,
            goals=goals,
            session_id=self._next_id(),
            command_id=self._next_id(),
        )

    def _goal(
        self,
        *,
        partition_id: UUID,
        region_id: UUID,
        spec: bytes,
    ) -> bytes:
        goal = _encode_coverage_goal_header(
            goal_id=self._next_id(),
            spec=spec,
        )
        target = _encode_region_target(
            partition_id=partition_id,
            region_id=region_id,
        )
        return goal + encode_bytes_field(7, target)

    def _next_id(self) -> UUID:
        value = self.command_id_factory()
        if not isinstance(value, UUID):
            raise TypeError("coverage command_id_factory must return UUID")
        return value


@dataclass(frozen=True, slots=True)
class _VerifiedStainCoverageCodec:
    """Exact localized dry-stain and wet-spill coverage encoder."""

    command_id_factory: Callable[[], UUID] = uuid4

    def encode(self, command: ControlCommand) -> EncodedCommand:
        if (
            not isinstance(command, CoverageCommand)
            or command.action is not CoverageAction.STAIN_MODE
        ):
            raise TypeError("codec expects CoverageCommand(stain_mode)")
        mission_id = _validate_coverage_mission_id(
            command.mission_id,
            command_key=command.command_key,
        )
        if not isinstance(command.region_ids, tuple):
            raise ValueError("coverage.stain_mode region_ids must be a tuple")
        if command.partition_id is not None or command.region_ids:
            raise ValueError("coverage.stain_mode does not accept room targets")
        if command.cleaning_mode is not CoverageCleaningMode.BOTH:
            raise ValueError(
                "coverage.stain_mode selects its cleaning program from stain_mode"
            )
        if command.coverage_setting is not CoverageSetting.STANDARD:
            raise ValueError("coverage.stain_mode does not accept a coverage setting")
        if command.ordered is not False:
            raise ValueError("coverage.stain_mode goals are always unordered")
        if not isinstance(command.stain_mode, StainMode):
            raise ValueError("coverage.stain_mode requires an exact stain_mode")
        if not isinstance(command.circles, tuple):
            raise ValueError("coverage.stain_mode circles must be a tuple")
        if not command.circles:
            raise ValueError("coverage.stain_mode requires at least one circle")
        if any(not isinstance(circle, DrawnCircle) for circle in command.circles):
            raise ValueError(
                "coverage.stain_mode circles must contain DrawnCircle values"
            )

        # This generation order is observable in the native conversion path:
        # session id, synthetic partition id, one id per goal, final command id.
        session_id = self._next_id()
        synthetic_partition_id = self._next_id()
        target = _encode_drawn_area_target(
            partition_id=synthetic_partition_id,
            circles=command.circles,
        )

        specs: list[bytes] = []
        if command.stain_mode is StainMode.DRY_STAIN:
            for floor in (0, 1):
                for behavior in range(4):
                    specs.append(
                        _encode_coverage_spec(
                            setting=1,
                            floor=floor,
                            cleaning_mode=0,
                            behavior=behavior,
                        )
                    )
        for _ in range(3):
            for behavior in range(4):
                specs.append(
                    _encode_coverage_spec(
                        setting=0,
                        floor=0,
                        cleaning_mode=1,
                        behavior=behavior,
                    )
                )

        goals = b"".join(
            encode_bytes_field(
                2,
                _encode_coverage_goal_header(
                    goal_id=self._next_id(),
                    spec=spec,
                )
                + encode_bytes_field(7, target),
            )
            for spec in specs
        )
        return _encode_coverage_envelope(
            mission_id=mission_id,
            goals=goals,
            session_id=session_id,
            command_id=self._next_id(),
        )

    def _next_id(self) -> UUID:
        value = self.command_id_factory()
        if not isinstance(value, UUID):
            raise TypeError("coverage command_id_factory must return UUID")
        return value


@dataclass(frozen=True, slots=True)
class _VerifiedReprioritizeCoverageCodec:
    """Exact Prioritize/Skip transformation and coverage-plan encoder."""

    command_id_factory: Callable[[], UUID] = uuid4

    def encode(self, command: ControlCommand) -> EncodedCommand:
        if not isinstance(command, ReprioritizeCoverageCommand):
            raise TypeError("codec expects ReprioritizeCoverageCommand")
        mission_id = _validate_coverage_mission_id(
            command.mission_id,
            command_key=command.command_key,
        )
        if not isinstance(command.action, ReprioritizeAction):
            raise ValueError("coverage.reprioritize requires Prioritize or Skip")
        partition_is_valid = self._validate_goals(command.goals)
        if not isinstance(command.current_region_id, UUID):
            raise ValueError("coverage.reprioritize requires current_region_id: UUID")
        if not isinstance(command.current_session_id, UUID):
            raise ValueError("coverage.reprioritize requires current_session_id: UUID")

        if not partition_is_valid:
            transformed = command.goals.goals
        elif command.action is ReprioritizeAction.PRIORITIZE:
            selected = command.selected_region_id
            if not isinstance(selected, UUID):
                raise ValueError(
                    "coverage.reprioritize Prioritize requires selected_region_id: UUID"
                )
            current = tuple(
                goal
                for goal in command.goals.goals
                if goal.region_id == command.current_region_id
            )
            remaining = tuple(
                goal
                for goal in command.goals.goals
                if goal.region_id != command.current_region_id
            )
            selected_indices = [
                index
                for index, goal in enumerate(remaining)
                if goal.region_id == selected
            ]
            insertion_index = selected_indices[-1] + 1 if selected_indices else 0
            transformed = (
                remaining[:insertion_index] + current + remaining[insertion_index:]
            )
        else:
            transformed = tuple(
                goal
                for goal in command.goals.goals
                if goal.region_id != command.current_region_id
            )

        goal_field = 1 if command.goals.ordered else 2
        goals = b"".join(
            encode_bytes_field(goal_field, self._encode_goal(goal))
            for goal in transformed
        )
        return _encode_coverage_envelope(
            mission_id=mission_id,
            goals=goals,
            session_id=command.current_session_id,
            command_id=self._next_id(),
        )

    @staticmethod
    def _validate_goals(goals: object) -> bool:
        if not isinstance(goals, CoverageGoals):
            raise ValueError("coverage.reprioritize requires a CoverageGoals plan")
        if not isinstance(goals.ordered, bool):
            raise ValueError("coverage goals ordered must be a boolean")
        if not isinstance(goals.goals, tuple):
            raise ValueError("coverage goals must be a tuple")
        goal_ids: set[UUID] = set()
        for goal in goals.goals:
            if not isinstance(goal, CoveragePlanGoal):
                raise ValueError("coverage goals must contain CoveragePlanGoal values")
            if not all(
                isinstance(value, UUID)
                for value in (
                    goal.goal_id,
                    goal.partition_id,
                    goal.region_id,
                )
            ):
                raise ValueError("coverage goal identifiers must be UUID values")
            spec = goal.spec
            if (
                not isinstance(spec, CoverageGoalSpec)
                or not isinstance(spec.setting, CoverageGoalSetting)
                or not isinstance(spec.floor, CleaningFloor)
                or not isinstance(
                    spec.cleaning_mode,
                    CoverageGoalCleaningMode,
                )
                or not isinstance(spec.behavior, CoverageBehavior)
            ):
                raise ValueError(
                    "coverage goals require exact typed cleaning specifications"
                )
            if (
                spec.cleaning_mode is CoverageGoalCleaningMode.MOP
                and spec.floor is CleaningFloor.CARPET
            ):
                raise ValueError("coverage goals cannot mop carpet")
            if goal.goal_id in goal_ids:
                raise ValueError("coverage goal IDs must be unique")
            goal_ids.add(goal.goal_id)
        if not goals.goals:
            return False
        partition_id = goals.goals[0].partition_id
        return all(goal.partition_id == partition_id for goal in goals.goals)

    @staticmethod
    def _encode_goal(goal: CoveragePlanGoal) -> bytes:
        spec = _encode_coverage_spec(
            setting=int(goal.spec.setting),
            floor=int(goal.spec.floor),
            cleaning_mode=int(goal.spec.cleaning_mode),
            behavior=int(goal.spec.behavior),
        )
        return _encode_coverage_goal_header(
            goal_id=goal.goal_id,
            spec=spec,
        ) + encode_bytes_field(
            7,
            _encode_region_target(
                partition_id=goal.partition_id,
                region_id=goal.region_id,
            ),
        )

    def _next_id(self) -> UUID:
        value = self.command_id_factory()
        if not isinstance(value, UUID):
            raise TypeError("coverage command_id_factory must return UUID")
        return value


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


def _require_map_action(
    command: ControlCommand,
    action: MapEnvironmentAction,
) -> MapEnvironmentCommand:
    if not isinstance(command, MapEnvironmentCommand) or command.action is not action:
        raise TypeError(f"codec expects MapEnvironmentCommand({action.value})")
    return command


def _u32(value: object, *, field_name: str) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 0 <= value <= 0xFFFFFFFF
    ):
        raise ValueError(f"{field_name} must be a u32")
    return value


def _mission_id_message(mission_id: int) -> bytes:
    return encode_fixed32_field(2, mission_id) if mission_id != 0 else b""


def _generated_full_uuid(value: UUID) -> bytes:
    """Encode a UUID generated inside the native Rust command conversion."""

    if not isinstance(value, UUID):
        raise TypeError("generated command identifiers must be UUID values")
    high = value.int >> 64
    low = value.int & ((1 << 64) - 1)
    fixed128 = b"".join(
        (
            encode_fixed64_field(1, high) if high != 0 else b"",
            encode_fixed64_field(2, low) if low != 0 else b"",
        )
    )
    # The generated-ID representation retains both the canonical byte string
    # and its fixed128 compatibility copy.
    return encode_bytes_field(1, value.bytes) + encode_bytes_field(2, fixed128)


def _ffi_full_uuid(value: UUID) -> bytes:
    """Encode a UUID that entered Rust through the Android UniFFI boundary."""

    if not isinstance(value, UUID):
        raise ValueError("map and schedule identifiers must be UUID values")
    raw = value.bytes
    ffi_bytes = raw[:8][::-1] + raw[8:][::-1]
    first = int.from_bytes(raw[:8], "little")
    second = int.from_bytes(raw[8:], "little")
    fixed128 = b"".join(
        (
            encode_fixed64_field(1, first) if first != 0 else b"",
            encode_fixed64_field(2, second) if second != 0 else b"",
        )
    )
    return encode_bytes_field(1, ffi_bytes) + encode_bytes_field(2, fixed128)


def _ffi_partition_id(value: UUID) -> bytes:
    return encode_bytes_field(1, _ffi_full_uuid(value))


def _ffi_region_id(value: UUID) -> bytes:
    return encode_bytes_field(2, _ffi_full_uuid(value))


def _map_point(point: MapPoint, *, field_name: str) -> bytes:
    if not isinstance(point, MapPoint):
        raise ValueError(f"{field_name} must be a MapPoint")
    x_meters = _float32_value(point.x_meters, field_name=f"{field_name}.x_meters")
    y_meters = _float32_value(point.y_meters, field_name=f"{field_name}.y_meters")
    return _encode_float32_field(1, -y_meters) + _encode_float32_field(2, -x_meters)


def _drawn_area(
    circles: object,
    *,
    field_name: str,
) -> bytes:
    if not isinstance(circles, tuple) or not circles:
        raise ValueError(f"{field_name} must be a non-empty tuple")
    encoded_circles: list[bytes] = []
    for index, circle in enumerate(circles):
        if not isinstance(circle, DrawnCircle):
            raise ValueError(f"{field_name} must contain DrawnCircle values")
        x_meters = _float32_value(
            circle.x_meters,
            field_name=f"{field_name}[{index}].x_meters",
        )
        y_meters = _float32_value(
            circle.y_meters,
            field_name=f"{field_name}[{index}].y_meters",
        )
        radius_meters = _float32_value(
            circle.radius_meters,
            field_name=f"{field_name}[{index}].radius_meters",
        )
        if radius_meters <= 0:
            raise ValueError(f"{field_name} radii must be greater than zero")
        point = _encode_float32_field(1, -y_meters) + _encode_float32_field(
            2, -x_meters
        )
        point_circle = encode_bytes_field(1, point) + encode_fixed32_field(
            2,
            _float32_bits(
                radius_meters,
                field_name=f"{field_name}[{index}].radius_meters",
            ),
        )
        encoded_circles.append(encode_bytes_field(1, point_circle))
    return encode_bytes_field(2, b"".join(encoded_circles))


def _map_command_has_extra_fields(
    command: MapEnvironmentCommand,
    *,
    allow_partition: bool = False,
    allow_change: bool = False,
    allow_overwrite: bool = False,
    allow_name: bool = False,
) -> bool:
    return bool(
        command.change_set
        or (command.partition_id is not None and not allow_partition)
        or (command.change is not None and not allow_change)
        or (command.overwrite is not None and not allow_overwrite)
        or (command.name is not None and not allow_name)
    )


@dataclass(frozen=True, slots=True)
class _VerifiedBuildPartitionCodec:
    """Exact native partition-build command, including its generated ID."""

    command_id_factory: Callable[[], UUID] = uuid4

    def encode(self, command: ControlCommand) -> EncodedCommand:
        build = _require_map_action(command, MapEnvironmentAction.BUILD_PARTITION)
        mission_id = _u32(
            build.mission_id,
            field_name="map.build_partition mission_id",
        )
        if _map_command_has_extra_fields(build, allow_overwrite=True):
            raise ValueError(
                "map.build_partition accepts only mission_id and overwrite"
            )
        if not isinstance(build.overwrite, bool):
            raise ValueError("map.build_partition requires overwrite: bool")
        command_id = self.command_id_factory()
        if not isinstance(command_id, UUID):
            raise TypeError("map command_id_factory must return UUID")
        operation = encode_bytes_field(1, _generated_full_uuid(command_id))
        options = encode_bytes_field(1, encode_bytes_field(1, b""))
        if build.overwrite:
            options += encode_varint_field(2, 1)
        payload = b"".join(
            (
                encode_bytes_field(1, _mission_id_message(mission_id)),
                encode_bytes_field(2, operation),
                encode_bytes_field(3, options),
            )
        )
        return EncodedCommand(payload, "build_regions")


def _room_label(value: object) -> bytes:
    if isinstance(value, RoomLabel):
        number = {
            RoomLabel.BATHROOM: 0,
            RoomLabel.BEDROOM: 1,
            RoomLabel.DINING_ROOM: 2,
            RoomLabel.LIVING_ROOM: 3,
            RoomLabel.KITCHEN: 4,
            RoomLabel.HALLWAY: 5,
        }[value]
        return encode_varint_field(1, number)
    if isinstance(value, str):
        return encode_bytes_field(2, value.encode())
    raise ValueError("room label must be a RoomLabel or custom string")


@dataclass(frozen=True, slots=True)
class _VerifiedEditRoomsCodec:
    """Exact rename, merge, and split map-room encoder."""

    def encode(self, command: ControlCommand) -> EncodedCommand:
        edit = _require_map_action(command, MapEnvironmentAction.EDIT_ROOMS)
        mission_id = _u32(edit.mission_id, field_name="map.edit_rooms mission_id")
        if not isinstance(edit.partition_id, UUID):
            raise ValueError("map.edit_rooms requires partition_id: UUID")
        if _map_command_has_extra_fields(
            edit,
            allow_partition=True,
            allow_change=True,
        ):
            raise ValueError(
                "map.edit_rooms accepts mission_id, partition_id, and a room edit"
            )

        action = edit.change
        if isinstance(action, RenameRoom):
            if not isinstance(action.region_id, UUID):
                raise ValueError("RenameRoom requires region_id: UUID")
            action_payload = encode_bytes_field(
                1,
                _ffi_region_id(action.region_id),
            ) + encode_bytes_field(
                2,
                _room_label(action.label),
            )
            action_field = 5
        elif isinstance(action, MergeRooms):
            if not isinstance(action.first_region_id, UUID) or not isinstance(
                action.second_region_id, UUID
            ):
                raise ValueError("MergeRooms requires two UUID region identifiers")
            first = _ffi_region_id(action.first_region_id)
            second = _ffi_region_id(action.second_region_id)
            action_payload = b"".join(
                (
                    encode_bytes_field(1, first),
                    encode_bytes_field(2, second),
                    encode_bytes_field(3, _room_label(action.label)),
                    encode_bytes_field(4, first),
                    encode_bytes_field(4, second),
                )
            )
            action_field = 6
        elif isinstance(action, SplitRoom):
            if not isinstance(action.region_id, UUID):
                raise ValueError("SplitRoom requires region_id: UUID")
            line = encode_bytes_field(
                1,
                _map_point(action.start, field_name="SplitRoom.start"),
            ) + encode_bytes_field(
                2,
                _map_point(action.end, field_name="SplitRoom.end"),
            )
            action_payload = encode_bytes_field(
                1,
                _ffi_region_id(action.region_id),
            ) + encode_bytes_field(2, line)
            action_field = 7
        else:
            raise ValueError(
                "map.edit_rooms requires RenameRoom, MergeRooms, or SplitRoom"
            )

        payload = b"".join(
            (
                encode_bytes_field(1, _mission_id_message(mission_id)),
                encode_bytes_field(4, _ffi_partition_id(edit.partition_id)),
                encode_bytes_field(action_field, action_payload),
            )
        )
        return EncodedCommand(payload, "rename_area_command")


def _compact_zone_region_id(value: int | UUID, *, field_name: str) -> int:
    if isinstance(value, UUID):
        raw = value.bytes
        if raw[:8] != b"\0" * 8 or raw[12:] != b"\0" * 4:
            raise ValueError(
                f"{field_name} UUID must use the native compact u32 layout"
            )
        return int.from_bytes(raw[8:12], "little")
    return _u32(value, field_name=field_name)


@dataclass(frozen=True, slots=True)
class _VerifiedZoneEditCodec:
    """Exact shared encoder for no-go, drive-only, and stair zones."""

    action: MapEnvironmentAction
    target: str

    def encode(self, command: ControlCommand) -> EncodedCommand:
        edit = _require_map_action(command, self.action)
        mission_id = _u32(edit.mission_id, field_name=f"{edit.command_key} mission_id")
        if _map_command_has_extra_fields(edit, allow_change=True):
            raise ValueError(f"{edit.command_key} accepts mission_id and a zone edit")
        if isinstance(edit.change, AddZones):
            change = encode_bytes_field(
                4,
                _drawn_area(
                    edit.change.circles,
                    field_name=f"{edit.command_key} circles",
                ),
            )
        elif isinstance(edit.change, RemoveZones):
            if (
                not isinstance(edit.change.region_ids, tuple)
                or not edit.change.region_ids
            ):
                raise ValueError(
                    f"{edit.command_key} removal requires region identifiers"
                )
            packed = b"".join(
                encode_varint(
                    _compact_zone_region_id(
                        value,
                        field_name=f"{edit.command_key} region_ids[{index}]",
                    )
                )
                for index, value in enumerate(edit.change.region_ids)
            )
            change = encode_bytes_field(3, packed)
        else:
            raise ValueError(f"{edit.command_key} requires AddZones or RemoveZones")
        payload = (
            encode_bytes_field(
                1,
                _mission_id_message(mission_id),
            )
            + change
        )
        return EncodedCommand(payload, self.target)


@dataclass(frozen=True, slots=True)
class _VerifiedSemanticsOverrideCodec:
    """Exact map surface/wire-semantics override encoder."""

    def encode(self, command: ControlCommand) -> EncodedCommand:
        edit = _require_map_action(
            command,
            MapEnvironmentAction.EDIT_SEMANTICS_OVERRIDE,
        )
        mission_id = _u32(
            edit.mission_id,
            field_name="map.edit_semantics_override mission_id",
        )
        if _map_command_has_extra_fields(edit, allow_change=True):
            raise ValueError(
                "map.edit_semantics_override accepts mission_id and SemanticsOverride"
            )
        if not isinstance(edit.change, SemanticsOverride):
            raise ValueError("map.edit_semantics_override requires SemanticsOverride")
        if not isinstance(edit.change.kind, SemanticsOverrideKind):
            raise ValueError("semantics override kind must be an exact enum")
        kind = {
            SemanticsOverrideKind.UNSET: 0,
            SemanticsOverrideKind.HARDFLOOR_ALLOW_WIRE: 1,
            SemanticsOverrideKind.CARPET_ALLOW_WIRE: 2,
            SemanticsOverrideKind.HARDFLOOR_DISALLOW_WIRE: 3,
            SemanticsOverrideKind.CARPET_DISALLOW_WIRE: 4,
        }[edit.change.kind]
        override = (
            encode_varint_field(2, kind) if kind != 0 else b""
        ) + encode_bytes_field(
            3,
            _drawn_area(
                edit.change.circles,
                field_name="map.edit_semantics_override circles",
            ),
        )
        payload = encode_bytes_field(
            1,
            _mission_id_message(mission_id),
        ) + encode_bytes_field(2, override)
        return EncodedCommand(payload, "semantics_override")


def _sink_posture(location: SinkSummonLocation) -> bytes:
    x_meters = _float32_value(
        location.x_meters,
        field_name="sink location x_meters",
    )
    y_meters = _float32_value(
        location.y_meters,
        field_name="sink location y_meters",
    )
    yaw_radians = _float32_value(
        location.yaw_radians,
        field_name="sink location yaw_radians",
    )
    orientation = _encode_float32_field(
        1, -math.sin(yaw_radians)
    ) + _encode_float32_field(2, -math.cos(yaw_radians))
    return b"".join(
        (
            _encode_float32_field(1, -y_meters),
            _encode_float32_field(2, -x_meters),
            encode_bytes_field(4, orientation),
        )
    )


@dataclass(frozen=True, slots=True)
class _VerifiedSinkSummonLocationCodec:
    """Exact add/modify and remove sink-summon map-location encoder."""

    def encode(self, command: ControlCommand) -> EncodedCommand:
        edit = _require_map_action(
            command,
            MapEnvironmentAction.EDIT_SINK_SUMMON_LOCATION,
        )
        mission_id = _u32(
            edit.mission_id,
            field_name="map.edit_sink_summon_location mission_id",
        )
        if _map_command_has_extra_fields(edit, allow_change=True):
            raise ValueError(
                "map.edit_sink_summon_location accepts mission_id and an "
                "optional SinkSummonLocation"
            )
        mission = _mission_id_message(mission_id)
        if edit.change is None:
            payload = encode_bytes_field(2, mission)
        elif isinstance(edit.change, SinkSummonLocation):
            location = encode_bytes_field(1, mission) + encode_bytes_field(
                2,
                _sink_posture(edit.change),
            )
            payload = encode_bytes_field(1, location)
        else:
            raise ValueError(
                "map.edit_sink_summon_location requires SinkSummonLocation or None"
            )
        return EncodedCommand(payload, "edit_sink_summon_location")


@dataclass(frozen=True, slots=True)
class _VerifiedFloorCodec:
    """Exact floor canonicalization and rename encoders."""

    action: MapEnvironmentAction

    def encode(self, command: ControlCommand) -> EncodedCommand:
        floor = _require_map_action(command, self.action)
        if self.action is MapEnvironmentAction.CANONICALIZE:
            if _map_command_has_extra_fields(floor):
                raise ValueError("map.canonicalize accepts only optional mission_id")
            if floor.mission_id is None:
                # CanonicalizeCommand.NextNoncanonicalMission.
                payload = encode_bytes_field(1, encode_bytes_field(2, b""))
            else:
                mission_id = _u32(
                    floor.mission_id,
                    field_name="map.canonicalize mission_id",
                )
                command_payload = encode_bytes_field(
                    1,
                    _mission_id_message(mission_id),
                )
                payload = encode_bytes_field(1, command_payload)
        else:
            mission_id = _u32(
                floor.mission_id,
                field_name="map.rename mission_id",
            )
            if any(
                value is not None
                for value in (
                    floor.partition_id,
                    floor.change,
                    floor.overwrite,
                )
            ):
                raise ValueError("map.rename accepts only mission_id and name")
            if floor.name is not None and floor.change_set:
                raise ValueError("map.rename name cannot be supplied twice")
            name: object
            if floor.name is not None:
                name = floor.name
            elif set(floor.change_set) == {"name"}:
                name = floor.change_set["name"]
            else:
                raise ValueError("map.rename requires name: str")
            if not isinstance(name, str):
                raise ValueError("map.rename name must be a string")
            rename = encode_bytes_field(
                1, _mission_id_message(mission_id)
            ) + encode_bytes_field(2, name.encode())
            payload = encode_bytes_field(3, rename)
        return EncodedCommand(payload, "floor_command")


@dataclass(frozen=True, slots=True)
class _VerifiedPersistenceCodec:
    """Exact encoders for the four native map-persistence variants."""

    action: MapEnvironmentAction

    def encode(self, command: ControlCommand) -> EncodedCommand:
        persistence = _require_map_action(command, self.action)
        if _map_command_has_extra_fields(persistence):
            raise ValueError(
                f"{persistence.command_key} does not accept map-edit arguments"
            )
        if self.action is MapEnvironmentAction.CLEAR_MAP:
            mission_id = _u32(
                persistence.mission_id,
                field_name="map.clear_map mission_id",
            )
            payload = encode_bytes_field(
                4,
                _mission_id_message(mission_id),
            )
        else:
            if persistence.mission_id is not None:
                raise ValueError(
                    f"{persistence.command_key} does not accept mission_id"
                )
            payload = {
                MapEnvironmentAction.PERSISTENCE_CLEAR: bytes.fromhex("1200"),
                MapEnvironmentAction.RESTORE_MAP: bytes.fromhex("080432021200"),
                MapEnvironmentAction.UPLOAD_MAP_FOR_DEBUG: bytes.fromhex(
                    "08053a021a00"
                ),
            }[self.action]
        return EncodedCommand(payload, "map_command")


@dataclass(frozen=True, slots=True)
class _VerifiedEmptyMapCodec:
    action: MapEnvironmentAction
    target: str

    def encode(self, command: ControlCommand) -> EncodedCommand:
        if (
            not isinstance(command, MapEnvironmentCommand)
            or command.action is not self.action
        ):
            raise TypeError(f"codec expects MapEnvironmentCommand({self.action.value})")
        if command.mission_id is not None or _map_command_has_extra_fields(command):
            raise ValueError(f"{command.command_key} does not accept arguments")
        return EncodedCommand(b"", self.target)


def _require_media_action(
    command: ControlCommand,
    action: MediaAction,
) -> MediaCommand:
    if not isinstance(command, MediaCommand) or command.action is not action:
        raise TypeError(f"codec expects MediaCommand({action.value})")
    return command


@dataclass(frozen=True, slots=True)
class _VerifiedRecordingCodec:
    """Exact encoder for recording enable and rolling-buffer flush."""

    action: MediaAction

    def encode(self, command: ControlCommand) -> EncodedCommand:
        media = _require_media_action(command, self.action)
        if self.action is MediaAction.RECORDING_ENABLE:
            if not isinstance(media.enabled, bool):
                raise ValueError("media.recording_enable requires enabled: bool")
            if media.recording_id is not None or media.confirm_for_each is not None:
                raise ValueError("media.recording_enable accepts only enabled")
            # The app uses the newer field-4 message, not the deprecated
            # top-level bool field exposed by the proto.
            payload = (
                bytes.fromhex("22020801") if media.enabled else bytes.fromhex("2200")
            )
        else:
            if any(
                value is not None
                for value in (
                    media.recording_id,
                    media.enabled,
                    media.confirm_for_each,
                )
            ):
                raise ValueError("media.flush_rolling_buffer does not accept arguments")
            payload = bytes.fromhex("1a00")
        return EncodedCommand(payload, "recording_command")


@dataclass(frozen=True, slots=True)
class _VerifiedRollingRecordingCodec:
    """Exact three-way encoder for enabled/confirmation/disabled."""

    def encode(self, command: ControlCommand) -> EncodedCommand:
        media = _require_media_action(
            command,
            MediaAction.ROLLING_BUFFER_CONFIG,
        )
        if media.recording_id is not None:
            raise ValueError("media.rolling_buffer_config does not accept recording_id")
        if not isinstance(media.enabled, bool):
            raise ValueError("media.rolling_buffer_config requires enabled: bool")
        if media.enabled:
            if not isinstance(media.confirm_for_each, bool):
                raise ValueError(
                    "enabled media.rolling_buffer_config requires "
                    "confirm_for_each: bool"
                )
            payload = (
                bytes.fromhex("1200")
                if media.confirm_for_each
                else bytes.fromhex("0a00")
            )
        else:
            if media.confirm_for_each is not None:
                raise ValueError(
                    "disabled media.rolling_buffer_config does not accept "
                    "confirm_for_each"
                )
            payload = bytes.fromhex("1a00")
        return EncodedCommand(payload, "toggle_rolling_recordings")


@dataclass(frozen=True, slots=True)
class _VerifiedConfirmRecordingCodec:
    """Exact recording-id plus save/delete action encoder."""

    action: MediaAction

    def encode(self, command: ControlCommand) -> EncodedCommand:
        media = _require_media_action(command, self.action)
        if media.enabled is not None or media.confirm_for_each is not None:
            raise ValueError(f"{media.command_key} accepts only recording_id")
        recording_id = media.recording_id
        if (
            isinstance(recording_id, bool)
            or not isinstance(recording_id, int)
            or not 0 <= recording_id <= 0xFFFFFFFFFFFFFFFF
        ):
            raise ValueError(f"{media.command_key} requires recording_id: u64")
        action = 0 if self.action is MediaAction.CONFIRM_SAVE else 1
        recording = encode_varint_field(1, recording_id) if recording_id != 0 else b""
        payload = encode_varint_field(1, action) + encode_bytes_field(2, recording)
        return EncodedCommand(payload, "recording_upload_confirmation")


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
            raise ValueError("device.configure_shipping accepts only retain_user_data")
        if not isinstance(command.retain_user_data, bool):
            raise ValueError(
                "device.configure_shipping requires retain_user_data: bool"
            )
        return EncodedCommand(
            bytes((0x08, int(command.retain_user_data))),
            "configure_shipping_command",
        )


def _require_schedule_action(
    command: ControlCommand,
    action: ScheduleAction,
) -> ScheduleCommand:
    if not isinstance(command, ScheduleCommand) or command.action is not action:
        raise TypeError(f"codec expects ScheduleCommand({action.value})")
    return command


def _schedule_command_has_extra_fields(
    command: ScheduleCommand,
    *,
    allow_key: bool = False,
    allow_event: bool = False,
    allow_sink_event: bool = False,
) -> bool:
    return bool(
        command.definition
        or (command.key is not None and not allow_key)
        or (command.event is not None and not allow_event)
        or (command.sink_event is not None and not allow_sink_event)
    )


def _schedule_key(key: object) -> bytes:
    if not isinstance(key, ScheduleEventKey):
        raise ValueError("schedule command requires a ScheduleEventKey")
    mission_id = _u32(key.mission_id, field_name="schedule key mission_id")
    if not isinstance(key.event_id, UUID):
        raise ValueError("schedule key event_id must be a UUID")
    event_id = encode_bytes_field(1, _ffi_full_uuid(key.event_id))
    return encode_bytes_field(
        1,
        _mission_id_message(mission_id),
    ) + encode_bytes_field(3, event_id)


def _sink_schedule_key(key: object) -> bytes:
    if not isinstance(key, ScheduleEventKey):
        raise ValueError("sink schedule command requires a ScheduleEventKey")
    mission_id = _u32(key.mission_id, field_name="sink schedule key mission_id")
    if not isinstance(key.event_id, UUID):
        raise ValueError("sink schedule key event_id must be a UUID")
    event_id = encode_bytes_field(1, _ffi_full_uuid(key.event_id))
    return encode_bytes_field(1, event_id) + encode_bytes_field(
        2,
        _mission_id_message(mission_id),
    )


def _weekdays(values: object, *, field_name: str) -> bytes:
    if not isinstance(values, tuple):
        raise ValueError(f"{field_name} must be a tuple of Weekday values")
    if any(not isinstance(value, Weekday) for value in values):
        raise ValueError(f"{field_name} must contain exact Weekday values")
    if len(set(values)) != len(values):
        raise ValueError(f"{field_name} cannot contain duplicates")
    fields = {
        Weekday.MONDAY: 1,
        Weekday.TUESDAY: 2,
        Weekday.WEDNESDAY: 3,
        Weekday.THURSDAY: 4,
        Weekday.FRIDAY: 5,
        Weekday.SATURDAY: 6,
        Weekday.SUNDAY: 7,
    }
    return b"".join(
        encode_varint_field(fields[weekday], 1)
        for weekday in fields
        if weekday in values
    )


def _signed_i64_varint(value: int, *, field_name: str) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not -(1 << 63) <= value < (1 << 63)
    ):
        raise ValueError(f"{field_name} must be a signed 64-bit integer")
    return value & ((1 << 64) - 1)


def _schedule_time(value: object, *, field_name: str) -> bytes:
    if not isinstance(value, ScheduleTime):
        raise ValueError(f"{field_name} must be a ScheduleTime")
    seconds = _u32(
        value.seconds_since_midnight,
        field_name=f"{field_name}.seconds_since_midnight",
    )
    if not isinstance(value.timezone_id, str):
        raise ValueError(f"{field_name}.timezone_id must be a string")
    if (
        isinstance(value.utc_offset_seconds, bool)
        or not isinstance(value.utc_offset_seconds, int)
        or not -(1 << 31) <= value.utc_offset_seconds < (1 << 31)
    ):
        raise ValueError(f"{field_name}.utc_offset_seconds must be an i32")
    timezone = (
        encode_bytes_field(2, value.timezone_id.encode()) if value.timezone_id else b""
    )
    if value.utc_offset_seconds != 0:
        timezone += encode_varint_field(
            3,
            _signed_i64_varint(
                value.utc_offset_seconds,
                field_name=f"{field_name}.utc_offset_seconds",
            ),
        )
    return (
        encode_varint_field(1, seconds) if seconds != 0 else b""
    ) + encode_bytes_field(4, timezone)


def _schedule_timing(
    weekdays: object,
    time: object,
    *,
    field_name: str,
) -> bytes:
    return encode_bytes_field(
        1,
        _weekdays(weekdays, field_name=f"{field_name}.weekdays"),
    ) + encode_bytes_field(
        3,
        _schedule_time(time, field_name=f"{field_name}.time"),
    )


def _schedule_goal_header(*, goal_id: UUID, spec: bytes) -> bytes:
    round_key = encode_bytes_field(2, _generated_full_uuid(goal_id))
    return encode_bytes_field(
        6,
        encode_bytes_field(1, round_key) + encode_bytes_field(3, spec),
    )


def _schedule_region_target(*, partition_id: UUID, region_id: UUID) -> bytes:
    return b"".join(
        (
            encode_bytes_field(1, _ffi_partition_id(partition_id)),
            encode_bytes_field(2, encode_bytes_field(1, b"")),
            encode_bytes_field(
                3,
                encode_bytes_field(3, _ffi_region_id(region_id)),
            ),
        )
    )


def _schedule_custom_target(
    *,
    partition_id: UUID,
    circles: tuple[DrawnCircle, ...],
) -> bytes:
    return b"".join(
        (
            encode_bytes_field(1, _ffi_partition_id(partition_id)),
            encode_bytes_field(
                2,
                encode_bytes_field(
                    3,
                    _drawn_area(circles, field_name="schedule custom circles"),
                ),
            ),
            encode_bytes_field(3, encode_bytes_field(2, b"")),
        )
    )


@dataclass(frozen=True, slots=True)
class _VerifiedEditScheduleCodec:
    """Exact regular cleaning-schedule command encoder."""

    action: ScheduleAction
    command_id_factory: Callable[[], UUID] = uuid4

    def encode(self, command: ControlCommand) -> EncodedCommand:
        schedule = _require_schedule_action(command, self.action)
        if self.action is ScheduleAction.ADD_OR_MODIFY:
            if _schedule_command_has_extra_fields(
                schedule,
                allow_key=True,
                allow_event=True,
            ):
                raise ValueError("schedule.add_or_modify accepts key and ScheduleEvent")
            key = _schedule_key(schedule.key)
            event = self._event(schedule.event)
            payload = encode_bytes_field(
                2,
                encode_bytes_field(1, key) + encode_bytes_field(2, event),
            )
        else:
            if _schedule_command_has_extra_fields(schedule, allow_key=True):
                raise ValueError(f"{schedule.command_key} accepts only key")
            key = _schedule_key(schedule.key)
            payload = encode_bytes_field(
                3 if self.action is ScheduleAction.REMOVE else 4,
                key,
            )
        return EncodedCommand(payload, "edit_schedule")

    def _event(self, value: object) -> bytes:
        if not isinstance(value, ScheduleEvent):
            raise ValueError("schedule.add_or_modify requires ScheduleEvent")
        if not isinstance(value.partition_id, UUID):
            raise ValueError("ScheduleEvent.partition_id must be a UUID")
        if not isinstance(value.cleaning_mode, CoverageCleaningMode):
            raise ValueError("ScheduleEvent.cleaning_mode must be an exact enum")
        if not isinstance(value.ordered, bool):
            raise ValueError("ScheduleEvent.ordered must be a bool")
        if value.name is not None and not isinstance(value.name, str):
            raise ValueError("ScheduleEvent.name must be a string or None")
        if not isinstance(value.enabled_state, ScheduleEnabledState):
            raise ValueError("ScheduleEvent.enabled_state must be an exact enum")

        specs: list[bytes] = []
        if value.cleaning_mode in {
            CoverageCleaningMode.VACUUM,
            CoverageCleaningMode.BOTH,
        }:
            if not isinstance(value.vacuum_setting, ScheduleCoverageSetting):
                raise ValueError("vacuum schedules require ScheduleCoverageSetting")
            setting = {
                ScheduleCoverageSetting.DEPRECATED_DEEP: 0,
                ScheduleCoverageSetting.STANDARD: 1,
                ScheduleCoverageSetting.QUICK: 2,
            }[value.vacuum_setting]
            for floor in (0, 1):
                for behavior in range(4):
                    specs.append(
                        _encode_coverage_spec(
                            setting=setting,
                            floor=floor,
                            cleaning_mode=0,
                            behavior=behavior,
                        )
                    )
        if value.cleaning_mode in {
            CoverageCleaningMode.MOP,
            CoverageCleaningMode.BOTH,
        }:
            for behavior in range(4):
                specs.append(
                    _encode_coverage_spec(
                        setting=1,
                        floor=0,
                        cleaning_mode=1,
                        behavior=behavior,
                    )
                )

        targets: tuple[bytes, ...]
        if isinstance(value.target, StandardScheduleTarget):
            if (
                not isinstance(value.target.region_ids, tuple)
                or not value.target.region_ids
                or any(
                    not isinstance(region_id, UUID)
                    for region_id in value.target.region_ids
                )
            ):
                raise ValueError(
                    "StandardScheduleTarget requires a non-empty UUID tuple"
                )
            targets = tuple(
                _schedule_region_target(
                    partition_id=value.partition_id,
                    region_id=region_id,
                )
                for region_id in value.target.region_ids
            )
        elif isinstance(value.target, CustomScheduleTarget):
            targets = (
                _schedule_custom_target(
                    partition_id=value.partition_id,
                    circles=value.target.circles,
                ),
            )
        else:
            raise ValueError(
                "ScheduleEvent.target must be StandardScheduleTarget or "
                "CustomScheduleTarget"
            )

        goal_field = 1 if value.ordered else 2
        goals = b"".join(
            encode_bytes_field(
                goal_field,
                _schedule_goal_header(
                    goal_id=self._next_id(),
                    spec=spec,
                )
                + encode_bytes_field(7, target),
            )
            for target in targets
            for spec in specs
        )
        enabled = {
            ScheduleEnabledState.ENABLED: 1,
            ScheduleEnabledState.DISABLED: 0,
            ScheduleEnabledState.SUGGESTED: 2,
        }[value.enabled_state]
        event = encode_bytes_field(
            1,
            _schedule_timing(
                value.weekdays,
                value.time,
                field_name="ScheduleEvent",
            ),
        )
        if value.name is not None:
            event += encode_bytes_field(2, value.name.encode())
        event += encode_varint_field(3, enabled)
        event += encode_bytes_field(7, goals)
        event += encode_bytes_field(9, b"")
        return event

    def _next_id(self) -> UUID:
        value = self.command_id_factory()
        if not isinstance(value, UUID):
            raise TypeError("schedule command_id_factory must return UUID")
        return value


def _schedule_duration(value: object) -> bytes:
    if not isinstance(value, ScheduleDuration):
        raise ValueError("sink schedule duration must be ScheduleDuration")
    if (
        isinstance(value.seconds, bool)
        or not isinstance(value.seconds, int)
        or not 0 <= value.seconds < (1 << 63)
    ):
        raise ValueError("schedule duration seconds must be a non-negative i64")
    if (
        isinstance(value.nanoseconds, bool)
        or not isinstance(value.nanoseconds, int)
        or not 0 <= value.nanoseconds < 1_000_000_000
    ):
        raise ValueError("schedule duration nanoseconds must be in [0, 1e9)")
    return (encode_varint_field(1, value.seconds) if value.seconds != 0 else b"") + (
        encode_varint_field(2, value.nanoseconds) if value.nanoseconds != 0 else b""
    )


@dataclass(frozen=True, slots=True)
class _VerifiedEditSinkScheduleCodec:
    """Exact add/modify and remove sink-summon schedule encoder."""

    action: ScheduleAction

    def encode(self, command: ControlCommand) -> EncodedCommand:
        schedule = _require_schedule_action(command, self.action)
        if self.action is ScheduleAction.SINK_SUMMON_ADD_OR_MODIFY:
            if _schedule_command_has_extra_fields(
                schedule,
                allow_key=True,
                allow_sink_event=True,
            ):
                raise ValueError(
                    "schedule.sink_summon_add_or_modify accepts key and "
                    "SinkSummonScheduleEvent"
                )
            if not isinstance(schedule.sink_event, SinkSummonScheduleEvent):
                raise ValueError(
                    "schedule.sink_summon_add_or_modify requires "
                    "SinkSummonScheduleEvent"
                )
            if not isinstance(schedule.sink_event.enabled, bool):
                raise ValueError("sink schedule enabled must be a bool")
            event = b"".join(
                (
                    encode_bytes_field(
                        1,
                        _schedule_timing(
                            schedule.sink_event.weekdays,
                            schedule.sink_event.time,
                            field_name="SinkSummonScheduleEvent",
                        ),
                    ),
                    encode_bytes_field(
                        4,
                        _schedule_duration(schedule.sink_event.duration),
                    ),
                    encode_varint_field(6, int(schedule.sink_event.enabled)),
                )
            )
            entry = encode_bytes_field(
                1,
                _sink_schedule_key(schedule.key),
            ) + encode_bytes_field(2, event)
            payload = encode_bytes_field(1, entry)
        else:
            if _schedule_command_has_extra_fields(schedule, allow_key=True):
                raise ValueError("schedule.sink_summon_remove accepts only key")
            payload = encode_bytes_field(2, _sink_schedule_key(schedule.key))
        return EncodedCommand(payload, "edit_sink_summon_schedule")


@dataclass(frozen=True, slots=True)
class _VerifiedGenerateSuggestedScheduleCodec:
    def encode(self, command: ControlCommand) -> EncodedCommand:
        if (
            not isinstance(command, ScheduleCommand)
            or command.action is not ScheduleAction.GENERATE_SUGGESTED
        ):
            raise TypeError("codec expects ScheduleCommand(generate_suggested)")
        if _schedule_command_has_extra_fields(command):
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
        return EncodedCommand(
            self.payload,
            "reboot_command"
            if self.action
            in {
                LifecycleAction.REBOOT,
                LifecycleAction.SHUTDOWN,
            }
            else "update_command",
        )


# This inventory is intentionally explicit so evidence and live-test state stay
# reviewable per command even though every current protocol-25 intent has a codec.
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
        live_verified=True,
        evidence=(
            "Official Android offline encoder fixture and native target; "
            "a bounded SDK command transitioned ready to returning to "
            "charging with no robot errors on 2026-07-28"
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
        live_verified=True,
        evidence=(
            "Matic Android 1.151.0 native UserCommand::to_proto disassembly "
            "proves both nested field-2 envelopes and float32 fields 1 and 2; "
            "bounded SDK delivery acknowledged with docked-to-ready "
            "state transition 2026-07-28"
        ),
    ),
    _spec(
        "navigation.navigate",
        CommandFamily.NAVIGATION,
        CommandRisk.MOTION,
        NavigationCommand,
        "NavigationCommand.Navigate",
        fields=(
            "missionId: u32",
            "xMeters: float32",
            "yMeters: float32",
            "yawRadians: float32",
        ),
        target=USER_COMMAND_HERMES_TARGET,
        wire_verified=True,
        live_verified=True,
        evidence=(
            "Matic Android 1.151.0 and 1.167.0 native to_proto and prost "
            "encoder paths prove the raw posture fields and nested envelope; "
            "the shared reflected basis and a bounded 2026-07-28 live run "
            "prove the canonical transform, reaching the requested pose "
            "within 0.012 m and 0.078 rad"
        ),
    ),
    _spec(
        "navigation.navigate_and_wait",
        CommandFamily.NAVIGATION,
        CommandRisk.MOTION,
        NavigationCommand,
        "NavigationCommand.NavigateAndWait",
        fields=(
            "missionId: u32",
            "xMeters: float32",
            "yMeters: float32",
            "yawRadians: float32",
            "fixedWaitSeconds: 900",
        ),
        target=USER_COMMAND_HERMES_TARGET,
        wire_verified=True,
        evidence=(
            "Matic Android 1.151.0 and 1.167.0 native variant conversion and "
            "prost encoder paths prove NavigateTo field 4 and its fixed wait; "
            "motion-capable and not live-tested"
        ),
    ),
    _spec(
        "navigation.navigate_and_explore",
        CommandFamily.NAVIGATION,
        CommandRisk.MOTION,
        NavigationCommand,
        "UserCommand.NavigationAndExplore",
        fields=(
            "missionId: u32",
            "xMeters: float32",
            "yMeters: float32",
            "yawRadians: float32",
            "Explore background task",
        ),
        target=USER_COMMAND_HERMES_TARGET,
        wire_verified=True,
        evidence=(
            "Matic Android 1.151.0 native UserCommand conversion and task "
            "encoders prove the NavigateTo plus Explore envelopes; "
            "motion-capable and not live-tested"
        ),
    ),
    _spec(
        "coverage.normal",
        CommandFamily.COVERAGE,
        CommandRisk.MOTION,
        CoverageCommand,
        "NormalCoverageCommand",
        fields=(
            "missionId: u32",
            "partitionId: UUID",
            "regionIds: list<UUID>",
            "cleaningMode: DisplayedCleaningMode",
            "coverageSetting: CoverageSetting",
            "ordered: bool",
        ),
        target=USER_COMMAND_HERMES_TARGET,
        wire_verified=True,
        live_verified=True,
        evidence=(
            "Matic Android 1.151.0 native CoverageCommand and Goal encoder "
            "paths plus an official Matic Android 1.167.0 offline synthetic "
            "golden vector; a bounded one-room run was acknowledged and its "
            "active plan/session decoded before Stop and Dock on 2026-07-28"
        ),
    ),
    _spec(
        "coverage.reprioritize",
        CommandFamily.COVERAGE,
        CommandRisk.MOTION,
        ReprioritizeCoverageCommand,
        "ReprioritizeCoverageCommand",
        target=USER_COMMAND_HERMES_TARGET,
        fields=(
            "action: Prioritize | Skip",
            "missionId: u32",
            "goals: CoverageGoals",
            "currentRegionId: UUID",
            "selectedRegionId: UUID?",
            "currentSessionId: UUID",
        ),
        wire_verified=True,
        evidence=(
            "Matic Android 1.151.0 native apply_reprioritize_action, Goal, "
            "CoverageSpec, and CoverageCommand encoder paths prove exact "
            "Prioritize/Skip transformations and envelope; motion-capable "
            "and not live-tested"
        ),
    ),
    _spec(
        "coverage.stain_mode",
        CommandFamily.COVERAGE,
        CommandRisk.MOTION,
        CoverageCommand,
        "StainModeCoverageCommand",
        target=USER_COMMAND_HERMES_TARGET,
        fields=(
            "missionId: u32",
            "stainMode: DryStain | WetSpill",
            "circles: list<DrawnCircle>",
        ),
        wire_verified=True,
        evidence=(
            "Matic Android 1.151.0 native stain goal construction, DrawnArea, "
            "PointCircle, CoverageSpec, Goal, and CoverageCommand encoder "
            "paths prove the exact goal plan and envelope; motion-capable "
            "and not live-tested"
        ),
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
        target="motor_command",
        fields=(
            "vacuumRpm: Option<float32>",
            "sweeperDuty: Option<float32>",
            "mopperDuty: Option<float32>",
            "headPosition: Option<float32>",
            "sideBrushDuty: Option<float32>",
        ),
        wire_verified=True,
        evidence=(
            "Matic Android 1.151.0 native Option<float32> encoder fields and "
            "exact target; not live-tested"
        ),
    ),
    _spec(
        "map.build_partition",
        CommandFamily.MAP_ENVIRONMENT,
        CommandRisk.PERSISTENT,
        MapEnvironmentCommand,
        "BuildPartitionCommand",
        fields=("missionId: u32", "overwrite: bool", "generated operation UUID"),
        target="build_regions",
        wire_verified=True,
        evidence=_NATIVE_SERIALIZER_EVIDENCE,
    ),
    _spec(
        "map.edit_rooms",
        CommandFamily.MAP_ENVIRONMENT,
        CommandRisk.PERSISTENT,
        MapEnvironmentCommand,
        "EditRoomsCommand",
        fields=("missionId: u32", "partitionId: UUID", "typed room edit"),
        target="rename_area_command",
        wire_verified=True,
        evidence=_NATIVE_SERIALIZER_EVIDENCE,
    ),
    _spec(
        "map.edit_no_go_zone",
        CommandFamily.MAP_ENVIRONMENT,
        CommandRisk.PERSISTENT,
        MapEnvironmentCommand,
        "NoGoZoneEdit",
        fields=("missionId: u32", "Add(circles) | Remove(regionIds)"),
        target="nogo_command",
        wire_verified=True,
        evidence=_NATIVE_SERIALIZER_EVIDENCE,
    ),
    _spec(
        "map.edit_drive_only_zone",
        CommandFamily.MAP_ENVIRONMENT,
        CommandRisk.PERSISTENT,
        MapEnvironmentCommand,
        "DriveOnlyZoneEdit",
        fields=("missionId: u32", "Add(circles) | Remove(regionIds)"),
        target="nogo_command",
        wire_verified=True,
        evidence=_NATIVE_SERIALIZER_EVIDENCE,
    ),
    _spec(
        "map.edit_stairs",
        CommandFamily.MAP_ENVIRONMENT,
        CommandRisk.PERSISTENT,
        MapEnvironmentCommand,
        "StairEdit",
        fields=("missionId: u32", "Add(circles) | Remove(regionIds)"),
        target="stair_command",
        wire_verified=True,
        evidence=_NATIVE_SERIALIZER_EVIDENCE,
    ),
    _spec(
        "map.edit_semantics_override",
        CommandFamily.MAP_ENVIRONMENT,
        CommandRisk.PERSISTENT,
        MapEnvironmentCommand,
        "SemanticsOverrideCommand",
        fields=("missionId: u32", "circles", "semantics kind"),
        target="semantics_override",
        wire_verified=True,
        evidence=_NATIVE_SERIALIZER_EVIDENCE,
    ),
    _spec(
        "map.edit_sink_summon_location",
        CommandFamily.MAP_ENVIRONMENT,
        CommandRisk.PERSISTENT,
        MapEnvironmentCommand,
        "EditSinkSummonLocationCommand",
        fields=("missionId: u32", "location: Option<Posture>"),
        target="edit_sink_summon_location",
        wire_verified=True,
        evidence=_NATIVE_SERIALIZER_EVIDENCE,
    ),
    _spec(
        "map.canonicalize",
        CommandFamily.MAP_ENVIRONMENT,
        CommandRisk.PERSISTENT,
        MapEnvironmentCommand,
        "FloorCommand.Canonicalize",
        fields=("missionId: Option<u32>",),
        target="floor_command",
        wire_verified=True,
        evidence=_NATIVE_SERIALIZER_EVIDENCE,
    ),
    _spec(
        "map.rename",
        CommandFamily.MAP_ENVIRONMENT,
        CommandRisk.PERSISTENT,
        MapEnvironmentCommand,
        "FloorCommand.Rename",
        fields=("missionId: u32", "name: String"),
        target="floor_command",
        wire_verified=True,
        evidence=_NATIVE_SERIALIZER_EVIDENCE,
    ),
    _spec(
        "map.persistence_clear",
        CommandFamily.MAP_ENVIRONMENT,
        CommandRisk.DESTRUCTIVE,
        MapEnvironmentCommand,
        "PersistenceCommand.Clear",
        target="map_command",
        wire_verified=True,
        evidence=_NATIVE_SERIALIZER_EVIDENCE,
    ),
    _spec(
        "map.clear_map",
        CommandFamily.MAP_ENVIRONMENT,
        CommandRisk.DESTRUCTIVE,
        MapEnvironmentCommand,
        "PersistenceCommand.ClearMap",
        fields=("missionId: u32",),
        target="map_command",
        wire_verified=True,
        evidence=_NATIVE_SERIALIZER_EVIDENCE,
    ),
    _spec(
        "map.restore_map",
        CommandFamily.MAP_ENVIRONMENT,
        CommandRisk.PERSISTENT,
        MapEnvironmentCommand,
        "PersistenceCommand.RestoreMap",
        target="map_command",
        wire_verified=True,
        evidence=_NATIVE_SERIALIZER_EVIDENCE,
    ),
    _spec(
        "map.upload_map_for_debug",
        CommandFamily.MAP_ENVIRONMENT,
        CommandRisk.SENSITIVE,
        MapEnvironmentCommand,
        "PersistenceCommand.UploadMapForDebug",
        target="map_command",
        wire_verified=True,
        evidence=_NATIVE_SERIALIZER_EVIDENCE,
    ),
    _spec(
        "map.clear_rgb_weights",
        CommandFamily.MAP_ENVIRONMENT,
        CommandRisk.DESTRUCTIVE,
        MapEnvironmentCommand,
        "ClearRgbWeightsCommand",
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
        fields=("ssid: String", "passphrase: Option<String>"),
        target="wifi_update_command",
        wire_verified=True,
        evidence=_NATIVE_SERIALIZER_EVIDENCE,
    ),
    _spec(
        "wifi.forget",
        CommandFamily.WIFI,
        CommandRisk.DESTRUCTIVE,
        WifiCommand,
        "WifiUpdateCommand.Forget",
        fields=("ssid: String",),
        target="wifi_update_command",
        wire_verified=True,
        evidence=_NATIVE_SERIALIZER_EVIDENCE,
    ),
    _spec(
        "device.rename",
        CommandFamily.DEVICE,
        CommandRisk.PERSISTENT,
        DeviceCommand,
        "NewBotNameRequest",
        fields=("newName: String",),
        target="new_bot_name",
        wire_verified=True,
        evidence=_NATIVE_SERIALIZER_EVIDENCE,
    ),
    _spec(
        "device.discoverability",
        CommandFamily.DEVICE,
        CommandRisk.SENSITIVE,
        DeviceCommand,
        "DiscoverableRequest",
        fields=("Enable(durationSeconds: u64) | Disable",),
        target="set_device_discoverable",
        wire_verified=True,
        evidence=_NATIVE_SERIALIZER_EVIDENCE,
    ),
    _spec(
        "device.new_mop_roll",
        CommandFamily.DEVICE,
        CommandRisk.PERSISTENT,
        DeviceCommand,
        "NewMopRollCommand",
        fields=("enabled: bool",),
        target="new_mop_roll_override_command",
        wire_verified=True,
        evidence=_NATIVE_SERIALIZER_EVIDENCE,
    ),
    _spec(
        "device.clear_calibration",
        CommandFamily.DEVICE,
        CommandRisk.DESTRUCTIVE,
        DeviceCommand,
        "ClearCalibrationCommand",
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
        target=_BINARY_SETTING_TARGETS[SettingAction.CHILD_LOCK],
        wire_verified=True,
        live_verified=True,
        evidence=(
            "Official Android scalar-bool serializer and exact target; native "
            "offline golden vectors prove canonical false omission and true "
            "payload; idempotent SDK live write acknowledged and state "
            "preserved 2026-07-22"
        ),
    ),
    _spec(
        "settings.pet_waste_avoidance",
        CommandFamily.SETTINGS,
        CommandRisk.PERSISTENT,
        SettingsCommand,
        "PetWasteEnableCommand",
        target=_BINARY_SETTING_TARGETS[SettingAction.PET_WASTE_AVOIDANCE],
        wire_verified=True,
        live_verified=True,
        evidence=(
            "Official Android scalar-bool serializer and exact target; native "
            "offline golden vectors prove canonical false omission and true "
            "payload; idempotent SDK live write acknowledged and state "
            "preserved 2026-07-22"
        ),
    ),
    _spec(
        "settings.voice",
        CommandFamily.SETTINGS,
        CommandRisk.PERSISTENT,
        SettingsCommand,
        "VoiceEnableCommand",
        target=_BINARY_SETTING_TARGETS[SettingAction.VOICE],
        wire_verified=True,
        live_verified=True,
        evidence=(
            "Official Android scalar-bool serializer and exact target; native "
            "offline golden vectors prove canonical false omission and true "
            "payload; idempotent SDK live write acknowledged and state "
            "preserved 2026-07-22; bounded true/false writes were acknowledged "
            "but the retained state remained false 2026-07-29"
        ),
    ),
    _spec(
        "settings.auto_record_voice",
        CommandFamily.SETTINGS,
        CommandRisk.SENSITIVE,
        SettingsCommand,
        "AutoRecordVoiceEnableCommand",
        fields=("enabled: bool",),
        target="auto_record_voice_enabled_command",
        wire_verified=True,
        live_verified=True,
        evidence=(
            "Official Android serializer, exact target, and native offline "
            "golden vectors; bounded true/false writes were "
            "acknowledged with telemetry uploads disabled, but the retained "
            "state remained false and no app-facing media event appeared "
            "2026-07-29"
        ),
    ),
    _spec(
        "settings.matter_pairing",
        CommandFamily.SETTINGS,
        CommandRisk.SENSITIVE,
        SettingsCommand,
        "MatterPairingEnableCommand",
        fields=("enabled: bool",),
        target="matter_pairing_command",
        wire_verified=True,
        evidence=_NATIVE_SERIALIZER_EVIDENCE,
    ),
    _spec(
        "settings.preview_release",
        CommandFamily.SETTINGS,
        CommandRisk.PERSISTENT,
        SettingsCommand,
        "UserRequestedPreviewEnableCommand",
        fields=("enabled: bool",),
        target="request_preview_release_command",
        wire_verified=True,
        evidence=_NATIVE_SERIALIZER_EVIDENCE,
    ),
    _spec(
        "settings.jukebox",
        CommandFamily.SETTINGS,
        CommandRisk.PERSISTENT,
        SettingsCommand,
        "JukeboxState",
        fields=("track: Option<OhHanukkah | DeckTheHalls | JingleBells>",),
        target="jukebox_command",
        wire_verified=True,
        live_verified=True,
        evidence=(
            "Official Android serializer, exact target, and native offline "
            "golden vectors; Jingle Bells and stop were "
            "acknowledged, both jukebox state transitions were observed, and "
            "the nearby owner confirmed audible playback 2026-07-29"
        ),
    ),
    _spec(
        "schedule.add_or_modify",
        CommandFamily.SCHEDULES,
        CommandRisk.PERSISTENT,
        ScheduleCommand,
        "EditScheduleCommand.AddOrModify",
        fields=("key: ScheduleEventKey", "event: ScheduleEvent"),
        target="edit_schedule",
        wire_verified=True,
        evidence=_NATIVE_SERIALIZER_EVIDENCE,
    ),
    _spec(
        "schedule.remove",
        CommandFamily.SCHEDULES,
        CommandRisk.DESTRUCTIVE,
        ScheduleCommand,
        "EditScheduleCommand.Remove",
        fields=("key: ScheduleEventKey(missionId: u32, eventId: UUID)",),
        target="edit_schedule",
        wire_verified=True,
        evidence=_NATIVE_SERIALIZER_EVIDENCE,
    ),
    _spec(
        "schedule.toggle",
        CommandFamily.SCHEDULES,
        CommandRisk.PERSISTENT,
        ScheduleCommand,
        "EditScheduleCommand.Toggle",
        fields=("key: ScheduleEventKey(missionId: u32, eventId: UUID)",),
        target="edit_schedule",
        wire_verified=True,
        evidence=_NATIVE_SERIALIZER_EVIDENCE,
    ),
    _spec(
        "schedule.generate_suggested",
        CommandFamily.SCHEDULES,
        CommandRisk.PERSISTENT,
        ScheduleCommand,
        "GenerateSuggestedScheduleCommand",
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
        fields=("key: ScheduleEventKey", "event: SinkSummonScheduleEvent"),
        target="edit_sink_summon_schedule",
        wire_verified=True,
        evidence=_NATIVE_SERIALIZER_EVIDENCE,
    ),
    _spec(
        "schedule.sink_summon_remove",
        CommandFamily.SCHEDULES,
        CommandRisk.DESTRUCTIVE,
        ScheduleCommand,
        "EditSinkSummonScheduleCommand.Remove",
        fields=("key: SinkSummonScheduleEventKey(missionId: u32, eventId: UUID)",),
        target="edit_sink_summon_schedule",
        wire_verified=True,
        evidence=_NATIVE_SERIALIZER_EVIDENCE,
    ),
    _spec(
        "media.recording_enable",
        CommandFamily.MEDIA,
        CommandRisk.SENSITIVE,
        MediaCommand,
        "RecordingCommand.Enabled",
        fields=("enabled: bool",),
        target="recording_command",
        wire_verified=True,
        evidence=_NATIVE_SERIALIZER_EVIDENCE,
    ),
    _spec(
        "media.rolling_buffer_config",
        CommandFamily.MEDIA,
        CommandRisk.SENSITIVE,
        MediaCommand,
        "RollingRecordingConfigKind",
        fields=("Enabled(confirmForEach: bool) | Disabled",),
        target="toggle_rolling_recordings",
        wire_verified=True,
        evidence=_NATIVE_SERIALIZER_EVIDENCE,
    ),
    _spec(
        "media.flush_rolling_buffer",
        CommandFamily.MEDIA,
        CommandRisk.SENSITIVE,
        MediaCommand,
        "RecordingCommand.FlushRollingBuffer",
        fields=("no arguments (nested RecordingCommand variant)",),
        target="recording_command",
        wire_verified=True,
        evidence=_NATIVE_SERIALIZER_EVIDENCE,
    ),
    _spec(
        "media.confirm_save",
        CommandFamily.MEDIA,
        CommandRisk.SENSITIVE,
        MediaCommand,
        "ConfirmRecordingCommand save",
        fields=("id: u64", "action: Save"),
        target="recording_upload_confirmation",
        wire_verified=True,
        evidence=_NATIVE_SERIALIZER_EVIDENCE,
    ),
    _spec(
        "media.confirm_delete",
        CommandFamily.MEDIA,
        CommandRisk.DESTRUCTIVE,
        MediaCommand,
        "ConfirmRecordingCommand delete",
        fields=("id: u64", "action: Delete"),
        target="recording_upload_confirmation",
        wire_verified=True,
        evidence=_NATIVE_SERIALIZER_EVIDENCE,
    ),
    _spec(
        "telemetry.uploader_config",
        CommandFamily.TELEMETRY,
        CommandRisk.SENSITIVE,
        TelemetryCommand,
        "UploaderConfigCommand",
        fields=("optIn: bool",),
        target="uploader_config_command",
        wire_verified=True,
        evidence=_NATIVE_SERIALIZER_EVIDENCE,
    ),
    _spec(
        "telemetry.support_ssh_permission",
        CommandFamily.TELEMETRY,
        CommandRisk.SENSITIVE,
        TelemetryCommand,
        "UserTunnelSshPermissionCommand",
        fields=("enabled: bool",),
        target="user_tunnel_ssh_permission_command",
        wire_verified=True,
        evidence=_NATIVE_SERIALIZER_EVIDENCE,
    ),
    _spec(
        "telemetry.push_notification_subscription",
        CommandFamily.TELEMETRY,
        CommandRisk.SENSITIVE,
        TelemetryCommand,
        "PushNotificationSubscriptionCommand",
        fields=("deviceId: String", "appBundle: String"),
        target="subscribe_push_notifications",
        wire_verified=True,
        evidence=_NATIVE_SERIALIZER_EVIDENCE,
    ),
    _spec(
        "lifecycle.update",
        CommandFamily.LIFECYCLE,
        CommandRisk.DESTRUCTIVE,
        LifecycleCommand,
        "UpdateBotCommand",
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
                f"codecs require WIRE_VERIFIED evidence: {sorted(unavailable_codecs)}"
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
        "navigation.navigate": _VerifiedNavigationCodec(NavigationMode.NAVIGATE),
        "navigation.navigate_and_wait": _VerifiedNavigationCodec(
            NavigationMode.NAVIGATE_AND_WAIT
        ),
        "navigation.navigate_and_explore": _VerifiedNavigationCodec(
            NavigationMode.NAVIGATE_AND_EXPLORE
        ),
        "coverage.normal": _VerifiedNormalCoverageCodec(),
        "coverage.reprioritize": _VerifiedReprioritizeCoverageCodec(),
        "coverage.stain_mode": _VerifiedStainCoverageCodec(),
        "cleaning.manual": _VerifiedManualCleanCodec(),
        "raw_motors.setpoints": _VerifiedRawMotorCodec(),
        "map.build_partition": _VerifiedBuildPartitionCodec(),
        "map.edit_rooms": _VerifiedEditRoomsCodec(),
        "map.edit_no_go_zone": _VerifiedZoneEditCodec(
            MapEnvironmentAction.EDIT_NO_GO_ZONE,
            "nogo_command",
        ),
        "map.edit_drive_only_zone": _VerifiedZoneEditCodec(
            MapEnvironmentAction.EDIT_DRIVE_ONLY_ZONE,
            "nogo_command",
        ),
        "map.edit_stairs": _VerifiedZoneEditCodec(
            MapEnvironmentAction.EDIT_STAIRS,
            "stair_command",
        ),
        "map.edit_semantics_override": _VerifiedSemanticsOverrideCodec(),
        "map.edit_sink_summon_location": _VerifiedSinkSummonLocationCodec(),
        "map.canonicalize": _VerifiedFloorCodec(MapEnvironmentAction.CANONICALIZE),
        "map.rename": _VerifiedFloorCodec(MapEnvironmentAction.RENAME),
        "map.persistence_clear": _VerifiedPersistenceCodec(
            MapEnvironmentAction.PERSISTENCE_CLEAR
        ),
        "map.clear_map": _VerifiedPersistenceCodec(MapEnvironmentAction.CLEAR_MAP),
        "map.restore_map": _VerifiedPersistenceCodec(MapEnvironmentAction.RESTORE_MAP),
        "map.upload_map_for_debug": _VerifiedPersistenceCodec(
            MapEnvironmentAction.UPLOAD_MAP_FOR_DEBUG
        ),
        "map.clear_rgb_weights": _VerifiedEmptyMapCodec(
            MapEnvironmentAction.CLEAR_RGB_WEIGHTS,
            "clear_rgb_weights_command",
        ),
        "wifi.scan": _VerifiedWifiScanCodec(),
        "wifi.connect": _VerifiedWifiUpdateCodec(WifiAction.CONNECT),
        "wifi.forget": _VerifiedWifiUpdateCodec(WifiAction.FORGET),
        "device.rename": _VerifiedDeviceRenameCodec(),
        "device.discoverability": _VerifiedDiscoverabilityCodec(),
        "device.new_mop_roll": _VerifiedDeviceBooleanCodec(
            DeviceAction.NEW_MOP_ROLL,
            "new_mop_roll_override_command",
        ),
        "device.clear_calibration": _VerifiedDeviceEmptyCodec(
            DeviceAction.CLEAR_CALIBRATION,
            "clear_online_calib_command",
        ),
        "device.configure_shipping": _VerifiedConfigureShippingCodec(),
        **{
            f"settings.{action.value}": _VerifiedBinarySettingCodec(action, target)
            for action, target in _BINARY_SETTING_TARGETS.items()
        },
        "settings.auto_record_voice": _VerifiedBinarySettingCodec(
            SettingAction.AUTO_RECORD_VOICE,
            "auto_record_voice_enabled_command",
        ),
        "settings.matter_pairing": _VerifiedBinarySettingCodec(
            SettingAction.MATTER_PAIRING,
            "matter_pairing_command",
        ),
        "settings.preview_release": _VerifiedBinarySettingCodec(
            SettingAction.PREVIEW_RELEASE,
            "request_preview_release_command",
        ),
        "settings.jukebox": _VerifiedJukeboxCodec(),
        "schedule.add_or_modify": _VerifiedEditScheduleCodec(
            ScheduleAction.ADD_OR_MODIFY
        ),
        "schedule.remove": _VerifiedEditScheduleCodec(ScheduleAction.REMOVE),
        "schedule.toggle": _VerifiedEditScheduleCodec(ScheduleAction.TOGGLE),
        "schedule.generate_suggested": _VerifiedGenerateSuggestedScheduleCodec(),
        "schedule.sink_summon_add_or_modify": _VerifiedEditSinkScheduleCodec(
            ScheduleAction.SINK_SUMMON_ADD_OR_MODIFY
        ),
        "schedule.sink_summon_remove": _VerifiedEditSinkScheduleCodec(
            ScheduleAction.SINK_SUMMON_REMOVE
        ),
        "media.recording_enable": _VerifiedRecordingCodec(MediaAction.RECORDING_ENABLE),
        "media.rolling_buffer_config": _VerifiedRollingRecordingCodec(),
        "media.flush_rolling_buffer": _VerifiedRecordingCodec(
            MediaAction.FLUSH_ROLLING_BUFFER
        ),
        "media.confirm_save": _VerifiedConfirmRecordingCodec(MediaAction.CONFIRM_SAVE),
        "media.confirm_delete": _VerifiedConfirmRecordingCodec(
            MediaAction.CONFIRM_DELETE
        ),
        "telemetry.uploader_config": _VerifiedUploaderConfigCodec(),
        "telemetry.support_ssh_permission": _VerifiedTelemetryBooleanCodec(
            TelemetryAction.SUPPORT_SSH_PERMISSION,
            "user_tunnel_ssh_permission_command",
        ),
        "telemetry.push_notification_subscription": (_VerifiedPushNotificationCodec()),
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
    """Encode through the evidence-backed default registry."""

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
    "UnverifiedProtocolVersionWarning",
    "encode_command",
    "ensure_protocol_compatible",
]
