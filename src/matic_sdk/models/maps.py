"""Typed values produced by the Matic map collection decoders."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from PIL import Image


MapTarget = Literal[
    "map_compressed_rgb",
    "map_compressed_rgb_higher",
    "map_integrated",
    "map_combined_coverage",
    "map_semantics",
    "map_semantics_override",
    "map-r8",
]
MapOrientation = Literal["canonical", "native"]


@dataclass(frozen=True, slots=True)
class CollectionEvent:
    """One add or delete event from a Hermes collection stream.

    ``payload`` is ``None`` for a delete or for an upsert whose value encoding
    is not understood. ``value_present`` preserves that distinction. ``key``
    is retained because Hermes collection identity is the encoded key, even
    when two compatible key encodings resolve to the same map coordinate.
    """

    key: bytes = field(repr=False)
    payload: bytes | None = field(repr=False)
    value_present: bool
    page_x: int | None
    page_y: int | None
    mission_id: int | None
    sequence: int | None
    started_at_ns: int | None
    value_tag: int | None = None
    value_id: bytes | None = field(default=None, repr=False)

    @property
    def operation(self) -> Literal["add", "delete"]:
        return "add" if self.value_present else "delete"


@dataclass(frozen=True, slots=True)
class MapTile:
    """A decoded 32 by 32 map layer in canonical page coordinates."""

    mission_id: int
    page_x: int
    page_y: int
    layer: str
    image: Image.Image
    target: MapTarget
    sequence: int | None = None


@dataclass(frozen=True, slots=True)
class MapBounds:
    """Inclusive page-coordinate bounds for a mosaic."""

    min_x: int
    max_x: int
    min_y: int
    max_y: int


@dataclass(frozen=True, slots=True)
class MapMosaic:
    """An assembled map layer plus enough metadata to place it in space."""

    mission_id: int
    layer: str
    image: Image.Image
    bounds: MapBounds
    tile_count: int
    orientation: MapOrientation
    y_down: bool


@dataclass(frozen=True, slots=True)
class DecodedMapEvent:
    """Decoded layers and non-fatal format observations for one event."""

    event: CollectionEvent
    tiles: tuple[MapTile, ...]
    warnings: tuple[str, ...] = ()
