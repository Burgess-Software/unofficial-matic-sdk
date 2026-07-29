"""Decode every accepted Hermes collection into a friendly public model."""

from __future__ import annotations

import hashlib
import math
import struct
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from typing import Any
from uuid import UUID

from matic_sdk.coverage import (
    CoverageDecodeError,
    decode_active_session_key,
    decode_coverage_plan,
)
from matic_sdk.media import extract_embedded_webps
from matic_sdk.models.collections import (
    ActiveSessionCollectionModel,
    BinarySettingCollectionModel,
    CoverageHistoryCollectionModel,
    CoverageLineCollectionModel,
    CoveragePlanCollectionModel,
    CoverageTimeCollectionModel,
    CustomerInfoCollectionModel,
    DockDetectionCollectionModel,
    FlythroughCollectionModel,
    FlythroughPose,
    FriendlyCollectionModel,
    JukeboxCollectionModel,
    LabeledMission,
    MapTileCollectionModel,
    MatterPairingCollectionModel,
    MediaAsset,
    MediaCollectionModel,
    MissionCollectionModel,
    MotorReading,
    MotorStatusCollectionModel,
    PathCollectionModel,
    Pose,
    PoseCollectionModel,
    Quaternion,
    RecapCollectionModel,
    RecordingsCollectionModel,
    RobotStatusCollectionModel,
    RollingRecordingCollectionModel,
    ScheduleEventCollectionModel,
    SinkSummonLocationCollectionModel,
    SinkSummonScheduleCollectionModel,
    SshPermissionCollectionModel,
    StructuredCollectionModel,
    UpdateStateCollectionModel,
    UploaderConfigCollectionModel,
    Vector2,
    Vector3,
    VersionCollectionModel,
    WifiStatusCollectionModel,
    ZoneCollectionModel,
)
from matic_sdk.models.control import JukeboxTrack
from matic_sdk.protocol.collections import (
    KNOWN_TARGET_SET,
    MAP_TARGETS,
    CollectionOperation,
    RawCollectionEvent,
)
from matic_sdk.protocol.wire import (
    ProtoWireError,
    WireField,
    WireType,
    decode_varint,
    parse_fields,
)

_WIFI_STATES = (
    "unknown",
    "connecting",
    "connected",
    "disconnected",
    "disconnecting",
    "roaming",
)
_ZONE_CLASSES = ("no_go", "stair", "drive_only")
_DOCK_DETECTION_METHODS = (
    "charger_contact",
    "visual_high_confidence",
    "visual_low_confidence",
)
_WEEKDAYS = (
    "sunday",
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
)
_JUKEBOX_TRACKS = ("oh_hanukkah", "deck_the_halls", "jingle_bells")
_PARKED_CODES = frozenset({106, 107})
_RETURNING_CODES = frozenset({104})
_CLEANING_CODES = frozenset({119})
_PAUSED_CODES = frozenset({120, 200, 302})


@dataclass(frozen=True, slots=True)
class _DecodeContext:
    target: str
    operation: CollectionOperation
    key: bytes
    payload: bytes
    fields: tuple[WireField, ...]
    sequence: int | None

    def common(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "operation": self.operation,
            "raw_payload": self.payload,
            "fields": self.fields,
        }


_Decoder = Callable[[_DecodeContext], FriendlyCollectionModel]


def _parse(data: bytes, *, limit: int = 100_000) -> tuple[WireField, ...]:
    if not data:
        return ()
    try:
        return parse_fields(data, max_fields=limit)
    except ProtoWireError:
        return ()


def _values(
    fields: Iterable[WireField],
    number: int,
    wire_type: WireType,
) -> tuple[int | bytes, ...]:
    return tuple(
        field.value
        for field in fields
        if field.number == number and field.wire_type is wire_type
    )


def _bytes(fields: Iterable[WireField], number: int) -> tuple[bytes, ...]:
    return tuple(
        value
        for value in _values(fields, number, WireType.LENGTH_DELIMITED)
        if isinstance(value, bytes)
    )


def _message(fields: Iterable[WireField], number: int) -> bytes | None:
    values = _bytes(fields, number)
    return values[0] if values else None


def _integer(
    fields: Iterable[WireField],
    number: int,
    wire_type: WireType = WireType.VARINT,
) -> int | None:
    for value in _values(fields, number, wire_type):
        if isinstance(value, int):
            return value
    return None


def _float32(fields: Iterable[WireField], number: int) -> float | None:
    value = _integer(fields, number, WireType.FIXED32)
    if value is None:
        return None
    return struct.unpack("<f", struct.pack("<I", value))[0]


def _float64(fields: Iterable[WireField], number: int) -> float | None:
    value = _integer(fields, number, WireType.FIXED64)
    if value is None:
        return None
    return struct.unpack("<d", struct.pack("<Q", value))[0]


