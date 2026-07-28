from __future__ import annotations

from hashlib import sha256
from uuid import UUID

import pytest

from matic_sdk.models.control import (
    AddZones,
    CoverageCleaningMode,
    CustomScheduleTarget,
    DrawnCircle,
    MapEnvironmentAction,
    MapEnvironmentCommand,
    MapPoint,
    MergeRooms,
    RemoveZones,
    RenameRoom,
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
    SinkSummonLocation,
    SinkSummonScheduleEvent,
    SplitRoom,
    StandardScheduleTarget,
    Weekday,
)
from matic_sdk.protocol.commands import (
    _VerifiedBuildPartitionCodec,
    _VerifiedEditScheduleCodec,
    encode_command,
)
from matic_sdk.protocol.wire import bytes_values, integer_values, parse_fields

REGION_ID = UUID("00112233-4455-6677-8899-aabbccddeeff")
SECOND_REGION_ID = UUID("ffeeddcc-bbaa-9988-7766-554433221100")
PARTITION_ID = UUID("10213243-5465-7687-98a9-babcbddcedfe")


def _single_bytes(payload: bytes, field_number: int) -> bytes:
    values = bytes_values(parse_fields(payload), field_number)
    assert len(values) == 1
    return values[0]


def test_build_partition_matches_native_generated_uuid_golden() -> None:
    command = MapEnvironmentCommand(
        MapEnvironmentAction.BUILD_PARTITION,
        mission_id=42,
        overwrite=True,
    )

    encoded = _VerifiedBuildPartitionCodec(lambda: UUID(int=0)).encode(command)

    assert encoded.hermes_target == "build_regions"
    assert encoded.payload.hex() == (
        "0a05152a00000012160a140a100000000000000000000000000000000012001a060a020a001001"
    )


@pytest.mark.parametrize(
    ("command", "target", "payload_hex"),
    [
        (
            MapEnvironmentCommand(
                MapEnvironmentAction.EDIT_NO_GO_ZONE,
                mission_id=42,
                change=AddZones((DrawnCircle(1.0, 2.0, 0.5),)),
            ),
            "nogo_command",
            "0a05152a000000221512130a110a0a0d000000c015000080bf150000003f",
        ),
        (
            MapEnvironmentCommand(
                MapEnvironmentAction.EDIT_DRIVE_ONLY_ZONE,
                mission_id=42,
                change=RemoveZones((7,)),
            ),
            "nogo_command",
            "0a05152a0000001a0107",
        ),
        (
            MapEnvironmentCommand(
                MapEnvironmentAction.EDIT_STAIRS,
                mission_id=42,
                change=RemoveZones((7,)),
            ),
            "stair_command",
            "0a05152a0000001a0107",
        ),
        (
            MapEnvironmentCommand(
                MapEnvironmentAction.EDIT_SEMANTICS_OVERRIDE,
                mission_id=42,
                change=SemanticsOverride(
                    (DrawnCircle(1.0, 2.0, 0.5),),
                    SemanticsOverrideKind.HARDFLOOR_ALLOW_WIRE,
                ),
            ),
            "semantics_override",
            ("0a05152a000000121910011a1512130a110a0a0d000000c015000080bf150000003f"),
        ),
        (
            MapEnvironmentCommand(
                MapEnvironmentAction.EDIT_SINK_SUMMON_LOCATION,
                mission_id=42,
            ),
            "edit_sink_summon_location",
            "1205152a000000",
        ),
        (
            MapEnvironmentCommand(
                MapEnvironmentAction.EDIT_SINK_SUMMON_LOCATION,
                mission_id=42,
                change=SinkSummonLocation(1.0, 2.0, 0.0),
            ),
            "edit_sink_summon_location",
            "0a1a0a05152a00000012110d000000c015000080bf220515000080bf",
        ),
    ],
)
def test_map_edits_match_native_goldens(
    command: MapEnvironmentCommand,
    target: str,
    payload_hex: str,
) -> None:
    encoded = encode_command(command, protocol_version=25)
    assert encoded.hermes_target == target
    assert encoded.payload.hex() == payload_hex


