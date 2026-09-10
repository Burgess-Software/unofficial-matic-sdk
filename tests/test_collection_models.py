from __future__ import annotations

import struct
from datetime import UTC, datetime, timedelta

from matic_sdk.collection_json import collection_model_to_dict
from matic_sdk.collection_models import (
    COLLECTION_DECODERS,
    COLLECTION_MODEL_TYPES,
    decode_collection_event,
    decode_collection_payload,
)
from matic_sdk.models.collections import (
    AudioRecordingStateCollectionModel,
    BagPassCollectionModel,
    BinarySettingCollectionModel,
    BrushRollJamOutcome,
    CoverageHistoryCollectionModel,
    CuesGestureIntent,
    CuesGestureStatus,
    CuesIntentCategory,
    CuesPointToCleanIntentKind,
    CuesRecordingIntent,
    CuesTaskIntent,
    CuesVoiceStatus,
    DeepMopOverrideCollectionModel,
    JukeboxCollectionModel,
    MapTileCollectionModel,
    MediaCollectionModel,
    PoseCollectionModel,
    RobotStatusCollectionModel,
    RollingRecordingReason,
    ScheduleEventCollectionModel,
    SessionStopReason,
    StructuredCollectionModel,
    TimeZoneCollectionModel,
    VersionCollectionModel,
    WaterFlowOverrideCollectionModel,
)
from matic_sdk.models.control import AudioRecordingMode, JukeboxTrack
from matic_sdk.protocol.collections import (
    KNOWN_TARGET_SET,
    CollectionOperation,
    CollectionValue,
    RawCollectionEvent,
)
from matic_sdk.protocol.wire import (
    encode_bytes_field,
    encode_fixed32_field,
    encode_varint_field,
)
from tests._map_fixtures import (
    collection_event,
    floor_payload,
    page_key,
    vp8x_webp,
)


def _float32(number: int, value: float) -> bytes:
    bits = struct.unpack("<I", struct.pack("<f", value))[0]
    return encode_fixed32_field(number, bits)


def test_every_verified_target_has_a_friendly_decoder_and_model_type() -> None:
    assert frozenset(COLLECTION_DECODERS) == KNOWN_TARGET_SET
    assert frozenset(COLLECTION_MODEL_TYPES) == KNOWN_TARGET_SET

    for target, model_type in COLLECTION_MODEL_TYPES.items():
        decoded = decode_collection_payload(target, b"")
        assert isinstance(decoded, model_type)
        assert decoded.target == target
        assert decoded.operation is CollectionOperation.UPSERT
        assert decoded.raw_payload == b""


def test_unknown_target_uses_lossless_structured_model() -> None:
    payload = encode_varint_field(27, 9)

    decoded = decode_collection_payload("future_collection", payload)

    assert isinstance(decoded, StructuredCollectionModel)
    assert decoded.schema_name == "future_collection"
    assert decoded.fields[0].number == 27
    assert decoded.raw_payload == payload