def _text(fields: Iterable[WireField], number: int) -> str | None:
    value = _message(fields, number)
    if value is None:
        return None
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _enum_name(value: int | None, names: tuple[str, ...]) -> str | None:
    if value is None:
        return None
    return names[value] if 0 <= value < len(names) else f"unknown_{value}"


def _signed32(value: int) -> int:
    value &= 0xFFFF_FFFF
    return value - 0x1_0000_0000 if value & 0x8000_0000 else value


def _zigzag32(value: int) -> int:
    return (value >> 1) ^ -(value & 1)


def _mission_id(data: bytes | None) -> int | None:
    fields = _parse(data or b"", limit=32)
    current = _integer(fields, 2, WireType.FIXED32)
    if current is not None:
        return current
    legacy = _integer(fields, 1, WireType.FIXED32)
    if legacy is not None:
        return legacy
    return _integer(fields, 1)


def _uuid(data: bytes | None, *, depth: int = 0) -> UUID | None:
    if data is None or depth > 5 or len(data) > 512:
        return None
    if len(data) == 16:
        return UUID(bytes=data)
    fields = _parse(data, limit=32)
    high = _integer(fields, 1, WireType.FIXED64)
    low = _integer(fields, 2, WireType.FIXED64)
    if high is not None or low is not None:
        return UUID(int=((high or 0) << 64) | (low or 0))
    for field_number in (2, 1, 3, 4):
        for value in _bytes(fields, field_number):
            found = _uuid(value, depth=depth + 1)
            if found is not None:
                return found
    return None


def _first_number(data: bytes | None, *, depth: int = 0) -> int | None:
    if data is None or depth > 4 or len(data) > 512:
        return None
    fields = _parse(data, limit=32)
    for wire_type in (WireType.VARINT, WireType.FIXED64, WireType.FIXED32):
        for field in fields:
            if field.wire_type is wire_type and isinstance(field.value, int):
                return field.value
    for field in fields:
        if field.wire_type is WireType.LENGTH_DELIMITED:
            assert isinstance(field.value, bytes)
            found = _first_number(field.value, depth=depth + 1)
            if found is not None:
                return found
    return None


def _vector2(data: bytes | None) -> Vector2 | None:
    fields = _parse(data or b"", limit=16)
    x = _float32(fields, 1)
    y = _float32(fields, 2)
    if x is None and y is None:
        return None
    return Vector2(x or 0.0, y or 0.0)


def _vector3(data: bytes | None) -> Vector3 | None:
    fields = _parse(data or b"", limit=16)
    x = _float32(fields, 1)
    y = _float32(fields, 2)
    z = _float32(fields, 3)
    if x is None and y is None and z is None:
        return None
    return Vector3(x or 0.0, y or 0.0, z or 0.0)


def _quaternion(data: bytes | None) -> Quaternion | None:
    fields = _parse(data or b"", limit=32)
    xyz = _vector3(_message(fields, 2))
    w = _float32(fields, 3)
    if xyz is not None or w is not None:
        xyz = xyz or Vector3(0.0, 0.0, 0.0)
        return Quaternion(xyz.x, xyz.y, xyz.z, w or 0.0)
    packed = [
        field.value
        for field in fields
        if field.number == 1
        and field.wire_type is WireType.FIXED32
        and isinstance(field.value, int)
    ]
    if len(packed) == 4:
        values = [struct.unpack("<f", struct.pack("<I", value))[0] for value in packed]
        return Quaternion(values[0], values[1], values[2], values[3])
    return None


def _pose(data: bytes | None) -> Pose | None:
    fields = _parse(data or b"", limit=64)
    inner = _message(fields, 1)
    inner_fields = _parse(inner or b"", limit=64)
    if not inner_fields:
        inner_fields = fields
    translation = _vector3(_message(inner_fields, 4))
    rotation = _quaternion(_message(inner_fields, 5))
    if translation is None:
        packed = [
            field.value
            for field in inner_fields
            if field.number == 1
            and field.wire_type is WireType.FIXED32
            and isinstance(field.value, int)
        ]
        if len(packed) >= 3:
            xyz = [
                struct.unpack("<f", struct.pack("<I", value))[0] for value in packed[:3]
            ]
            translation = Vector3(*xyz)
    if translation is None and rotation is None:
        return None
    return Pose(
        translation or Vector3(0.0, 0.0, 0.0),
        rotation or Quaternion(0.0, 0.0, 0.0, 1.0),
    )


def _timestamp(data: bytes | None, *, depth: int = 0) -> datetime | None:
    if data is None or depth > 4 or len(data) > 256:
        return None
    fields = _parse(data, limit=32)
    seconds = _integer(fields, 1)
    nanos = _integer(fields, 2)
    if seconds is not None and 946_684_800 <= seconds <= 4_102_444_800:
        if nanos is None or 0 <= nanos < 1_000_000_000:
            return datetime.fromtimestamp(
                seconds + (nanos or 0) / 1_000_000_000,
                tz=UTC,
            )
    for field in fields:
        if field.wire_type is WireType.LENGTH_DELIMITED:
            assert isinstance(field.value, bytes)
            result = _timestamp(field.value, depth=depth + 1)
            if result is not None:
                return result
    return None


