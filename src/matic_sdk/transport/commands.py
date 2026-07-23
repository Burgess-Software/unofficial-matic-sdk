"""Evidence-backed Hermes command transport."""

from __future__ import annotations

import re

from matic_sdk.models.control import (
    TransportAcknowledgement,
    TransportAckStatus,
)
from matic_sdk.protocol.commands import EncodedCommand
from matic_sdk.protocol.grpc import GrpcProtocolError
from matic_sdk.protocol.wire import WireType, encode_bytes_field, parse_fields
from matic_sdk.transport.h2 import H2Transport

SEND_TO_CHANNEL_PATH = "/hermes.Hermes/SendToChannel"
HERMES_TARGET_HEADER = "hermes-target"
_CHANNEL_NAME_RE = re.compile(r"[a-z][a-z0-9_]*")


def _encode_channel_request(command: EncodedCommand) -> bytes:
    """Encode the proven ``ChannelRequest`` protobuf envelope.

    ``channel_name`` is field 1 and the already encoded command value is field
    2. Protobuf omits field 2 for a valid empty command message. The same
    channel name is also sent as ``hermes-target`` metadata.
    """

    if not isinstance(command.payload, bytes):
        raise ValueError("command payload must be bytes")
    if not isinstance(command.hermes_target, str) or not _CHANNEL_NAME_RE.fullmatch(
        command.hermes_target
    ):
        raise ValueError("Hermes target must be a lowercase ASCII channel name")
    channel_name = command.hermes_target.encode("ascii")
    request = encode_bytes_field(1, channel_name)
    if command.payload:
        request += encode_bytes_field(2, command.payload)
    return request


def _validate_channel_response(payload: bytes) -> None:
    """Validate the optional ``bytes value = 1`` response without exposing it."""

    for field in parse_fields(payload, max_fields=32):
        if field.number == 1 and field.wire_type is not WireType.LENGTH_DELIMITED:
            raise GrpcProtocolError("ChannelResponse value has the wrong wire type")


class _HermesCommandTransport:
    """Send one proven channel request and require one unary response.

    There is deliberately no retry. One ``ChannelResponse`` and gRPC status 0
    acknowledge RPC delivery; they do not prove physical effect. Response bytes
    are intentionally neither interpreted nor surfaced as command output.
    """

    def __init__(self, transport: H2Transport) -> None:
        self._transport = transport

    async def send_channel(
        self,
        command: EncodedCommand,
    ) -> TransportAcknowledgement:
        payload = _encode_channel_request(command)
        response = await self._transport.unary(
            SEND_TO_CHANNEL_PATH,
            payload,
            metadata=((HERMES_TARGET_HEADER, command.hermes_target),),
            mutating=True,
        )
        if len(response.messages) != 1:
            raise GrpcProtocolError(
                "SendToChannel must return exactly one ChannelResponse"
            )
        _validate_channel_response(response.messages[0])
        return TransportAcknowledgement(
            TransportAckStatus.ACKNOWLEDGED,
            code="grpc-status-0",
            detail="Hermes returned one ChannelResponse",
        )
