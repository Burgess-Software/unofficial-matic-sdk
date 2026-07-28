"""Strict decoders for the robot's active coverage plan.

The collection transport deliberately exposes application payloads as bytes.
This module decodes only the small, native-verified subset required to safely
reuse the robot's current goals in a reprioritization command. Large partition
geometry fields are left opaque.
"""

from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import UUID

from matic_sdk.models.control import (
    CleaningFloor,
    CoverageBehavior,
    CoverageGoalCleaningMode,
    CoverageGoals,
    CoverageGoalSetting,
    CoverageGoalSpec,
    CoveragePlanGoal,
)
from matic_sdk.protocol.collections import CollectionOperation
from matic_sdk.protocol.wire import (
    ProtoWireError,
    WireField,
    WireType,
    parse_fields,
)
from matic_sdk.telemetry import TelemetrySession

if TYPE_CHECKING:
    from collections.abc import Iterable

    from matic_sdk.client import MaticClient


_MAX_COVERAGE_PLAN_BYTES = 8 * 1024 * 1024
_MAX_ROOT_FIELDS = 4_096
_MAX_REPORTS = 10_000
_MAX_NESTED_FIELDS = 1_024
_UNSEEN = object()


class CoverageDecodeError(ValueError):
    """A live coverage payload is malformed or outside the proven schema."""


@dataclass(frozen=True, slots=True)
class DecodedCoveragePlan:
    """The command-relevant subset of one active ``coverage_plan`` value."""

    mission_id: int
    goals: CoverageGoals
    current_region_id: UUID


@dataclass(frozen=True, slots=True)
class DecodedActiveSessionKey:
    """Identifiers decoded from one non-empty ``active_session_key`` value."""

    mission_id: int
    session_id: UUID


@dataclass(frozen=True, slots=True)
class ReprioritizationSnapshot:
    """One coherent active plan and coverage-session identity."""

    goals: CoverageGoals
    mission_id: int
    current_region_id: UUID
    current_session_id: UUID

    @property
    def region_ids(self) -> tuple[UUID, ...]:
        """Unique room IDs in their first-seen execution order."""

        return tuple(dict.fromkeys(goal.region_id for goal in self.goals.goals))


def _parse(
    message: bytes,
    path: str,
    *,
    max_fields: int = _MAX_NESTED_FIELDS,
) -> tuple[WireField, ...]:
    try:
        return parse_fields(message, max_fields=max_fields)
    except ProtoWireError as error:
        raise CoverageDecodeError(f"{path}: {error}") from error


def _matches(
    fields: Iterable[WireField],
    number: int,
    wire_type: WireType,
    path: str,
) -> tuple[WireField, ...]:
    matches = tuple(field for field in fields if field.number == number)
    if any(field.wire_type is not wire_type for field in matches):
        raise CoverageDecodeError(
            f"{path} must use protobuf wire type {int(wire_type)}"
        )
    return matches


def _optional_message(
    fields: Iterable[WireField],
    number: int,
    path: str,
) -> bytes | None:
    matches = _matches(fields, number, WireType.LENGTH_DELIMITED, path)
    if len(matches) > 1:
        raise CoverageDecodeError(f"{path} must not be repeated")
    if not matches:
        return None
    value = matches[0].value
    assert isinstance(value, bytes)
    return value


def _required_message(
    fields: Iterable[WireField],
    number: int,
    path: str,
) -> bytes:
    value = _optional_message(fields, number, path)
    if value is None:
        raise CoverageDecodeError(f"{path} is required")
    return value


def _repeated_messages(
    fields: Iterable[WireField],
    number: int,
    path: str,
) -> tuple[bytes, ...]:
    matches = _matches(fields, number, WireType.LENGTH_DELIMITED, path)
    values: list[bytes] = []
    for field in matches:
        value = field.value
        assert isinstance(value, bytes)
        values.append(value)
    return tuple(values)


def _optional_integer(
    fields: Iterable[WireField],
    number: int,
    wire_type: WireType,
    path: str,
) -> int | None:
    matches = _matches(fields, number, wire_type, path)
    if len(matches) > 1:
        raise CoverageDecodeError(f"{path} must not be repeated")
    if not matches:
        return None
    value = matches[0].value
    assert isinstance(value, int)
    return value