def test_stable_172_live_captured_targets_have_typed_lossless_models() -> None:
    availability = decode_collection_payload(
        "voice_available",
        bytes.fromhex("0801"),
    )
    idle = decode_collection_payload(
        "user_audio_recording_state",
        bytes.fromhex("1200"),
    )

    assert isinstance(availability, BinarySettingCollectionModel)
    assert availability.enabled is True
    assert isinstance(idle, AudioRecordingStateCollectionModel)
    assert idle.mode is None
    assert idle.recording is False

    for mode_number, expected in enumerate(AudioRecordingMode, start=1):
        recording = decode_collection_payload(
            "user_audio_recording_state",
            bytes((0x08, mode_number)),
        )
        assert isinstance(recording, AudioRecordingStateCollectionModel)
        assert recording.mode is expected
        assert recording.recording is True

    future_mode_payload = bytes.fromhex("0863a00601")
    future_mode = decode_collection_payload(
        "user_audio_recording_state",
        future_mode_payload,
    )
    assert isinstance(future_mode, AudioRecordingStateCollectionModel)
    assert future_mode.mode == "unknown_99"
    assert future_mode.recording is True
    assert future_mode.raw_payload == future_mode_payload
    assert [field.number for field in future_mode.fields] == [1, 100]

    disabled = decode_collection_payload(
        "deep_mop_override_setting_state",
        bytes.fromhex("0a00"),
    )
    enabled = decode_collection_payload(
        "deep_mop_override_setting_state",
        bytes.fromhex("1200"),
    )
    assert isinstance(disabled, DeepMopOverrideCollectionModel)
    assert disabled.enabled is False
    assert isinstance(enabled, DeepMopOverrideCollectionModel)
    assert enabled.enabled is True

    default_flow = decode_collection_payload("water_flow_override_state", b"")
    neutral_flow = decode_collection_payload(
        "water_flow_override_state",
        bytes.fromhex("0a050d0000803f"),
    )
    assert isinstance(default_flow, WaterFlowOverrideCollectionModel)
    assert default_flow.factor == 0.0
    assert isinstance(neutral_flow, WaterFlowOverrideCollectionModel)
    assert neutral_flow.factor == 1.0

    time_zone_payload = bytes.fromhex(
        "0a1c120f416d65726963612f4368696361676f18a0d7feffffffffffff01"
    )
    time_zone = decode_collection_payload("time_zone", time_zone_payload)
    assert isinstance(time_zone, TimeZoneCollectionModel)
    assert time_zone.time_zone == "America/Chicago"
    assert time_zone.utc_offset == timedelta(hours=-6)
    assert time_zone.raw_payload == time_zone_payload


def test_stable_172_bag_pass_native_schema_is_typed_but_app_static() -> None:
    not_owned = decode_collection_payload("bag_pass_status", b"")
    started = encode_varint_field(1, 1_735_689_600)
    expires = encode_varint_field(1, 1_738_368_000)
    active_payload = encode_bytes_field(
        1,
        encode_bytes_field(1, started) + encode_bytes_field(2, expires),
    )
    owned = decode_collection_payload("bag_pass_status", active_payload)

    assert isinstance(not_owned, BagPassCollectionModel)
    assert not_owned.owned is False
    assert not_owned.started_at is None
    assert not_owned.expires_at is None
    assert isinstance(owned, BagPassCollectionModel)
    assert owned.owned is True
    assert owned.started_at == datetime(2025, 1, 1, tzinfo=UTC)
    assert owned.expires_at == datetime(2025, 2, 1, tzinfo=UTC)
    assert owned.raw_payload == active_payload


def test_raw_event_decode_convenience_preserves_operation_and_payload() -> None:
    payload = encode_varint_field(1, 1)
    event = RawCollectionEvent(
        target="child_lock_enabled_state",
        operation=CollectionOperation.UPSERT,
        key=b"",
        value=CollectionValue(payload),
        sequence_id=None,
        received_at=datetime.now(UTC),
        raw_response=b"",
    )

    decoded = event.decode()

    assert isinstance(decoded, BinarySettingCollectionModel)
    assert decoded.enabled is True
    assert decode_collection_event(event) == decoded


