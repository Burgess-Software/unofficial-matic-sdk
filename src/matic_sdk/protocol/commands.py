"""Fail-closed command codec registry for Hermes protocol version 25.

Static analysis recovered command type names, five complete inner
``UserCommand`` payloads, and, for joystick control, field names and scalar
types. It did *not* establish the ``ChannelRequest`` envelope, the
``hermes-target`` value, or acknowledgement semantics. Consequently the
default registry documents the surface but deliberately contains no transport
encoders. Adding a codec requires positive end-to-end wire evidence; callers
never get a guessing or raw-payload escape hatch.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Protocol

from matic_sdk.models.control import (
    CleaningCommand,
    CommandFamily,
    CommandRisk,
    ControlCommand,
    CoverageCommand,
    DeviceCommand,
    JoystickCommand,
    LifecycleCommand,
    MapEnvironmentCommand,
    MediaCommand,
    NavigationCommand,
    RawMotorCommand,
    ScheduleCommand,
    SettingsCommand,
    TelemetryCommand,
    UserCommand,
    WifiCommand,
)

DEFAULT_PROTOCOL_VERSION = 25
SUPPORTED_PROTOCOL_VERSIONS = frozenset({DEFAULT_PROTOCOL_VERSION})
USER_COMMAND_HERMES_TARGET = "user_command"


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
            "tags, ChannelRequest envelope, or channel target remain unresolved"
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
) -> CommandSpec:
    return CommandSpec(
        key=key,
        family=family,
        risk=risk,
        model_type=model_type,
        native_type=native_type,
        evidence_level=(
            CodecEvidenceLevel.PAYLOAD_VERIFIED
            if payload is not None
            else (
                CodecEvidenceLevel.STATIC_FIELDS
                if fields
                else CodecEvidenceLevel.STATIC_TYPE
            )
        ),
        known_fields=fields,
        known_payload=payload,
        known_hermes_target=(
            USER_COMMAND_HERMES_TARGET if family is CommandFamily.USER else None
        ),
        requires_unsafe_controls=unsafe,
    )


# This inventory is intentionally explicit.  It is useful documentation even
# while every entry remains fail-closed at the wire boundary.
COMMAND_SPECS: tuple[CommandSpec, ...] = (
    _spec(
        "user.stop",
        CommandFamily.USER,
        CommandRisk.STATIONARY,
        UserCommand,
        "UserCommand.Stop",
        payload=bytes.fromhex("7a040a022200"),
    ),
    _spec(
        "user.stay_put",
        CommandFamily.USER,
        CommandRisk.STATIONARY,
        UserCommand,
        "UserCommand.StayPut",
        payload=bytes.fromhex("820100"),
    ),
    _spec(
        "user.pause",
        CommandFamily.USER,
        CommandRisk.STATIONARY,
        UserCommand,
        "UserCommand.Pause",
        payload=bytes.fromhex("4801880101"),
    ),
    _spec(
        "user.resume",
        CommandFamily.USER,
        CommandRisk.MOTION,
        UserCommand,
        "UserCommand.Resume",
        payload=bytes.fromhex("4800880100"),
    ),
    _spec(
        "user.dock",
        CommandFamily.USER,
        CommandRisk.MOTION,
        UserCommand,
        "UserCommand.Dock",
        payload=bytes.fromhex("12042a020800"),
    ),
    _spec(
        "user.explore",
        CommandFamily.USER,
        CommandRisk.MOTION,
        UserCommand,
        "UserCommand.Explore",
    ),
    _spec(
        "user.re_explore",
        CommandFamily.USER,
        CommandRisk.MOTION,
        UserCommand,
        "UserCommand.ReExplore",
    ),
    _spec(
        "user.redo_coverage",
        CommandFamily.USER,
        CommandRisk.MOTION,
        UserCommand,
        "UserCommand.RedoCoverage",
    ),
    _spec(
        "user.resume_coverage",
        CommandFamily.USER,
        CommandRisk.MOTION,
        UserCommand,
        "UserCommand.ResumeCoverage",
    ),
    _spec(
        "user.trace_calibration",
        CommandFamily.USER,
        CommandRisk.RAW_ACTUATION,
        UserCommand,
        "UserCommand.TraceCalib8",
        unsafe=True,
    ),
    _spec(
        "user.joystick",
        CommandFamily.USER,
        CommandRisk.MOTION,
        JoystickCommand,
        "UserCommand.Joystick(JoystickControl)",
        fields=("linearMetersPerSecond: float32", "angularRadiansPerSecond: float32"),
    ),
    _spec(
        "navigation.navigate",
        CommandFamily.NAVIGATION,
        CommandRisk.MOTION,
        NavigationCommand,
        "NavigationCommand.Navigate",
    ),
    _spec(
        "navigation.navigate_and_wait",
        CommandFamily.NAVIGATION,
        CommandRisk.MOTION,
        NavigationCommand,
        "NavigationCommand.NavigateAndWait",
    ),
    _spec(
        "navigation.navigate_and_explore",
        CommandFamily.NAVIGATION,
        CommandRisk.MOTION,
        NavigationCommand,
        "UserCommand.NavigationAndExplore",
    ),
    _spec(
        "coverage.normal",
        CommandFamily.COVERAGE,
        CommandRisk.MOTION,
        CoverageCommand,
        "NormalCoverageCommand",
    ),
    _spec(
        "coverage.reprioritize",
        CommandFamily.COVERAGE,
        CommandRisk.MOTION,
        CoverageCommand,
        "ReprioritizeCoverageCommand",
    ),
    _spec(
        "coverage.stain_mode",
        CommandFamily.COVERAGE,
        CommandRisk.MOTION,
        CoverageCommand,
        "StainModeCoverageCommand",
    ),
    _spec(
        "cleaning.manual",
        CommandFamily.CLEANING,
        CommandRisk.MOTION,
        CleaningCommand,
        "UserCommand.ManualClean",
    ),
    _spec(
        "raw_motors.setpoints",
        CommandFamily.RAW_MOTORS,
        CommandRisk.RAW_ACTUATION,
        RawMotorCommand,
        "CleaningMotorCommand",
        unsafe=True,
        fields=(
            "vacuum rpm",
            "sweeper duty",
            "mopper duty",
            "head position",
            "side-brush duty",
        ),
    ),
    _spec(
        "map.build_partition",
        CommandFamily.MAP_ENVIRONMENT,
        CommandRisk.PERSISTENT,
        MapEnvironmentCommand,
        "BuildPartitionCommand",
        unsafe=True,
    ),
    _spec(
        "map.edit_rooms",
        CommandFamily.MAP_ENVIRONMENT,
        CommandRisk.PERSISTENT,
        MapEnvironmentCommand,
        "EditRoomsCommand",
        unsafe=True,
    ),
    _spec(
        "map.edit_no_go_zone",
        CommandFamily.MAP_ENVIRONMENT,
        CommandRisk.PERSISTENT,
        MapEnvironmentCommand,
        "NoGoZoneEdit",
        unsafe=True,
    ),
    _spec(
        "map.edit_drive_only_zone",
        CommandFamily.MAP_ENVIRONMENT,
        CommandRisk.PERSISTENT,
        MapEnvironmentCommand,
        "DriveOnlyZoneEdit",
        unsafe=True,
    ),
    _spec(
        "map.edit_floor",
        CommandFamily.MAP_ENVIRONMENT,
        CommandRisk.PERSISTENT,
        MapEnvironmentCommand,
        "FloorCommand",
        unsafe=True,
    ),
    _spec(
        "map.edit_stairs",
        CommandFamily.MAP_ENVIRONMENT,
        CommandRisk.PERSISTENT,
        MapEnvironmentCommand,
        "StairEdit",
        unsafe=True,
    ),
    _spec(
        "map.edit_semantics_override",
        CommandFamily.MAP_ENVIRONMENT,
        CommandRisk.PERSISTENT,
        MapEnvironmentCommand,
        "SemanticsOverrideCommand",
        unsafe=True,
    ),
    _spec(
        "map.edit_sink_summon_location",
        CommandFamily.MAP_ENVIRONMENT,
        CommandRisk.PERSISTENT,
        MapEnvironmentCommand,
        "EditSinkSummonLocationCommand",
        unsafe=True,
    ),
    _spec(
        "map.canonicalize",
        CommandFamily.MAP_ENVIRONMENT,
        CommandRisk.PERSISTENT,
        MapEnvironmentCommand,
        "CanonicalizeCommand",
        unsafe=True,
    ),
    _spec(
        "map.rename",
        CommandFamily.MAP_ENVIRONMENT,
        CommandRisk.PERSISTENT,
        MapEnvironmentCommand,
        "RenameCommand",
        unsafe=True,
    ),
    _spec(
        "map.persistence_clear",
        CommandFamily.MAP_ENVIRONMENT,
        CommandRisk.DESTRUCTIVE,
        MapEnvironmentCommand,
        "PersistenceCommand.Clear",
        unsafe=True,
    ),
    _spec(
        "map.clear_map",
        CommandFamily.MAP_ENVIRONMENT,
        CommandRisk.DESTRUCTIVE,
        MapEnvironmentCommand,
        "PersistenceCommand.ClearMap",
        unsafe=True,
    ),
    _spec(
        "map.restore_map",
        CommandFamily.MAP_ENVIRONMENT,
        CommandRisk.PERSISTENT,
        MapEnvironmentCommand,
        "PersistenceCommand.RestoreMap",
        unsafe=True,
    ),
    _spec(
        "map.upload_map_for_debug",
        CommandFamily.MAP_ENVIRONMENT,
        CommandRisk.SENSITIVE,
        MapEnvironmentCommand,
        "PersistenceCommand.UploadMapForDebug",
        unsafe=True,
    ),
    _spec(
        "map.clear_rgb_weights",
        CommandFamily.MAP_ENVIRONMENT,
        CommandRisk.DESTRUCTIVE,
        MapEnvironmentCommand,
        "ClearRgbWeightsCommand",
        unsafe=True,
    ),
    _spec(
        "wifi.scan",
        CommandFamily.WIFI,
        CommandRisk.SENSITIVE,
        WifiCommand,
        "WifiScanCommand",
        unsafe=True,
    ),
    _spec(
        "wifi.connect",
        CommandFamily.WIFI,
        CommandRisk.PERSISTENT,
        WifiCommand,
        "WifiUpdateCommand.Connect",
        unsafe=True,
    ),
    _spec(
        "wifi.forget",
        CommandFamily.WIFI,
        CommandRisk.DESTRUCTIVE,
        WifiCommand,
        "WifiUpdateCommand.Forget",
        unsafe=True,
    ),
    _spec(
        "device.rename",
        CommandFamily.DEVICE,
        CommandRisk.PERSISTENT,
        DeviceCommand,
        "NewBotNameRequest",
        unsafe=True,
    ),
    _spec(
        "device.discoverability",
        CommandFamily.DEVICE,
        CommandRisk.SENSITIVE,
        DeviceCommand,
        "DiscoverableRequest",
        unsafe=True,
    ),
    _spec(
        "device.new_mop_roll",
        CommandFamily.DEVICE,
        CommandRisk.PERSISTENT,
        DeviceCommand,
        "NewMopRollCommand",
        unsafe=True,
    ),
    _spec(
        "device.clear_calibration",
        CommandFamily.DEVICE,
        CommandRisk.DESTRUCTIVE,
        DeviceCommand,
        "Calibration clear command",
        unsafe=True,
    ),
    _spec(
        "device.configure_shipping",
        CommandFamily.DEVICE,
        CommandRisk.DESTRUCTIVE,
        DeviceCommand,
        "ConfigureShippingCommand",
        unsafe=True,
    ),
    _spec(
        "settings.child_lock",
        CommandFamily.SETTINGS,
        CommandRisk.PERSISTENT,
        SettingsCommand,
        "ChildLockEnableCommand",
        unsafe=True,
    ),
    _spec(
        "settings.pet_waste_avoidance",
        CommandFamily.SETTINGS,
        CommandRisk.PERSISTENT,
        SettingsCommand,
        "PetWasteEnableCommand",
        unsafe=True,
    ),
    _spec(
        "settings.voice",
        CommandFamily.SETTINGS,
        CommandRisk.PERSISTENT,
        SettingsCommand,
        "VoiceEnableCommand",
        unsafe=True,
    ),
    _spec(
        "settings.auto_record_voice",
        CommandFamily.SETTINGS,
        CommandRisk.SENSITIVE,
        SettingsCommand,
        "AutoRecordVoiceEnableCommand",
        unsafe=True,
    ),
    _spec(
        "settings.matter_pairing",
        CommandFamily.SETTINGS,
        CommandRisk.SENSITIVE,
        SettingsCommand,
        "MatterPairingEnableCommand",
        unsafe=True,
    ),
    _spec(
        "settings.preview_release",
        CommandFamily.SETTINGS,
        CommandRisk.PERSISTENT,
        SettingsCommand,
        "UserRequestedPreview command",
        unsafe=True,
    ),
    _spec(
        "settings.jukebox",
        CommandFamily.SETTINGS,
        CommandRisk.PERSISTENT,
        SettingsCommand,
        "JukeboxState",
        unsafe=True,
    ),
    _spec(
        "schedule.add_or_modify",
        CommandFamily.SCHEDULES,
        CommandRisk.PERSISTENT,
        ScheduleCommand,
        "EditScheduleCommand.AddOrModify",
        unsafe=True,
    ),
    _spec(
        "schedule.remove",
        CommandFamily.SCHEDULES,
        CommandRisk.DESTRUCTIVE,
        ScheduleCommand,
        "EditScheduleCommand.Remove",
        unsafe=True,
    ),
    _spec(
        "schedule.toggle",
        CommandFamily.SCHEDULES,
        CommandRisk.PERSISTENT,
        ScheduleCommand,
        "EditScheduleCommand.Toggle",
        unsafe=True,
    ),
    _spec(
        "schedule.generate_suggested",
        CommandFamily.SCHEDULES,
        CommandRisk.PERSISTENT,
        ScheduleCommand,
        "GenerateSuggestedScheduleCommand",
        unsafe=True,
    ),
    _spec(
        "schedule.sink_summon_add_or_modify",
        CommandFamily.SCHEDULES,
        CommandRisk.PERSISTENT,
        ScheduleCommand,
        "EditSinkSummonScheduleCommand.AddOrModify",
        unsafe=True,
    ),
    _spec(
        "schedule.sink_summon_remove",
        CommandFamily.SCHEDULES,
        CommandRisk.DESTRUCTIVE,
        ScheduleCommand,
        "EditSinkSummonScheduleCommand.Remove",
        unsafe=True,
    ),
    _spec(
        "media.recording_enable",
        CommandFamily.MEDIA,
        CommandRisk.SENSITIVE,
        MediaCommand,
        "RecordingCommand.Enabled",
        unsafe=True,
    ),
    _spec(
        "media.rolling_buffer_config",
        CommandFamily.MEDIA,
        CommandRisk.SENSITIVE,
        MediaCommand,
        "RollingRecordingConfigKind",
        unsafe=True,
    ),
    _spec(
        "media.flush_rolling_buffer",
        CommandFamily.MEDIA,
        CommandRisk.SENSITIVE,
        MediaCommand,
        "RecordingCommand.FlushRollingBuffer",
        unsafe=True,
    ),
    _spec(
        "media.confirm_save",
        CommandFamily.MEDIA,
        CommandRisk.SENSITIVE,
        MediaCommand,
        "ConfirmRecordingCommand save",
        unsafe=True,
    ),
    _spec(
        "media.confirm_delete",
        CommandFamily.MEDIA,
        CommandRisk.DESTRUCTIVE,
        MediaCommand,
        "ConfirmRecordingCommand delete",
        unsafe=True,
    ),
    _spec(
        "telemetry.uploader_config",
        CommandFamily.TELEMETRY,
        CommandRisk.SENSITIVE,
        TelemetryCommand,
        "UploaderConfigCommand",
        unsafe=True,
    ),
    _spec(
        "telemetry.support_ssh_permission",
        CommandFamily.TELEMETRY,
        CommandRisk.SENSITIVE,
        TelemetryCommand,
        "UserTunnelSshPermissionCommand",
        unsafe=True,
    ),
    _spec(
        "telemetry.push_notification_subscription",
        CommandFamily.TELEMETRY,
        CommandRisk.SENSITIVE,
        TelemetryCommand,
        "PushNotificationSubscriptionCommand",
        unsafe=True,
    ),
    _spec(
        "lifecycle.update",
        CommandFamily.LIFECYCLE,
        CommandRisk.DESTRUCTIVE,
        LifecycleCommand,
        "Update bot command",
        unsafe=True,
    ),
    _spec(
        "lifecycle.reboot",
        CommandFamily.LIFECYCLE,
        CommandRisk.DESTRUCTIVE,
        LifecycleCommand,
        "Reboot command",
        unsafe=True,
    ),
    _spec(
        "lifecycle.shutdown",
        CommandFamily.LIFECYCLE,
        CommandRisk.DESTRUCTIVE,
        LifecycleCommand,
        "ShutdownCommand",
        unsafe=True,
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
        unverified_codecs = {
            key
            for key in codec_map
            if by_key[key].evidence_level is not CodecEvidenceLevel.WIRE_VERIFIED
        }
        if unverified_codecs:
            raise ValueError(
                f"codecs require WIRE_VERIFIED evidence: {sorted(unverified_codecs)}"
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
        if not encoded.hermes_target:
            raise ValueError("command codec produced an empty hermes target")
        return encoded


COMMAND_REGISTRY = CommandRegistry()


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