def _duration(data: bytes | None, *, depth: int = 0) -> timedelta | None:
    if data is None or depth > 3 or len(data) > 128:
        return None
    fields = _parse(data, limit=32)
    seconds = _integer(fields, 1)
    nanos = _integer(fields, 2)
    if seconds is not None and seconds < 100 * 365 * 24 * 60 * 60:
        if nanos is None or nanos < 1_000_000_000:
            return timedelta(seconds=seconds, microseconds=(nanos or 0) / 1_000)
    for field in fields:
        if field.wire_type is WireType.LENGTH_DELIMITED:
            assert isinstance(field.value, bytes)
            result = _duration(field.value, depth=depth + 1)
            if result is not None:
                return result
    return None


def _packed_varints(data: bytes) -> tuple[int, ...]:
    values: list[int] = []
    offset = 0
    try:
        while offset < len(data):
            value, offset = decode_varint(data, offset)
            values.append(value)
    except ProtoWireError:
        return ()
    return tuple(values)


def _state_codes(fields: Iterable[WireField], number: int) -> tuple[int, ...]:
    values: list[int] = []
    for field in fields:
        if field.number != number:
            continue
        if field.wire_type is WireType.VARINT and isinstance(field.value, int):
            values.append(field.value)
        elif field.wire_type is WireType.LENGTH_DELIMITED:
            assert isinstance(field.value, bytes)
            values.extend(_packed_varints(field.value))
    return tuple(values)


def _map_key(data: bytes) -> tuple[int | None, int | None, int | None]:
    fields = _parse(data, limit=32)
    page_message = _message(fields, 1)
    page_fields = _parse(page_message or b"", limit=16)
    x = _integer(page_fields, 3)
    y = _integer(page_fields, 4)
    if x is not None:
        x = _zigzag32(x)
    else:
        legacy_x = _integer(page_fields, 1)
        x = _signed32(legacy_x) if legacy_x is not None else None
    if y is not None:
        y = _zigzag32(y)
    else:
        legacy_y = _integer(page_fields, 2)
        y = _signed32(legacy_y) if legacy_y is not None else None
    if page_message is not None:
        x = 0 if x is None else x
        y = 0 if y is None else y
    return x, y, _mission_id(_message(fields, 2))


def _decode_map(context: _DecodeContext) -> MapTileCollectionModel:
    page_x, page_y, mission_id = _map_key(context.key)
    layers: tuple[str, ...] = ()
    tiles: tuple[Any, ...] = ()
    warnings: tuple[str, ...] = ()
    try:
        from matic_sdk.maps import decode_map_event
        from matic_sdk.models.maps import CollectionEvent

        event = CollectionEvent(
            key=context.key,
            payload=context.payload
            if context.operation is CollectionOperation.UPSERT
            else None,
            value_present=context.operation is CollectionOperation.UPSERT,
            page_x=page_x,
            page_y=page_y,
            mission_id=mission_id,
            sequence=context.sequence,
            started_at_ns=None,
        )
        decoded = decode_map_event(event, target=context.target)  # type: ignore[arg-type]
        tiles = decoded.tiles
        layers = tuple(tile.layer for tile in tiles)
        warnings = decoded.warnings
    except ModuleNotFoundError:
        warnings = ("install unofficial-matic-sdk[maps] to render this map tile",)
    return MapTileCollectionModel(
        **context.common(),
        mission_id=mission_id,
        page_x=page_x,
        page_y=page_y,
        layers=layers,
        tiles=tiles,
        warnings=warnings,
    )


def _decode_pose(context: _DecodeContext) -> PoseCollectionModel:
    key_fields = _parse(context.key, limit=16)
    mission_id = _integer(key_fields, 2, WireType.FIXED32)
    monotonic = _integer(context.fields, 1)
    clock = _message(context.fields, 3)
    if clock is not None:
        monotonic = _integer(_parse(clock, limit=16), 1) or monotonic
    return PoseCollectionModel(
        **context.common(),
        mission_id=mission_id,
        pose=_pose(_message(context.fields, 2)),
        monotonic_time_ns=monotonic,
        observed_at=_timestamp(_message(context.fields, 5)),
    )


def _decode_dock(context: _DecodeContext) -> DockDetectionCollectionModel:
    key_fields = _parse(context.key, limit=32)
    mission_id = _mission_id(_message(key_fields, 1))
    dock_id = _uuid(_message(key_fields, 3))
    if dock_id is None:
        dock_id = _uuid(_message(key_fields, 2))
    method = _integer(context.fields, 3)
    if method is None and context.payload:
        method = 0
    return DockDetectionCollectionModel(
        **context.common(),
        mission_id=mission_id,
        dock_id=dock_id,
        pose=_pose(context.payload),
        detection_method=_enum_name(method, _DOCK_DETECTION_METHODS),
        detected_at=_timestamp(_message(context.fields, 2)),
    )


