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
from enum import IntEnum, StrEnum
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
    """One robot-relative linear and angular velocity command."""

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
    """A canonical mission-relative 2D destination and heading."""

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


class CoverageCleaningMode(StrEnum):
    """Cleaning mechanisms selected for a normal coverage run."""

    VACUUM = "vacuum"
    MOP = "mop"
    BOTH = "vacuum_and_mop"


class CoverageSetting(StrEnum):
    """Coverage pass density displayed by the official client."""

    QUICK = "quick"
    STANDARD = "standard"


class StainMode(StrEnum):
    """Localized stain-cleaning program selected by the official client."""

    DRY_STAIN = "dry_stain"
    WET_SPILL = "wet_spill"


class CoverageGoalSetting(IntEnum):
    """Exact setting values carried by a serialized coverage goal."""

    DEPRECATED_DEEP = 0
    STANDARD = 1
    QUICK = 2


class CleaningFloor(IntEnum):
    """Floor material selected by one coverage goal."""

    HARD_FLOOR = 0
    CARPET = 1


class CoverageGoalCleaningMode(IntEnum):
    """Cleaning mechanism selected by one coverage goal."""

    VACUUM = 0
    MOP = 1


class CoverageBehavior(IntEnum):
    """Path behavior selected by one coverage goal."""

    INTERIOR = 0
    PERIMETER = 1
    TOEKICK = 2
    TRANSITION = 3


@dataclass(frozen=True, slots=True)
class DrawnCircle:
    """One mission-relative circular stain target."""

    x_meters: float
    y_meters: float
    radius_meters: float


@dataclass(frozen=True, slots=True)
class CoverageGoalSpec:
    """Wire-complete cleaning specification for one retained plan goal."""

    setting: CoverageGoalSetting
    floor: CleaningFloor
    cleaning_mode: CoverageGoalCleaningMode
    behavior: CoverageBehavior


@dataclass(frozen=True, slots=True)
class CoveragePlanGoal:
    """One standard room goal from the robot's current coverage plan."""

    goal_id: UUID
    partition_id: UUID
    region_id: UUID
    spec: CoverageGoalSpec


@dataclass(frozen=True, slots=True)
class CoverageGoals:
    """The robot's current goal sequence and its ordered/unordered marker."""

    goals: tuple[CoveragePlanGoal, ...]
    ordered: bool


class ReprioritizeAction(StrEnum):
    """Safe exact subset of the official reprioritization actions."""

    PRIORITIZE = "prioritize"
    SKIP = "skip"


@dataclass(frozen=True, slots=True)
class CoverageCommand(ControlCommand):
    """Typed normal-coverage or localized stain-cleaning input.

    The action-specific codecs reject fields that do not belong to the selected
    variant so an ignored option cannot silently change the requested job.
    Reprioritization uses a dedicated model because it carries the robot's
    current goal plan and coverage-session identity.
    """

    action: CoverageAction
    mission_id: int
    partition_id: UUID | None = None
    region_ids: tuple[UUID, ...] = ()
    cleaning_mode: CoverageCleaningMode = CoverageCleaningMode.BOTH
    coverage_setting: CoverageSetting = CoverageSetting.STANDARD
    ordered: bool = False
    stain_mode: StainMode | None = None
    circles: tuple[DrawnCircle, ...] = ()

    command_prefix: ClassVar[str] = "coverage"

    @property
    def command_key(self) -> str:
        return f"{self.command_prefix}.{self.action.value}"


@dataclass(frozen=True, slots=True)
class ReprioritizeCoverageCommand(ControlCommand):
    """Reorder or skip the current region without regenerating plan goals."""

    action: ReprioritizeAction
    mission_id: int
    goals: CoverageGoals
    current_region_id: UUID
    current_session_id: UUID
    selected_region_id: UUID | None = None

    command_prefix: ClassVar[str] = "coverage"

    @property
    def command_key(self) -> str:
        return f"{self.command_prefix}.reprioritize"


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
    """Direct cleaning-mechanism setpoints."""

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
class MapPoint:
    """One point in the SDK's mission-relative map coordinate frame."""

    x_meters: float
    y_meters: float


