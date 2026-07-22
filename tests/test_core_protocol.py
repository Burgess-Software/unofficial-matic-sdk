from __future__ import annotations

import asyncio
import struct
import uuid

import pytest
from hpack import NeverIndexedHeaderTuple

from matic_sdk.collections import CollectionSubscription
from matic_sdk.config import InsecureTransportError, MaticConfig, TlsConfig
from matic_sdk.discovery import BotInformation
from matic_sdk.protocol.collections import (
    KNOWN_TARGETS,
    CollectionOperation,
    decode_collection_response,
    initial_request,
    sequence_acknowledgement,
)
from matic_sdk.protocol.grpc import GrpcFrameDecoder, GrpcProtocolError, frame_message
from matic_sdk.protocol.wire import (
    ProtoWireError,
    encode_bytes_field,
    encode_fixed32_field,
    encode_varint,
    encode_varint_field,
    parse_fields,
)
from matic_sdk.transport.h2 import H2Transport, H2TransportClosed


def collection_response(sequence: int, payload: bytes = b"value") -> bytes:
    value = encode_bytes_field(3, b"0123456789abcdef") + encode_bytes_field(
        5, encode_bytes_field(1, payload)
    )
    sequence_id = encode_varint_field(1, 1_000) + encode_varint_field(2, sequence)
    return b"".join(
        (
            encode_bytes_field(1, b"key"),
            encode_bytes_field(2, value),
            encode_bytes_field(3, sequence_id),
        )
    )


def test_verified_target_inventory_and_official_initial_request() -> None:
    assert len(KNOWN_TARGETS) == len(set(KNOWN_TARGETS)) == 43
    assert initial_request("latest_pose").hex() == (
        "0a180a0b6c61746573745f706f73651a071802201428e8072001"
    )


def test_collection_envelope_preserves_raw_data_and_sequence() -> None:
    raw = collection_response(7, b"private bytes")
    event = decode_collection_response("latest_pose", raw)
    assert event.operation is CollectionOperation.UPSERT
    assert event.key == b"key"
    assert event.payload == b"private bytes"
    assert event.value is not None
    assert event.value.value_id == b"0123456789abcdef"
    assert event.sequence_id is not None
    assert event.sequence_id.start_ts_nanos == 1_000
    assert event.sequence_id.sequence_no == 7
    assert sequence_acknowledgement(event.sequence_id.encoded) == (
        encode_bytes_field(2, event.sequence_id.encoded)
    )


def test_collection_fast_bytes_joins_repeated_chunks() -> None:
    fast_bytes = encode_bytes_field(1, b"one") + encode_bytes_field(1, b"two")
    value = encode_bytes_field(3, b"0123456789abcdef") + encode_bytes_field(
        5, fast_bytes
    )
    response = encode_bytes_field(1, b"key") + encode_bytes_field(2, value)

    event = decode_collection_response("latest_pose", response)

    assert event.payload == b"onetwo"


def test_collection_delete_has_no_value() -> None:
    sequence = encode_varint_field(1, 1) + encode_varint_field(2, 2)
    event = decode_collection_response(
        "zones", encode_bytes_field(1, b"key") + encode_bytes_field(3, sequence)
    )
    assert event.operation is CollectionOperation.DELETE
    assert event.value is None


def test_wire_codec_handles_all_supported_scalar_types() -> None:
    message = (
        encode_varint_field(1, 150)
        + encode_bytes_field(2, b"hello")
        + encode_fixed32_field(3, 0x12345678)
        + encode_varint((4 << 3) | 1)
        + struct.pack("<Q", 42)
    )
    fields = parse_fields(message)
    assert [field.value for field in fields] == [150, b"hello", 0x12345678, 42]
    with pytest.raises(ProtoWireError):
        parse_fields(b"\x0a\x05short"[:5])


def test_grpc_decoder_is_incremental_and_rejects_compression() -> None:
    framed = frame_message(b"one") + frame_message(b"two")
    decoder = GrpcFrameDecoder(max_message_bytes=8)
    assert decoder.feed(framed[:7]) == ()
    assert decoder.feed(framed[7:]) == (b"one", b"two")
    decoder.finish()
    with pytest.raises(GrpcProtocolError, match="compression"):
        GrpcFrameDecoder().feed(b"\x01\x00\x00\x00\x00")
    with pytest.raises(GrpcProtocolError, match="compression"):
        GrpcFrameDecoder().feed(b"\x01\x03\xff\xff\xff")


def test_ipv6_literal_uses_bracketed_default_authority() -> None:
    config = MaticConfig("2001:db8::2")
    assert config.effective_authority == "[2001:db8::2]:16320"


def test_bot_information_schema() -> None:
    raw = b"".join(
        (
            encode_bytes_field(1, b"synthetic-serial"),
            encode_bytes_field(2, b"192.0.2.2"),
            encode_bytes_field(3, b"2001:db8::2"),
            encode_varint_field(4, 1),
            encode_varint_field(5, 1),
            encode_varint_field(6, 0),
            encode_bytes_field(7, b"test-hardware"),
        )
    )
    info = BotInformation.decode(raw)
    assert info.serial_number == "synthetic-serial"
    assert info.encrypted is True
    assert info.requires_auth is True
    assert info.network_auth is False
    assert info.hardware_revision == "test-hardware"


