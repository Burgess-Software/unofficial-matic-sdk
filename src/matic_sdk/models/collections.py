"""Friendly, lossless models for Hermes collection values.

The public models expose fields whose meaning is supported by the official
client bindings and captured wire data. Every model also retains the parsed
protobuf fields and original payload so newer firmware fields remain available
without being guessed or discarded.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, TypeAlias
from uuid import UUID

from matic_sdk.models.control import AudioRecordingMode, JukeboxTrack
from matic_sdk.protocol.collections import CollectionOperation
from matic_sdk.protocol.wire import WireField

if TYPE_CHECKING:
    from matic_sdk.coverage import DecodedCoveragePlan
    from matic_sdk.models.maps import MapTile


@dataclass(frozen=True, slots=True, kw_only=True)
class CollectionModel:
    """Common metadata retained by every friendly collection model."""

    target: str
    operation: CollectionOperation
    raw_payload: bytes = field(repr=False)
    fields: tuple[WireField, ...] = field(default=(), repr=False)

    @property
    def deleted(self) -> bool:
        """Whether this event removes the item identified by its Hermes key."""

        return self.operation is CollectionOperation.DELETE


@dataclass(frozen=True, slots=True, kw_only=True)
class StructuredCollectionModel(CollectionModel):
    """Lossless fallback for an accepted target or a newly introduced target."""

    schema_name: str


@dataclass(frozen=True, slots=True)
class Vector2:
    x: float
    y: float


@dataclass(frozen=True, slots=True)
class Vector3:
    x: float
    y: float
    z: float


@dataclass(frozen=True, slots=True)
class Quaternion:
    x: float
    y: float
    z: float
    w: float


@dataclass(frozen=True, slots=True)
class Pose:
    translation: Vector3
    rotation: Quaternion


@dataclass(frozen=True, slots=True, kw_only=True)
class MapTileCollectionModel(CollectionModel):
    """One map tile add/delete with lossless typed layers when Pillow is installed."""

    mission_id: int | None
    page_x: int | None
    page_y: int | None
    layers: tuple[str, ...] = ()
    tiles: tuple[MapTile, ...] = field(default=(), repr=False)
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class PoseCollectionModel(CollectionModel):
    """Latest robot pose in one mission coordinate frame."""

    mission_id: int | None
    pose: Pose | None
    monotonic_time_ns: int | None = None
    observed_at: datetime | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class DockDetectionCollectionModel(CollectionModel):
    """One retained dock pose hypothesis."""

    mission_id: int | None
    dock_id: UUID | None
    pose: Pose | None
    detection_method: str | None
    detected_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class LabeledMission:
    mission_id: int
    floor_label: str | None


@dataclass(frozen=True, slots=True, kw_only=True)
class MissionCollectionModel(CollectionModel):
    """Active and displayed map/mission selection."""

    active_mission_id: int | None
    displayed_mission_id: int | None
    displayed_mission_explored: bool
    labeled_missions: tuple[LabeledMission, ...] = field(default=(), repr=False)


@dataclass(frozen=True, slots=True, kw_only=True)
class ActiveSessionCollectionModel(CollectionModel):
    """Current coverage-session identity, or an idle state."""

    mission_id: int | None
    session_id: UUID | None

    @property
    def active(self) -> bool:
        return self.mission_id is not None and self.session_id is not None


@dataclass(frozen=True, slots=True, kw_only=True)
class ZoneCollectionModel(CollectionModel):
    """One no-go, stair, or drive-only region."""

    mission_id: int | None
    zone_id: int | UUID | None
    zone_class: str | None
    outer_border: tuple[Vector2, ...] = field(default=(), repr=False)
    holes: tuple[tuple[Vector2, ...], ...] = field(default=(), repr=False)


@dataclass(frozen=True, slots=True, kw_only=True)
class CoveragePlanCollectionModel(CollectionModel):
    """Summary plus the strict command-compatible active coverage plan."""

    mission_id: int | None
    goal_count: int
    has_current_candidate: bool
    current_region_id: UUID | None
    actionable_plan: DecodedCoveragePlan | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True, kw_only=True)
class SinkSummonLocationCollectionModel(CollectionModel):
    """Mission-relative position where the robot can be summoned for service."""

    mission_id: int | None
    location: Vector2 | None
    heading_radians: float | None


class SessionCompletionKind(StrEnum):
    """App-facing classification of how much of a cleaning session ran."""

    COMPLETED = "completed"
    INCOMPLETE = "incomplete"
    NOT_STARTED = "not_started"
    UNSPECIFIED = "unspecified"


class SessionStopReason(StrEnum):
    """Reasons exposed by the app for a historical cleaning-session stop."""

    BOT_CANCELLED = "bot_cancelled"
    FINISHED = "finished"
    LOST = "lost"
    MISSION_DELETED = "mission_deleted"
    USER_CANCELLED = "user_cancelled"
    USER_SKIPPED = "user_skipped"
    REPLACED_BY_SCHEDULE = "replaced_by_schedule"
    WRONG_CANONICAL_MISSION = "wrong_canonical_mission"
    PROLONGED_PAUSE = "prolonged_pause"
    STALE = "stale"
    BOT_SKIPPED = "bot_skipped"
    PARTITION_DELETED = "partition_deleted"


class StopReasonKind(StrEnum):
    """Presentation category returned by app 1.175 for a stop reason."""

    INCOMPLETE = "incomplete"
    STOPPED_INTENTIONALLY = "stopped_intentionally"
    STOPPED_UNEXPECTEDLY = "stopped_unexpectedly"


@dataclass(frozen=True, slots=True)
class CoverageSessionOutcome:
    """Outcome details available for one historical cleaning session."""

    completion_kind: SessionCompletionKind | str | None
    stop_reason: SessionStopReason | str | None


@dataclass(frozen=True, slots=True, kw_only=True)
class CoverageHistoryCollectionModel(CollectionModel):
    """One historical coverage-session summary."""

    session_id: UUID | None
    mission_id: int | None
    started_at: datetime | None
    ended_at: datetime | None
    resumable: bool
    outcome: CoverageSessionOutcome | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class RecapCollectionModel(CollectionModel):
    """Monthly cleaning recap."""

    month: int | None
    year: int | None
    sweep_square_feet: float
    mop_square_feet: float
    cleaning_sessions: int
    favorite_room: str | None = field(default=None, repr=False)
    sweep_time: timedelta | None = None
    mop_time: timedelta | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class PathCollectionModel(CollectionModel):
    """Mission-relative path or approximate trajectory."""

    mission_id: int | None
    points: tuple[Vector2, ...] = field(default=(), repr=False)


@dataclass(frozen=True, slots=True, kw_only=True)
class CoverageLineCollectionModel(CollectionModel):
    """Current coverage corridor or marker line."""

    mission_id: int | None
    start: Vector2 | None
    end: Vector2 | None


@dataclass(frozen=True, slots=True)
class FlythroughPose:
    camera_location: Vector3
    camera_target: Vector3


@dataclass(frozen=True, slots=True, kw_only=True)
class FlythroughCollectionModel(CollectionModel):
    """Camera poses used by the app's retained map flythrough."""

    mission_id: int | None
    poses: tuple[FlythroughPose, ...] = field(default=(), repr=False)


