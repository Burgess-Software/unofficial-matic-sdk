"""Async, multiplexed HTTP/2 transport for raw gRPC streams."""

from __future__ import annotations

import asyncio
import contextlib
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass

import h2.config
import h2.connection
import h2.errors
import h2.events
import h2.exceptions
from hpack import NeverIndexedHeaderTuple

from matic_sdk.config import InsecureTransportError, MaticConfig
from matic_sdk.protocol.grpc import (
    GrpcFrameDecoder,
    GrpcResponse,
    Metadata,
    frame_message,
)
from matic_sdk.transport.tls import (
    TlsConnectionInfo,
    create_ssl_context,
    verify_peer,
)


class H2TransportError(ConnectionError):
    """The HTTP/2 connection or stream failed."""


class H2TransportClosed(H2TransportError):
    """The transport is not connected."""


class H2StreamReset(H2TransportError):
    """The peer reset a stream."""


@dataclass(frozen=True, slots=True)
class _Headers:
    values: Metadata
    trailers: bool = False


@dataclass(frozen=True, slots=True)
class _Data:
    value: bytes


@dataclass(frozen=True, slots=True)
class _Ended:
    pass


@dataclass(slots=True)
class _StreamState:
    queue: asyncio.Queue[object]
    ended: bool = False


def _metadata(values: Iterable[tuple[str, str]]) -> Metadata:
    return tuple((str(name), str(value)) for name, value in values)


class GrpcStream:
    """One bidirectional gRPC stream on a shared :class:`H2Transport`."""

    def __init__(
        self,
        transport: H2Transport,
        stream_id: int,
        state: _StreamState,
        *,
        max_message_bytes: int,
    ) -> None:
        self._transport = transport
        self.stream_id = stream_id
        self._state = state
        self._decoder = GrpcFrameDecoder(max_message_bytes=max_message_bytes)
        self._messages: deque[bytes] = deque()
        self._headers: list[tuple[str, str]] = []
        self._trailers: list[tuple[str, str]] = []
        self._received_end = False
        self._sending_ended = False
        self._locally_cancelled = False

    @property
    def headers(self) -> Metadata:
        return tuple(self._headers)

    @property
    def trailers(self) -> Metadata:
        return tuple(self._trailers)

    @property
    def sending_ended(self) -> bool:
        return self._sending_ended

    async def send_message(self, payload: bytes, *, end_stream: bool = False) -> None:
        if self._sending_ended:
            raise H2TransportError("the gRPC request side is already closed")
        await self._transport._send_data(
            self.stream_id, frame_message(payload), end_stream=end_stream
        )
        if end_stream:
            self._sending_ended = True

    async def finish_sending(self) -> None:
        if not self._sending_ended:
            await self._transport._send_data(self.stream_id, b"", end_stream=True)
            self._sending_ended = True

    def __aiter__(self) -> GrpcStream:
        return self

    async def __anext__(self) -> bytes:
        while True:
            if self._messages:
                return self._messages.popleft()
            if self._received_end:
                raise StopAsyncIteration

            event = await self._state.queue.get()
            if isinstance(event, BaseException):
                raise event
            if isinstance(event, _Headers):
                destination = self._trailers if event.trailers else self._headers
                destination.extend(event.values)
            elif isinstance(event, _Data):
                self._messages.extend(self._decoder.feed(event.value))
            elif isinstance(event, _Ended):
                self._decoder.finish()
                self._received_end = True
                if not self._locally_cancelled:
                    GrpcResponse((), self.headers, self.trailers).raise_for_status(
                        require_status=True
                    )
            if self._messages:
                return self._messages.popleft()
            if self._received_end:
                raise StopAsyncIteration

    async def cancel(self) -> None:
        if self._received_end or self._locally_cancelled:
            return
        self._locally_cancelled = True
        await self._transport._reset_stream(self.stream_id)
        self._received_end = True

    async def aclose(self) -> None:
        await self.cancel()

    async def __aenter__(self) -> GrpcStream:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.cancel()


