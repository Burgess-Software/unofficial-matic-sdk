"""Decode Matic's sparse 32x32x24 colored surface voxels and export PLY."""

from __future__ import annotations

import os
import stat
import struct
import tempfile
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol

from . import maps as _maps
from .models.maps import CollectionEvent

TILE_SIZE = 32
DEPTH_LEVELS = 24
OFFICIAL_MIN_VISIBLE_DEPTH = 7
VOXEL_EDGE_METERS = 0.015
APP_NATIVE_PRE_SCALE_OFFSET = 0.0075
_VERTEX = struct.Struct("<fffBBB")
_VISIBLE_DEPTH_MASK = (1 << (DEPTH_LEVELS - OFFICIAL_MIN_VISIBLE_DEPTH)) - 1

CoordinateMode = Literal["centered", "app-native"]


class _BinaryWriter(Protocol):
    def write(self, data: bytes, /) -> int: ...


class VoxelDecodeError(ValueError):
    """Raised when sparse voxel data cannot be decoded without losing alignment."""


@dataclass(frozen=True, slots=True)
class CompressedVoxelTile:
    """One sparse colored surface tile in canonical page coordinates."""

    event_index: int
    key: bytes = field(repr=False)
    mission_id: int
    page_x: int
    page_y: int
    surface: bytes = field(repr=False)
    rgb: bytes = field(repr=False)


@dataclass(frozen=True, slots=True)
class ColoredVoxel:
    """An occupied voxel in normalized map indices."""

    map_x: int
    depth: int
    map_y: int
    red: int
    green: int
    blue: int

    @property
    def color(self) -> tuple[int, int, int]:
        return self.red, self.green, self.blue


@dataclass(frozen=True, slots=True)
class VoxelExportSummary:
    mission_id: int
    tile_count: int
    surface_voxels: int
    visible_voxels: int
    exported_voxels: int
    coordinate_mode: CoordinateMode
    index_bounds: tuple[tuple[int, int, int], tuple[int, int, int]] | None
    meter_bounds: tuple[tuple[float, float, float], tuple[float, float, float]] | None


def decode_compressed_tile(
    event: CollectionEvent, *, event_index: int = 0
) -> CompressedVoxelTile:
    """Decode a compressed-RGB map add into strict sparse voxel buffers."""

    if event.payload is None:
        raise VoxelDecodeError("event has no decodable compressed tile payload")
    if event.mission_id is None or event.page_x is None or event.page_y is None:
        raise VoxelDecodeError("tile key has no decodable mission/page coordinates")
    fields = _maps._parsed_or_none(event.payload)
    if fields is None:
        raise VoxelDecodeError("compressed tile payload is not a protobuf message")
    identified = _maps.identify_map_target(event.payload)
    if identified not in ("map_compressed_rgb", "map_compressed_rgb_higher"):
        raise VoxelDecodeError(
            f"payload identifies as {identified or 'unknown'}, not compressed RGB"
        )

    surface: bytes | None = None
    surface_array = _maps._first_length(fields, 4)
    if surface_array is not None:
        surface = _maps._extract_ndarray_buffer(surface_array)
    if surface is None or len(surface) != 3072:
        candidate = _maps._blob_with_size(_maps._collect_blobs(event.payload), 3072)
        surface = candidate.data if candidate else None
    if surface is None:
        raise VoxelDecodeError("compressed tile has no 3,072-byte surface-bit array")
    if len(surface) != 3072:
        raise VoxelDecodeError(
            f"surface-bit array is {len(surface)} bytes; expected 3,072"
        )

    rgb = _maps._exact_fast_field(fields, 6)
    if rgb is None:
        raise VoxelDecodeError("compressed tile has no field-6 RGB buffer")
    occupied = sum(byte.bit_count() for byte in surface)
    expected_rgb = occupied * 3
    if len(rgb) != expected_rgb:
        raise VoxelDecodeError(
            f"RGB buffer is {len(rgb)} bytes; {occupied} surface bits require "
            f"exactly {expected_rgb} bytes"
        )
    return CompressedVoxelTile(
        event_index=event_index,
        key=event.key,
        mission_id=event.mission_id,
        page_x=event.page_x,
        page_y=event.page_y,
        surface=surface,
        rgb=rgb,
    )


