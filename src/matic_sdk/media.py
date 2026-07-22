"""Recover self-delimiting WebP images embedded in collection payloads."""

from __future__ import annotations

import hashlib
import struct
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from matic_sdk._private import ensure_private_directory, open_new_private


@dataclass(frozen=True, slots=True)
class EmbeddedWebP:
    """A validated WebP RIFF container found inside a larger byte stream."""

    offset: int
    data: bytes = field(repr=False)
    width: int
    height: int
    sha256: str


def webp_dimensions(data: bytes) -> tuple[int, int] | None:
    """Read dimensions from a bounded VP8X, VP8, or VP8L WebP container."""

    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        return None
    riff_size = struct.unpack_from("<I", data, 4)[0]
    container_end = riff_size + 8
    if riff_size < 4 or container_end > len(data):
        return None

    offset = 12
    dimensions: tuple[int, int] | None = None
    while offset + 8 <= container_end:
        kind = data[offset : offset + 4]
        size = struct.unpack_from("<I", data, offset + 4)[0]
        payload_start = offset + 8
        payload_end = payload_start + size
        if payload_end > container_end:
            return None
        chunk = data[payload_start:payload_end]
        found: tuple[int, int] | None = None
        if kind == b"VP8X" and len(chunk) >= 10:
            found = (
                1 + int.from_bytes(chunk[4:7], "little"),
                1 + int.from_bytes(chunk[7:10], "little"),
            )
        elif kind == b"VP8 " and len(chunk) >= 10 and chunk[3:6] == b"\x9d\x01\x2a":
            found = (
                struct.unpack_from("<H", chunk, 6)[0] & 0x3FFF,
                struct.unpack_from("<H", chunk, 8)[0] & 0x3FFF,
            )
        elif kind == b"VP8L" and len(chunk) >= 5 and chunk[0] == 0x2F:
            packed = int.from_bytes(chunk[1:5], "little")
            found = (packed & 0x3FFF) + 1, ((packed >> 14) & 0x3FFF) + 1
        if found is not None:
            if dimensions is not None or found[0] < 1 or found[1] < 1:
                return None
            dimensions = found
        offset = payload_end + (size & 1)
        if offset > container_end:
            return None
    return dimensions if offset == container_end else None


def extract_embedded_webps(data: bytes) -> tuple[EmbeddedWebP, ...]:
    """Return validated, non-overlapping WebP containers found in ``data``."""

    images: list[EmbeddedWebP] = []
    cursor = 0
    while True:
        offset = data.find(b"RIFF", cursor)
        if offset < 0:
            return tuple(images)
        if offset + 12 <= len(data) and data[offset + 8 : offset + 12] == b"WEBP":
            riff_size = struct.unpack_from("<I", data, offset + 4)[0]
            total_size = riff_size + 8
            end = offset + total_size
            if riff_size >= 4 and end <= len(data):
                encoded = data[offset:end]
                dimensions = webp_dimensions(encoded)
                if dimensions is not None:
                    images.append(
                        EmbeddedWebP(
                            offset=offset,
                            data=encoded,
                            width=dimensions[0],
                            height=dimensions[1],
                            sha256=hashlib.sha256(encoded).hexdigest(),
                        )
                    )
                    cursor = end
                    continue
        cursor = offset + 4


def save_embedded_webps(
    images: Iterable[EmbeddedWebP],
    output_directory: str | Path,
    *,
    prefix: str = "image",
) -> tuple[Path, ...]:
    """Write extracted images using deterministic private filenames."""

    safe_prefix = "".join(
        character if character.isalnum() or character in "-_" else "-"
        for character in prefix
    ).strip("-")
    if not safe_prefix:
        safe_prefix = "image"
    output = ensure_private_directory(output_directory)
    paths: list[Path] = []
    for index, image in enumerate(images):
        path = output / f"{safe_prefix}-{index:04d}.webp"
        with open_new_private(path) as handle:
            handle.write(image.data)
        paths.append(path)
    return tuple(paths)


__all__ = [
    "EmbeddedWebP",
    "extract_embedded_webps",
    "save_embedded_webps",
    "webp_dimensions",
]