@pytest.mark.parametrize(
    ("change", "payload_hex"),
    [
        (
            RenameRoom(REGION_ID, RoomLabel.KITCHEN),
            (
                "0a05152a000000"
                "22280a260a108776655443322110feeddcbdbcbaa998"
                "12120910213243546576871198a9babcbddcedfe"
                "2a2e0a2812260a107766554433221100ffeeddccbbaa998"
                "81212090011223344556677118899aabbccddeeff"
                "12020804"
            ),
        ),
        (
            MergeRooms(REGION_ID, SECOND_REGION_ID, RoomLabel.KITCHEN),
            (
                "0a05152a000000"
                "22280a260a108776655443322110feeddcbdbcbaa998"
                "12120910213243546576871198a9babcbddcedfe"
                "32ac01"
                "0a2812260a107766554433221100ffeeddccbbaa998"
                "81212090011223344556677118899aabbccddeeff"
                "122812260a108899aabbccddeeff0011223344556677"
                "121209ffeeddccbbaa9988117766554433221100"
                "1a020804"
                "222812260a107766554433221100ffeeddccbbaa998"
                "81212090011223344556677118899aabbccddeeff"
                "222812260a108899aabbccddeeff0011223344556677"
                "121209ffeeddccbbaa9988117766554433221100"
            ),
        ),
        (
            SplitRoom(
                REGION_ID,
                MapPoint(1.0, 2.0),
                MapPoint(3.0, 4.0),
            ),
            (
                "0a05152a000000"
                "22280a260a108776655443322110feeddcbdbcbaa998"
                "12120910213243546576871198a9babcbddcedfe"
                "3a44"
                "0a2812260a107766554433221100ffeeddccbbaa998"
                "81212090011223344556677118899aabbccddeeff"
                "12180a0a0d000000c015000080bf"
                "120a0d000080c015000040c0"
            ),
        ),
    ],
)
def test_room_edits_match_native_goldens(
    change: RenameRoom | MergeRooms | SplitRoom,
    payload_hex: str,
) -> None:
    command = MapEnvironmentCommand(
        MapEnvironmentAction.EDIT_ROOMS,
        mission_id=42,
        partition_id=PARTITION_ID,
        change=change,
    )
    assert encode_command(command, protocol_version=25).payload.hex() == payload_hex


@pytest.mark.parametrize(
    ("action", "payload_hex"),
    [
        (
            ScheduleAction.REMOVE,
            (
                "1a310a05152a0000001a280a260a107766554433221100"
                "ffeeddccbbaa99881212090011223344556677118899aabbccddeeff"
            ),
        ),
        (
            ScheduleAction.TOGGLE,
            (
                "22310a05152a0000001a280a260a107766554433221100"
                "ffeeddccbbaa99881212090011223344556677118899aabbccddeeff"
            ),
        ),
    ],
)
def test_regular_schedule_key_commands_match_native_goldens(
    action: ScheduleAction,
    payload_hex: str,
) -> None:
    command = ScheduleCommand(
        action,
        key=ScheduleEventKey(42, REGION_ID),
    )
    assert encode_command(command, protocol_version=25).payload.hex() == payload_hex


def test_sink_schedule_add_matches_native_golden() -> None:
    event = SinkSummonScheduleEvent(
        weekdays=(Weekday.SUNDAY, Weekday.MONDAY, Weekday.FRIDAY),
        time=ScheduleTime(28_800, "America/Chicago", -21_600),
        duration=ScheduleDuration(600),
        enabled=True,
    )
    command = ScheduleCommand(
        ScheduleAction.SINK_SUMMON_ADD_OR_MODIFY,
        key=ScheduleEventKey(42, REGION_ID),
        sink_event=event,
    )

    encoded = encode_command(command, protocol_version=25)

    assert encoded.hermes_target == "edit_sink_summon_schedule"
    assert encoded.payload.hex() == (
        "0a6a"
        "0a310a280a260a107766554433221100ffeeddccbbaa998"
        "81212090011223344556677118899aabbccddeeff1205152a000000"
        "1235"
        "0a2c0a060801280138011a220880e101221c"
        "120f416d65726963612f4368696361676f"
        "18a0d7feffffffffffff01"
        "220308d804"
        "3001"
    )