@dataclass(frozen=True, slots=True, kw_only=True)
class WifiStatusCollectionModel(CollectionModel):
    """Current Wi-Fi state and scan metadata."""

    state: str
    current_network_ssid: str | None = field(default=None, repr=False)
    ip_address: str | None = field(default=None, repr=False)
    known_network_count: int | None = None
    other_network_count: int | None = None


@dataclass(frozen=True, slots=True)
class MotorReading:
    voltage: float
    current: float
    rpm: float


@dataclass(frozen=True, slots=True, kw_only=True)
class MotorStatusCollectionModel(CollectionModel):
    """Electrical/mechanical readings for the six app-facing motors."""

    drive_left: MotorReading
    drive_right: MotorReading
    vacuum: MotorReading
    sweeper: MotorReading
    mopper: MotorReading
    brush: MotorReading


class CuesVoiceStatus(StrEnum):
    """App-facing Matic Cues voice pipeline state."""

    DISABLED = "disabled"
    LISTENING_FOR_WAKE_WORD = "listening_for_wake_word"
    LISTENING_FOR_INTENT = "listening_for_intent"
    THINKING_FOR_INTENT = "thinking_for_intent"
    CLASSIFIED = "classified"
    REJECTED = "rejected"


class CuesGestureStatus(StrEnum):
    """App-facing pointing and follow-person workflow state."""

    AWAITING_POINTED_TARGET = "awaiting_pointed_target"
    POINTED_TARGET_ACCEPTED = "pointed_target_accepted"
    NO_TARGET_FOUND = "no_target_found"
    FACING_USER = "facing_user"
    PERSON_NOT_FOUND = "person_not_found"
    FOLLOWING = "following"
    FOLLOWING_PAUSED = "following_paused"
    MOVING_CLOSER = "moving_closer"
    MOVING_BACK = "moving_back"
    # Retained for callers decoding Stable 172 data; removed from app 1.175.
    REPOSITIONING = "repositioning"
    REAWAITING_POINTED_TARGET = "reawaiting_pointed_target"
    AWAITING_STEP_BACK = "awaiting_step_back"