class VoxelCollectionState:
    """Track the final compressed tile state under Hermes add/delete semantics."""

    def __init__(self) -> None:
        self._by_key: dict[bytes, CompressedVoxelTile] = {}
        self._event_index = 0

    def apply(self, event: CollectionEvent) -> CompressedVoxelTile | None:
        if event.operation == "delete":
            self._by_key.pop(event.key, None)
            self._event_index += 1
            return None
        try:
            tile = decode_compressed_tile(event, event_index=self._event_index)
        except VoxelDecodeError:
            # A malformed replacement makes the value unknown.  Keeping the
            # previous tile would silently invent stale collection state.
            self._by_key.pop(event.key, None)
            self._event_index += 1
            raise
        self._by_key[event.key] = tile
        self._event_index += 1
        return tile

    def apply_message(self, data: bytes) -> CompressedVoxelTile | None:
        return self.apply(_maps.parse_collection_event(data))

    @property
    def tiles(self) -> tuple[CompressedVoxelTile, ...]:
        # Raw keys own collection identity; coordinates own geometry identity.
        # Resolve compatible alternate key encodings by the last event.
        resolved: dict[tuple[int, int, int], CompressedVoxelTile] = {}
        for tile in sorted(self._by_key.values(), key=lambda value: value.event_index):
            resolved[(tile.mission_id, tile.page_x, tile.page_y)] = tile
        return tuple(resolved.values())

    def clear(self) -> None:
        self._by_key.clear()


def tile_counts(tile: CompressedVoxelTile) -> tuple[int, int]:
    """Return all occupied voxels and the official-client visible subset."""

    raw_count = sum(byte.bit_count() for byte in tile.surface)
    visible_count = 0
    for offset in range(0, len(tile.surface), 3):
        word = int.from_bytes(tile.surface[offset : offset + 3], "big")
        visible_count += (word & _VISIBLE_DEPTH_MASK).bit_count()
    return raw_count, visible_count


def iter_colored_voxels(tile: CompressedVoxelTile) -> Iterator[ColoredVoxel]:
    """Yield occupied voxels in pixel-major, then depth-major RGB order."""

    cursor = 0
    for source_pixel in range(TILE_SIZE * TILE_SIZE):
        local_x = source_pixel // TILE_SIZE
        local_y = source_pixel % TILE_SIZE
        map_x = tile.page_x * TILE_SIZE + local_x
        map_y = tile.page_y * TILE_SIZE + local_y
        for depth in range(DEPTH_LEVELS):
            if not _maps._surface_bit(tile.surface, source_pixel, depth):
                continue
            end = cursor + 3
            if end > len(tile.rgb):
                raise VoxelDecodeError("RGB cursor exceeded the buffer")
            red, green, blue = tile.rgb[cursor:end]
            cursor = end
            yield ColoredVoxel(map_x, depth, map_y, red, green, blue)
    if cursor != len(tile.rgb):
        raise VoxelDecodeError(f"RGB cursor consumed {cursor} of {len(tile.rgb)} bytes")


def position_meters(
    tile: CompressedVoxelTile,
    voxel: ColoredVoxel,
    *,
    coordinate_mode: CoordinateMode = "centered",
) -> tuple[float, float, float]:
    """Convert normalized voxel indices to the selected app-facing scene frame."""

    local_x = voxel.map_x - tile.page_x * TILE_SIZE
    local_y = voxel.map_y - tile.page_y * TILE_SIZE
    if coordinate_mode == "centered":
        x_grid = voxel.map_x + 0.5
        y_grid = voxel.depth - OFFICIAL_MIN_VISIBLE_DEPTH + 0.5
        z_grid = voxel.map_y + 0.5
    elif coordinate_mode == "app-native":
        x_grid = (
            tile.page_x * TILE_SIZE
            + (TILE_SIZE - 1 - local_y)
            + APP_NATIVE_PRE_SCALE_OFFSET
        )
        y_grid = voxel.depth - OFFICIAL_MIN_VISIBLE_DEPTH + APP_NATIVE_PRE_SCALE_OFFSET
        z_grid = (
            tile.page_y * TILE_SIZE
            + (TILE_SIZE - 1 - local_x)
            + APP_NATIVE_PRE_SCALE_OFFSET
        )
    else:
        raise VoxelDecodeError(f"unsupported coordinate mode: {coordinate_mode}")
    return (
        x_grid * VOXEL_EDGE_METERS,
        y_grid * VOXEL_EDGE_METERS,
        z_grid * VOXEL_EDGE_METERS,
    )


def _ply_header(vertex_count: int, coordinate_mode: CoordinateMode) -> bytes:
    geometry = (
        "geometric centers of 0.015-meter voxels"
        if coordinate_mode == "centered"
        else "released tile-decoder Transform3D instance origins"
    )
    return (
        "ply\n"
        "format binary_little_endian 1.0\n"
        "comment Matic app-facing colored surface voxels\n"
        f"comment voxel_edge_meters {VOXEL_EDGE_METERS}\n"
        f"comment coordinate_mode {coordinate_mode}\n"
        f"comment coordinate_semantics {geometry}\n"
        "comment axes x=map-x y=up/rebased-depth z=map-y\n"
        f"element vertex {vertex_count}\n"
        "property float x\n"
        "property float y\n"
        "property float z\n"
        "property uchar red\n"
        "property uchar green\n"
        "property uchar blue\n"
        "end_header\n"
    ).encode("ascii")