def _mission_from_nested(data: bytes | None) -> int | None:
    if data is None:
        return None
    fields = _parse(data, limit=64)
    direct = _mission_id(data)
    if direct is not None:
        return direct
    for field in fields:
        if field.wire_type is WireType.LENGTH_DELIMITED:
            assert isinstance(field.value, bytes)
            found = _mission_from_nested(field.value)
            if found is not None:
                return found
    return None


def _decode_mission(context: _DecodeContext) -> MissionCollectionModel:
    labeled: list[LabeledMission] = []
    labels = _parse(_message(context.fields, 6) or b"", limit=128)
    for encoded in _bytes(labels, 1):
        item = _parse(encoded, limit=16)
        mission_id = _mission_id(_message(item, 1))
        label_fields = _parse(_message(item, 2) or b"", limit=16)
        assigned = _integer(label_fields, 1)
        named = _text(label_fields, 2)
        label = (
            named
            if named is not None
            else (f"floor {assigned}" if assigned is not None else None)
        )
        if mission_id is not None:
            labeled.append(LabeledMission(mission_id, label))
    return MissionCollectionModel(
        **context.common(),
        active_mission_id=_mission_from_nested(_message(context.fields, 4)),
        displayed_mission_id=_mission_from_nested(_message(context.fields, 5)),
        displayed_mission_explored=bool(_integer(context.fields, 3) or 0),
        labeled_missions=tuple(labeled),
    )


def _decode_active_session(
    context: _DecodeContext,
) -> ActiveSessionCollectionModel:
    decoded = None
    if context.payload:
        try:
            decoded = decode_active_session_key(context.payload)
        except CoverageDecodeError:
            decoded = None
    return ActiveSessionCollectionModel(
        **context.common(),
        mission_id=decoded.mission_id if decoded else None,
        session_id=decoded.session_id if decoded else None,
    )


def _walk(data: bytes | None) -> tuple[Vector2, ...]:
    fields = _parse(data or b"", limit=10_000)
    return tuple(
        point
        for encoded in _bytes(fields, 1)
        if (point := _vector2(encoded)) is not None
    )


def _decode_zone(context: _DecodeContext) -> ZoneCollectionModel:
    key = _parse(context.key, limit=32)
    mission_id = _mission_id(_message(key, 1))
    zone_id: int | UUID | None = _uuid(_message(key, 2))
    if zone_id is None:
        zone_id = _integer(key, 2)
    zone_class = _enum_name(_integer(key, 3), _ZONE_CLASSES)
    border = _parse(_message(context.fields, 1) or b"", limit=64)
    outer = _walk(_message(border, 2))
    holes = tuple(_walk(value) for value in _bytes(border, 3))
    return ZoneCollectionModel(
        **context.common(),
        mission_id=mission_id,
        zone_id=zone_id,
        zone_class=zone_class,
        outer_border=outer,
        holes=holes,
    )


def _coverage_goal_count(fields: tuple[WireField, ...]) -> int:
    reports = _parse(_message(fields, 7) or b"", limit=20_000)
    count = 0
    for report in _bytes(reports, 1):
        if _message(_parse(report, limit=64), 1) is not None:
            count += 1
    return count


def _decode_plan(context: _DecodeContext) -> CoveragePlanCollectionModel:
    plan = None
    if context.payload:
        try:
            plan = decode_coverage_plan(context.payload)
        except CoverageDecodeError:
            plan = None
    mission_id = plan.mission_id if plan else _mission_id(_message(context.fields, 11))
    candidate = _message(context.fields, 14)
    return CoveragePlanCollectionModel(
        **context.common(),
        mission_id=mission_id,
        goal_count=_coverage_goal_count(context.fields),
        has_current_candidate=candidate is not None,
        current_region_id=plan.current_region_id if plan else None,
        actionable_plan=plan,
    )


def _heading(data: bytes | None) -> float | None:
    fields = _parse(data or b"", limit=16)
    direct = _float32(fields, 3)
    if direct is not None:
        return direct
    rotation = _parse(_message(fields, 4) or b"", limit=16)
    real = _float32(rotation, 1)
    imaginary = _float32(rotation, 2)
    if real is None and imaginary is None:
        return None
    return math.atan2(imaginary or 0.0, real or 0.0)


def _decode_sink_location(
    context: _DecodeContext,
) -> SinkSummonLocationCollectionModel:
    key_fields = _parse(context.key, limit=16)
    mission_id = _integer(key_fields, 2, WireType.FIXED32)
    return SinkSummonLocationCollectionModel(
        **context.common(),
        mission_id=mission_id,
        location=_vector2(context.payload),
        heading_radians=_heading(context.payload),
    )


