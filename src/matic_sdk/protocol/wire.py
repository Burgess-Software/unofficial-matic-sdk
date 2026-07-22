"""Small, schema-independent protobuf wire helpers.

Only the wire types observed in Matic's public client protocol are supported.
Keeping this layer schema-independent lets the SDK preserve unknown fields as
firmware evolves without shipping generated proprietary schemas.
"""

from __future__ import annotations

import struct
from collections.abc import Iterable
from dataclasses import dataclass
from enum import IntEnum


class ProtoWireError(ValueError):
    """Malformed or unsupported protobuf wire data."""


class WireType(IntEnum):
    VARINT = 0
    FIXED64 = 1
    LENGTH_DELIMITED = 2
    FIXED32 = 5


@dataclass(frozen=True, slots=True)
class WireField:
    number: int
    wire_type: WireType
    value: int | bytes


def encode_varint(value: int) -> bytes:
    if value < 0:
        raise ValueError("protobuf varints must be non-negative")
    output = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            output.append(byte | 0x80)
        else:
            output.append(byte)
            return bytes(output)


def decode_varint(
    data: bytes | bytearray | memoryview, offset: int = 0
) -> tuple[int, int]:
    value = 0
    shift = 0
    while offset < len(data) and shift < 70:
        byte = data[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        if byte < 0x80:
            return value, offset
        shift += 7
    raise ProtoWireError("truncated or oversized protobuf varint")


def encode_tag(field_number: int, wire_type: WireType) -> bytes:
    if field_number <= 0:
        raise ValueError("protobuf field numbers must be positive")
    return encode_varint((field_number << 3) | int(wire_type))


def encode_varint_field(field_number: int, value: int) -> bytes:
    return encode_tag(field_number, WireType.VARINT) + encode_varint(value)


def encode_bytes_field(field_number: int, value: bytes) -> bytes:
    return (
        encode_tag(field_number, WireType.LENGTH_DELIMITED)
        + encode_varint(len(value))
        + value
    )


def encode_fixed32_field(field_number: int, value: int) -> bytes:
    return encode_tag(field_number, WireType.FIXED32) + struct.pack("<I", value)


def encode_fixed64_field(field_number: int, value: int) -> bytes:
    return encode_tag(field_number, WireType.FIXED64) + struct.pack("<Q", value)


def parse_fields(data: bytes, *, max_fields: int = 100_000) -> tuple[WireField, ...]:
    fields: list[WireField] = []
    offset = 0
    while offset < len(data):
        if len(fields) >= max_fields:
            raise ProtoWireError("protobuf message exceeds the field limit")
        tag, offset = decode_varint(data, offset)
        number = tag >> 3
        wire_value = tag & 0x07
        if number == 0:
            raise ProtoWireError("protobuf field zero is invalid")
        try:
            wire_type = WireType(wire_value)
        except ValueError as exc:
            raise ProtoWireError(
                f"unsupported protobuf wire type {wire_value}"
            ) from exc

        if wire_type is WireType.VARINT:
            value, offset = decode_varint(data, offset)
        elif wire_type is WireType.FIXED64:
            end = offset + 8
            if end > len(data):
                raise ProtoWireError("truncated fixed64 field")
            value = struct.unpack("<Q", data[offset:end])[0]
            offset = end
        elif wire_type is WireType.LENGTH_DELIMITED:
            length, offset = decode_varint(data, offset)
            end = offset + length
            if end > len(data):
                raise ProtoWireError("truncated length-delimited field")
            value = data[offset:end]
            offset = end
        else:
            end = offset + 4
            if end > len(data):
                raise ProtoWireError("truncated fixed32 field")
            value = struct.unpack("<I", data[offset:end])[0]
            offset = end
        fields.append(WireField(number, wire_type, value))
    return tuple(fields)


def bytes_values(fields: Iterable[WireField], number: int) -> tuple[bytes, ...]:
    return tuple(
        field.value
        for field in fields
        if field.number == number
        and field.wire_type is WireType.LENGTH_DELIMITED
        and isinstance(field.value, bytes)
    )


def integer_values(fields: Iterable[WireField], number: int) -> tuple[int, ...]:
    return tuple(
        field.value
        for field in fields
        if field.number == number
        and field.wire_type in (WireType.VARINT, WireType.FIXED32, WireType.FIXED64)
        and isinstance(field.value, int)
    )


def first_bytes(fields: Iterable[WireField], number: int) -> bytes | None:
    return next(iter(bytes_values(fields, number)), None)


def first_integer(fields: Iterable[WireField], number: int) -> int | None:
    return next(iter(integer_values(fields, number)), None)
