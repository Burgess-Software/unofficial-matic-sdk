from __future__ import annotations

import struct
from datetime import UTC, datetime

from matic_sdk.collection_json import collection_model_to_dict
from matic_sdk.collection_models import (
    COLLECTION_DECODERS,
    COLLECTION_MODEL_TYPES,
    decode_collection_event,
    decode_collection_payload,
)
from matic_sdk.models.collections import (
    BinarySettingCollectionModel,
    JukeboxCollectionModel,
    MapTileCollectionModel,
    MediaCollectionModel,
    PoseCollectionModel,
    RobotStatusCollectionModel,
    ScheduleEventCollectionModel,
    StructuredCollectionModel,
    VersionCollectionModel,
)
from matic_sdk.models.control import JukeboxTrack
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
    status_payload = encode_bytes_field(1, states) + _float32(9, 0.73)

    status = decode_collection_payload("kabuki_state", status_payload)
    version = decode_collection_payload(
        "current_version",
        encode_bytes_field(1, b"v200.1")
        + encode_bytes_field(2, b"stable")
        + encode_varint_field(3, 26),
    )

    assert isinstance(status, RobotStatusCollectionModel)
    assert status.state_codes == (104, 120)
    assert status.activity == "paused"
    assert status.is_paused
    assert status.is_navigating
    assert status.battery_percentage == 73
    assert isinstance(version, VersionCollectionModel)
    assert version.version_name == "v200.1"
    assert version.profile_name == "stable"
    assert version.protocol_version == 26


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