def test_pose_model_decodes_translation_rotation_and_timing() -> None:
    translation = _float32(1, 1.25) + _float32(2, -2.5) + _float32(3, 0.125)
    quaternion_xyz = _float32(1, 0.0) + _float32(2, 0.0) + _float32(3, 0.5)
    quaternion = encode_bytes_field(2, quaternion_xyz) + _float32(3, 0.866)
    pose = encode_bytes_field(4, translation) + encode_bytes_field(5, quaternion)
    pose_info = encode_bytes_field(1, pose)
    clock = encode_varint_field(1, 123_456)
    timestamp = encode_varint_field(1, 1_700_000_000) + encode_varint_field(
        2, 250_000_000
    )
    payload = (
        encode_bytes_field(2, pose_info)
        + encode_bytes_field(3, clock)
        + encode_bytes_field(5, encode_bytes_field(1, timestamp))
    )
    key = encode_fixed32_field(2, 0x12345678)

    decoded = decode_collection_payload("latest_pose", payload, key=key)

    assert isinstance(decoded, PoseCollectionModel)
    assert decoded.mission_id == 0x12345678
    assert decoded.pose is not None
    assert decoded.pose.translation.x == 1.25
    assert decoded.pose.translation.y == -2.5
    assert decoded.pose.rotation.z == 0.5
    assert decoded.monotonic_time_ns == 123_456
    assert decoded.observed_at == datetime(
        2023, 11, 14, 22, 13, 20, 250_000, tzinfo=UTC
    )


def test_map_model_reuses_the_proven_tile_decoder() -> None:
    payload = floor_payload({(2, 3): (10, 20, 30, 255)})
    encoded = collection_event(
        page_x=-2,
        page_y=3,
        payload=payload,
        sequence=7,
    )
    from matic_sdk.protocol.collections import decode_collection_response

    raw = decode_collection_response("map_compressed_rgb", encoded)
    decoded = raw.decode()

    assert isinstance(decoded, MapTileCollectionModel)
    assert (decoded.page_x, decoded.page_y) == (-2, 3)
    assert decoded.mission_id == 0x1234ABCD
    assert decoded.layers == ("floor-rgba",)
    assert decoded.tiles[0].image.getpixel((2, 3)) == (10, 20, 30, 255)


def test_robot_status_and_version_models_expose_live_control_feedback() -> None:
    states = bytes((104, 120))
    status_payload = (
        encode_bytes_field(1, states) + encode_varint_field(1, 211) + _float32(9, 0.73)
    )

    status = decode_collection_payload("kabuki_state", status_payload)
    version = decode_collection_payload(
        "current_version",
        encode_bytes_field(1, b"v200.1")
        + encode_bytes_field(2, b"stable")
        + encode_varint_field(3, 26),
    )

    assert isinstance(status, RobotStatusCollectionModel)
    assert status.state_codes == (104, 120, 211)
    assert status.activity == "paused"
    assert status.is_paused
    assert status.is_navigating
    assert status.battery_percentage == 73
    assert status.is_recording
    assert status.is_following_person is None
    assert status.voice_status is None
    assert status.gesture_status is None
    assert isinstance(version, VersionCollectionModel)
    assert version.version_name == "v200.1"
    assert version.profile_name == "stable"
    assert version.protocol_version == 26


def test_app_175_cues_and_diagnostic_enums_are_public_string_values() -> None:
    assert CuesVoiceStatus.LISTENING_FOR_WAKE_WORD == "listening_for_wake_word"
    assert CuesGestureStatus.POINTED_TARGET_ACCEPTED == "pointed_target_accepted"
    assert CuesIntentCategory.GESTURE == "gesture"
    assert CuesTaskIntent.REDO_LAST_CLEAN == "redo_last_clean"
    assert CuesGestureIntent.FOLLOW_PERSON == "follow_person"
    assert CuesGestureStatus.FOLLOWING_PAUSED == "following_paused"
    assert CuesGestureStatus.MOVING_CLOSER == "moving_closer"
    assert CuesGestureStatus.MOVING_BACK == "moving_back"
    assert CuesPointToCleanIntentKind.STAIN == "stain"
    assert CuesRecordingIntent.RECORD_DOA == "record_doa"
    assert RollingRecordingReason.BOT_LOST == "bot_lost"
    assert RollingRecordingReason.TILTED == "tilted"
    assert BrushRollJamOutcome.INSUFFICIENT_CHARGING == "insufficient_charging"