def _required_integer(
    fields: Iterable[WireField],
    number: int,
    wire_type: WireType,
    path: str,
) -> int:
    value = _optional_integer(fields, number, wire_type, path)
    if value is None:
        raise CoverageDecodeError(f"{path} is required")
    return value


def _decode_mission_id(message: bytes, path: str) -> int:
    fields = _parse(message, path, max_fields=16)
    # A present MissionId message with an omitted proto3 scalar means zero.
    value = _optional_integer(fields, 2, WireType.FIXED32, f"{path}.value")
    return value if value is not None else 0


def _decode_uuid(message: bytes, path: str) -> UUID:
    fields = _parse(message, path, max_fields=16)
    legacy = _optional_message(fields, 1, f"{path}.id_deprecated")
    fixed = _optional_message(fields, 2, f"{path}.id")
    if legacy is not None and fixed is not None:
        raise CoverageDecodeError(
            f"{path} cannot contain both legacy and fixed UUID values"
        )
    if legacy is not None:
        if len(legacy) != 16:
            raise CoverageDecodeError(f"{path}.id_deprecated must contain 16 bytes")
        return UUID(bytes=legacy)
    if fixed is None:
        raise CoverageDecodeError(f"{path} does not contain a UUID value")

    fixed_fields = _parse(fixed, f"{path}.id", max_fields=16)
    high = _optional_integer(
        fixed_fields,
        1,
        WireType.FIXED64,
        f"{path}.id.high",
    )
    low = _optional_integer(
        fixed_fields,
        2,
        WireType.FIXED64,
        f"{path}.id.low",
    )
    return UUID(int=((high or 0) << 64) | (low or 0))


def _decode_identifier(message: bytes, path: str) -> UUID:
    fields = _parse(message, path, max_fields=16)
    uuid_message = _required_message(fields, 2, f"{path}.id")
    return _decode_uuid(uuid_message, f"{path}.id")


def _decode_spec(message: bytes, path: str) -> CoverageGoalSpec:
    fields = _parse(message, path, max_fields=32)
    raw_setting = _required_integer(
        fields,
        1,
        WireType.VARINT,
        f"{path}.setting",
    )
    raw_floor = _required_integer(
        fields,
        2,
        WireType.VARINT,
        f"{path}.floor",
    )
    raw_cleaning_mode = _required_integer(
        fields,
        4,
        WireType.VARINT,
        f"{path}.cleaning_mode",
    )
    raw_behavior = _required_integer(
        fields,
        5,
        WireType.VARINT,
        f"{path}.behavior",
    )
    try:
        spec = CoverageGoalSpec(
            setting=CoverageGoalSetting(raw_setting),
            floor=CleaningFloor(raw_floor),
            cleaning_mode=CoverageGoalCleaningMode(raw_cleaning_mode),
            behavior=CoverageBehavior(raw_behavior),
        )
    except ValueError as error:
        raise CoverageDecodeError(f"{path} contains an unknown enum value") from error
    if (
        spec.cleaning_mode is CoverageGoalCleaningMode.MOP
        and spec.floor is CleaningFloor.CARPET
    ):
        raise CoverageDecodeError(f"{path} cannot request mopping on carpet")
    return spec