class CuesIntentCategory(StrEnum):
    """Top-level category returned by the Cues intent classifier."""

    TASK = "task"
    GESTURE = "gesture"
    UNKNOWN = "unknown"
    RECORDING = "recording"


class CuesTaskIntent(StrEnum):
    """Task intent names exposed by Stable 172's native client binding."""

    CLEAN = "clean"
    CLEAN_ALL = "clean_all"
    DOCK = "dock"
    GO_AWAY = "go_away"
    NAVIGATE = "navigate"
    PAUSE = "pause"
    REDO_LAST_CLEAN = "redo_last_clean"
    RESUME = "resume"
    SINK_SUMMON = "sink_summon"
    STOP = "stop"


class CuesGestureIntent(StrEnum):
    """Gesture intent names exposed by Stable 172's native client binding."""

    FOLLOW_PERSON = "follow_person"
    POINT_TO_CLEAN = "point_to_clean"


class CuesPointToCleanIntentKind(StrEnum):
    """Point-to-clean classification added by app 1.175."""

    GENERAL = "general"
    STAIN = "stain"


class CuesRecordingIntent(StrEnum):
    """Diagnostic recording intents exposed by the Cues classifier."""

    ROLLING_RECORDING = "rolling_recording"
    AMBIENT_AUDIO = "ambient_audio"
    RECORD_DOA = "record_doa"


class BrushRollJamOutcome(StrEnum):
    """Result exposed by the app's brush-roll diagnostic workflow."""

    NOT_JAMMED = "not_jammed"
    STILL_JAMMED = "still_jammed"
    FAILED_TO_RUN = "failed_to_run"
    INSUFFICIENT_CHARGING = "insufficient_charging"


class RollingRecordingReason(StrEnum):
    """Reasons exposed by app 1.175 for retaining a rolling recording."""

    BOT_LOST = "bot_lost"
    BRUSH_ROLL_JAMMED = "brush_roll_jammed"
    BRUSH_ROLL_MISSING = "brush_roll_missing"
    CLOTH_DETECTED = "cloth_detected"
    CRITICAL_STUCK = "critical_stuck"
    DOA_FAILED = "doa_failed"
    DOCKING_ANOMALY = "docking_anomaly"
    DOCKING_FAILED = "docking_failed"
    DUCT_CLOGGED = "duct_clogged"
    IMU_BIAS_HIGH = "imu_bias_high"
    INCORRECT_SEMANTICS = "incorrect_semantics"
    INCORRECT_TOEKICK = "incorrect_toekick"
    LOW_CHARGE_DOCKING_FAILED = "low_charge_docking_failed"
    MIRROR_DETECTED = "mirror_detected"
    MOPPING_START = "mopping_start"
    MOPPING_WHEEL_SLIP = "mopping_wheel_slip"
    MOP_ROLL_JAMMED = "mop_roll_jammed"
    MOP_ROLL_MISSING = "mop_roll_missing"
    NETWORK_COMMAND = "network_command"
    NEW_DOCK_DETECTION = "new_dock_detection"
    PERIMETER_ANOMALY = "perimeter_anomaly"
    PETWASTE_DETECTED = "petwaste_detected"
    POINT_TO_CLEAN = "point_to_clean"
    STALE_DOCK_REMOVAL = "stale_dock_removal"
    SWEEPER_DISLODGED = "sweeper_dislodged"
    TILTED = "tilted"
    VACUUMING_START = "vacuuming_start"
    VENT_DETECTED = "vent_detected"
    VOICE_COMMAND = "voice_command"


