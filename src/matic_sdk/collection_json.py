"""Privacy-aware JSON views of friendly collection models."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import fields, is_dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Any
from uuid import UUID

from matic_sdk.models.collections import CollectionModel, MediaAsset

_REDACTED = "[REDACTED]"
_OMITTED_FIELDS = frozenset({"data", "fields", "raw_payload"})
_SENSITIVE_FIELDS = frozenset(
    {
        "current_network_ssid",
        "email",
        "favorite_room",
        "floor_label",
        "ip_address",
        "manual_code",
        "name",
        "qr_code",
        "sha256",
    }
)


def _image_summary(value: object) -> dict[str, object] | None:
    """Describe a Pillow image without importing the optional dependency."""

    value_type = type(value)
    if not value_type.__module__.startswith("PIL."):
        return None
    size = getattr(value, "size", None)
    mode = getattr(value, "mode", None)
    if (
        not isinstance(size, tuple)
        or len(size) != 2
        or not all(isinstance(dimension, int) for dimension in size)
    ):
        return {"type": value_type.__name__}
    return {
        "type": value_type.__name__,
        "mode": mode if isinstance(mode, str) else None,
        "width": size[0],
        "height": size[1],
    }


def _json_value(value: object, *, include_sensitive: bool) -> object:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, Enum):
        return _json_value(value.value, include_sensitive=include_sensitive)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, timedelta):
        return value.total_seconds()
    if isinstance(value, bytes):
        return {"byte_count": len(value)}
    if isinstance(value, MediaAsset):
        result: dict[str, object] = {
            "type": type(value).__name__,
            "media_type": value.media_type,
            "byte_count": len(value.data),
            "width": value.width,
            "height": value.height,
        }
        if value.sha256 is not None:
            result["sha256"] = value.sha256 if include_sensitive else _REDACTED
        return result

    image = _image_summary(value)
    if image is not None:
        return image

    if is_dataclass(value) and not isinstance(value, type):
        result = {"type": type(value).__name__}
        for model_field in fields(value):
            name = model_field.name
            if name in _OMITTED_FIELDS:
                continue
            item = getattr(value, name)
            if name in _SENSITIVE_FIELDS and item is not None and not include_sensitive:
                result[name] = _REDACTED
            else:
                result[name] = _json_value(
                    item,
                    include_sensitive=include_sensitive,
                )
        return result

    if isinstance(value, Mapping):
        return {
            str(key): _json_value(item, include_sensitive=include_sensitive)
            for key, item in value.items()
        }
    if isinstance(value, Sequence):
        return [
            _json_value(item, include_sensitive=include_sensitive) for item in value
        ]
    return {"type": type(value).__name__}


def collection_model_to_dict(
    model: CollectionModel,
    *,
    include_sensitive: bool = False,
) -> dict[str, Any]:
    """Return a JSON-safe model view without raw protobuf or media bytes.

    Network identifiers, customer metadata, household labels, pairing codes,
    and stable media hashes are redacted unless ``include_sensitive`` is true.
    Raw payloads, parsed wire fields, and media data are never returned.
    """

    if not isinstance(model, CollectionModel):
        raise TypeError("expected a CollectionModel")
    result = _json_value(model, include_sensitive=include_sensitive)
    if not isinstance(result, dict):
        raise AssertionError("collection model serialization must produce an object")
    result["deleted"] = model.deleted
    return result


__all__ = ["collection_model_to_dict"]