def _decode_standard_goal(message: bytes, index: int) -> CoveragePlanGoal:
    path = f"coverage_plan.reports[{index}].goal"
    fields = _parse(message, path, max_fields=128)

    header_message = _required_message(fields, 6, f"{path}.header")
    target_message = _required_message(fields, 7, f"{path}.area_selection")

    header_fields = _parse(header_message, f"{path}.header", max_fields=32)
    round_key_message = _required_message(
        header_fields,
        1,
        f"{path}.header.round_key",
    )
    round_key_fields = _parse(
        round_key_message,
        f"{path}.header.round_key",
        max_fields=16,
    )
    goal_id_message = _required_message(
        round_key_fields,
        2,
        f"{path}.header.round_key.goal_id",
    )
    goal_id = _decode_uuid(
        goal_id_message,
        f"{path}.header.round_key.goal_id",
    )
    spec_message = _required_message(
        header_fields,
        3,
        f"{path}.header.spec",
    )
    spec = _decode_spec(spec_message, f"{path}.header.spec")

    target_fields = _parse(
        target_message,
        f"{path}.area_selection",
        max_fields=32,
    )
    partition_target = _required_message(
        target_fields,
        1,
        f"{path}.area_selection.partition",
    )
    partition_fields = _parse(
        partition_target,
        f"{path}.area_selection.partition",
        max_fields=16,
    )
    partition_id_message = _required_message(
        partition_fields,
        1,
        f"{path}.area_selection.partition.partition_id",
    )
    partition_id = _decode_uuid(
        partition_id_message,
        f"{path}.area_selection.partition.partition_id",
    )

    selection_kind = _required_message(
        target_fields,
        2,
        f"{path}.area_selection.kind",
    )
    kind_fields = _parse(
        selection_kind,
        f"{path}.area_selection.kind",
        max_fields=16,
    )
    complete = _optional_message(
        kind_fields,
        1,
        f"{path}.area_selection.kind.complete",
    )
    drawn = _optional_message(
        kind_fields,
        3,
        f"{path}.area_selection.kind.drawn",
    )
    if complete is None or drawn is not None:
        raise CoverageDecodeError(f"{path} is not a standard complete-area goal")

    region_selection = _required_message(
        target_fields,
        3,
        f"{path}.area_selection.region",
    )
    region_fields = _parse(
        region_selection,
        f"{path}.area_selection.region",
        max_fields=16,
    )
    if (
        _optional_message(
            region_fields,
            2,
            f"{path}.area_selection.region.custom",
        )
        is not None
    ):
        raise CoverageDecodeError(f"{path} contains a custom-area target")
    region_id_message = _required_message(
        region_fields,
        3,
        f"{path}.area_selection.region.region_id",
    )
    region_id = _decode_identifier(
        region_id_message,
        f"{path}.area_selection.region.region_id",
    )
    return CoveragePlanGoal(goal_id, partition_id, region_id, spec)


def _decode_current_region(message: bytes) -> UUID:
    path = "coverage_plan.candidate_key"
    fields = _parse(message, path, max_fields=32)
    area_key_message = _required_message(fields, 1, f"{path}.area_key")
    area_key_fields = _parse(area_key_message, f"{path}.area_key", max_fields=32)
    area_id_message = _required_message(
        area_key_fields,
        4,
        f"{path}.area_key.area_id",
    )
    return _decode_identifier(area_id_message, f"{path}.area_key.area_id")


def decode_coverage_plan(payload: bytes) -> DecodedCoveragePlan | None:
    """Decode an actionable plan, or ``None`` while idle or still selecting.

    Root geometry fields are intentionally skipped. If reports contain goals,
    every retained goal must be a complete standard-region goal in one
    partition; unsupported or malformed variants fail closed. The robot can
    publish those goals before choosing a current candidate region, so that
    transitional state remains non-actionable until a later update.
    """

    if not isinstance(payload, bytes):
        raise TypeError("coverage_plan payload must be bytes")
    if not payload:
        return None
    if len(payload) > _MAX_COVERAGE_PLAN_BYTES:
        raise CoverageDecodeError("coverage_plan exceeds the 8 MiB safety limit")

    fields = _parse(
        payload,
        "coverage_plan",
        max_fields=_MAX_ROOT_FIELDS,
    )
    reports_message = _optional_message(fields, 7, "coverage_plan.reports")
    mission_message = _optional_message(fields, 11, "coverage_plan.mission_id")
    candidate_message = _optional_message(
        fields,
        14,
        "coverage_plan.candidate_key",
    )
    if reports_message is None:
        return None

    reports_fields = _parse(
        reports_message,
        "coverage_plan.reports",
        max_fields=_MAX_REPORTS,
    )
    report_messages = _repeated_messages(
        reports_fields,
        1,
        "coverage_plan.reports.report",
    )
    if len(report_messages) > _MAX_REPORTS:
        raise CoverageDecodeError("coverage_plan contains too many reports")

    goals: list[CoveragePlanGoal] = []
    for index, report_message in enumerate(report_messages):
        report_path = f"coverage_plan.reports[{index}]"
        report_fields = _parse(report_message, report_path, max_fields=64)
        goal_message = _optional_message(
            report_fields,
            1,
            f"{report_path}.goal",
        )
        if goal_message is not None:
            goals.append(_decode_standard_goal(goal_message, index))
    if not goals:
        return None

    if mission_message is None:
        raise CoverageDecodeError("coverage_plan.mission_id is required")
    if candidate_message is None:
        return None

    goal_ids = [goal.goal_id for goal in goals]
    if len(goal_ids) != len(set(goal_ids)):
        raise CoverageDecodeError("coverage_plan goal IDs must be unique")
    partition_id = goals[0].partition_id
    if any(goal.partition_id != partition_id for goal in goals[1:]):
        raise CoverageDecodeError("coverage_plan goals span multiple partitions")

    return DecodedCoveragePlan(
        mission_id=_decode_mission_id(
            mission_message,
            "coverage_plan.mission_id",
        ),
        goals=CoverageGoals(tuple(goals), ordered=True),
        current_region_id=_decode_current_region(candidate_message),
    )


