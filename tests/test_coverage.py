from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

import pytest

import matic_sdk.coverage as coverage_module
from matic_sdk.client import MaticClient
from matic_sdk.coverage import (
    CoverageDecodeError,
    decode_active_session_key,
    decode_coverage_plan,
    fetch_reprioritization_snapshot,
)
from matic_sdk.models.control import (
    CleaningFloor,
    CoverageBehavior,
    CoverageGoalCleaningMode,
    CoverageGoalSetting,
    ReprioritizeAction,
    ReprioritizeCoverageCommand,
)
from matic_sdk.protocol.collections import (
    CollectionOperation,
    CollectionValue,
    RawCollectionEvent,
)
from matic_sdk.protocol.commands import _VerifiedReprioritizeCoverageCodec
from matic_sdk.protocol.wire import (
    WireType,
    bytes_values,
    encode_bytes_field,
    encode_fixed32_field,
    encode_fixed64_field,
    encode_tag,
    encode_varint_field,
    parse_fields,
)

MISSION_ID = 0x12345678
PARTITION_ID = UUID("11111111-2222-3333-4444-555555555555")
REGION_A = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
REGION_B = UUID("01234567-89ab-cdef-0123-456789abcdef")
GOAL_A = UUID("10000000-0000-0000-0000-000000000001")
GOAL_B = UUID("10000000-0000-0000-0000-000000000002")
SESSION_ID = UUID("fedcba98-7654-3210-fedc-ba9876543210")


def _uuid_message(value: UUID, *, legacy: bool = False) -> bytes:
    if legacy:
        return encode_bytes_field(1, value.bytes)
    high = value.int >> 64
    low = value.int & ((1 << 64) - 1)
    fixed = encode_fixed64_field(1, high) + encode_fixed64_field(2, low)
    return encode_bytes_field(2, fixed)


def _identifier(value: UUID, *, legacy: bool = False) -> bytes:
    return encode_bytes_field(2, _uuid_message(value, legacy=legacy))


def _mission_id(value: int) -> bytes:
    return encode_fixed32_field(2, value)


def _active_session_payload(
    *,
    mission_id: int = MISSION_ID,
    session_id: UUID = SESSION_ID,
    legacy_session_id: bool = False,
) -> bytes:
    return encode_bytes_field(1, _mission_id(mission_id)) + encode_bytes_field(
        2,
        _identifier(session_id, legacy=legacy_session_id),
    )


def _spec(
    *,
    setting: int = 1,
    floor: int = 0,
    cleaning_mode: int = 0,
    behavior: int = 0,
    omit: int | None = None,
) -> bytes:
    values = {
        1: setting,
        2: floor,
        4: cleaning_mode,
        5: behavior,
    }
    return b"".join(
        encode_varint_field(number, value)
        for number, value in values.items()
        if number != omit
    )


def _goal(
    goal_id: UUID,
    region_id: UUID,
    *,
    partition_id: UUID = PARTITION_ID,
    spec: bytes | None = None,
    selection_kind: bytes | None = None,
    custom_region: bool = False,
) -> bytes:
    round_key = encode_bytes_field(2, _uuid_message(goal_id))
    header = encode_bytes_field(1, round_key) + encode_bytes_field(
        3,
        _spec() if spec is None else spec,
    )
    partition = encode_bytes_field(1, _uuid_message(partition_id))
    kind = encode_bytes_field(1, b"") if selection_kind is None else selection_kind
    region = (
        encode_bytes_field(2, b"") if custom_region else b""
    ) + encode_bytes_field(3, _identifier(region_id))
    target = b"".join(
        (
            encode_bytes_field(1, partition),
            encode_bytes_field(2, kind),
            encode_bytes_field(3, region),
        )
    )
    return encode_bytes_field(6, header) + encode_bytes_field(7, target)


def _candidate_key(region_id: UUID) -> bytes:
    area_key = encode_bytes_field(4, _identifier(region_id))
    return encode_bytes_field(1, area_key)


