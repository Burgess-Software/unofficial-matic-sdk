from __future__ import annotations

import hashlib
import stat

import pytest

from matic_sdk.media import (
    extract_embedded_webps,
    save_embedded_webps,
    webp_dimensions,
)
from tests._map_fixtures import (
    riff_webp,
    vp8_webp,
    vp8l_webp,
    vp8x_webp,
)


@pytest.mark.parametrize(
    ("encoded", "expected"),
    [
        (vp8x_webp(640, 480), (640, 480)),
        (vp8_webp(320, 200), (320, 200)),
        (vp8l_webp(123, 456), (123, 456)),
    ],
)
def test_webp_dimensions_supports_all_released_container_variants(
    encoded: bytes, expected: tuple[int, int]
) -> None:
    assert webp_dimensions(encoded) == expected


def test_webp_dimensions_rejects_truncated_and_non_webp_data() -> None:
    encoded = vp8x_webp(10, 20)

    assert webp_dimensions(encoded[:-1]) is None
    assert webp_dimensions(b"not a webp") is None


def test_webp_dimensions_validates_chunks_after_image_header() -> None:
    encoded = vp8x_webp(10, 20)
    malformed_chunk = b"BAD!" + (100).to_bytes(4, "little") + b"short"
    body = b"WEBP" + encoded[12:] + malformed_chunk
    malformed = b"RIFF" + len(body).to_bytes(4, "little") + body

    assert webp_dimensions(malformed) is None


def test_extract_embedded_webps_finds_valid_non_overlapping_containers() -> None:
    first = vp8x_webp(10, 20)
    second = vp8l_webp(30, 40)
    invalid = b"RIFF" + (500).to_bytes(4, "little") + b"WEBPbad"
    prefix = b"protobuf-prefix"
    between = b"wrapped" + invalid + b"suffix"

    images = extract_embedded_webps(prefix + first + between + second)

    assert [(image.width, image.height) for image in images] == [(10, 20), (30, 40)]
    assert images[0].offset == len(prefix)
    assert images[0].data == first
    assert images[0].sha256 == hashlib.sha256(first).hexdigest()
    assert images[1].offset == len(prefix) + len(first) + len(between)


def test_nested_riff_signature_is_not_returned_twice() -> None:
    inner = vp8l_webp(2, 3)
    dimensions = b"\x00\x00\x00\x00" + (4).to_bytes(3, "little")
    dimensions += (5).to_bytes(3, "little")
    outer_body = b"VP8X" + len(dimensions).to_bytes(4, "little") + dimensions
    outer_body += b"META" + len(inner).to_bytes(4, "little") + inner
    outer = b"RIFF" + (4 + len(outer_body)).to_bytes(4, "little")
    outer += b"WEBP" + outer_body

    images = extract_embedded_webps(outer)

    assert len(images) == 1
    assert images[0].data == outer
    assert (images[0].width, images[0].height) == (5, 6)


def test_unknown_chunk_before_image_data_is_skipped() -> None:
    unknown = riff_webp(b"JUNK", b"odd")
    # Replace the JUNK-only container's body with JUNK followed by a VP8L chunk.
    junk_chunk = unknown[12:]
    image_chunk = vp8l_webp(7, 8)[12:]
    body = b"WEBP" + junk_chunk + image_chunk
    encoded = b"RIFF" + len(body).to_bytes(4, "little") + body

    assert webp_dimensions(encoded) == (7, 8)


def test_save_embedded_webps_uses_sanitized_private_filenames(tmp_path) -> None:
    images = extract_embedded_webps(vp8x_webp(10, 20) + vp8l_webp(30, 40))

    paths = save_embedded_webps(images, tmp_path / "media", prefix="map thumbnail")

    assert [path.name for path in paths] == [
        "map-thumbnail-0000.webp",
        "map-thumbnail-0001.webp",
    ]
    assert paths[0].read_bytes() == images[0].data
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in paths)

    with pytest.raises(FileExistsError):
        save_embedded_webps(images, tmp_path / "media", prefix="map thumbnail")