def _decode_history(context: _DecodeContext) -> CoverageHistoryCollectionModel:
    event = _parse(_message(context.fields, 5) or b"", limit=256)
    return CoverageHistoryCollectionModel(
        **context.common(),
        session_id=_uuid(context.key),
        mission_id=_mission_id(_message(context.fields, 1)),
        started_at=_timestamp(_message(event, 3)),
        ended_at=_timestamp(_message(event, 4)),
        resumable=bool(_integer(context.fields, 6) or 0),
    )


def _decode_recap(context: _DecodeContext) -> RecapCollectionModel:
    key = _parse(context.key, limit=16)
    area = _parse(_message(context.fields, 1) or b"", limit=16)
    favorite = _parse(_message(context.fields, 6) or b"", limit=16)
    cleaning = _parse(_message(context.fields, 7) or b"", limit=16)
    sweep_square_meters = _float32(area, 1) or 0.0
    mop_square_meters = _float32(area, 2) or 0.0
    square_feet_per_square_meter = 10.76391041671
    return RecapCollectionModel(
        **context.common(),
        month=_integer(key, 1),
        year=_integer(key, 2),
        sweep_square_feet=sweep_square_meters * square_feet_per_square_meter,
        mop_square_feet=mop_square_meters * square_feet_per_square_meter,
        cleaning_sessions=_integer(context.fields, 2) or 0,
        favorite_room=_text(favorite, 2),
        sweep_time=_duration(_message(cleaning, 1)),
        mop_time=_duration(_message(cleaning, 2)),
    )


def _decode_path(context: _DecodeContext) -> PathCollectionModel:
    points = tuple(
        point
        for encoded in _bytes(context.fields, 1)
        if (point := _vector2(encoded)) is not None
    )
    return PathCollectionModel(
        **context.common(),
        mission_id=_mission_id(_message(context.fields, 2)),
        points=points,
    )


def _decode_line(context: _DecodeContext) -> CoverageLineCollectionModel:
    line = _parse(_message(context.fields, 2) or b"", limit=16)
    if not line:
        line = context.fields
    return CoverageLineCollectionModel(
        **context.common(),
        mission_id=_mission_id(_message(context.fields, 1)),
        start=_vector2(_message(line, 1)),
        end=_vector2(_message(line, 2)),
    )


def _decode_flythrough(context: _DecodeContext) -> FlythroughCollectionModel:
    poses: list[FlythroughPose] = []
    for encoded in _bytes(context.fields, 1):
        fields = _parse(encoded, limit=16)
        location = _vector3(_message(fields, 1))
        target = _vector3(_message(fields, 2))
        if location is not None and target is not None:
            poses.append(FlythroughPose(location, target))
    return FlythroughCollectionModel(
        **context.common(),
        mission_id=_mission_id(_message(context.fields, 2)),
        poses=tuple(poses),
    )


def _decode_wifi(context: _DecodeContext) -> WifiStatusCollectionModel:
    scan = _parse(_message(context.fields, 5) or b"", limit=10_000)
    known = len(_bytes(scan, 1)) if scan else None
    other = len(_bytes(scan, 2)) if scan else None
    state = _enum_name(_integer(context.fields, 1), _WIFI_STATES) or "unknown"
    return WifiStatusCollectionModel(
        **context.common(),
        state=state,
        current_network_ssid=_text(context.fields, 10),
        ip_address=_text(context.fields, 4),
        known_network_count=known,
        other_network_count=other,
    )


def _motor(data: bytes | None) -> MotorReading:
    fields = _parse(data or b"", limit=32)
    return MotorReading(
        voltage=_float32(fields, 2) or 0.0,
        current=_float32(fields, 1) or 0.0,
        rpm=_float32(fields, 3) or 0.0,
    )


def _decode_motors(context: _DecodeContext) -> MotorStatusCollectionModel:
    return MotorStatusCollectionModel(
        **context.common(),
        drive_left=_motor(_message(context.fields, 1)),
        drive_right=_motor(_message(context.fields, 2)),
        vacuum=_motor(_message(context.fields, 3)),
        sweeper=_motor(_message(context.fields, 4)),
        mopper=_motor(_message(context.fields, 5)),
        brush=_motor(_message(context.fields, 6)),
    )


def _activity(codes: tuple[int, ...], errors: tuple[int, ...]) -> str:
    code_set = frozenset(codes)
    if errors:
        return "error"
    if code_set & _PAUSED_CODES:
        return "paused"
    if code_set & _CLEANING_CODES:
        return "cleaning"
    if code_set & _RETURNING_CODES:
        return "returning_to_dock"
    if 107 in code_set:
        return "charging"
    if 106 in code_set:
        return "docked"
    return "ready"