def _coverage_plan_from_goal_messages(
    goals: tuple[bytes, ...],
    *,
    mission_id: int | None = MISSION_ID,
    candidate_region_id: UUID | None = REGION_A,
    extra_reports: tuple[bytes, ...] = (),
) -> bytes:
    reports = b"".join(
        encode_bytes_field(1, report)
        for report in (
            *(encode_bytes_field(1, goal) for goal in goals),
            *extra_reports,
        )
    )
    root = encode_bytes_field(7, reports)
    if mission_id is not None:
        root += encode_bytes_field(11, _mission_id(mission_id))
    if candidate_region_id is not None:
        root += encode_bytes_field(14, _candidate_key(candidate_region_id))
    return root


def _positive_plan_payload(*, mission_id: int = MISSION_ID) -> bytes:
    return _coverage_plan_from_goal_messages(
        (
            _goal(GOAL_A, REGION_A),
            _goal(
                GOAL_B,
                REGION_B,
                spec=_spec(
                    setting=2,
                    floor=1,
                    cleaning_mode=0,
                    behavior=3,
                ),
            ),
        ),
        mission_id=mission_id,
        extra_reports=(encode_bytes_field(3, _identifier(REGION_A)),),
    )


def _only_message(message: bytes, field_number: int) -> bytes:
    values = bytes_values(parse_fields(message), field_number)
    assert len(values) == 1
    return values[0]


def _commanded_goal_messages(payload: bytes) -> tuple[bytes, ...]:
    user_command = _only_message(payload, 15)
    coverage_task = _only_message(user_command, 1)
    coverage = _only_message(coverage_task, 3)
    commanded_goals = _only_message(coverage, 5)
    fields = parse_fields(commanded_goals)
    assert not bytes_values(fields, 2)
    return bytes_values(fields, 1)


def _event(
    target: str,
    payload: bytes | None,
    *,
    operation: CollectionOperation = CollectionOperation.UPSERT,
) -> RawCollectionEvent:
    value = (
        CollectionValue(payload=payload)
        if operation is CollectionOperation.UPSERT
        else None
    )
    return RawCollectionEvent(
        target=target,
        operation=operation,
        key=b"",
        value=value,
        sequence_id=None,
        received_at=datetime.now(UTC),
        raw_response=b"synthetic",
    )


def _install_fake_telemetry(
    monkeypatch: pytest.MonkeyPatch,
    events: tuple[RawCollectionEvent, ...],
) -> list[object]:
    instances: list[object] = []

    class FakeTelemetrySession:
        def __init__(self, client: object, targets: tuple[str, ...]) -> None:
            del client
            assert targets == ("coverage_plan", "active_session_key")
            self.events = events
            self.index = 0
            self.closed = False
            instances.append(self)

        async def __aenter__(self) -> FakeTelemetrySession:
            return self

        async def __aexit__(self, *_: object) -> None:
            await self.aclose()

        def __aiter__(self) -> FakeTelemetrySession:
            return self

        async def __anext__(self) -> object:
            if self.index < len(self.events):
                event = self.events[self.index]
                self.index += 1
                return SimpleNamespace(event=event)
            await asyncio.Future()
            raise AssertionError("unreachable")

        async def aclose(self) -> None:
            self.closed = True

    monkeypatch.setattr(coverage_module, "TelemetrySession", FakeTelemetrySession)
    return instances


def _client(*, operation_timeout: float = 0.2) -> object:
    return SimpleNamespace(config=SimpleNamespace(operation_timeout=operation_timeout))


@pytest.mark.asyncio
async def test_client_snapshot_method_delegates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[object, float | None]] = []

    async def fake_fetch(
        client: object,
        *,
        timeout: float | None,  # noqa: ASYNC109 - mirrors the public method
    ) -> None:
        calls.append((client, timeout))

    monkeypatch.setattr(
        coverage_module,
        "fetch_reprioritization_snapshot",
        fake_fetch,
    )
    client = object.__new__(MaticClient)

    assert await client.reprioritization_snapshot(timeout=1.5) is None
    assert calls == [(client, 1.5)]