@dataclass(frozen=True, slots=True, kw_only=True)
class RobotStatusCollectionModel(CollectionModel):
    """Main robot state, errors, battery, and commonly useful activity flags."""

    state_codes: tuple[int, ...]
    error_codes: tuple[int, ...]
    activity: str
    battery_percentage: int | None
    name: str | None = field(default=None, repr=False)
    is_paused: bool = False
    is_charging: bool = False
    is_navigating: bool = False
    is_cleaning: bool = False
    is_recording: bool = False
    is_following_person: bool | None = None
    voice_status: CuesVoiceStatus | str | None = None
    gesture_status: CuesGestureStatus | str | None = None
    voice_intent_category: CuesIntentCategory | str | None = None
    voice_intent: (
        CuesTaskIntent | CuesGestureIntent | CuesRecordingIntent | str | None
    ) = None
    point_to_clean_intent_kind: CuesPointToCleanIntentKind | str | None = None
    brush_roll_jam_outcome: BrushRollJamOutcome | str | None = None
    time_until_idle_dock: timedelta | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class VersionCollectionModel(CollectionModel):
    """Robot software/profile and Hermes command protocol version."""

    version_name: str | None
    profile_name: str | None
    protocol_version: int | None


@dataclass(frozen=True, slots=True, kw_only=True)
class CoverageTimeCollectionModel(CollectionModel):
    """Current coverage timing and progress, or an idle state."""

    session_id: UUID | None
    elapsed: timedelta | None
    remaining: timedelta | None
    progress_percentage: float | None


@dataclass(frozen=True, slots=True, kw_only=True)
class UpdateStateCollectionModel(CollectionModel):
    """Updater state and optional progress."""

    state: str
    progress_percentage: float | None = None
    total_gigabytes: float | None = None
    release_name: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class BinarySettingCollectionModel(CollectionModel):
    """One boolean robot preference."""

    setting: str
    enabled: bool | None


@dataclass(frozen=True, slots=True, kw_only=True)
class AudioRecordingStateCollectionModel(CollectionModel):
    """Current Stable 172 audio-processing mode; no audio samples are exposed."""

    mode: AudioRecordingMode | str | None

    @property
    def recording(self) -> bool:
        return self.mode is not None


@dataclass(frozen=True, slots=True, kw_only=True)
class DeepMopOverrideCollectionModel(CollectionModel):
    """Whether the retained deep-mop override is enabled."""

    enabled: bool | None


@dataclass(frozen=True, slots=True, kw_only=True)
class WaterFlowOverrideCollectionModel(CollectionModel):
    """Retained water-flow multiplier from the app-supported 0.5x-2.0x range."""

    factor: float | None


@dataclass(frozen=True, slots=True, kw_only=True)
class TimeZoneCollectionModel(CollectionModel):
    """Robot time-zone identifier and current UTC offset."""

    time_zone: str | None
    utc_offset: timedelta | None


@dataclass(frozen=True, slots=True, kw_only=True)
class BagPassCollectionModel(CollectionModel):
    """Bag-pass ownership interval recovered from Stable 172 native parsing."""

    owned: bool
    started_at: datetime | None
    expires_at: datetime | None


@dataclass(frozen=True, slots=True, kw_only=True)
class MatterPairingCollectionModel(CollectionModel):
    """Matter pairing state; pairing codes are intentionally hidden from repr."""

    enabled: bool
    qr_code: str | None = field(default=None, repr=False)
    manual_code: str | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True, kw_only=True)
class RollingRecordingCollectionModel(CollectionModel):
    """Rolling-recording policy."""

    enabled: bool
    confirm_for_each: bool | None


@dataclass(frozen=True, slots=True, kw_only=True)
class UploaderConfigCollectionModel(CollectionModel):
    """Telemetry uploader enrollment and customer choice."""

    customer_baseline: bool
    opted_in: bool | None


@dataclass(frozen=True, slots=True, kw_only=True)
class SshPermissionCollectionModel(CollectionModel):
    """Customer-granted support tunnel permission."""

    enabled: bool | None


@dataclass(frozen=True, slots=True, kw_only=True)
class ScheduleEventCollectionModel(CollectionModel):
    """One regular cleaning schedule."""

    mission_id: int | None
    event_id: UUID | int | None
    name: str | None = field(default=None, repr=False)
    weekdays: tuple[str, ...] = ()
    minutes_after_midnight: int | None = None
    enabled: bool | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class SinkSummonScheduleCollectionModel(CollectionModel):
    """One scheduled sink-summon visit."""

    mission_id: int | None
    event_id: UUID | int | None
    weekdays: tuple[str, ...] = ()
    minutes_after_midnight: int | None = None
    duration: timedelta | None = None
    enabled: bool | None = None