def _decode_robot_status(context: _DecodeContext) -> RobotStatusCollectionModel:
    states = _state_codes(context.fields, 1)
    errors = _state_codes(context.fields, 2)
    state_set = frozenset(states)
    battery = _float32(context.fields, 9)
    return RobotStatusCollectionModel(
        **context.common(),
        state_codes=states,
        error_codes=errors,
        activity=_activity(states, errors),
        battery_percentage=(
            max(0, min(100, round(battery * 100))) if battery is not None else None
        ),
        name=_text(context.fields, 1),
        is_paused=bool(state_set & _PAUSED_CODES),
        is_charging=107 in state_set,
        is_navigating=bool(state_set & _RETURNING_CODES),
        is_cleaning=bool(state_set & _CLEANING_CODES),
        time_until_idle_dock=_duration(_message(context.fields, 18)),
    )


def _decode_version(context: _DecodeContext) -> VersionCollectionModel:
    return VersionCollectionModel(
        **context.common(),
        version_name=_text(context.fields, 1),
        profile_name=_text(context.fields, 2),
        protocol_version=_integer(context.fields, 3),
    )


def _decode_coverage_time(
    context: _DecodeContext,
) -> CoverageTimeCollectionModel:
    session = _parse(_message(context.fields, 4) or b"", limit=128)
    progress = _float64(session, 2)
    if progress is not None and progress <= 1:
        progress *= 100
    elapsed = _duration(_message(context.fields, 1))
    remaining = _duration(_message(context.fields, 2))
    if remaining is None:
        remaining = _duration(_message(context.fields, 3))
    if session:
        remaining = _duration(_message(session, 4)) or remaining
    return CoverageTimeCollectionModel(
        **context.common(),
        session_id=_uuid(_message(session, 1)),
        elapsed=elapsed,
        remaining=remaining,
        progress_percentage=progress,
    )


def _decode_update(context: _DecodeContext) -> UpdateStateCollectionModel:
    names = ("idle", "busy", "progress", "error", "complete", "available")
    selected = next(
        (
            number
            for number in range(1, 7)
            if _message(context.fields, number) is not None
        ),
        None,
    )
    if selected is None:
        numeric = _integer(context.fields, 1)
        state = _enum_name(numeric, names) or "unknown"
        detail = context.payload
    else:
        state = names[selected - 1]
        detail = _message(context.fields, selected) or b""
    detail_fields = _parse(detail, limit=32)
    progress = _float64(detail_fields, 1)
    total = _float64(detail_fields, 2)
    return UpdateStateCollectionModel(
        **context.common(),
        state=state,
        progress_percentage=progress,
        total_gigabytes=total,
        release_name=_text(detail_fields, 1) if state == "available" else None,
    )


def _decode_binary(context: _DecodeContext) -> BinarySettingCollectionModel:
    enabled: bool | None
    if context.operation is CollectionOperation.DELETE:
        enabled = None
    else:
        value = _integer(context.fields, 1)
        enabled = bool(value or 0)
    return BinarySettingCollectionModel(
        **context.common(),
        setting=context.target.removesuffix("_enabled_state"),
        enabled=enabled,
    )


def _decode_matter(context: _DecodeContext) -> MatterPairingCollectionModel:
    info_bytes = _message(context.fields, 1)
    info = _parse(info_bytes or b"", limit=16)
    return MatterPairingCollectionModel(
        **context.common(),
        enabled=info_bytes is not None,
        qr_code=_text(info, 1),
        manual_code=_text(info, 2),
    )


def _decode_rolling(context: _DecodeContext) -> RollingRecordingCollectionModel:
    enabled_data = _message(context.fields, 2)
    disabled = _message(context.fields, 3) is not None
    enabled_fields = _parse(enabled_data or b"", limit=16)
    confirm = _integer(enabled_fields, 1)
    return RollingRecordingCollectionModel(
        **context.common(),
        enabled=enabled_data is not None and not disabled,
        confirm_for_each=bool(confirm) if confirm is not None else None,
    )


def _decode_uploader(context: _DecodeContext) -> UploaderConfigCollectionModel:
    opted = _integer(context.fields, 2)
    baseline = _message(context.fields, 1) is not None
    return UploaderConfigCollectionModel(
        **context.common(),
        customer_baseline=not baseline,
        opted_in=bool(opted) if opted is not None else None,
    )


def _decode_ssh(context: _DecodeContext) -> SshPermissionCollectionModel:
    value = _integer(context.fields, 1)
    enabled = (
        None if context.operation is CollectionOperation.DELETE else bool(value or 0)
    )
    return SshPermissionCollectionModel(
        **context.common(),
        enabled=enabled,
    )


def _schedule_key(data: bytes) -> tuple[int | None, UUID | int | None]:
    fields = _parse(data, limit=32)
    mission_id = _mission_id(_message(fields, 1))
    event_id: UUID | int | None = _uuid(_message(fields, 3))
    if event_id is None:
        event_id = _uuid(_message(fields, 2))
    if event_id is None:
        event_id = _integer(fields, 2)
    return mission_id, event_id