def test_regular_schedule_add_matches_full_native_serializer_vector() -> None:
    # These goal IDs and the digest come from one offline execution of the
    # official ARM64 serializer. Reusing its generated IDs makes all 1,905
    # payload bytes deterministic without checking in an opaque binary fixture.
    goal_ids = iter(
        UUID(value)
        for value in (
            "60e97a74-ea33-48d6-9adf-c56a688b34a6",
            "2c287010-439c-4562-b4a2-7811533b0375",
            "7e61254d-2f66-47a0-b904-bf8e55ab5fb8",
            "1dfd54f6-1e66-4c70-8992-b443b73b23d4",
            "e240268a-11ec-4000-884f-485074dc6204",
            "fcd0a71a-27a9-4998-984e-53b6ee6083c3",
            "a1584f12-b676-4946-a00b-c4741658f7f4",
            "f1de666a-4e5d-47d9-935d-e978b1a34f79",
            "ebfacd1c-6d84-49ae-9bdd-5278ed24af1a",
            "082b1ebd-d5b2-4ef4-921e-641f9bca4ee5",
            "0e48db97-fc77-46a1-9284-bf97ecbfa297",
            "4e4e0739-e52b-499a-b751-e04441d2cc6d",
        )
    )
    event = ScheduleEvent(
        weekdays=(Weekday.MONDAY, Weekday.FRIDAY),
        time=ScheduleTime(28_800, "America/Chicago", -21_600),
        target=StandardScheduleTarget((REGION_ID,)),
        partition_id=PARTITION_ID,
        cleaning_mode=CoverageCleaningMode.BOTH,
        name="Morning",
        ordered=True,
        vacuum_setting=ScheduleCoverageSetting.QUICK,
        enabled_state=ScheduleEnabledState.DISABLED,
    )
    command = ScheduleCommand(
        ScheduleAction.ADD_OR_MODIFY,
        key=ScheduleEventKey(42, REGION_ID),
        event=event,
    )
    codec = _VerifiedEditScheduleCodec(
        ScheduleAction.ADD_OR_MODIFY,
        lambda: next(goal_ids),
    )

    encoded = codec.encode(command)

    assert encoded.hermes_target == "edit_schedule"
    assert len(encoded.payload) == 1_905
    assert sha256(encoded.payload).hexdigest() == (
        "a46964ea0e29dc3e6cc58e57b02a58048de749a85b5f5f1002f20da71fa935ad"
    )


def test_custom_mop_schedule_has_four_unordered_drawn_area_goals() -> None:
    event = ScheduleEvent(
        weekdays=(Weekday.TUESDAY,),
        time=ScheduleTime(0, "UTC", 0),
        target=CustomScheduleTarget((DrawnCircle(1.0, 2.0, 0.5),)),
        partition_id=PARTITION_ID,
        cleaning_mode=CoverageCleaningMode.MOP,
        vacuum_setting=None,
    )
    command = ScheduleCommand(
        ScheduleAction.ADD_OR_MODIFY,
        key=ScheduleEventKey(42, REGION_ID),
        event=event,
    )
    codec = _VerifiedEditScheduleCodec(
        ScheduleAction.ADD_OR_MODIFY,
        lambda: UUID(int=0),
    )

    payload = codec.encode(command).payload
    entry = _single_bytes(payload, 2)
    encoded_event = _single_bytes(entry, 2)
    goals = _single_bytes(encoded_event, 7)
    goal_values = bytes_values(parse_fields(goals), 2)

    assert len(goal_values) == 4
    for behavior, goal in enumerate(goal_values):
        header = _single_bytes(goal, 6)
        spec = _single_bytes(header, 3)
        assert integer_values(parse_fields(spec), 1) == (1,)
        assert integer_values(parse_fields(spec), 2) == (0,)
        assert integer_values(parse_fields(spec), 4) == (1,)
        assert integer_values(parse_fields(spec), 5) == (behavior,)

        target = _single_bytes(goal, 7)
        custom = _single_bytes(target, 2)
        drawn_area = _single_bytes(custom, 3)
        assert _single_bytes(drawn_area, 2)
        discriminator = _single_bytes(target, 3)
        assert _single_bytes(discriminator, 2) == b""


def test_zone_uuid_must_use_native_compact_u32_layout() -> None:
    command = MapEnvironmentCommand(
        MapEnvironmentAction.EDIT_NO_GO_ZONE,
        mission_id=42,
        change=RemoveZones((REGION_ID,)),
    )

    with pytest.raises(ValueError, match="compact u32 layout"):
        encode_command(command, protocol_version=25)