@pytest.mark.parametrize("legacy", [False, True], ids=["canonical", "legacy"])
def test_decode_active_session_key_accepts_verified_uuid_forms(legacy: bool) -> None:
    decoded = decode_active_session_key(
        _active_session_payload(legacy_session_id=legacy)
    )

    assert decoded is not None
    assert decoded.mission_id == MISSION_ID
    assert decoded.session_id == SESSION_ID


def test_decode_active_session_key_empty_is_inactive() -> None:
    assert decode_active_session_key(b"") is None


@pytest.mark.parametrize(
    "payload",
    [
        b"\x0a\x05\x15",
        encode_tag(1, WireType.VARINT) + b"\x01",
        encode_bytes_field(1, _mission_id(MISSION_ID)),
    ],
    ids=["truncated", "wrong-wire-type", "missing-session"],
)
def test_decode_active_session_key_rejects_malformed_payloads(payload: bytes) -> None:
    with pytest.raises(CoverageDecodeError):
        decode_active_session_key(payload)


def test_decode_active_session_key_rejects_ambiguous_uuid() -> None:
    uuid_value = _uuid_message(SESSION_ID)
    ambiguous = encode_bytes_field(1, SESSION_ID.bytes) + uuid_value
    payload = encode_bytes_field(1, _mission_id(MISSION_ID)) + encode_bytes_field(
        2,
        encode_bytes_field(2, ambiguous),
    )

    with pytest.raises(CoverageDecodeError, match="both legacy and fixed"):
        decode_active_session_key(payload)


def test_decode_positive_coverage_plan() -> None:
    decoded = decode_coverage_plan(_positive_plan_payload())

    assert decoded is not None
    assert decoded.mission_id == MISSION_ID
    assert decoded.current_region_id == REGION_A
    assert decoded.goals.ordered is True
    assert [goal.goal_id for goal in decoded.goals.goals] == [GOAL_A, GOAL_B]
    assert [goal.region_id for goal in decoded.goals.goals] == [REGION_A, REGION_B]
    assert {goal.partition_id for goal in decoded.goals.goals} == {PARTITION_ID}
    assert decoded.goals.goals[0].spec.setting is CoverageGoalSetting.STANDARD
    assert decoded.goals.goals[0].spec.floor is CleaningFloor.HARD_FLOOR
    assert decoded.goals.goals[0].spec.cleaning_mode is CoverageGoalCleaningMode.VACUUM
    assert decoded.goals.goals[0].spec.behavior is CoverageBehavior.INTERIOR
    assert decoded.goals.goals[1].spec.setting is CoverageGoalSetting.QUICK
    assert decoded.goals.goals[1].spec.floor is CleaningFloor.CARPET
    assert decoded.goals.goals[1].spec.behavior is CoverageBehavior.TRANSITION


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        encode_bytes_field(10, b"opaque geometry"),
        encode_bytes_field(7, b""),
        _coverage_plan_from_goal_messages(
            (),
            extra_reports=(encode_bytes_field(3, _identifier(REGION_A)),),
        ),
    ],
    ids=["empty", "no-reports", "empty-reports", "finished-only-report"],
)
def test_decode_idle_coverage_plan(payload: bytes) -> None:
    assert decode_coverage_plan(payload) is None


@pytest.mark.parametrize(
    "spec",
    [
        _spec(setting=99),
        _spec(floor=99),
        _spec(cleaning_mode=99),
        _spec(behavior=99),
    ],
    ids=["setting", "floor", "cleaning-mode", "behavior"],
)
def test_decode_coverage_plan_rejects_unknown_enums(spec: bytes) -> None:
    payload = _coverage_plan_from_goal_messages((_goal(GOAL_A, REGION_A, spec=spec),))

    with pytest.raises(CoverageDecodeError, match="unknown enum"):
        decode_coverage_plan(payload)


