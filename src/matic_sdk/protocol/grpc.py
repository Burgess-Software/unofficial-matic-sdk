"""gRPC message framing and status handling."""

from __future__ import annotations

import struct
from dataclasses import dataclass
from urllib.parse import unquote

Metadata = tuple[tuple[str, str], ...]


class GrpcProtocolError(RuntimeError):
    """Invalid gRPC framing or metadata."""


class GrpcStatusError(RuntimeError):
    """A non-success gRPC status."""

    def __init__(self, status: int | str, message: str | None = None) -> None:
        self.status = int(status)
        self.message = unquote(message) if message else None
        suffix = f": {self.message}" if self.message else ""
        super().__init__(f"gRPC status {self.status}{suffix}")


def frame_message(payload: bytes) -> bytes:
    return b"\x00" + struct.pack(">I", len(payload)) + payload


class GrpcFrameDecoder:
    """Incrementally decode uncompressed gRPC message frames."""

    def __init__(self, *, max_message_bytes: int = 64 * 1024 * 1024) -> None:
        if max_message_bytes < 1:
            raise ValueError("max_message_bytes must be positive")
        self.max_message_bytes = max_message_bytes
        self._buffer = bytearray()

    @property
    def partial_bytes(self) -> int:
        return len(self._buffer)

    def feed(self, data: bytes) -> tuple[bytes, ...]:
        self._buffer.extend(data)
        messages: list[bytes] = []
        while len(self._buffer) >= 5:
            compressed = self._buffer[0]
            length = struct.unpack(">I", self._buffer[1:5])[0]
            if compressed != 0:
                raise GrpcProtocolError(
                    f"unsupported gRPC compression flag {compressed}"
                )
            if length > self.max_message_bytes:
                raise GrpcProtocolError(
                    f"gRPC message is {length} bytes; limit is {self.max_message_bytes}"
                )
            if len(self._buffer) < length + 5:
                break
            messages.append(bytes(self._buffer[5 : length + 5]))
            del self._buffer[: length + 5]
        return tuple(messages)

    def finish(self) -> None:
        if self._buffer:
            raise GrpcProtocolError(
                f"gRPC stream ended with {len(self._buffer)} partial frame bytes"
            )


def metadata_value(metadata: Metadata, key: str) -> str | None:
    key = key.casefold()
    return next(
        (value for name, value in reversed(metadata) if name.casefold() == key), None
    )


def grpc_status(headers: Metadata, trailers: Metadata) -> tuple[int | None, str | None]:
    merged = (*headers, *trailers)
    raw_status = metadata_value(merged, "grpc-status")
    message = metadata_value(merged, "grpc-message")
    if raw_status is None:
        return None, message
    try:
        return int(raw_status), message
    except ValueError as exc:
        raise GrpcProtocolError(f"invalid grpc-status {raw_status!r}") from exc


@dataclass(frozen=True, slots=True)
class GrpcResponse:
    messages: tuple[bytes, ...]
    headers: Metadata
    trailers: Metadata

    @property
    def status(self) -> int | None:
        return grpc_status(self.headers, self.trailers)[0]

    def raise_for_status(self, *, require_status: bool = True) -> None:
        status, message = grpc_status(self.headers, self.trailers)
        if status is None:
            if require_status:
                raise GrpcProtocolError("gRPC response ended without grpc-status")
            return
        if status != 0:
            raise GrpcStatusError(status, message)
