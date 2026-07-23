"""Typed command intents and command-result models.

These classes describe what a caller wants the robot to do.  They are not a
claim that the corresponding protobuf wire format has been recovered.  The
protocol registry owns that evidence boundary and refuses to encode intents
whose wire schema is still unknown.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import ClassVar
from uuid import UUID, uuid4


def utc_now() -> datetime:
    """Return an aware UTC timestamp."""

    return datetime.now(UTC)


class CommandFamily(StrEnum):
    """High-level groups recovered from the official client bindings."""

    USER = "user"
    NAVIGATION = "navigation"
    COVERAGE = "coverage"
    CLEANING = "cleaning"
    MAP_ENVIRONMENT = "map_environment"
    WIFI = "wifi"
    DEVICE = "device"
    SETTINGS = "settings"
    SCHEDULES = "schedules"
    MEDIA = "media"
    TELEMETRY = "telemetry"
    RAW_MOTORS = "raw_motors"
    LIFECYCLE = "lifecycle"


class CommandRisk(StrEnum):
    """Safety classification used by the command guard."""

    STATIONARY = "stationary"
    MOTION = "motion"
    PERSISTENT = "persistent"
    SENSITIVE = "sensitive"
    RAW_ACTUATION = "raw_actuation"
    DESTRUCTIVE = "destructive"


@dataclass(frozen=True, slots=True)
class ControlCommand:
    """Base class for a typed, pre-wire command intent."""

    command_prefix: ClassVar[str]

    @property
    def command_key(self) -> str:
        """Return the stable registry key for this command."""

        raise NotImplementedError


class UserAction(StrEnum):
    STOP = "stop"
    STAY_PUT = "stay_put"
    PAUSE = "pause"
    RESUME = "resume"
    DOCK = "dock"
    EXPLORE = "explore"
    RE_EXPLORE = "re_explore"
    REDO_COVERAGE = "redo_coverage"
    RESUME_COVERAGE = "resume_coverage"
    TRACE_CALIBRATION = "trace_calibration"


@dataclass(frozen=True, slots=True)
class UserCommand(ControlCommand):
    """Simple variants of the recovered ``UserCommand`` sum type.

    Optional values are action-specific and codecs reject values that do not
    belong to the selected variant.  Keeping them separate prevents a generic
    bag of arguments from silently producing a different protobuf command.
    """

    action: UserAction
    until_localized: bool | None = None
    mission_id: int | None = None
    coverage_session_id: UUID | None = None

    command_prefix: ClassVar[str] = "user"

    @property
    def command_key(self) -> str:
        return f"{self.command_prefix}.{self.action.value}"


@dataclass(frozen=True, slots=True)
class JoystickCommand(ControlCommand):
    """Robot-relative velocity intent.

    The exact protobuf fields and containing command envelope are known, but
    direct execution remains blocked. Use :class:`matic_sdk.safety.TeleopSession`
    so a future live-control path cannot bypass its dead-man watchdog.
    """

    linear_mps: float
    angular_rad_s: float

    command_prefix: ClassVar[str] = "user"

    @property
    def command_key(self) -> str:
        return f"{self.command_prefix}.joystick"


class NavigationMode(StrEnum):
    NAVIGATE = "navigate"
    NAVIGATE_AND_WAIT = "navigate_and_wait"
    NAVIGATE_AND_EXPLORE = "navigate_and_explore"


@dataclass(frozen=True, slots=True)
class MissionPosture:
    """A high-level mission-relative 2D destination."""

    mission_id: int
    x_meters: float
    y_meters: float
    yaw_radians: float


@dataclass(frozen=True, slots=True)
class NavigationCommand(ControlCommand):
    mode: NavigationMode
    destination: MissionPosture

    command_prefix: ClassVar[str] = "navigation"

    @property
    def command_key(self) -> str:
        return f"{self.command_prefix}.{self.mode.value}"


class CoverageAction(StrEnum):
    NORMAL = "normal"
    REPRIORITIZE = "reprioritize"
    STAIN_MODE = "stain_mode"


@dataclass(frozen=True, slots=True)
class CoverageCommand(ControlCommand):
    """Coverage intent; fields are SDK-level inputs, not wire fields."""

    action: CoverageAction
    mission_id: int | None = None
    region_ids: tuple[str, ...] = ()
    options: Mapping[str, object] = field(default_factory=dict, repr=False)

    command_prefix: ClassVar[str] = "coverage"

    @property
    def command_key(self) -> str:
        return f"{self.command_prefix}.{self.action.value}"


class CleaningAction(StrEnum):
    MANUAL = "manual"


class ExplicitFloorCleaningMode(StrEnum):
    """Exact manual-clean modes exposed by the Android binding."""

    SWEEPING_CARPET = "sweeping_carpet"
    SWEEPING_HARDFLOOR = "sweeping_hardfloor"
    MOPPING_HARDFLOOR = "mopping_hardfloor"
    SWEEPING_TRANSITION = "sweeping_transition"


class CleaningIntensity(StrEnum):
    """Exact manual-clean intensity variants exposed by the binding."""

    BASE = "base"
    MAX = "max"


@dataclass(frozen=True, slots=True)
class CleaningCommand(ControlCommand):
    mode: ExplicitFloorCleaningMode
    intensity: CleaningIntensity
    action: CleaningAction = CleaningAction.MANUAL

    command_prefix: ClassVar[str] = "cleaning"

    @property
    def command_key(self) -> str:
        return f"{self.command_prefix}.{self.action.value}"


@dataclass(frozen=True, slots=True)
class RawMotorCommand(ControlCommand):
    """Direct cleaning-mechanism setpoints; always requires unsafe arming."""

    vacuum_rpm: float | None = None
    sweeper_duty: float | None = None
    mopper_duty: float | None = None
    head_position: float | None = None
    side_brush_duty: float | None = None

    command_prefix: ClassVar[str] = "raw_motors"

    @property
    def command_key(self) -> str:
        return f"{self.command_prefix}.setpoints"


class MapEnvironmentAction(StrEnum):
    BUILD_PARTITION = "build_partition"
    EDIT_ROOMS = "edit_rooms"
    EDIT_NO_GO_ZONE = "edit_no_go_zone"
    EDIT_DRIVE_ONLY_ZONE = "edit_drive_only_zone"
    EDIT_STAIRS = "edit_stairs"
    EDIT_SEMANTICS_OVERRIDE = "edit_semantics_override"
    EDIT_SINK_SUMMON_LOCATION = "edit_sink_summon_location"
    CANONICALIZE = "canonicalize"
    RENAME = "rename"
    PERSISTENCE_CLEAR = "persistence_clear"
    CLEAR_MAP = "clear_map"
    RESTORE_MAP = "restore_map"
    UPLOAD_MAP_FOR_DEBUG = "upload_map_for_debug"
    CLEAR_RGB_WEIGHTS = "clear_rgb_weights"


@dataclass(frozen=True, slots=True)
class MapEnvironmentCommand(ControlCommand):
    action: MapEnvironmentAction
    mission_id: int | None = None
    change_set: Mapping[str, object] = field(default_factory=dict, repr=False)

    command_prefix: ClassVar[str] = "map"

    @property
    def command_key(self) -> str:
        return f"{self.command_prefix}.{self.action.value}"


class WifiAction(StrEnum):
    SCAN = "scan"
    CONNECT = "connect"
    FORGET = "forget"


@dataclass(frozen=True, slots=True)
class WifiCommand(ControlCommand):
    action: WifiAction
    ssid: str | None = field(default=None, repr=False)
    passphrase: str | None = field(default=None, repr=False)

    command_prefix: ClassVar[str] = "wifi"

    @property
    def command_key(self) -> str:
        return f"{self.command_prefix}.{self.action.value}"


class DeviceAction(StrEnum):
    RENAME = "rename"
    DISCOVERABILITY = "discoverability"
    NEW_MOP_ROLL = "new_mop_roll"
    CLEAR_CALIBRATION = "clear_calibration"
    CONFIGURE_SHIPPING = "configure_shipping"


@dataclass(frozen=True, slots=True)
class DeviceCommand(ControlCommand):
    action: DeviceAction
    new_name: str | None = None
    enabled: bool | None = None
    discoverable_seconds: int | None = None
    retain_user_data: bool | None = None

    command_prefix: ClassVar[str] = "device"

    @property
    def command_key(self) -> str:
        return f"{self.command_prefix}.{self.action.value}"


class SettingAction(StrEnum):
    CHILD_LOCK = "child_lock"
    PET_WASTE_AVOIDANCE = "pet_waste_avoidance"
    VOICE = "voice"
    AUTO_RECORD_VOICE = "auto_record_voice"
    MATTER_PAIRING = "matter_pairing"
    PREVIEW_RELEASE = "preview_release"
    JUKEBOX = "jukebox"


class JukeboxTrack(StrEnum):
    OH_HANUKKAH = "oh_hanukkah"
    DECK_THE_HALLS = "deck_the_halls"
    JINGLE_BELLS = "jingle_bells"


@dataclass(frozen=True, slots=True)
class SettingsCommand(ControlCommand):
    action: SettingAction
    value: bool | JukeboxTrack | None

    command_prefix: ClassVar[str] = "settings"

    @property
    def command_key(self) -> str:
        return f"{self.command_prefix}.{self.action.value}"


class ScheduleAction(StrEnum):
    ADD_OR_MODIFY = "add_or_modify"
    REMOVE = "remove"
    TOGGLE = "toggle"
    GENERATE_SUGGESTED = "generate_suggested"
    SINK_SUMMON_ADD_OR_MODIFY = "sink_summon_add_or_modify"
    SINK_SUMMON_REMOVE = "sink_summon_remove"


@dataclass(frozen=True, slots=True)
class ScheduleEventKey:
    """Native schedule key: a mission u32 plus an event UUID."""

    mission_id: int
    event_id: str


@dataclass(frozen=True, slots=True)
class ScheduleCommand(ControlCommand):
    action: ScheduleAction
    key: ScheduleEventKey | None = None
    definition: Mapping[str, object] = field(default_factory=dict, repr=False)

    command_prefix: ClassVar[str] = "schedule"

    @property
    def command_key(self) -> str:
        return f"{self.command_prefix}.{self.action.value}"


class MediaAction(StrEnum):
    RECORDING_ENABLE = "recording_enable"
    ROLLING_BUFFER_CONFIG = "rolling_buffer_config"
    FLUSH_ROLLING_BUFFER = "flush_rolling_buffer"
    CONFIRM_SAVE = "confirm_save"
    CONFIRM_DELETE = "confirm_delete"


@dataclass(frozen=True, slots=True)
class MediaCommand(ControlCommand):
    action: MediaAction
    recording_id: int | None = None
    enabled: bool | None = None
    confirm_for_each: bool | None = None

    command_prefix: ClassVar[str] = "media"

    @property
    def command_key(self) -> str:
        return f"{self.command_prefix}.{self.action.value}"


class TelemetryAction(StrEnum):
    UPLOADER_CONFIG = "uploader_config"
    SUPPORT_SSH_PERMISSION = "support_ssh_permission"
    PUSH_NOTIFICATION_SUBSCRIPTION = "push_notification_subscription"


@dataclass(frozen=True, slots=True)
class TelemetryCommand(ControlCommand):
    action: TelemetryAction
    enabled: bool | None = None
    device_id: str | None = field(default=None, repr=False)
    app_bundle: str | None = None

    command_prefix: ClassVar[str] = "telemetry"

    @property
    def command_key(self) -> str:
        return f"{self.command_prefix}.{self.action.value}"


class LifecycleAction(StrEnum):
    UPDATE = "update"
    REBOOT = "reboot"
    SHUTDOWN = "shutdown"


@dataclass(frozen=True, slots=True)
class LifecycleCommand(ControlCommand):
    action: LifecycleAction

    command_prefix: ClassVar[str] = "lifecycle"

    @property
    def command_key(self) -> str:
        return f"{self.command_prefix}.{self.action.value}"


class TransportAckStatus(StrEnum):
    ACKNOWLEDGED = "acknowledged"
    REJECTED = "rejected"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class TransportAcknowledgement:
    """What Hermes reported about delivery, independent of robot state."""

    status: TransportAckStatus
    received_at: datetime = field(default_factory=utc_now)
    code: str | None = None
    detail: str | None = None


class ObservedEffectStatus(StrEnum):
    OBSERVED = "observed"
    NOT_OBSERVED = "not_observed"
    TIMED_OUT = "timed_out"
    NOT_CHECKED = "not_checked"


@dataclass(frozen=True, slots=True)
class ObservedEffect:
    """An independent telemetry observation made after a command send."""

    status: ObservedEffectStatus
    observed_at: datetime = field(default_factory=utc_now)
    collection: str | None = None
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class CommandReceipt:
    """Command outcome with delivery and physical effect kept separate."""

    command_key: str
    protocol_version: int
    transport: TransportAcknowledgement
    observed_effect: ObservedEffect | None = None
    command_id: str = field(default_factory=lambda: str(uuid4()))
    issued_at: datetime = field(default_factory=utc_now)

    @property
    def transport_acknowledged(self) -> bool:
        return self.transport.status is TransportAckStatus.ACKNOWLEDGED

    @property
    def effect_observed(self) -> bool:
        return (
            self.observed_effect is not None
            and self.observed_effect.status is ObservedEffectStatus.OBSERVED
        )


__all__ = [
    "CleaningAction",
    "CleaningCommand",
    "CleaningIntensity",
    "CommandFamily",
    "CommandReceipt",
    "CommandRisk",
    "ControlCommand",
    "CoverageAction",
    "CoverageCommand",
    "DeviceAction",
    "DeviceCommand",
    "ExplicitFloorCleaningMode",
    "JoystickCommand",
    "JukeboxTrack",
    "LifecycleAction",
    "LifecycleCommand",
    "MapEnvironmentAction",
    "MapEnvironmentCommand",
    "MediaAction",
    "MediaCommand",
    "MissionPosture",
    "NavigationCommand",
    "NavigationMode",
    "ObservedEffect",
    "ObservedEffectStatus",
    "RawMotorCommand",
    "ScheduleAction",
    "ScheduleCommand",
    "ScheduleEventKey",
    "SettingAction",
    "SettingsCommand",
    "TelemetryAction",
    "TelemetryCommand",
    "TransportAckStatus",
    "TransportAcknowledgement",
    "UserAction",
    "UserCommand",
    "WifiAction",
    "WifiCommand",
]