def test_decode_coverage_plan_rejects_missing_spec_field() -> None:
    payload = _coverage_plan_from_goal_messages(
        (_goal(GOAL_A, REGION_A, spec=_spec(omit=5)),)
    )

    with pytest.raises(CoverageDecodeError, match=r"spec\.behavior is required"):
        decode_coverage_plan(payload)


def test_decode_coverage_plan_rejects_duplicate_goal_id() -> None:
    payload = _coverage_plan_from_goal_messages(
        (_goal(GOAL_A, REGION_A), _goal(GOAL_A, REGION_B))
    )

    with pytest.raises(CoverageDecodeError, match="goal IDs must be unique"):
        decode_coverage_plan(payload)


def test_decode_coverage_plan_rejects_multiple_partitions() -> None:
    payload = _coverage_plan_from_goal_messages(
        (
            _goal(GOAL_A, REGION_A),
            _goal(
                GOAL_B,
                REGION_B,
                partition_id=UUID("22222222-3333-4444-5555-666666666666"),
            ),
        )
    )

    with pytest.raises(CoverageDecodeError, match="multiple partitions"):
        decode_coverage_plan(payload)


def test_decode_coverage_plan_rejects_mopping_carpet() -> None:
    payload = _coverage_plan_from_goal_messages(
        (
            _goal(
                GOAL_A,
                REGION_A,
                spec=_spec(floor=1, cleaning_mode=1),
            ),
        )
    )

    with pytest.raises(CoverageDecodeError, match="mopping on carpet"):
        decode_coverage_plan(payload)


@pytest.mark.parametrize(
    "goal",
    [
        _goal(
            GOAL_A,
            REGION_A,
            selection_kind=(encode_bytes_field(1, b"") + encode_bytes_field(3, b"")),
        ),
        _goal(GOAL_A, REGION_A, custom_region=True),
    ],
    ids=["mixed-selection-kind", "custom-region"],
)
def test_decode_coverage_plan_rejects_mixed_or_custom_targets(goal: bytes) -> None:
    with pytest.raises(CoverageDecodeError):
        decode_coverage_plan(_coverage_plan_from_goal_messages((goal,)))


@pytest.mark.parametrize(
    ("mission_id", "candidate_region_id", "message"),
    [
        (None, REGION_A, "mission_id is required"),
        (MISSION_ID, None, "candidate_key is required"),
    ],
)
def test_decode_coverage_plan_rejects_missing_plan_identity(
    mission_id: int | None,
    candidate_region_id: UUID | None,
    message: str,
) -> None:
    payload = _coverage_plan_from_goal_messages(
        (_goal(GOAL_A, REGION_A),),
        mission_id=mission_id,
        candidate_region_id=candidate_region_id,
    )

    with pytest.raises(CoverageDecodeError, match=message):
        decode_coverage_plan(payload)


@pytest.mark.parametrize(
    "plan_first", [True, False], ids=["plan-first", "session-first"]
)
@pytest.mark.asyncio
async def test_snapshot_assembler_accepts_either_collection_order(
    monkeypatch: pytest.MonkeyPatch,
    plan_first: bool,
) -> None:
    plan_event = _event("coverage_plan", _positive_plan_payload())
    active_event = _event("active_session_key", _active_session_payload())
    events = (plan_event, active_event) if plan_first else (active_event, plan_event)
    instances = _install_fake_telemetry(monkeypatch, events)

    snapshot = await fetch_reprioritization_snapshot(_client())

    assert snapshot is not None
    assert snapshot.mission_id == MISSION_ID
    assert snapshot.current_region_id == REGION_A
    assert snapshot.current_session_id == SESSION_ID
    assert snapshot.region_ids == (REGION_A, REGION_B)
    assert instances and instances[0].closed is True


@pytest.mark.asyncio
async def test_snapshot_assembler_waits_for_mission_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stale_session_id = UUID("00000000-0000-0000-0000-000000000009")
    events = (
        _event("active_session_key", _active_session_payload(mission_id=9)),
        _event("coverage_plan", _positive_plan_payload()),
        _event(
            "active_session_key",
            _active_session_payload(session_id=stale_session_id),
        ),
    )
    _install_fake_telemetry(monkeypatch, events)

    snapshot = await fetch_reprioritization_snapshot(_client())

    assert snapshot is not None
    assert snapshot.mission_id == MISSION_ID
    assert snapshot.current_session_id == stale_session_id


