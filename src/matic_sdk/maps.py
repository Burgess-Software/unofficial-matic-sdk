"""Decode Hermes map collection events and assemble 32x32 map mosaics.

The decoder intentionally operates on bytes already received by a transport.
It contains no networking, device identifiers, or credentials.  Known fields
come from the Android client's protobuf types; bounded wire parsing is used for
the compatible legacy wrappers seen in released devices.
"""

from __future__ import annotations

import math
import struct
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, get_args

from PIL import Image

from matic_sdk._private import ensure_private_directory, open_new_private

from .models.maps import (
    CollectionEvent,
    DecodedMapEvent,
    MapBounds,
    MapCellValue,
    MapClassification,
    MapMosaic,
    MapOrientation,
    MapTarget,
    MapTile,
    MapValueKind,
)

TILE_SIZE = 32
PIXELS_PER_TILE = TILE_SIZE * TILE_SIZE
MAX_PROTO_FIELDS = 100_000
MAX_PROTO_DEPTH = 12
SUPPORTED_MAP_TARGETS = frozenset(get_args(MapTarget))


class MapDecodeError(ValueError):
    """Raised when a map message cannot be decoded without guessing."""


@dataclass(frozen=True, slots=True)
class _WireField:
    number: int
    wire_type: int
    value: int | bytes


@dataclass(frozen=True, slots=True)
class _Blob:
    path: tuple[int, ...]
    data: bytes