class H2Transport:
    """A single async HTTP/2 connection supporting concurrent gRPC streams."""

    def __init__(
        self,
        config: MaticConfig,
        *,
        default_metadata: Iterable[tuple[str, str]] = (),
        user_agent: str = "unofficial-matic-sdk/0.1",
    ) -> None:
        self.config = config
        self._default_metadata = _metadata(default_metadata)
        self._assert_metadata_allowed(self._default_metadata)
        self._user_agent = user_agent
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._connection: h2.connection.H2Connection | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._streams: dict[int, _StreamState] = {}
        self._lock = asyncio.Lock()
        self._window_event = asyncio.Event()
        self._window_event.set()
        self._closing = False
        self._terminal_error: BaseException | None = None
        self.tls_info: TlsConnectionInfo | None = None

    def _assert_metadata_allowed(self, metadata: Metadata) -> None:
        if self.config.tls.verified:
            return
        if any(name.casefold() == "authorization" for name, _ in metadata):
            raise InsecureTransportError("authorization metadata requires verified TLS")

    @property
    def connected(self) -> bool:
        return (
            self._writer is not None
            and not self._writer.is_closing()
            and self._reader_task is not None
            and not self._reader_task.done()
        )

    async def connect(self) -> None:
        if self.connected:
            return
        if self._writer is not None:
            raise H2TransportError("transport cannot be reconnected after closure")
        context = create_ssl_context(self.config.tls)
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(
                    self.config.host,
                    self.config.port,
                    ssl=context,
                    server_hostname=self.config.effective_sni,
                ),
                timeout=self.config.connect_timeout,
            )
            ssl_object = self._writer.get_extra_info("ssl_object")
            if ssl_object is None:
                raise H2TransportError("Hermes connection did not negotiate TLS")
            self.tls_info = verify_peer(ssl_object, self.config.tls)
            self._connection = h2.connection.H2Connection(
                config=h2.config.H2Configuration(
                    client_side=True,
                    header_encoding="utf-8",
                )
            )
            self._connection.initiate_connection()
            await self._flush_locked()
            self._reader_task = asyncio.create_task(
                self._reader_loop(), name="matic-hermes-h2-reader"
            )
        except BaseException:
            if self._writer is not None:
                self._writer.close()
                with contextlib.suppress(Exception):
                    await self._writer.wait_closed()
            self._writer = None
            self._reader = None
            raise

    def _require_connection(self) -> h2.connection.H2Connection:
        if self._terminal_error is not None:
            raise H2TransportClosed(
                "Hermes transport terminated"
            ) from self._terminal_error
        if self._connection is None or self._writer is None or self._closing:
            raise H2TransportClosed("Hermes transport is closed")
        if self._reader_task is not None and self._reader_task.done():
            raise H2TransportClosed("Hermes transport reader is not running")
        return self._connection

    async def _flush_locked(self) -> None:
        connection = self._connection
        writer = self._writer
        if connection is None or writer is None:
            return
        pending = connection.data_to_send()
        if pending:
            writer.write(pending)
            await writer.drain()

    async def _reader_loop(self) -> None:
        assert self._reader is not None
        try:
            while not self._closing:
                incoming = await self._reader.read(65_535)
                if not incoming:
                    raise H2TransportClosed("Hermes TLS connection closed")
                async with self._lock:
                    connection = self._require_connection()
                    events = connection.receive_data(incoming)
                    for event in events:
                        if isinstance(event, h2.events.DataReceived):
                            connection.acknowledge_received_data(
                                event.flow_controlled_length, event.stream_id
                            )
                        elif isinstance(event, h2.events.WindowUpdated):
                            self._window_event.set()
                    await self._flush_locked()
                for event in events:
                    self._dispatch(event)
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            if not self._closing:
                self._terminal_error = exc
                self._fail_all(exc)
                if self._writer is not None:
                    self._writer.close()
                    with contextlib.suppress(Exception):
                        await self._writer.wait_closed()

    def _dispatch(self, event: h2.events.Event) -> None:
        if isinstance(event, h2.events.ConnectionTerminated):
            error = H2TransportError(
                f"HTTP/2 connection terminated with error {event.error_code}"
            )
            self._terminal_error = error
            self._fail_all(error)
            if self._writer is not None:
                self._writer.close()
            return
        stream_id = getattr(event, "stream_id", None)
        state = self._streams.get(stream_id)
        if state is None:
            return
        if isinstance(event, h2.events.ResponseReceived):
            state.queue.put_nowait(_Headers(_metadata(event.headers)))
        elif isinstance(event, h2.events.TrailersReceived):
            state.queue.put_nowait(_Headers(_metadata(event.headers), trailers=True))
        elif isinstance(event, h2.events.DataReceived):
            state.queue.put_nowait(_Data(bytes(event.data)))
        elif isinstance(event, h2.events.StreamReset):
            state.ended = True
            state.queue.put_nowait(
                H2StreamReset(
                    f"HTTP/2 stream {stream_id} reset with error {event.error_code}"
                )
            )
            self._streams.pop(stream_id, None)
        elif isinstance(event, h2.events.StreamEnded):
            state.ended = True
            state.queue.put_nowait(_Ended())
            self._streams.pop(stream_id, None)

    def _fail_all(self, error: BaseException) -> None:
        self._window_event.set()
        for state in tuple(self._streams.values()):
            if not state.ended:
                state.ended = True
                state.queue.put_nowait(error)
        self._streams.clear()

    def _request_headers(
        self,
        path: str,
        metadata: Iterable[tuple[str, str]],
    ) -> list[tuple[str, str] | NeverIndexedHeaderTuple]:
        if not path.startswith("/"):
            raise ValueError("gRPC path must begin with a slash")
        extras = (*self._default_metadata, *_metadata(metadata))
        for name, _ in extras:
            if name.startswith(":"):
                raise ValueError("call metadata cannot contain HTTP/2 pseudo-headers")
        protected_extras = [
            NeverIndexedHeaderTuple(name, value)
            if name.casefold() in {"authorization", "proxy-authorization"}
            else (name, value)
            for name, value in extras
        ]
        return [
            (":method", "POST"),
            (":scheme", "https"),
            (":authority", self.config.effective_authority),
            (":path", path),
            ("content-type", "application/grpc"),
            ("te", "trailers"),
            ("user-agent", self._user_agent),
            *protected_extras,
        ]

    async def open_grpc_stream(
        self,
        path: str,
        *,
        metadata: Iterable[tuple[str, str]] = (),
        mutating: bool = False,
    ) -> GrpcStream:
        call_metadata = _metadata(metadata)
        self._assert_metadata_allowed(call_metadata)
        if mutating or path == "/hermes.Hermes/SendToChannel":
            self.config.tls.assert_mutating_allowed()
        async with self._lock:
            connection = self._require_connection()
            stream_id = connection.get_next_available_stream_id()
            state = _StreamState(asyncio.Queue())
            self._streams[stream_id] = state
            try:
                connection.send_headers(
                    stream_id,
                    self._request_headers(path, call_metadata),
                    end_stream=False,
                )
                await self._flush_locked()
            except BaseException:
                self._streams.pop(stream_id, None)
                raise
        return GrpcStream(
            self,
            stream_id,
            state,
            max_message_bytes=self.config.max_message_bytes,
        )

    async def _send_data(
        self, stream_id: int, data: bytes, *, end_stream: bool
    ) -> None:
        view = memoryview(data)
        while view:
            wait_for_window = False
            async with self._lock:
                connection = self._require_connection()
                if stream_id not in self._streams:
                    raise H2TransportClosed(f"HTTP/2 stream {stream_id} is closed")
                window = min(
                    connection.local_flow_control_window(stream_id),
                    connection.max_outbound_frame_size,
                )
                if window <= 0:
                    self._window_event.clear()
                    wait_for_window = True
                else:
                    length = min(window, len(view))
                    final = end_stream and length == len(view)
                    connection.send_data(
                        stream_id, bytes(view[:length]), end_stream=final
                    )
                    view = view[length:]
                    await self._flush_locked()
            if wait_for_window:
                await self._window_event.wait()
        if not data and end_stream:
            async with self._lock:
                connection = self._require_connection()
                connection.end_stream(stream_id)
                await self._flush_locked()

    async def _reset_stream(self, stream_id: int) -> None:
        async with self._lock:
            connection = self._connection
            state = self._streams.pop(stream_id, None)
            if state is None or connection is None:
                return
            state.ended = True
            with contextlib.suppress(h2.exceptions.H2Error):
                connection.reset_stream(
                    stream_id, error_code=h2.errors.ErrorCodes.CANCEL
                )
                await self._flush_locked()

    async def unary(
        self,
        path: str,
        payload: bytes = b"",
        *,
        metadata: Iterable[tuple[str, str]] = (),
        mutating: bool = False,
        timeout: float | None = None,  # noqa: ASYNC109 - per-RPC deadline
    ) -> GrpcResponse:
        stream = await self.open_grpc_stream(path, metadata=metadata, mutating=mutating)

        async def exchange() -> GrpcResponse:
            await stream.send_message(payload, end_stream=True)
            messages = tuple([message async for message in stream])
            response = GrpcResponse(messages, stream.headers, stream.trailers)
            response.raise_for_status()
            return response

        try:
            return await asyncio.wait_for(
                exchange(), timeout=timeout or self.config.operation_timeout
            )
        except BaseException:
            await stream.cancel()
            raise

    async def close(self) -> None:
        if self._closing:
            return
        self._closing = True
        self._fail_all(H2TransportClosed("Hermes transport closed"))
        task = self._reader_task
        if task is not None:
            task.cancel()
        async with self._lock:
            if self._connection is not None:
                with contextlib.suppress(h2.exceptions.H2Error, OSError):
                    self._connection.close_connection()
                    await self._flush_locked()
        if task is not None and task is not asyncio.current_task():
            with contextlib.suppress(asyncio.CancelledError):
                await task
        if self._writer is not None:
            self._writer.close()
            with contextlib.suppress(Exception):
                await self._writer.wait_closed()

    async def __aenter__(self) -> H2Transport:
        await self.connect()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()