@pytest.mark.asyncio
async def test_snapshot_assembler_replaces_stale_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events = (
        _event("active_session_key", _active_session_payload()),
        _event("coverage_plan", _positive_plan_payload(mission_id=9)),
        _event("coverage_plan", _positive_plan_payload()),
    )
    _install_fake_telemetry(monkeypatch, events)

    snapshot = await fetch_reprioritization_snapshot(_client())

    assert snapshot is not None
    assert snapshot.mission_id == MISSION_ID


@pytest.mark.asyncio
async def test_snapshot_assembler_replaces_deleted_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events = (
        _event("active_session_key", _active_session_payload()),
        _event(
            "coverage_plan",
            None,
            operation=CollectionOperation.DELETE,
        ),
        _event("coverage_plan", _positive_plan_payload()),
    )
    _install_fake_telemetry(monkeypatch, events)

    snapshot = await fetch_reprioritization_snapshot(_client())

    assert snapshot is not None
    assert snapshot.mission_id == MISSION_ID


@pytest.mark.parametrize(
    "inactive_event",
    [
        _event(
            "active_session_key",
            None,
            operation=CollectionOperation.DELETE,
        ),
        _event("active_session_key", b""),
    ],
    ids=["delete", "empty-upsert"],
)
@pytest.mark.asyncio
async def test_snapshot_assembler_returns_none_for_inactive_session(
    monkeypatch: pytest.MonkeyPatch,
    inactive_event: RawCollectionEvent,
) -> None:
    instances = _install_fake_telemetry(
        monkeypatch,
        (_event("coverage_plan", _positive_plan_payload()), inactive_event),
    )

    assert await fetch_reprioritization_snapshot(_client()) is None
    assert instances and instances[0].closed is True


@pytest.mark.asyncio
async def test_snapshot_assembler_rejects_upsert_without_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instances = _install_fake_telemetry(
        monkeypatch,
        (_event("active_session_key", None),),
    )

    with pytest.raises(CoverageDecodeError, match="no decodable payload"):
        await fetch_reprioritization_snapshot(_client())

    assert instances and instances[0].closed is True


@pytest.mark.asyncio
async def test_snapshot_assembler_timeout_closes_telemetry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instances = _install_fake_telemetry(
        monkeypatch,
        (
            _event("active_session_key", _active_session_payload(mission_id=9)),
            _event("coverage_plan", _positive_plan_payload()),
        ),
    )

    with pytest.raises(TimeoutError):
        await fetch_reprioritization_snapshot(_client(), timeout=0.01)

    assert instances and instances[0].closed is True


def test_reprioritize_codec_roundtrip_preserves_decoded_goals() -> None:
    decoded = decode_coverage_plan(_positive_plan_payload())
    assert decoded is not None
    codec = _VerifiedReprioritizeCoverageCodec(
        command_id_factory=lambda: UUID("99999999-8888-7777-6666-555555555555")
    )
    command = ReprioritizeCoverageCommand(
        action=ReprioritizeAction.PRIORITIZE,
        mission_id=decoded.mission_id,
        goals=decoded.goals,
        current_region_id=UUID("ffffffff-ffff-ffff-ffff-ffffffffffff"),
        selected_region_id=REGION_B,
        current_session_id=SESSION_ID,
    )

    encoded = codec.encode(command)
    emitted_goals = _commanded_goal_messages(encoded.payload)
    roundtripped = decode_coverage_plan(
        _coverage_plan_from_goal_messages(
            emitted_goals,
            mission_id=decoded.mission_id,
            candidate_region_id=decoded.current_region_id,
        )
    )

    assert roundtripped is not None
    assert roundtripped.goals == decoded.goals
    assert roundtripped.current_region_id == decoded.current_region_id