@dataclass(frozen=True, slots=True)
class MediaAsset:
    """Retained image or video bytes with safe display metadata."""

    media_type: str
    data: bytes = field(repr=False)
    width: int | None = None
    height: int | None = None
    sha256: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class MediaCollectionModel(CollectionModel):
    """Retained image/video collection item."""

    item_id: UUID | int | None
    assets: tuple[MediaAsset, ...] = field(default=(), repr=False)


@dataclass(frozen=True, slots=True, kw_only=True)
class RecordingsCollectionModel(CollectionModel):
    """Scratch/saved recording list."""

    recording_count: int


@dataclass(frozen=True, slots=True, kw_only=True)
class CustomerInfoCollectionModel(CollectionModel):
    """App customer metadata."""

    email: str | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True, kw_only=True)
class JukeboxCollectionModel(CollectionModel):
    """Seasonal jukebox selection."""

    track: JukeboxTrack | str | None


FriendlyCollectionModel: TypeAlias = (
    StructuredCollectionModel
    | AudioRecordingStateCollectionModel
    | BagPassCollectionModel
    | DeepMopOverrideCollectionModel
    | WaterFlowOverrideCollectionModel
    | TimeZoneCollectionModel
    | MapTileCollectionModel
    | PoseCollectionModel
    | DockDetectionCollectionModel
    | MissionCollectionModel
    | ActiveSessionCollectionModel
    | ZoneCollectionModel
    | CoveragePlanCollectionModel
    | SinkSummonLocationCollectionModel
    | CoverageHistoryCollectionModel
    | RecapCollectionModel
    | PathCollectionModel
    | CoverageLineCollectionModel
    | FlythroughCollectionModel
    | WifiStatusCollectionModel
    | MotorStatusCollectionModel
    | RobotStatusCollectionModel
    | VersionCollectionModel
    | CoverageTimeCollectionModel
    | UpdateStateCollectionModel
    | BinarySettingCollectionModel
    | MatterPairingCollectionModel
    | RollingRecordingCollectionModel
    | UploaderConfigCollectionModel
    | SshPermissionCollectionModel
    | ScheduleEventCollectionModel
    | SinkSummonScheduleCollectionModel
    | MediaCollectionModel
    | RecordingsCollectionModel
    | CustomerInfoCollectionModel
    | JukeboxCollectionModel
)


__all__ = [
    "ActiveSessionCollectionModel",
    "AudioRecordingStateCollectionModel",
    "BagPassCollectionModel",
    "BinarySettingCollectionModel",
    "BrushRollJamOutcome",
    "CollectionModel",
    "CoverageHistoryCollectionModel",
    "CoverageLineCollectionModel",
    "CoveragePlanCollectionModel",
    "CoverageSessionOutcome",
    "CoverageTimeCollectionModel",
    "CuesGestureIntent",
    "CuesGestureStatus",
    "CuesIntentCategory",
    "CuesPointToCleanIntentKind",
    "CuesRecordingIntent",
    "CuesTaskIntent",
    "CuesVoiceStatus",
    "CustomerInfoCollectionModel",
    "DeepMopOverrideCollectionModel",
    "DockDetectionCollectionModel",
    "FlythroughCollectionModel",
    "FlythroughPose",
    "FriendlyCollectionModel",
    "JukeboxCollectionModel",
    "LabeledMission",
    "MapTileCollectionModel",
    "MatterPairingCollectionModel",
    "MediaAsset",
    "MediaCollectionModel",
    "MissionCollectionModel",
    "MotorReading",
    "MotorStatusCollectionModel",
    "PathCollectionModel",
    "Pose",
    "PoseCollectionModel",
    "Quaternion",
    "RecapCollectionModel",
    "RecordingsCollectionModel",
    "RobotStatusCollectionModel",
    "RollingRecordingCollectionModel",
    "RollingRecordingReason",
    "ScheduleEventCollectionModel",
    "SessionCompletionKind",
    "SessionStopReason",
    "SinkSummonLocationCollectionModel",
    "SinkSummonScheduleCollectionModel",
    "SshPermissionCollectionModel",
    "StopReasonKind",
    "StructuredCollectionModel",
    "TimeZoneCollectionModel",
    "UpdateStateCollectionModel",
    "UploaderConfigCollectionModel",
    "Vector2",
    "Vector3",
    "VersionCollectionModel",
    "WaterFlowOverrideCollectionModel",
    "WifiStatusCollectionModel",
    "ZoneCollectionModel",
]