def decode_active_session_key(payload: bytes) -> DecodedActiveSessionKey | None:
    """Decode the current coverage session, or ``None`` when the robot is idle."""

    if not isinstance(payload, bytes):
        raise TypeError("active_session_key payload must be bytes")
    if not payload:
        return None
    fields = _parse(payload, "active_session_key", max_fields=32)
    mission_message = _required_message(
        fields,
        1,
        "active_session_key.mission_id",
    )
    session_message = _required_message(
        fields,
        2,
        "active_session_key.session_id",
    )
    return DecodedActiveSessionKey(
        mission_id=_decode_mission_id(
            mission_message,
            "active_session_key.mission_id",
        ),
        session_id=_decode_identifier(
            session_message,
            "active_session_key.session_id",
        ),
    )


async def fetch_reprioritization_snapshot(
    client: MaticClient,
    *,
    timeout: float | None = None,  # noqa: ASYNC109 - public timeout setting
) -> ReprioritizationSnapshot | None:
    """Wait for one coherent active-session and coverage-plan snapshot.

    The two collections are joined by mission ID. An explicit inactive-session
    value returns ``None``; stale or not-yet-matching values continue waiting.
    """

    timeout_seconds = client.config.operation_timeout if timeout is None else timeout
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or not math.isfinite(timeout_seconds)
        or timeout_seconds <= 0
    ):
        raise ValueError("timeout must be a positive number")

    session = TelemetrySession(
        client,
        ("coverage_plan", "active_session_key"),
    )
    plan: DecodedCoveragePlan | None | object = _UNSEEN
    active: DecodedActiveSessionKey | None | object = _UNSEEN
    try:
        async with asyncio.timeout(float(timeout_seconds)):
            async with session:
                async for update in session:
                    event = update.event
                    if event.target == "coverage_plan":
                        if event.operation is CollectionOperation.DELETE:
                            plan = None
                        elif event.payload is None:
                            raise CoverageDecodeError(
                                "coverage_plan UPSERT has no decodable payload"
                            )
                        else:
                            plan = decode_coverage_plan(event.payload)
                    elif event.target == "active_session_key":
                        if event.operation is CollectionOperation.DELETE:
                            active = None
                        elif event.payload is None:
                            raise CoverageDecodeError(
                                "active_session_key UPSERT has no decodable payload"
                            )
                        else:
                            active = decode_active_session_key(event.payload)
                        if active is None:
                            return None

                    if (
                        isinstance(plan, DecodedCoveragePlan)
                        and isinstance(active, DecodedActiveSessionKey)
                        and plan.mission_id == active.mission_id
                    ):
                        return ReprioritizationSnapshot(
                            goals=plan.goals,
                            mission_id=plan.mission_id,
                            current_region_id=plan.current_region_id,
                            current_session_id=active.session_id,
                        )
        raise ConnectionError(
            "coverage telemetry ended before a coherent snapshot arrived"
        )
    finally:
        await session.aclose()


__all__ = [
    "CoverageDecodeError",
    "DecodedActiveSessionKey",
    "DecodedCoveragePlan",
    "ReprioritizationSnapshot",
    "decode_active_session_key",
    "decode_coverage_plan",
    "fetch_reprioritization_snapshot",
]