def _weekdays(data: bytes | None) -> tuple[str, ...]:
    fields = _parse(data or b"", limit=32)
    return tuple(
        name
        for number, name in enumerate(_WEEKDAYS, start=1)
        if bool(_integer(fields, number) or 0)
    )


def _schedule_time(data: bytes | None) -> int | None:
    return _integer(_parse(data or b"", limit=16), 1)


def _decode_schedule(context: _DecodeContext) -> ScheduleEventCollectionModel:
    mission_id, event_id = _schedule_key(context.key)
    details = _parse(_message(context.fields, 1) or b"", limit=64)
    enabled = _integer(details, 9)
    return ScheduleEventCollectionModel(
        **context.common(),
        mission_id=mission_id,
        event_id=event_id,
        name=_text(context.fields, 2),
        weekdays=_weekdays(_message(details, 1)),
        minutes_after_midnight=_schedule_time(_message(details, 3)),
        enabled=bool(enabled) if enabled is not None else None,
    )


def _decode_sink_schedule(
    context: _DecodeContext,
) -> SinkSummonScheduleCollectionModel:
    mission_id, event_id = _schedule_key(context.key)
    enabled = _integer(context.fields, 4)
    return SinkSummonScheduleCollectionModel(
        **context.common(),
        mission_id=mission_id,
        event_id=event_id,
        weekdays=_weekdays(_message(context.fields, 1)),
        minutes_after_midnight=_schedule_time(_message(context.fields, 2)),
        duration=_duration(_message(context.fields, 3)),
        enabled=bool(enabled) if enabled is not None else None,
    )


def _mp4_assets(data: bytes, *, depth: int = 0) -> tuple[MediaAsset, ...]:
    if depth > 6:
        return ()
    if len(data) >= 12 and data[4:8] == b"ftyp":
        return (
            MediaAsset(
                media_type="video/mp4",
                data=data,
                sha256=hashlib.sha256(data).hexdigest(),
            ),
        )
    assets: list[MediaAsset] = []
    for field in _parse(data, limit=10_000):
        if field.wire_type is WireType.LENGTH_DELIMITED:
            assert isinstance(field.value, bytes)
            assets.extend(_mp4_assets(field.value, depth=depth + 1))
    return tuple(assets)


def _decode_media(context: _DecodeContext) -> MediaCollectionModel:
    assets = [
        MediaAsset(
            media_type="image/webp",
            data=image.data,
            width=image.width,
            height=image.height,
            sha256=image.sha256,
        )
        for image in extract_embedded_webps(context.payload)
    ]
    assets.extend(_mp4_assets(context.payload))
    return MediaCollectionModel(
        **context.common(),
        item_id=_uuid(context.key) or _first_number(context.key),
        assets=tuple(assets),
    )


def _decode_recordings(context: _DecodeContext) -> RecordingsCollectionModel:
    repeated = _bytes(context.fields, 1)
    count = len(repeated)
    if len(repeated) == 1:
        nested = _parse(repeated[0], limit=10_000)
        nested_recordings = _bytes(nested, 1)
        if nested_recordings:
            count = len(nested_recordings)
    return RecordingsCollectionModel(
        **context.common(),
        recording_count=count,
    )


def _decode_customer(context: _DecodeContext) -> CustomerInfoCollectionModel:
    return CustomerInfoCollectionModel(
        **context.common(),
        email=_text(context.fields, 1),
    )


def _decode_jukebox(context: _DecodeContext) -> JukeboxCollectionModel:
    track_name = _enum_name(_integer(context.fields, 1), _JUKEBOX_TRACKS)
    track: JukeboxTrack | str | None = track_name
    if track_name is not None and not track_name.startswith("unknown_"):
        track = JukeboxTrack(track_name)
    return JukeboxCollectionModel(
        **context.common(),
        track=track,
    )


def _decode_structured(context: _DecodeContext) -> StructuredCollectionModel:
    return StructuredCollectionModel(
        **context.common(),
        schema_name=context.target,
    )