def test_history_decodes_confirmed_app_175_stop_reason_variants() -> None:
    for variant, expected in (
        (5, SessionStopReason.FINISHED),
        (8, SessionStopReason.USER_CANCELLED),
    ):
        event = encode_bytes_field(10, encode_bytes_field(variant, b""))
        history = decode_collection_payload(
            "coverage_session_history",
            encode_bytes_field(5, event),
        )

        assert isinstance(history, CoverageHistoryCollectionModel)
        assert history.outcome is not None
        assert history.outcome.stop_reason is expected
        assert history.outcome.completion_kind is None


def test_history_preserves_unmapped_stop_reason_variant() -> None:
    event = encode_bytes_field(10, encode_bytes_field(12, b""))
    history = decode_collection_payload(
        "coverage_session_history",
        encode_bytes_field(5, event),
    )

    assert isinstance(history, CoverageHistoryCollectionModel)
    assert history.outcome is not None
    assert history.outcome.stop_reason == "unknown_variant_12"


def test_schedule_and_media_models_have_named_fields() -> None:
    weekdays = encode_varint_field(2, 1) + encode_varint_field(6, 1)
    schedule_time = encode_varint_field(1, 8 * 60 + 30)
    details = encode_bytes_field(1, weekdays) + encode_bytes_field(3, schedule_time)
    schedule_payload = encode_bytes_field(1, details) + encode_bytes_field(
        2, b"synthetic schedule"
    )
    schedule = decode_collection_payload(
        "schedule_events",
        schedule_payload,
        key=page_key(0, 0),
    )

    image = vp8x_webp(64, 48)
    media = decode_collection_payload(
        "coverage_session_thumbnails",
        encode_bytes_field(2, encode_bytes_field(1, image)),
    )

    assert isinstance(schedule, ScheduleEventCollectionModel)
    assert schedule.weekdays == ("monday", "friday")
    assert schedule.minutes_after_midnight == 510
    assert isinstance(media, MediaCollectionModel)
    assert len(media.assets) == 1
    assert (media.assets[0].width, media.assets[0].height) == (64, 48)
    assert "VP8X" not in repr(media)


def test_jukebox_model_uses_typed_tracks_and_preserves_unknown_values() -> None:
    selected = decode_collection_payload(
        "jukebox_state",
        encode_varint_field(1, 2),
    )
    future = decode_collection_payload(
        "jukebox_state",
        encode_varint_field(1, 9),
    )
    stopped = decode_collection_payload("jukebox_state", b"")

    assert isinstance(selected, JukeboxCollectionModel)
    assert selected.track is JukeboxTrack.JINGLE_BELLS
    assert isinstance(future, JukeboxCollectionModel)
    assert future.track == "unknown_9"
    assert isinstance(stopped, JukeboxCollectionModel)
    assert stopped.track is None


def test_sensitive_friendly_fields_are_hidden_from_repr() -> None:
    email = "owner@example.invalid"
    ssid = "private-network"

    customer = decode_collection_payload(
        "app_customer_info",
        encode_bytes_field(1, email.encode()),
    )
    wifi = decode_collection_payload(
        "wifi_status",
        encode_varint_field(1, 2) + encode_bytes_field(10, ssid.encode()),
    )

    assert email not in repr(customer)
    assert ssid not in repr(wifi)


def test_json_view_is_lossless_in_memory_but_safe_for_terminal_output() -> None:
    image = vp8x_webp(64, 48)
    media = decode_collection_payload(
        "coverage_session_thumbnails",
        encode_bytes_field(2, encode_bytes_field(1, image)),
    )

    encoded = collection_model_to_dict(media)

    assert encoded["type"] == "MediaCollectionModel"
    assert "raw_payload" not in encoded
    assert "fields" not in encoded
    assets = encoded["assets"]
    assert isinstance(assets, list)
    assert assets[0]["byte_count"] == len(image)
    assert assets[0]["sha256"] == "[REDACTED]"
    assert "data" not in assets[0]
