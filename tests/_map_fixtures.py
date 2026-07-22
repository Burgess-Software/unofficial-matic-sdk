"""Synthetic protobuf and media builders with no captured device data."""

from __future__ import annotations

import struct
from collections.abc import Mapping, Sequence

TILE_SIZE = 32
PIXELS_PER_TILE = TILE_SIZE * TILE_SIZE


def varint(value: int) -> bytes:
    value &= 0xFFFF_FFFF_FFFF_FFFF
    encoded = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            encoded.append(byte | 0x80)
        else:
            encoded.append(byte)
            return bytes(encoded)


def proto_varint(number: int, value: int) -> bytes:
    return varint(number << 3) + varint(value)


def proto_sint32(number: int, value: int) -> bytes:
    encoded = ((value << 1) ^ (value >> 31)) & 0xFFFF_FFFF
    return proto_varint(number, encoded)


def proto_bytes(number: int, value: bytes) -> bytes:
    return varint((number << 3) | 2) + varint(len(value)) + value


def proto_fixed32(number: int, value: int) -> bytes:
    return varint((number << 3) | 5) + struct.pack("<I", value)


def fast_bytes(value: bytes) -> bytes:
    return proto_bytes(1, value)


def page_key(
    page_x: int,
    page_y: int,
    *,
    mission_id: int = 0x1234ABCD,
    alternate_suffix: bytes = b"",
) -> bytes:
    page = proto_sint32(3, page_x) + proto_sint32(4, page_y)
    mission = proto_fixed32(2, mission_id)
    return proto_bytes(1, page) + proto_bytes(2, mission) + alternate_suffix


def collection_event(
    *,
    page_x: int,
    page_y: int = 0,
    payload: bytes | None,
    sequence: int,
    mission_id: int = 0x1234ABCD,
    alternate_suffix: bytes = b"",
) -> bytes:
    key = page_key(
        page_x,
        page_y,
        mission_id=mission_id,
        alternate_suffix=alternate_suffix,
    )
    response = proto_bytes(1, key)
    if payload is not None:
        value = proto_bytes(3, bytes(range(16))) + proto_bytes(5, fast_bytes(payload))
        response += proto_bytes(2, value)
    sequence_id = proto_varint(1, 1_000_000) + proto_varint(2, sequence)
    return response + proto_bytes(3, sequence_id)


def floor_payload(
    pixels: Mapping[tuple[int, int], tuple[int, int, int, int]],
    *,
    background: tuple[int, int, int, int] = (0, 0, 0, 0),
) -> bytes:
    channels = [bytearray() for _ in range(4)]
    for x in range(TILE_SIZE):
        for y in range(TILE_SIZE):
            color = pixels.get((x, y), background)
            for channel, value in zip(channels, color, strict=True):
                channel.extend(struct.pack("<e", float(value)))
    planes = b"".join(channels)
    ndarray = proto_varint(1, 8) + proto_bytes(5, fast_bytes(planes))
    map_tile = proto_bytes(1, ndarray)
    return proto_bytes(7, proto_bytes(1, map_tile))


def set_surface_bit(surface: bytearray, pixel: int, depth: int) -> None:
    bit_index = pixel * 24 + depth
    surface[bit_index >> 3] |= 0x80 >> (bit_index & 7)


def compressed_payload(
    points: Sequence[tuple[int, int, tuple[int, int, int]]],
    *,
    truncate_rgb: bool = False,
) -> bytes:
    surface = bytearray(3072)
    colors: dict[tuple[int, int], tuple[int, int, int]] = {}
    for pixel, depth, color in points:
        set_surface_bit(surface, pixel, depth)
        colors[(pixel, depth)] = color
    rgb = bytearray()
    for pixel in range(PIXELS_PER_TILE):
        for depth in range(24):
            color = colors.get((pixel, depth))
            if color is not None:
                rgb.extend(color)
    if truncate_rgb and rgb:
        rgb.pop()
    surface_array = proto_bytes(5, fast_bytes(bytes(surface)))
    return proto_bytes(4, surface_array) + proto_bytes(6, fast_bytes(bytes(rgb)))


def integrated_payload(*, include_override: bool = False) -> bytes:
    surface = bytearray(3072)
    set_surface_bit(surface, 0, 7)
    surface_array = proto_bytes(5, fast_bytes(bytes(surface)))
    occupancy = bytes([0x10]) + bytes(511)
    semantics = bytes([0x20]) + bytes(511)
    payload = (
        proto_bytes(5, surface_array)
        + proto_bytes(7, fast_bytes(occupancy))
        + proto_bytes(8, fast_bytes(semantics))
    )
    if include_override:
        payload += proto_bytes(9, fast_bytes(bytes([0x40]) + bytes(511)))
    return payload


def r8_payload(plane: bytes) -> bytes:
    if len(plane) != 1024:
        raise ValueError("R8 fixture must contain exactly 1,024 bytes")
    return proto_bytes(1, proto_bytes(5, fast_bytes(plane)))


def grpc_frame(payload: bytes) -> bytes:
    return b"\x00" + len(payload).to_bytes(4, "big") + payload


def riff_webp(kind: bytes, chunk: bytes) -> bytes:
    payload = kind + len(chunk).to_bytes(4, "little") + chunk
    if len(chunk) & 1:
        payload += b"\x00"
    body = b"WEBP" + payload
    return b"RIFF" + len(body).to_bytes(4, "little") + body


def vp8x_webp(width: int, height: int) -> bytes:
    chunk = b"\x00\x00\x00\x00" + (width - 1).to_bytes(3, "little")
    chunk += (height - 1).to_bytes(3, "little")
    return riff_webp(b"VP8X", chunk)


def vp8_webp(width: int, height: int) -> bytes:
    chunk = b"\x00\x00\x00\x9d\x01\x2a"
    chunk += width.to_bytes(2, "little") + height.to_bytes(2, "little")
    return riff_webp(b"VP8 ", chunk)


def vp8l_webp(width: int, height: int) -> bytes:
    packed = (width - 1) | ((height - 1) << 14)
    return riff_webp(b"VP8L", b"\x2f" + packed.to_bytes(4, "little"))