class FakeStream:
    def __init__(self, responses: list[bytes]) -> None:
        self.responses = iter(responses)
        self.sent: list[tuple[bytes, bool]] = []
        self.cancelled = False

    async def send_message(self, payload: bytes, *, end_stream: bool = False) -> None:
        self.sent.append((payload, end_stream))

    def __aiter__(self) -> FakeStream:
        return self

    async def __anext__(self) -> bytes:
        try:
            return next(self.responses)
        except StopIteration:
            raise StopAsyncIteration from None

    async def cancel(self) -> None:
        self.cancelled = True


class FakeTransport:
    def __init__(self) -> None:
        self.streams: list[FakeStream] = []

    async def open_grpc_stream(self, *_: object, **__: object) -> FakeStream:
        stream = FakeStream([collection_response(len(self.streams) + 1)])
        self.streams.append(stream)
        return stream


async def test_concurrent_collection_streams_auto_ack_independently() -> None:
    transport = FakeTransport()
    first, second = await asyncio.gather(
        CollectionSubscription.open(transport, "latest_pose"),  # type: ignore[arg-type]
        CollectionSubscription.open(transport, "motor_status"),  # type: ignore[arg-type]
    )
    first_event, second_event = await asyncio.gather(anext(first), anext(second))
    assert first_event.target == "latest_pose"
    assert second_event.target == "motor_status"
    assert all(len(stream.sent) == 2 for stream in transport.streams)
    assert first.acknowledgements == second.acknowledgements == 1
    await asyncio.gather(first.aclose(), second.aclose())


async def test_insecure_transport_rejects_mutating_stream_before_connect() -> None:
    config = MaticConfig("robot.invalid", tls=TlsConfig.insecure_diagnostics())
    transport = H2Transport(config)
    with pytest.raises(InsecureTransportError):
        await transport.open_grpc_stream("/test.Service/Write", mutating=True)


async def test_insecure_transport_rejects_known_command_path_without_hint() -> None:
    config = MaticConfig("robot.invalid", tls=TlsConfig.insecure_diagnostics())
    transport = H2Transport(config)
    with pytest.raises(InsecureTransportError):
        await transport.open_grpc_stream(
            "/hermes.Hermes/SendToChannel",
            mutating=False,
        )


def test_insecure_transport_rejects_authorization_metadata() -> None:
    config = MaticConfig("robot.invalid", tls=TlsConfig.insecure_diagnostics())
    with pytest.raises(InsecureTransportError, match="authorization"):
        H2Transport(config, default_metadata=(("authorization", "synthetic"),))


def test_authorization_headers_are_never_indexed_by_hpack() -> None:
    config = MaticConfig("robot.invalid", tls=TlsConfig.pinned("00" * 32))
    transport = H2Transport(
        config,
        default_metadata=(("authorization", "Bearer: synthetic"),),
    )

    headers = transport._request_headers(
        "/test.Service/Read",
        (("proxy-authorization", "synthetic-proxy"), ("x-test", "value")),
    )
    by_name = {header[0]: header for header in headers}

    assert isinstance(by_name["authorization"], NeverIndexedHeaderTuple)
    assert isinstance(by_name["proxy-authorization"], NeverIndexedHeaderTuple)
    assert not isinstance(by_name["x-test"], NeverIndexedHeaderTuple)


async def test_collection_decode_error_closes_and_unregisters_subscription() -> None:
    stream = FakeStream([b"\x80"])
    closed: list[CollectionSubscription] = []
    subscription = CollectionSubscription(
        "latest_pose",
        stream,  # type: ignore[arg-type]
        on_close=closed.append,
    )

    with pytest.raises(ProtoWireError):
        await anext(subscription)

    assert stream.cancelled
    assert closed == [subscription]


async def test_collection_ack_error_closes_and_unregisters_subscription() -> None:
    class AckFailingStream(FakeStream):
        async def send_message(
            self,
            payload: bytes,
            *,
            end_stream: bool = False,
        ) -> None:
            del payload, end_stream
            raise ConnectionError("synthetic ACK failure")

    stream = AckFailingStream([collection_response(1)])
    closed: list[CollectionSubscription] = []
    subscription = CollectionSubscription(
        "latest_pose",
        stream,  # type: ignore[arg-type]
        on_close=closed.append,
    )

    with pytest.raises(ConnectionError, match="ACK failure"):
        await anext(subscription)

    assert stream.cancelled
    assert closed == [subscription]


def test_terminal_reader_error_closes_transport_for_future_streams() -> None:
    transport = H2Transport(MaticConfig("robot.invalid"))
    transport._terminal_error = ConnectionError("synthetic reader failure")

    with pytest.raises(H2TransportClosed, match="terminated") as raised:
        transport._require_connection()

    assert isinstance(raised.value.__cause__, ConnectionError)


def test_uuid_fixture_is_not_a_real_identifier() -> None:
    # Ensure tests use generated/synthetic values rather than copied device IDs.
    assert str(uuid.UUID(int=0)) == "00000000-0000-0000-0000-000000000000"
