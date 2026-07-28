"""Hermes collection targets and reconstructed request/response envelopes."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import TYPE_CHECKING

from matic_sdk.protocol.wire import (
    ProtoWireError,
    bytes_values,
    encode_bytes_field,
    encode_varint_field,
    first_bytes,
    first_integer,
    parse_fields,
)

if TYPE_CHECKING:
    from matic_sdk.models.collections import FriendlyCollectionModel

MAP_TARGETS = (
    "map_compressed_rgb",
    "map_compressed_rgb_higher",
    "map_integrated",
    "map_combined_coverage",
    "map_semantics",
    "map_semantics_override",
)
CORE_TARGETS = (
    "latest_pose",
    "dock_detections",
    "displayed_mission",
    "active_session_key",
    "zones",
    "coverage_plan",
    "sink_summon_locations",
)
HISTORY_TARGETS = ("coverage_session_history", "recap_history")
ACTIVITY_TARGETS = (
    "approximate_trajectory",
    "coverage_corridor",
    "coverage_marker",
    "flythrough",
)
DEVICE_TARGETS = (
    "wifi_status",
    "motor_status",
    "kabuki_state",
    "current_version",
    "coverage_time",
    "update_state",
)
SETTINGS_TARGETS = (
    "petwaste_enabled_state",
    "child_lock_enabled_state",
    "requested_preview_release_state",
    "voice_enabled_state",
    "auto_record_voice_enabled_state",
    "matter_pairing_state",
    "rolling_recordings_config_state",
    "uploader_config_state",
    "user_tunnel_ssh_permission",
)
SCHEDULE_TARGETS = (
    "schedule_events",
    "schedule_event_previews",
    "sink_summons",
)
MEDIA_TARGETS = (
    "coverage_session_thumbnails",
    "scratch_recordings",
    "recording_thumbnails",
    "recording_videos",
)
ACCOUNT_TARGETS = ("app_customer_info",)
EXTRA_TARGETS = ("jukebox_state",)

TARGET_GROUPS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "maps": MAP_TARGETS,
        "core": CORE_TARGETS,
        "history": HISTORY_TARGETS,
        "activity": ACTIVITY_TARGETS,
        "device": DEVICE_TARGETS,
        "settings": SETTINGS_TARGETS,
        "schedule": SCHEDULE_TARGETS,
        "media": MEDIA_TARGETS,
        "account": ACCOUNT_TARGETS,
        "extra": EXTRA_TARGETS,
    }
)
KNOWN_TARGETS = tuple(target for values in TARGET_GROUPS.values() for target in values)
KNOWN_TARGET_SET = frozenset(KNOWN_TARGETS)
if len(KNOWN_TARGETS) != 43 or len(KNOWN_TARGET_SET) != 43:
    raise AssertionError("the verified collection inventory must contain 43 targets")


@dataclass(frozen=True, slots=True)
class SubscriptionConfig:
    """Values emitted by the official Android client's fresh subscription."""

    mode: int = 2
    batch_size: int = 20
    window_size: int = 1_000

    def encode(self) -> bytes:
        return b"".join(
            (
                encode_varint_field(3, self.mode),
                encode_varint_field(4, self.batch_size),
                encode_varint_field(5, self.window_size),
            )
        )


DEFAULT_SUBSCRIPTION_CONFIG = SubscriptionConfig()


def initial_request(
    target: str,
    *,
    config: SubscriptionConfig = DEFAULT_SUBSCRIPTION_CONFIG,
    fresh: bool = True,
) -> bytes:
    if not target or any(character.isspace() for character in target):
        raise ValueError("collection target must be a non-empty metadata token")
    initial = b"".join(
        (
            encode_bytes_field(1, target.encode("utf-8")),
            encode_bytes_field(3, config.encode()),
            encode_varint_field(4, int(fresh)),
        )
    )
    # CollectionRequest oneof field 1: InitialRequest.
    return encode_bytes_field(1, initial)


def sequence_acknowledgement(encoded_sequence_id: bytes) -> bytes:
    if not encoded_sequence_id:
        raise ValueError("cannot acknowledge an empty SequenceId")
    # CollectionRequest oneof field 2: the exact returned SequenceId.
    return encode_bytes_field(2, encoded_sequence_id)


class CollectionOperation(StrEnum):
    UPSERT = "upsert"
    DELETE = "delete"


@dataclass(frozen=True, slots=True)
class SequenceId:
    start_ts_nanos: int | None
    sequence_no: int | None
    encoded: bytes = field(repr=False)


@dataclass(frozen=True, slots=True)
class CollectionValue:
    """A schema-independent collection value wrapper."""

    payload: bytes | None = field(repr=False)
    value_id: bytes | None = field(default=None, repr=False)
    raw_message: bytes = field(default=b"", repr=False)
    encoding_field: int | None = None


@dataclass(frozen=True, slots=True)
class RawCollectionEvent:
    """Lossless collection envelope with a registered friendly decoder."""

    target: str
    operation: CollectionOperation
    key: bytes = field(repr=False)
    value: CollectionValue | None = field(repr=False)
    sequence_id: SequenceId | None
    received_at: datetime
    raw_response: bytes = field(repr=False)

    @property
    def payload(self) -> bytes | None:
        return self.value.payload if self.value else None

    def decode(self) -> FriendlyCollectionModel:
        """Return this event's registered friendly, lossless model."""

        from matic_sdk.collection_models import decode_collection_event

        return decode_collection_event(self)


def _unwrap_fast_bytes(message: bytes) -> bytes | None:
    try:
        chunks = bytes_values(parse_fields(message, max_fields=16), 1)
        return b"".join(chunks) if chunks else None
    except ProtoWireError:
        return None


def decode_collection_value(message: bytes) -> CollectionValue:
    fields = parse_fields(message)
    value_id = first_bytes(fields, 3)
    fast_messages = bytes_values(fields, 5)
    for fast_message in reversed(fast_messages):
        payload = _unwrap_fast_bytes(fast_message)
        if payload is not None:
            return CollectionValue(payload, value_id, message, 5)
    deprecated = bytes_values(fields, 4)
    if deprecated:
        return CollectionValue(b"".join(deprecated), value_id, message, 4)
    # Older values sometimes used field 3 directly for data.  A canonical
    # value identifier is 16 bytes, so only expose non-identifier data here.
    if value_id is not None and len(value_id) != 16:
        return CollectionValue(value_id, None, message, 3)
    return CollectionValue(None, value_id, message, None)


def decode_sequence_id(message: bytes) -> SequenceId:
    fields = parse_fields(message, max_fields=16)
    return SequenceId(
        start_ts_nanos=first_integer(fields, 1),
        sequence_no=first_integer(fields, 2),
        encoded=message,
    )


def decode_collection_response(
    target: str,
    response: bytes,
    *,
    received_at: datetime | None = None,
) -> RawCollectionEvent:
    fields = parse_fields(response)
    key = first_bytes(fields, 1) or b""
    value_message = first_bytes(fields, 2)
    sequence_message = first_bytes(fields, 3)
    value = (
        decode_collection_value(value_message) if value_message is not None else None
    )
    sequence = (
        decode_sequence_id(sequence_message) if sequence_message is not None else None
    )
    return RawCollectionEvent(
        target=target,
        operation=(
            CollectionOperation.UPSERT
            if value_message is not None
            else CollectionOperation.DELETE
        ),
        key=key,
        value=value,
        sequence_id=sequence,
        received_at=received_at or datetime.now(UTC),
        raw_response=response,
    )
