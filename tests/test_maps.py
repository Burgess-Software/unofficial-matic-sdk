from __future__ import annotations

import stat

import pytest
from PIL import Image

from matic_sdk.maps import (
    MapCollectionState,
    MapDecodeError,
    build_mosaics,
    decode_map_event,
    parse_collection_event,
    save_mosaics,
    split_grpc_frames,
    transpose_x_major,
)
from matic_sdk.models.maps import MapTile
from tests._map_fixtures import (
    collection_event,
    compressed_payload,
    floor_payload,
    grpc_frame,
    integrated_payload,
    page_key,
    proto_bytes,
    proto_varint,
    r8_payload,
)


def test_collection_envelope_decodes_current_page_and_lifecycle_fields() -> None:
    payload = floor_payload({(2, 3): (10, 20, 30, 255)})
    encoded = collection_event(page_x=-2, page_y=3, payload=payload, sequence=7)

    event = parse_collection_event(encoded)

    assert event.operation == "add"
    assert (event.page_x, event.page_y) == (-2, 3)
    assert event.mission_id == 0x1234ABCD
    assert event.sequence == 7
    assert event.started_at_ns == 1_000_000
    assert event.value_tag == 5
    assert event.value_id == bytes(range(16))
    assert event.payload == payload

    deleted = parse_collection_event(
        collection_event(page_x=-2, page_y=3, payload=None, sequence=8)
    )
    assert deleted.operation == "delete"
    assert deleted.payload is None
    assert deleted.key == event.key


def test_grpc_frame_splitter_accepts_only_complete_uncompressed_frames() -> None:
    first = b"one"
    second = b"two"
    encoded = grpc_frame(first) + grpc_frame(second)

    assert split_grpc_frames(encoded) == (first, second)
    assert split_grpc_frames(encoded[:-1]) is None
    assert split_grpc_frames(b"raw protobuf") is None


def test_floor_tile_transposes_x_major_storage() -> None:
    encoded = collection_event(
        page_x=0,
        payload=floor_payload({(2, 3): (10, 20, 30, 255)}),
        sequence=1,
    )

    decoded = decode_map_event(parse_collection_event(encoded))

    assert decoded.warnings == ()
    assert len(decoded.tiles) == 1
    tile = decoded.tiles[0]
    assert tile.layer == "floor-rgba"
    assert tile.image.getpixel((2, 3)) == (10, 20, 30, 255)
    assert tile.image.getpixel((3, 2)) == (0, 0, 0, 0)


def test_compressed_surface_consumes_hidden_rgb_before_visible_layers() -> None:
    payload = compressed_payload([(0, 3, (1, 2, 3)), (0, 7, (20, 21, 22))])
    event = parse_collection_event(
        collection_event(page_x=0, payload=payload, sequence=1)
    )

    decoded = decode_map_event(event)

    assert decoded.warnings == ()
    assert [tile.layer for tile in decoded.tiles] == ["surface-rgb"]
    assert decoded.tiles[0].image.getpixel((0, 0)) == (20, 21, 22, 255)


def test_collection_state_replaces_and_deletes_exact_keys() -> None:
    state = MapCollectionState()
    state.apply_message(
        collection_event(
            page_x=0,
            payload=floor_payload({}, background=(100, 0, 0, 255)),
            sequence=1,
        )
    )
    state.apply_message(
        collection_event(
            page_x=1,
            payload=floor_payload({}, background=(0, 100, 0, 255)),
            sequence=2,
        )
    )
    state.apply_message(
        collection_event(
            page_x=1,
            payload=floor_payload({}, background=(0, 0, 100, 255)),
            sequence=3,
        )
    )
    state.apply_message(collection_event(page_x=0, payload=None, sequence=4))

    assert len(state.tiles) == 1
    assert state.tiles[0].page_x == 1
    assert state.tiles[0].sequence == 3
    assert state.tiles[0].image.getpixel((0, 0)) == (0, 0, 100, 255)


def test_unknown_upsert_is_not_misclassified_as_delete() -> None:
    state = MapCollectionState()
    state.apply_message(
        collection_event(
            page_x=0,
            payload=floor_payload({}, background=(100, 0, 0, 255)),
            sequence=1,
        )
    )
    key = page_key(0, 0)
    unknown_value = proto_bytes(99, b"unknown")
    sequence = proto_varint(1, 1_000_000) + proto_varint(2, 2)
    encoded = (
        proto_bytes(1, key) + proto_bytes(2, unknown_value) + proto_bytes(3, sequence)
    )

    event = parse_collection_event(encoded)
    decoded = state.apply(event)

    assert event.operation == "add"
    assert event.payload is None
    assert decoded.warnings == ("upsert value has no decodable map payload",)
    assert state.tiles == ()