def _decode_varint(data: bytes, offset: int) -> tuple[int, int]:
    value = 0
    shift = 0
    while offset < len(data) and shift < 70:
        byte = data[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        if byte < 0x80:
            return value, offset
        shift += 7
    raise MapDecodeError("truncated or oversized protobuf varint")


def _parse_message(
    data: bytes, *, max_fields: int = MAX_PROTO_FIELDS
) -> list[_WireField]:
    fields: list[_WireField] = []
    offset = 0
    while offset < len(data):
        if len(fields) >= max_fields:
            raise MapDecodeError(f"protobuf message exceeds {max_fields} fields")
        tag, offset = _decode_varint(data, offset)
        number = tag >> 3
        wire_type = tag & 7
        if number == 0:
            raise MapDecodeError("protobuf field number zero")
        value: int | bytes
        if wire_type == 0:
            value, offset = _decode_varint(data, offset)
        elif wire_type in (1, 5):
            size = 8 if wire_type == 1 else 4
            end = offset + size
            if end > len(data):
                raise MapDecodeError(f"truncated fixed{size * 8} field")
            value = data[offset:end]
            offset = end
        elif wire_type == 2:
            length, offset = _decode_varint(data, offset)
            end = offset + length
            if end > len(data):
                raise MapDecodeError("truncated length-delimited field")
            value = data[offset:end]
            offset = end
        else:
            raise MapDecodeError(f"unsupported protobuf wire type {wire_type}")
        fields.append(_WireField(number, wire_type, value))
    return fields


def _parsed_or_none(data: bytes) -> list[_WireField] | None:
    if not data:
        return []
    try:
        fields = _parse_message(data)
    except MapDecodeError:
        return None
    # Raw image buffers can accidentally parse as protobuf.  Private schemas
    # used here have small field numbers, so reject implausibly large tags.
    if fields and max(field.number for field in fields) > 512:
        return None
    return fields


def _length_fields(fields: Sequence[_WireField], number: int) -> list[bytes]:
    return [
        field.value
        for field in fields
        if field.number == number
        and field.wire_type == 2
        and isinstance(field.value, bytes)
    ]


def _first_length(fields: Sequence[_WireField], number: int) -> bytes | None:
    values = _length_fields(fields, number)
    return values[0] if values else None


def _first_integer(fields: Sequence[_WireField], number: int) -> int | None:
    for field in fields:
        if field.number != number:
            continue
        if field.wire_type == 0 and isinstance(field.value, int):
            return field.value
        if field.wire_type == 5 and isinstance(field.value, bytes):
            return struct.unpack("<I", field.value)[0]
        if field.wire_type == 1 and isinstance(field.value, bytes):
            return struct.unpack("<Q", field.value)[0]
    return None


def _signed_int32(value: int | None) -> int | None:
    if value is None:
        return None
    value &= 0xFFFF_FFFF
    return value - 0x1_0000_0000 if value & 0x8000_0000 else value


def _decode_sint32(value: int | None) -> int | None:
    if value is None:
        return None
    value &= 0xFFFF_FFFF
    return (value >> 1) ^ -(value & 1)


def _decode_mission_id(data: bytes | None) -> int | None:
    fields = _parsed_or_none(data or b"")
    if fields is None:
        return None
    current = _first_integer(fields, 2)
    return current if current is not None else _first_integer(fields, 1)


def _decode_page_and_mission(data: bytes) -> tuple[int | None, int | None, int | None]:
    fields = _parsed_or_none(data)
    if fields is None:
        return None, None, None
    page_data = _first_length(fields, 1)
    mission = _decode_mission_id(_first_length(fields, 2))
    if page_data is None:
        return None, None, mission
    page = _parsed_or_none(page_data)
    if page is None:
        return None, None, mission

    # Current PageIndex uses ZigZag sint32 fields 3/4.  Ordinary signed fields
    # 1/2 are retained for old captures.  Omitted proto3 coordinates are zero.
    x = _decode_sint32(_first_integer(page, 3))
    y = _decode_sint32(_first_integer(page, 4))
    if x is None:
        x = _signed_int32(_first_integer(page, 1))
    if y is None:
        y = _signed_int32(_first_integer(page, 2))
    return x if x is not None else 0, y if y is not None else 0, mission


def _unwrap_fast_bytes(data: bytes) -> bytes | None:
    fields = _parsed_or_none(data)
    if fields is None:
        return None
    chunks = _length_fields(fields, 1)
    return b"".join(chunks) if chunks else None


def _unwrap_value(data: bytes) -> tuple[bytes | None, int | None, bytes | None]:
    fields = _parse_message(data)
    value_id = _first_length(fields, 3)
    for encoded in reversed(_length_fields(fields, 5)):
        payload = _unwrap_fast_bytes(encoded)
        if payload is not None:
            return payload, 5, value_id
    deprecated = _length_fields(fields, 4)
    if deprecated:
        return b"".join(deprecated), 4, value_id
    if value_id is not None and len(value_id) != 16:
        return value_id, 3, None
    return None, None, value_id


def parse_collection_event(data: bytes) -> CollectionEvent:
    """Parse one unframed Hermes ``CollectionResponse`` message."""

    fields = _parse_message(data)
    key = _first_length(fields, 1) or b""
    value_message = _first_length(fields, 2)
    payload: bytes | None = None
    value_tag: int | None = None
    value_id: bytes | None = None
    if value_message is not None:
        payload, value_tag, value_id = _unwrap_value(value_message)
    page_x, page_y, mission_id = _decode_page_and_mission(key)
    sequence: int | None = None
    started_at_ns: int | None = None
    sequence_message = _first_length(fields, 3)
    if sequence_message is not None:
        sequence_fields = _parsed_or_none(sequence_message)
        if sequence_fields is not None:
            started_at_ns = _first_integer(sequence_fields, 1)
            sequence = _first_integer(sequence_fields, 2)
    return CollectionEvent(
        key=key,
        payload=payload,
        value_present=value_message is not None,
        page_x=page_x,
        page_y=page_y,
        mission_id=mission_id,
        sequence=sequence,
        started_at_ns=started_at_ns,
        value_tag=value_tag,
        value_id=value_id,
    )


def split_grpc_frames(data: bytes) -> tuple[bytes, ...] | None:
    """Split a complete sequence of uncompressed gRPC frames.

    ``None`` means the input does not look like a framed stream and should be
    treated as one raw protobuf message.  Compressed frames are deliberately
    not accepted because silently returning compressed bytes would misdecode.
    """

    if len(data) < 5 or data[0] != 0:
        return None
    frames: list[bytes] = []
    offset = 0
    while offset < len(data):
        if offset + 5 > len(data) or data[offset] != 0:
            return None
        length = struct.unpack_from(">I", data, offset + 1)[0]
        offset += 5
        end = offset + length
        if end > len(data):
            return None
        frames.append(data[offset:end])
        offset = end
    return tuple(frames)


def _collect_blobs(
    data: bytes, *, path: tuple[int, ...] = (), depth: int = 0
) -> list[_Blob]:
    if depth >= MAX_PROTO_DEPTH:
        return []
    fields = _parsed_or_none(data)
    if fields is None:
        return []
    blobs: list[_Blob] = []
    occurrences: defaultdict[int, int] = defaultdict(int)
    for field in fields:
        if field.wire_type != 2 or not isinstance(field.value, bytes):
            continue
        occurrences[field.number] += 1
        child_path = (*path, field.number)
        if occurrences[field.number] > 1:
            child_path = (*child_path, -occurrences[field.number])
        blobs.append(_Blob(child_path, field.value))
        blobs.extend(_collect_blobs(field.value, path=child_path, depth=depth + 1))
    return blobs


def _blob_with_size(blobs: Iterable[_Blob], size: int) -> _Blob | None:
    return max(
        (blob for blob in blobs if len(blob.data) == size),
        key=lambda blob: len(blob.path),
        default=None,
    )


def _exact_fast_field(fields: Sequence[_WireField], number: int) -> bytes | None:
    for encoded in reversed(_length_fields(fields, number)):
        unwrapped = _unwrap_fast_bytes(encoded)
        if unwrapped is not None:
            return unwrapped
    return None


def _extract_ndarray_buffer(data: bytes) -> bytes | None:
    fields = _parsed_or_none(data)
    if fields is None:
        return None
    for number in (5, 3, 1):
        result = _exact_fast_field(fields, number)
        if result is not None:
            return result
    useful = [
        blob
        for blob in _collect_blobs(data)
        if len(blob.data) in (512, 1024, 3072, 8192)
    ]
    return max(useful, key=lambda blob: len(blob.path)).data if useful else None


def _extract_floor_rgba(
    fields: Sequence[_WireField], blobs: Sequence[_Blob]
) -> bytes | None:
    optional = _first_length(fields, 7)
    optional_fields = _parsed_or_none(optional or b"")
    if optional_fields is not None:
        map_tile = _first_length(optional_fields, 1)
        map_fields = _parsed_or_none(map_tile or b"")
        if map_fields is not None:
            ndarray = _first_length(map_fields, 1)
            if ndarray is not None:
                raw = _extract_ndarray_buffer(ndarray)
                if raw is not None and len(raw) == 8192:
                    return raw
    candidate = _blob_with_size(blobs, 8192)
    return candidate.data if candidate else None


def _transposed_pixel_index(pixel: int) -> int:
    return (pixel % TILE_SIZE) * TILE_SIZE + pixel // TILE_SIZE


def transpose_x_major(raw: bytes) -> bytes:
    """Convert a 32x32 x-major Matic plane to row-major image order."""

    if len(raw) != PIXELS_PER_TILE:
        raise MapDecodeError(
            f"tile buffer is {len(raw)} bytes; expected {PIXELS_PER_TILE}"
        )
    return bytes(
        raw[_transposed_pixel_index(pixel)] for pixel in range(PIXELS_PER_TILE)
    )


def _clamp_byte(value: float) -> int:
    if not math.isfinite(value):
        return 0
    return max(0, min(255, round(value)))


def render_floor_rgba(raw: bytes) -> Image.Image:
    """Render four planar float16 channels into an RGBA tile."""

    if len(raw) != 8192:
        raise MapDecodeError(f"floor RGBA buffer is {len(raw)} bytes; expected 8192")
    planes = [memoryview(raw)[index * 2048 : (index + 1) * 2048] for index in range(4)]
    output = bytearray(PIXELS_PER_TILE * 4)
    for pixel in range(PIXELS_PER_TILE):
        source = _transposed_pixel_index(pixel)
        for channel, plane in enumerate(planes):
            output[pixel * 4 + channel] = _clamp_byte(
                float(struct.unpack_from("<e", plane, source * 2)[0])
            )
    return Image.frombytes("RGBA", (TILE_SIZE, TILE_SIZE), bytes(output))


def _surface_bit(surface: bytes, pixel: int, depth: int) -> bool:
    bit_index = pixel * 24 + depth
    return bool(surface[bit_index >> 3] & (0x80 >> (bit_index & 7)))


def render_compressed_surface(
    surface: bytes, rgb: bytes
) -> tuple[Image.Image, str | None]:
    """Flatten visible colored surface voxels into an RGBA tile."""

    if len(surface) != 3072:
        raise MapDecodeError(
            f"surface-bit buffer is {len(surface)} bytes; expected 3072"
        )
    expected_rgb = sum(byte.bit_count() for byte in surface) * 3
    warning = None
    if len(rgb) != expected_rgb:
        warning = f"RGB buffer is {len(rgb)} bytes; surface bits imply {expected_rgb}"
    output = bytearray(PIXELS_PER_TILE * 4)
    cursor = 0
    for source_pixel in range(PIXELS_PER_TILE):
        for depth in range(24):
            if not _surface_bit(surface, source_pixel, depth):
                continue
            if cursor + 3 > len(rgb):
                return Image.frombytes("RGBA", (32, 32), bytes(output)), warning
            red, green, blue = rgb[cursor : cursor + 3]
            cursor += 3
            if depth >= 7:
                x = source_pixel // TILE_SIZE
                y = source_pixel % TILE_SIZE
                start = (y * TILE_SIZE + x) * 4
                output[start : start + 4] = bytes((red, green, blue, 255))
    return Image.frombytes("RGBA", (32, 32), bytes(output)), warning


def _render_surface_mask(surface: bytes) -> Image.Image:
    values = bytearray(PIXELS_PER_TILE)
    for source_pixel in range(PIXELS_PER_TILE):
        visible = sum(
            _surface_bit(surface, source_pixel, depth) for depth in range(7, 24)
        )
        x = source_pixel // TILE_SIZE
        y = source_pixel % TILE_SIZE
        values[y * TILE_SIZE + x] = min(255, visible * 32)
    return Image.frombytes("L", (32, 32), bytes(values))


def _unpack_nibbles(raw: bytes) -> bytes:
    if len(raw) != 512:
        raise MapDecodeError(f"nibble map is {len(raw)} bytes; expected 512")
    output = bytearray(PIXELS_PER_TILE)
    for index, value in enumerate(raw):
        output[index * 2] = value >> 4
        output[index * 2 + 1] = value & 0x0F
    return bytes(output)


def _render_classification(
    values: bytes,
    kind: MapValueKind,
) -> Image.Image:
    if len(values) != PIXELS_PER_TILE:
        raise MapDecodeError(
            f"classification tile is {len(values)} bytes; expected 1024"
        )
    if kind is MapValueKind.SEMANTICS:
        rendered = bytes(255 if value == 2 else 0 for value in values)
    elif kind is MapValueKind.SEMANTICS_OVERRIDE:
        rendered = bytes(255 if value in (2, 4) else 0 for value in values)
    else:
        rendered = bytes(min(255, value * 17) for value in values)
    return Image.frombytes("L", (32, 32), rendered)


def _render_nibbles(
    raw: bytes,
    kind: MapValueKind,
) -> tuple[Image.Image, MapClassification]:
    values = transpose_x_major(_unpack_nibbles(raw))
    classification = MapClassification(kind, values)
    return _render_classification(values, kind), classification


def _render_r8_tile(
    raw: bytes,
    target: MapTarget,
) -> list[tuple[str, Image.Image, MapClassification | None]]:
    if len(raw) != 1024:
        raise MapDecodeError(f"map tile is {len(raw)} bytes; expected 1024")
    values = transpose_x_major(raw)
    if target == "map_combined_coverage":
        sweep = bytes(255 if value & 0x0F else 0 for value in values)
        mop = bytes(255 if value >> 4 else 0 for value in values)
        return [
            ("coverage-sweep", Image.frombytes("L", (32, 32), sweep), None),
            ("coverage-mop", Image.frombytes("L", (32, 32), mop), None),
        ]
    if target == "map_semantics":
        classification = MapClassification(MapValueKind.SEMANTICS, values)
        return [
            (
                "semantics",
                _render_classification(values, MapValueKind.SEMANTICS),
                classification,
            )
        ]
    if target == "map_semantics_override":
        classification = MapClassification(MapValueKind.SEMANTICS_OVERRIDE, values)
        return [
            (
                "semantics-override",
                _render_classification(values, MapValueKind.SEMANTICS_OVERRIDE),
                classification,
            )
        ]
    return [("map-r8", Image.frombytes("L", (32, 32), values), None)]


def identify_map_target(payload: bytes) -> MapTarget | None:
    """Identify a known map payload using exact schema fields and sizes."""

    blobs = _collect_blobs(payload)
    sizes = [len(blob.data) for blob in blobs]
    if 8192 in sizes:
        return "map_compressed_rgb"
    if 3072 in sizes and sizes.count(512) >= 2:
        return "map_integrated"
    # All four R8 collections carry a 1,024-byte plane. Their payloads are
    # intentionally not auto-labeled because the target changes its meaning.
    fields = _parsed_or_none(payload)
    if fields is not None and _first_length(fields, 4) and _first_length(fields, 6):
        return "map_compressed_rgb"
    return None


def decode_map_event(
    event: CollectionEvent, *, target: MapTarget | Literal["auto"] = "auto"
) -> DecodedMapEvent:
    """Decode the map layers carried by one collection event."""

    if target != "auto" and target not in SUPPORTED_MAP_TARGETS:
        raise MapDecodeError(f"unsupported map target: {target}")
    if event.operation == "delete":
        return DecodedMapEvent(event, ())
    if event.payload is None:
        return DecodedMapEvent(
            event,
            (),
            ("upsert value has no decodable map payload",),
        )
    if event.page_x is None or event.page_y is None or event.mission_id is None:
        return DecodedMapEvent(
            event, (), ("map payload has no decodable PageAndMission key",)
        )
    selected: MapTarget | None = (
        identify_map_target(event.payload) if target == "auto" else target
    )
    if selected is None:
        blobs = _collect_blobs(event.payload)
        target_warning = (
            "1,024-byte R8 payload is target-ambiguous; specify "
            "map_combined_coverage, map_semantics, map_semantics_override, or "
            "map-r8"
            if any(len(blob.data) == 1024 for blob in blobs)
            else "payload did not match a known map format"
        )
        return DecodedMapEvent(event, (), (target_warning,))
    fields = _parsed_or_none(event.payload)
    if fields is None:
        return DecodedMapEvent(event, (), ("map payload is not a protobuf message",))
    blobs = _collect_blobs(event.payload)
    tiles: list[MapTile] = []
    warnings: list[str] = []

    def add(
        layer: str,
        image: Image.Image,
        classification: MapClassification | None = None,
    ) -> None:
        tiles.append(
            MapTile(
                mission_id=event.mission_id or 0,
                page_x=event.page_x or 0,
                page_y=event.page_y or 0,
                layer=layer,
                image=image,
                target=selected or "map-r8",
                sequence=event.sequence,
                classification=classification,
            )
        )

    if selected in ("map_compressed_rgb", "map_compressed_rgb_higher"):
        floor = _extract_floor_rgba(fields, blobs)
        if floor is not None:
            add("floor-rgba", render_floor_rgba(floor))
        compressed_surface: bytes | None = None
        surface_array = _first_length(fields, 4)
        if surface_array is not None:
            compressed_surface = _extract_ndarray_buffer(surface_array)
        if compressed_surface is None or len(compressed_surface) != 3072:
            candidate = _blob_with_size(blobs, 3072)
            compressed_surface = candidate.data if candidate else None
        rgb = _exact_fast_field(fields, 6)
        if (
            compressed_surface is not None
            and len(compressed_surface) == 3072
            and rgb is not None
        ):
            surface_image, warning = render_compressed_surface(compressed_surface, rgb)
            add("surface-rgb", surface_image)
            if warning:
                warnings.append(warning)
            if floor is not None:
                composite = render_floor_rgba(floor)
                composite.alpha_composite(surface_image)
                add("composite", composite)
        elif floor is None:
            warnings.append(
                "compressed tile has neither floor RGBA nor complete surface RGB"
            )
        return DecodedMapEvent(event, tuple(tiles), tuple(warnings))

    if selected == "map_integrated":
        integrated_surface: bytes | None = None
        integrated_surface_array = _first_length(fields, 5)
        if integrated_surface_array is not None:
            integrated_surface = _extract_ndarray_buffer(integrated_surface_array)
        if integrated_surface is None or len(integrated_surface) != 3072:
            candidate = _blob_with_size(blobs, 3072)
            integrated_surface = candidate.data if candidate else None
        occupancy = _exact_fast_field(fields, 7)
        semantics = _exact_fast_field(fields, 8)
        if integrated_surface is None or occupancy is None or semantics is None:
            return DecodedMapEvent(
                event,
                (),
                ("integrated tile is missing a required surface or data buffer",),
            )
        override = _exact_fast_field(fields, 9) or bytes(512)
        add("integrated-surface", _render_surface_mask(integrated_surface))
        for name, plane, kind in (
            ("occupancy", occupancy, MapValueKind.GEOMETRIC_OCCUPANCY),
            ("semantics", semantics, MapValueKind.SEMANTICS),
            (
                "semantics-override",
                override,
                MapValueKind.SEMANTICS_OVERRIDE,
            ),
        ):
            image, classification = _render_nibbles(plane, kind)
            add(name, image, classification)
        return DecodedMapEvent(event, tuple(tiles), tuple(warnings))

    raw: bytes | None = None
    ndarray = _first_length(fields, 1)
    if ndarray is not None:
        raw = _extract_ndarray_buffer(ndarray)
    if raw is None or len(raw) != 1024:
        candidate = _blob_with_size(blobs, 1024)
        raw = candidate.data if candidate else None
    if raw is None:
        return DecodedMapEvent(event, (), ("map tile has no 1024-byte R8 buffer",))
    for layer, image, tile_classification in _render_r8_tile(raw, selected):
        add(layer, image, tile_classification)
    return DecodedMapEvent(event, tuple(tiles), tuple(warnings))


class MapCollectionState:
    """Apply collection adds/deletes and expose the final decoded tile state."""

    def __init__(self, *, target: MapTarget | Literal["auto"] = "auto") -> None:
        if target != "auto" and target not in SUPPORTED_MAP_TARGETS:
            raise MapDecodeError(f"unsupported map target: {target}")
        self.target = target
        self._by_key: dict[bytes, tuple[int, tuple[MapTile, ...]]] = {}
        self._event_index = 0

    def apply(self, event: CollectionEvent) -> DecodedMapEvent:
        decoded = decode_map_event(event, target=self.target)
        if event.operation == "delete":
            self._by_key.pop(event.key, None)
        else:
            # Replacing the whole tuple also removes layers omitted by an
            # updated value; retaining them would invent stale collection data.
            self._by_key[event.key] = (self._event_index, decoded.tiles)
        self._event_index += 1
        return decoded

    def apply_message(self, data: bytes) -> DecodedMapEvent:
        return self.apply(parse_collection_event(data))

    @property
    def tiles(self) -> tuple[MapTile, ...]:
        # Alternate protobuf key encodings can resolve to the same geometry.
        # Later collection events own that coordinate/layer.
        resolved: dict[tuple[int, int, int, str], MapTile] = {}
        for _, tiles in sorted(self._by_key.values(), key=lambda item: item[0]):
            for tile in tiles:
                resolved[(tile.mission_id, tile.page_x, tile.page_y, tile.layer)] = tile
        return tuple(resolved.values())

    def clear(self) -> None:
        self._by_key.clear()


def classification_counts(
    tiles: Iterable[MapTile],
    kind: MapValueKind,
) -> dict[MapCellValue, int]:
    """Aggregate one categorical map plane across decoded tiles."""

    if not isinstance(kind, MapValueKind):
        raise TypeError("classification kind must be MapValueKind")
    counts: Counter[MapCellValue] = Counter()
    for tile in tiles:
        classification = tile.classification
        if classification is not None and classification.kind is kind:
            counts.update(classification.counts)
    return dict(counts)


def orient_tile(
    tile: MapTile, orientation: MapOrientation
) -> tuple[tuple[int, int], Image.Image]:
    """Convert a canonical tile to canonical or native app coordinates."""

    if orientation == "canonical":
        return (tile.page_x, tile.page_y), tile.image
    if orientation == "native":
        return (-tile.page_y - 1, -tile.page_x - 1), tile.image.transpose(
            Image.Transpose.TRANSVERSE
        )
    raise MapDecodeError(f"unsupported orientation: {orientation}")


def build_mosaics(
    tiles: Sequence[MapTile],
    *,
    scale: int = 1,
    orientation: MapOrientation = "canonical",
    y_down: bool = True,
    max_pixels: int = 100_000_000,
) -> tuple[MapMosaic, ...]:
    """Assemble final tiles into one Pillow image per mission and layer."""

    if not isinstance(scale, int) or scale < 1:
        raise MapDecodeError("scale must be a positive integer")
    grouped: defaultdict[
        tuple[int, str], dict[tuple[int, int], tuple[MapTile, Image.Image]]
    ] = defaultdict(dict)
    for tile in tiles:
        coordinate, image = orient_tile(tile, orientation)
        grouped[(tile.mission_id, tile.layer)][coordinate] = (tile, image)

    mosaics: list[MapMosaic] = []
    for (mission_id, layer), positioned in sorted(grouped.items()):
        xs = [coordinate[0] for coordinate in positioned]
        ys = [coordinate[1] for coordinate in positioned]
        bounds = MapBounds(min(xs), max(xs), min(ys), max(ys))
        width = (bounds.max_x - bounds.min_x + 1) * TILE_SIZE
        height = (bounds.max_y - bounds.min_y + 1) * TILE_SIZE
        if width * height * scale * scale > max_pixels:
            raise MapDecodeError(
                f"refusing {width * scale}x{height * scale} mosaic; "
                "raise max_pixels only for trusted coordinates"
            )
        mode = next(iter(positioned.values()))[1].mode
        background: int | tuple[int, ...] = (0, 0, 0, 0) if mode == "RGBA" else 0
        image = Image.new(mode, (width, height), background)
        for (x, y), (_, tile_image) in positioned.items():
            left = (x - bounds.min_x) * TILE_SIZE
            top = (
                (y - bounds.min_y) * TILE_SIZE
                if y_down
                else (bounds.max_y - y) * TILE_SIZE
            )
            image.paste(tile_image, (left, top))
        if scale != 1:
            image = image.resize(
                (image.width * scale, image.height * scale), Image.Resampling.NEAREST
            )
        mosaics.append(
            MapMosaic(
                mission_id=mission_id,
                layer=layer,
                image=image,
                bounds=bounds,
                tile_count=len(positioned),
                orientation=orientation,
                y_down=y_down,
            )
        )
    return tuple(mosaics)


def save_mosaics(
    mosaics: Sequence[MapMosaic], output_directory: str | Path
) -> tuple[Path, ...]:
    """Save mosaics as private PNG files and return their paths."""

    output = ensure_private_directory(output_directory)
    paths: list[Path] = []
    for mosaic in mosaics:
        safe_layer = "".join(
            character if character.isalnum() or character in "-_" else "-"
            for character in mosaic.layer
        )
        path = output / f"mission-{mosaic.mission_id:08x}-{safe_layer}.png"
        with open_new_private(path) as handle:
            mosaic.image.save(handle, format="PNG")
        paths.append(path)
    return tuple(paths)


__all__ = [
    "SUPPORTED_MAP_TARGETS",
    "MapCollectionState",
    "MapDecodeError",
    "build_mosaics",
    "classification_counts",
    "decode_map_event",
    "identify_map_target",
    "orient_tile",
    "parse_collection_event",
    "render_compressed_surface",
    "render_floor_rgba",
    "save_mosaics",
    "split_grpc_frames",
    "transpose_x_major",
]