class RoomLabel(StrEnum):
    """Built-in room labels exposed by the official client."""

    BATHROOM = "bathroom"
    BEDROOM = "bedroom"
    DINING_ROOM = "dining_room"
    LIVING_ROOM = "living_room"
    KITCHEN = "kitchen"
    HALLWAY = "hallway"


@dataclass(frozen=True, slots=True)
class RenameRoom:
    """Rename or relabel one mapped room."""

    region_id: UUID
    label: RoomLabel | str


@dataclass(frozen=True, slots=True)
class MergeRooms:
    """Merge two mapped rooms and assign the resulting label."""

    first_region_id: UUID
    second_region_id: UUID
    label: RoomLabel | str


@dataclass(frozen=True, slots=True)
class SplitRoom:
    """Split one mapped room along a mission-relative line."""

    region_id: UUID
    start: MapPoint
    end: MapPoint


@dataclass(frozen=True, slots=True)
class AddZones:
    """Add one or more circular map zones."""

    circles: tuple[DrawnCircle, ...]


@dataclass(frozen=True, slots=True)
class RemoveZones:
    """Remove zones by their native compact region identifiers.

    The official binding exposes these as UUID-shaped values even though the
    command reduces them to a constrained 32-bit region identifier. Supplying
    an integer directly is the least surprising representation for SDK users.
    """

    region_ids: tuple[int | UUID, ...]


class SemanticsOverrideKind(StrEnum):
    """Surface/wire interpretation assigned to a drawn map area."""

    UNSET = "unset"
    HARDFLOOR_ALLOW_WIRE = "hardfloor_allow_wire"
    CARPET_ALLOW_WIRE = "carpet_allow_wire"
    HARDFLOOR_DISALLOW_WIRE = "hardfloor_disallow_wire"
    CARPET_DISALLOW_WIRE = "carpet_disallow_wire"


@dataclass(frozen=True, slots=True)
class SemanticsOverride:
    """Apply one semantic interpretation to circular map areas."""

    circles: tuple[DrawnCircle, ...]
    kind: SemanticsOverrideKind


@dataclass(frozen=True, slots=True)
class SinkSummonLocation:
    """Mission-relative pose where the robot should meet the sink."""

    x_meters: float
    y_meters: float
    yaw_radians: float


@dataclass(frozen=True, slots=True)
class MapEnvironmentCommand(ControlCommand):
    action: MapEnvironmentAction
    mission_id: int | None = None
    change_set: Mapping[str, object] = field(default_factory=dict, repr=False)
    partition_id: UUID | None = None
    change: (
        RenameRoom
        | MergeRooms
        | SplitRoom
        | AddZones
        | RemoveZones
        | SemanticsOverride
        | SinkSummonLocation
        | None
    ) = None
    overwrite: bool | None = None
    name: str | None = None

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


class Weekday(StrEnum):
    SUNDAY = "sunday"
    MONDAY = "monday"
    TUESDAY = "tuesday"
    WEDNESDAY = "wednesday"
    THURSDAY = "thursday"
    FRIDAY = "friday"
    SATURDAY = "saturday"


@dataclass(frozen=True, slots=True)
class ScheduleTime:
    """Local time-of-day plus the timezone snapshot used by the robot."""

    seconds_since_midnight: int
    timezone_id: str
    utc_offset_seconds: int


@dataclass(frozen=True, slots=True)
class StandardScheduleTarget:
    """Mapped rooms selected by a regular cleaning schedule."""

    region_ids: tuple[UUID, ...]


@dataclass(frozen=True, slots=True)
class CustomScheduleTarget:
    """Circular custom area selected by a cleaning schedule."""

    circles: tuple[DrawnCircle, ...]


