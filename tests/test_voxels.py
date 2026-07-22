from __future__ import annotations

import io
import math
import stat
import struct

import pytest

from matic_sdk.maps import parse_collection_event
from matic_sdk.voxels import (
    VoxelCollectionState,
    VoxelDecodeError,
    decode_compressed_tile,
    export_ply,
    iter_colored_voxels,
    position_meters,
    tile_counts,
    write_ply,
)
from tests._map_fixtures import collection_event, compressed_payload, proto_bytes

_VERTEX = struct.Struct("<fffBBB")


def _event(
    *,
    page_x: int = 1,
    sequence: int = 1,
    truncate_rgb: bool = False,
    suffix: bytes = b"",
):
    payload = compressed_payload(
        [(69, 3, (10, 11, 12)), (69, 7, (20, 21, 22))],
        truncate_rgb=truncate_rgb,
    )
    return parse_collection_event(
        collection_event(
            page_x=page_x,
            payload=payload,
            sequence=sequence,
            alternate_suffix=suffix,
        )
    )


def test_sparse_tile_decodes_32_by_32_by_24_rgb_alignment() -> None:
    tile = decode_compressed_tile(_event())

    assert len(tile.surface) == 32 * 32 * 24 // 8
    assert tile_counts(tile) == (2, 1)
    voxels = list(iter_colored_voxels(tile))
    assert [(voxel.map_x, voxel.depth, voxel.map_y) for voxel in voxels] == [
        (34, 3, 5),
        (34, 7, 5),
    ]
    assert [voxel.color for voxel in voxels] == [(10, 11, 12), (20, 21, 22)]


def test_strict_tile_decode_rejects_truncated_rgb() -> None:
    with pytest.raises(VoxelDecodeError, match="require exactly 6 bytes"):
        decode_compressed_tile(_event(truncate_rgb=True))


def test_collection_state_replaces_deletes_and_resolves_coordinate_collisions() -> None:
    state = VoxelCollectionState()
    state.apply(_event(page_x=0, sequence=1))
    state.apply(_event(page_x=0, sequence=2))
    state.apply(_event(page_x=1, sequence=3))
    state.apply(_event(page_x=1, sequence=4, suffix=proto_bytes(3, b"alternate-key")))

    # Two raw keys resolve to page 1; the later geometry wins.
    assert len(state.tiles) == 2
    page_one = next(tile for tile in state.tiles if tile.page_x == 1)
    assert page_one.event_index == 3

    state.apply(
        parse_collection_event(collection_event(page_x=0, payload=None, sequence=5))
    )
    assert {tile.page_x for tile in state.tiles} == {1}


def test_malformed_replacement_does_not_leave_stale_state() -> None:
    state = VoxelCollectionState()
    state.apply(_event(page_x=1, sequence=1))

    with pytest.raises(VoxelDecodeError):
        state.apply(_event(page_x=1, sequence=2, truncate_rgb=True))

    assert state.tiles == ()


def test_centered_ply_filters_hidden_depth_after_consuming_its_rgb() -> None:
    tile = decode_compressed_tile(_event())
    output = io.BytesIO()

    summary = write_ply(output, (tile,))

    header, body = output.getvalue().split(b"end_header\n", 1)
    assert b"element vertex 1\n" in header
    assert summary.surface_voxels == 2
    assert summary.visible_voxels == 1
    assert summary.exported_voxels == 1
    x, y, z, red, green, blue = _VERTEX.unpack(body)
    assert math.isclose(x, 0.5175, abs_tol=1e-7)
    assert math.isclose(y, 0.0075, abs_tol=1e-7)
    assert math.isclose(z, 0.0825, abs_tol=1e-7)
    assert (red, green, blue) == (20, 21, 22)


def test_all_depths_ply_and_app_native_coordinate_mode() -> None:
    tile = decode_compressed_tile(_event())
    output = io.BytesIO()

    summary = write_ply(output, (tile,), all_depths=True, coordinate_mode="app-native")

    _, body = output.getvalue().split(b"end_header\n", 1)
    assert summary.exported_voxels == 2
    assert len(body) == 2 * _VERTEX.size
    visible = _VERTEX.unpack_from(body, _VERTEX.size)
    assert math.isclose(visible[0], 0.8701125, abs_tol=1e-7)
    assert math.isclose(visible[1], 0.0001125, abs_tol=1e-7)
    assert math.isclose(visible[2], 0.4351125, abs_tol=1e-7)
    assert visible[3:] == (20, 21, 22)


def test_position_rejects_unknown_coordinate_mode() -> None:
    tile = decode_compressed_tile(_event())
    voxel = list(iter_colored_voxels(tile))[1]

    with pytest.raises(VoxelDecodeError, match="unsupported coordinate mode"):
        position_meters(tile, voxel, coordinate_mode="sideways")  # type: ignore[arg-type]


def test_export_ply_writes_private_file(tmp_path) -> None:
    tile = decode_compressed_tile(_event())
    destination = tmp_path / "voxels" / "surface.ply"

    summary = export_ply((tile,), destination)

    assert summary.exported_voxels == 1
    assert destination.read_bytes().startswith(b"ply\n")
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600

    with pytest.raises(FileExistsError):
        export_ply((tile,), destination)