def write_ply(
    output: _BinaryWriter,
    tiles: Sequence[CompressedVoxelTile],
    *,
    all_depths: bool = False,
    coordinate_mode: CoordinateMode = "centered",
) -> VoxelExportSummary:
    """Write one mission's tiles as a binary little-endian PLY stream."""

    if not tiles:
        raise VoxelDecodeError("at least one compressed voxel tile is required")
    missions = {tile.mission_id for tile in tiles}
    if len(missions) != 1:
        raise VoxelDecodeError("one PLY file cannot contain multiple missions")
    if coordinate_mode not in ("centered", "app-native"):
        raise VoxelDecodeError(f"unsupported coordinate mode: {coordinate_mode}")

    raw_count = 0
    visible_count = 0
    for tile in tiles:
        raw, visible = tile_counts(tile)
        raw_count += raw
        visible_count += visible
    export_count = raw_count if all_depths else visible_count
    output.write(_ply_header(export_count, coordinate_mode))

    index_min: list[int] | None = None
    index_max: list[int] | None = None
    meter_min: list[float] | None = None
    meter_max: list[float] | None = None
    written = 0
    for tile in sorted(tiles, key=lambda item: (item.page_x, item.page_y)):
        for voxel in iter_colored_voxels(tile):
            if not all_depths and voxel.depth < OFFICIAL_MIN_VISIBLE_DEPTH:
                continue
            indices = [voxel.map_x, voxel.depth, voxel.map_y]
            if index_min is None:
                index_min = indices.copy()
                index_max = indices.copy()
            else:
                assert index_max is not None
                for axis in range(3):
                    index_min[axis] = min(index_min[axis], indices[axis])
                    index_max[axis] = max(index_max[axis], indices[axis])
            position = position_meters(tile, voxel, coordinate_mode=coordinate_mode)
            if meter_min is None:
                meter_min = list(position)
                meter_max = list(position)
            else:
                assert meter_max is not None
                for axis in range(3):
                    meter_min[axis] = min(meter_min[axis], position[axis])
                    meter_max[axis] = max(meter_max[axis], position[axis])
            output.write(_VERTEX.pack(*position, *voxel.color))
            written += 1
    if written != export_count:
        raise VoxelDecodeError(
            f"counted {export_count} PLY vertices but wrote {written}"
        )

    index_bounds: tuple[tuple[int, int, int], tuple[int, int, int]] | None = None
    if index_min is not None and index_max is not None:
        index_bounds = (
            (index_min[0], index_min[1], index_min[2]),
            (index_max[0], index_max[1], index_max[2]),
        )
    meter_bounds: (
        tuple[tuple[float, float, float], tuple[float, float, float]] | None
    ) = None
    if meter_min is not None and meter_max is not None:
        meter_bounds = (
            (meter_min[0], meter_min[1], meter_min[2]),
            (meter_max[0], meter_max[1], meter_max[2]),
        )
    return VoxelExportSummary(
        mission_id=next(iter(missions)),
        tile_count=len(tiles),
        surface_voxels=raw_count,
        visible_voxels=visible_count,
        exported_voxels=export_count,
        coordinate_mode=coordinate_mode,
        index_bounds=index_bounds,
        meter_bounds=meter_bounds,
    )


def export_ply(
    tiles: Sequence[CompressedVoxelTile],
    path: str | Path,
    *,
    all_depths: bool = False,
    coordinate_mode: CoordinateMode = "centered",
) -> VoxelExportSummary:
    """Atomically export one mission to a private PLY file."""

    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    parent_info = destination.parent.lstat()
    if stat.S_ISLNK(parent_info.st_mode) or not stat.S_ISDIR(parent_info.st_mode):
        raise OSError(f"PLY parent is not a real directory: {destination.parent}")
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w+b",
            prefix=f".{destination.name}.",
            dir=destination.parent,
            delete=False,
        ) as output:
            temporary = Path(output.name)
            os.chmod(temporary, 0o600)
            summary = write_ply(
                output,
                tiles,
                all_depths=all_depths,
                coordinate_mode=coordinate_mode,
            )
            output.flush()
            os.fsync(output.fileno())
        os.link(temporary, destination, follow_symlinks=False)
        temporary.unlink()
        temporary = None
        return summary
    except BaseException:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise


__all__ = [
    "APP_NATIVE_PRE_SCALE_OFFSET",
    "DEPTH_LEVELS",
    "OFFICIAL_MIN_VISIBLE_DEPTH",
    "TILE_SIZE",
    "VOXEL_EDGE_METERS",
    "ColoredVoxel",
    "CompressedVoxelTile",
    "VoxelCollectionState",
    "VoxelDecodeError",
    "VoxelExportSummary",
    "decode_compressed_tile",
    "export_ply",
    "iter_colored_voxels",
    "position_meters",
    "tile_counts",
    "write_ply",
]