class ScheduleCoverageSetting(StrEnum):
    """Vacuum pass density supported by stored schedules."""

    QUICK = "quick"
    STANDARD = "standard"
    DEPRECATED_DEEP = "deprecated_deep"


class ScheduleEnabledState(StrEnum):
    """Explicit state carried by a regular schedule event."""

    ENABLED = "enabled"
    DISABLED = "disabled"
    SUGGESTED = "suggested"


@dataclass(frozen=True, slots=True)
class ScheduleEvent:
    """Complete regular cleaning schedule definition."""

    weekdays: tuple[Weekday, ...]
    time: ScheduleTime
    target: StandardScheduleTarget | CustomScheduleTarget
    partition_id: UUID
    cleaning_mode: CoverageCleaningMode
    name: str | None = None
    ordered: bool = False
    vacuum_setting: ScheduleCoverageSetting | None = ScheduleCoverageSetting.STANDARD
    enabled_state: ScheduleEnabledState = ScheduleEnabledState.ENABLED


@dataclass(frozen=True, slots=True)
class ScheduleDuration:
    """Exact protobuf duration used by sink-summon schedules."""

    seconds: int
    nanoseconds: int = 0


@dataclass(frozen=True, slots=True)
class SinkSummonScheduleEvent:
    """Complete sink-summon schedule definition."""

    weekdays: tuple[Weekday, ...]
    time: ScheduleTime
    duration: ScheduleDuration
    enabled: bool = True


@dataclass(frozen=True, slots=True)
class ScheduleEventKey:
    """Native schedule key: a mission u32 plus an event UUID."""

    mission_id: int
    event_id: UUID


@dataclass(frozen=True, slots=True)
class ScheduleCommand(ControlCommand):
    action: ScheduleAction
    key: ScheduleEventKey | None = None
    definition: Mapping[str, object] = field(default_factory=dict, repr=False)
    event: ScheduleEvent | None = None
    sink_event: SinkSummonScheduleEvent | None = None

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
    "AddZones",
    "CleaningAction",
    "CleaningCommand",
    "CleaningFloor",
    "CleaningIntensity",
    "CommandFamily",
    "CommandReceipt",
    "CommandRisk",
    "ControlCommand",
    "CoverageAction",
    "CoverageBehavior",
    "CoverageCleaningMode",
    "CoverageCommand",
    "CoverageGoalCleaningMode",
    "CoverageGoalSetting",
    "CoverageGoalSpec",
    "CoverageGoals",
    "CoveragePlanGoal",
    "CoverageSetting",
    "CustomScheduleTarget",
    "DeviceAction",
    "DeviceCommand",
    "DrawnCircle",
    "ExplicitFloorCleaningMode",
    "JoystickCommand",
    "JukeboxTrack",
    "LifecycleAction",
    "LifecycleCommand",
    "MapEnvironmentAction",
    "MapEnvironmentCommand",
    "MapPoint",
    "MediaAction",
    "MediaCommand",
    "MergeRooms",
    "MissionPosture",
    "NavigationCommand",
    "NavigationMode",
    "ObservedEffect",
    "ObservedEffectStatus",
    "RawMotorCommand",
    "RemoveZones",
    "RenameRoom",
    "ReprioritizeAction",
    "ReprioritizeCoverageCommand",
    "RoomLabel",
    "ScheduleAction",
    "ScheduleCommand",
    "ScheduleCoverageSetting",
    "ScheduleDuration",
    "ScheduleEnabledState",
    "ScheduleEvent",
    "ScheduleEventKey",
    "ScheduleTime",
    "SemanticsOverride",
    "SemanticsOverrideKind",
    "SettingAction",
    "SettingsCommand",
    "SinkSummonLocation",
    "SinkSummonScheduleEvent",
    "SplitRoom",
    "StainMode",
    "StandardScheduleTarget",
    "TelemetryAction",
    "TelemetryCommand",
    "TransportAckStatus",
    "TransportAcknowledgement",
    "UserAction",
    "UserCommand",
    "Weekday",
    "WifiAction",
    "WifiCommand",
]