_decoders: dict[str, _Decoder] = {
    **dict.fromkeys(MAP_TARGETS, _decode_map),
    "latest_pose": _decode_pose,
    "dock_detections": _decode_dock,
    "displayed_mission": _decode_mission,
    "active_session_key": _decode_active_session,
    "zones": _decode_zone,
    "coverage_plan": _decode_plan,
    "sink_summon_locations": _decode_sink_location,
    "coverage_session_history": _decode_history,
    "recap_history": _decode_recap,
    "approximate_trajectory": _decode_path,
    "coverage_corridor": _decode_line,
    "coverage_marker": _decode_line,
    "flythrough": _decode_flythrough,
    "wifi_status": _decode_wifi,
    "motor_status": _decode_motors,
    "kabuki_state": _decode_robot_status,
    "current_version": _decode_version,
    "coverage_time": _decode_coverage_time,
    "update_state": _decode_update,
    "petwaste_enabled_state": _decode_binary,
    "child_lock_enabled_state": _decode_binary,
    "requested_preview_release_state": _decode_binary,
    "voice_enabled_state": _decode_binary,
    "auto_record_voice_enabled_state": _decode_binary,
    "matter_pairing_state": _decode_matter,
    "rolling_recordings_config_state": _decode_rolling,
    "uploader_config_state": _decode_uploader,
    "user_tunnel_ssh_permission": _decode_ssh,
    "schedule_events": _decode_schedule,
    "schedule_event_previews": _decode_media,
    "sink_summons": _decode_sink_schedule,
    "coverage_session_thumbnails": _decode_media,
    "scratch_recordings": _decode_recordings,
    "recording_thumbnails": _decode_media,
    "recording_videos": _decode_media,
    "app_customer_info": _decode_customer,
    "jukebox_state": _decode_jukebox,
}
if frozenset(_decoders) != KNOWN_TARGET_SET:
    missing = sorted(KNOWN_TARGET_SET - _decoders.keys())
    extra = sorted(_decoders.keys() - KNOWN_TARGET_SET)
    raise AssertionError(
        f"friendly collection registry mismatch; missing={missing}, extra={extra}"
    )

COLLECTION_DECODERS = MappingProxyType(_decoders)
COLLECTION_MODEL_TYPES = MappingProxyType(
    {
        **dict.fromkeys(MAP_TARGETS, MapTileCollectionModel),
        "latest_pose": PoseCollectionModel,
        "dock_detections": DockDetectionCollectionModel,
        "displayed_mission": MissionCollectionModel,
        "active_session_key": ActiveSessionCollectionModel,
        "zones": ZoneCollectionModel,
        "coverage_plan": CoveragePlanCollectionModel,
        "sink_summon_locations": SinkSummonLocationCollectionModel,
        "coverage_session_history": CoverageHistoryCollectionModel,
        "recap_history": RecapCollectionModel,
        "approximate_trajectory": PathCollectionModel,
        "coverage_corridor": CoverageLineCollectionModel,
        "coverage_marker": CoverageLineCollectionModel,
        "flythrough": FlythroughCollectionModel,
        "wifi_status": WifiStatusCollectionModel,
        "motor_status": MotorStatusCollectionModel,
        "kabuki_state": RobotStatusCollectionModel,
        "current_version": VersionCollectionModel,
        "coverage_time": CoverageTimeCollectionModel,
        "update_state": UpdateStateCollectionModel,
        "petwaste_enabled_state": BinarySettingCollectionModel,
        "child_lock_enabled_state": BinarySettingCollectionModel,
        "requested_preview_release_state": BinarySettingCollectionModel,
        "voice_enabled_state": BinarySettingCollectionModel,
        "auto_record_voice_enabled_state": BinarySettingCollectionModel,
        "matter_pairing_state": MatterPairingCollectionModel,
        "rolling_recordings_config_state": RollingRecordingCollectionModel,
        "uploader_config_state": UploaderConfigCollectionModel,
        "user_tunnel_ssh_permission": SshPermissionCollectionModel,
        "schedule_events": ScheduleEventCollectionModel,
        "schedule_event_previews": MediaCollectionModel,
        "sink_summons": SinkSummonScheduleCollectionModel,
        "coverage_session_thumbnails": MediaCollectionModel,
        "scratch_recordings": RecordingsCollectionModel,
        "recording_thumbnails": MediaCollectionModel,
        "recording_videos": MediaCollectionModel,
        "app_customer_info": CustomerInfoCollectionModel,
        "jukebox_state": JukeboxCollectionModel,
    }
)
if frozenset(COLLECTION_MODEL_TYPES) != KNOWN_TARGET_SET:
    raise AssertionError("every verified target must expose a friendly model type")


def decode_collection_payload(
    target: str,
    payload: bytes,
    *,
    key: bytes = b"",
    operation: CollectionOperation = CollectionOperation.UPSERT,
    sequence: int | None = None,
) -> FriendlyCollectionModel:
    """Decode one application payload without requiring a live event object."""

    if not isinstance(payload, bytes):
        raise TypeError("collection payload must be bytes")
    if not isinstance(key, bytes):
        raise TypeError("collection key must be bytes")
    context = _DecodeContext(
        target=target,
        operation=operation,
        key=key,
        payload=payload,
        fields=_parse(payload),
        sequence=sequence,
    )
    return COLLECTION_DECODERS.get(target, _decode_structured)(context)


def decode_collection_event(event: RawCollectionEvent) -> FriendlyCollectionModel:
    """Decode a raw auto-acknowledged collection event into its friendly model."""

    sequence = event.sequence_id.sequence_no if event.sequence_id else None
    return decode_collection_payload(
        event.target,
        event.payload or b"",
        key=event.key,
        operation=event.operation,
        sequence=sequence,
    )


__all__ = [
    "COLLECTION_DECODERS",
    "COLLECTION_MODEL_TYPES",
    "decode_collection_event",
    "decode_collection_payload",
]