def test_r8_auto_detection_requires_semantic_target() -> None:
    plane = bytes([0x21]) + bytes(1023)
    event = parse_collection_event(
        collection_event(page_x=0, payload=r8_payload(plane), sequence=1)
    )

    ambiguous = decode_map_event(event)
    coverage = decode_map_event(event, target="map_combined_coverage")

    assert ambiguous.tiles == ()
    assert "target-ambiguous" in ambiguous.warnings[0]
    assert [tile.layer for tile in coverage.tiles] == [
        "coverage-sweep",
        "coverage-mop",
    ]
    assert coverage.tiles[0].image.getpixel((0, 0)) == 255
    assert coverage.tiles[1].image.getpixel((0, 0)) == 255


def test_semantic_r8_targets_apply_target_specific_masks() -> None:
    plane = bytes([2, 4]) + bytes(1022)
    event = parse_collection_event(
        collection_event(page_x=0, payload=r8_payload(plane), sequence=1)
    )

    semantics = decode_map_event(event, target="map_semantics")
    override = decode_map_event(event, target="map_semantics_override")

    assert semantics.tiles[0].image.getpixel((0, 0)) == 255
    assert semantics.tiles[0].image.getpixel((0, 1)) == 0
    assert override.tiles[0].image.getpixel((0, 0)) == 255
    assert override.tiles[0].image.getpixel((0, 1)) == 255


def test_integrated_map_decodes_without_optional_override_plane() -> None:
    event = parse_collection_event(
        collection_event(page_x=0, payload=integrated_payload(), sequence=1)
    )

    decoded = decode_map_event(event)

    assert decoded.warnings == ()
    assert [tile.layer for tile in decoded.tiles] == [
        "integrated-surface",
        "occupancy",
        "semantics",
        "semantics-override",
    ]
    assert decoded.tiles[0].image.getpixel((0, 0)) == 32
    assert decoded.tiles[2].image.getpixel((0, 0)) == 255
    assert decoded.tiles[3].image.getbbox() is None


def test_integrated_map_never_synthesizes_layers_without_required_data() -> None:
    event = parse_collection_event(
        collection_event(page_x=0, payload=proto_bytes(1, b"arbitrary"), sequence=1)
    )

    decoded = decode_map_event(event, target="map_integrated")

    assert decoded.tiles == ()
    assert "missing a required" in decoded.warnings[0]


def test_public_map_api_rejects_unknown_explicit_target() -> None:
    event = parse_collection_event(
        collection_event(page_x=0, payload=r8_payload(bytes(1024)), sequence=1)
    )

    with pytest.raises(MapDecodeError, match="unsupported map target"):
        decode_map_event(event, target="map_typo")  # type: ignore[arg-type]
    with pytest.raises(MapDecodeError, match="unsupported map target"):
        MapCollectionState(target="map_typo")  # type: ignore[arg-type]


def test_mosaics_support_canonical_and_native_app_orientation() -> None:
    red = Image.new("RGBA", (32, 32), (255, 0, 0, 255))
    blue = Image.new("RGBA", (32, 32), (0, 0, 255, 255))
    tiles = (
        MapTile(1, 0, 0, "floor", red, "map_compressed_rgb"),
        MapTile(1, 1, 0, "floor", blue, "map_compressed_rgb"),
    )

    canonical = build_mosaics(tiles)[0]
    assert canonical.image.size == (64, 32)
    assert canonical.image.getpixel((0, 0)) == (255, 0, 0, 255)
    assert canonical.image.getpixel((32, 0)) == (0, 0, 255, 255)

    native = build_mosaics(tiles, orientation="native")[0]
    assert native.image.size == (32, 64)
    assert native.bounds.min_y == -2
    assert native.image.getpixel((0, 0)) == (0, 0, 255, 255)
    assert native.image.getpixel((0, 32)) == (255, 0, 0, 255)


def test_mosaic_preserves_semitransparent_rgba_pixels() -> None:
    color = (100, 50, 25, 128)
    tile = MapTile(
        1,
        0,
        0,
        "floor",
        Image.new("RGBA", (32, 32), color),
        "map_compressed_rgb",
    )

    mosaic = build_mosaics((tile,))[0]

    assert mosaic.image.getpixel((0, 0)) == color


def test_mosaic_size_guard_includes_output_scale() -> None:
    tile = MapTile(
        1,
        0,
        0,
        "floor",
        Image.new("L", (32, 32)),
        "map-r8",
    )

    with pytest.raises(MapDecodeError, match="refusing 64x64"):
        build_mosaics((tile,), scale=2, max_pixels=4095)


def test_save_mosaics_uses_private_file_permissions(tmp_path) -> None:
    tile = MapTile(
        1,
        0,
        0,
        "floor",
        Image.new("L", (32, 32), 5),
        "map-r8",
    )
    mosaic = build_mosaics((tile,))[0]

    paths = save_mosaics((mosaic,), tmp_path / "maps")

    assert len(paths) == 1
    assert paths[0].name == "mission-00000001-floor.png"
    assert stat.S_IMODE(paths[0].stat().st_mode) == 0o600

    with pytest.raises(FileExistsError):
        save_mosaics((mosaic,), tmp_path / "maps")


def test_transpose_rejects_non_tile_buffer() -> None:
    with pytest.raises(MapDecodeError, match="expected 1024"):
        transpose_x_major(b"too short")
