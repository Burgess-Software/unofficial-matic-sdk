"""Typed values produced by the Matic map collection decoders."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum, StrEnum
from types import MappingProxyType
from typing import TYPE_CHECKING, ClassVar, Literal, TypeAlias

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


class MapValueKind(StrEnum):
    """Typed categorical plane carried by a decoded map tile."""

    GEOMETRIC_OCCUPANCY = "geometric_occupancy"
    SEMANTICS = "semantics"
    SEMANTICS_OVERRIDE = "semantics_override"


class GeometricOccupancy(Enum):
    """App-facing geometric occupancy categories."""

    FREE = 0
    UNKNOWN = 1
    HOLE = 2
    OBSTACLE = 3
    TOEKICK = 4
    LOW_OBSTACLE = 5
    HIGH_TOEKICK = 6


class SemanticsKind(Enum):
    """App-facing floor and retained perception categories."""

    UNKNOWN = 0
    HARD_FLOOR = 1
    CARPET = 2
    WIRE = 3
    POOP = 4
    PET = 5


class SemanticsOverrideMapValue(Enum):
    """Categorical values stored in the semantic-override map plane."""

    UNSET = 0
    HARDFLOOR_ALLOW_WIRE = 1
    CARPET_ALLOW_WIRE = 2
    HARDFLOOR_DISALLOW_WIRE = 3
    CARPET_DISALLOW_WIRE = 4


@dataclass(frozen=True, slots=True, order=True)
class UnknownMapValue:
    """A forward-compatible categorical value not named by this SDK."""

    code: int

    def __post_init__(self) -> None:
        if isinstance(self.code, bool) or not isinstance(self.code, int):
            raise TypeError("unknown map value code must be an integer")
        if not 0 <= self.code <= 0xFF:
            raise ValueError("unknown map value must fit in one byte")

    @property
    def name(self) -> str:
        return f"unknown_{self.code}"


MapCellValue: TypeAlias = (
    GeometricOccupancy | SemanticsKind | SemanticsOverrideMapValue | UnknownMapValue
)


@dataclass(frozen=True, slots=True)
class MapClassification:
    """Lossless categorical values for one canonical 32 by 32 map tile.

    ``codes`` are canonical row-major values, so ``code_at(x, y)`` and
    ``value_at(x, y)`` use the same coordinates as the rendered tile image.
    Unknown firmware values are returned as :class:`UnknownMapValue`.
    """

    kind: MapValueKind
    codes: bytes = field(repr=False)

    width: ClassVar[int] = 32
    height: ClassVar[int] = 32

    def __post_init__(self) -> None:
        if not isinstance(self.kind, MapValueKind):
            raise TypeError("map classification kind must be MapValueKind")
        if not isinstance(self.codes, bytes):
            raise TypeError("map classification codes must be immutable bytes")
        if len(self.codes) != self.width * self.height:
            raise ValueError("map classification must contain exactly 1,024 values")

    def code_at(self, x: int, y: int) -> int:
        """Return the exact firmware code at one canonical tile coordinate."""

        if (
            isinstance(x, bool)
            or not isinstance(x, int)
            or isinstance(y, bool)
            or not isinstance(y, int)
        ):
            raise TypeError("map classification coordinates must be integers")
        if not 0 <= x < self.width or not 0 <= y < self.height:
            raise IndexError("map classification coordinate is outside 32x32 tile")
        return self.codes[y * self.width + x]

    def value_at(self, x: int, y: int) -> MapCellValue:
        """Return a known enum member or a lossless unknown-value wrapper."""

        return self._decode(self.code_at(x, y))

    @property
    def counts(self) -> Mapping[MapCellValue, int]:
        """Count typed values without discarding unknown firmware codes."""

        by_value = {
            self._decode(code): count
            for code, count in sorted(Counter(self.codes).items())
        }
        return MappingProxyType(by_value)

    @property
    def named_counts(self) -> Mapping[str, int]:
        """Return stable lowercase names suitable for logs and JSON."""

        named = {
            (
                value.name if isinstance(value, UnknownMapValue) else value.name.lower()
            ): count
            for value, count in self.counts.items()
        }
        return MappingProxyType(named)

    def _decode(self, code: int) -> MapCellValue:
        try:
            if self.kind is MapValueKind.GEOMETRIC_OCCUPANCY:
                return GeometricOccupancy(code)
            if self.kind is MapValueKind.SEMANTICS:
                return SemanticsKind(code)
            return SemanticsOverrideMapValue(code)
        except ValueError:
            return UnknownMapValue(code)


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
    classification: MapClassification | None = None


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
