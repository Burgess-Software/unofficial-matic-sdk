from __future__ import annotations

import base64
import json
import uuid

import pytest

from matic_sdk.experimental.remote import (
    PortalControlKind,
    PortalTunnelGate,
    PortalTunnelState,
    RemoteProtocolError,
    build_portal_tunnel_url,
    decode_remote_access_token,
    decode_unverified_jwt,
    encode_remote_token_request,
    parse_portal_control_message,
)
from matic_sdk.protocol.wire import encode_bytes_field, encode_varint_field


def b64url(value: object) -> str:
    encoded = json.dumps(value, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(encoded).rstrip(b"=").decode()


def synthetic_jwt() -> str:
    signature = base64.urlsafe_b64encode(b"synthetic-signature").rstrip(b"=").decode()
    return ".".join(
        (
            b64url({"alg": "synthetic"}),
            b64url({"sub": "not-a-real-device", "exp": 4_102_444_800}),
            signature,
        )
    )


def synthetic_remote_response() -> bytes:
    return b"".join(
        (
            encode_bytes_field(1, b"synthetic-device"),
            encode_bytes_field(3, b"wss://portal.example.invalid/"),
            encode_bytes_field(4, synthetic_jwt().encode()),
        )
    )


def test_remote_access_response_is_strict_and_redacts_token() -> None:
    decoded = decode_remote_access_token(synthetic_remote_response())
    assert decoded.device_serial == "synthetic-device"
    assert decoded.portal_tunnel_url == (
        "wss://portal.example.invalid/connect/client/agent_hermes"
    )
    assert decoded.authorization_header() == (
        "Authorization",
        f"Bearer {synthetic_jwt()}",
    )
    assert synthetic_jwt() not in repr(decoded)
    assert decoded.inspect_unverified_claims().claims["sub"] == "not-a-real-device"


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        b"\x0a\x05x",
        encode_bytes_field(1, b"synthetic-device"),
        synthetic_remote_response() + encode_bytes_field(4, synthetic_jwt().encode()),
        synthetic_remote_response() + encode_bytes_field(9, b"unknown"),
        encode_varint_field(1, 123)
        + encode_bytes_field(3, b"wss://portal.example.invalid/")
        + encode_bytes_field(4, synthetic_jwt().encode()),
    ],
)
def test_remote_access_response_rejects_unverified_schema_changes(
    payload: bytes,
) -> None:
    with pytest.raises(RemoteProtocolError):
        decode_remote_access_token(payload)


def test_remote_token_request_is_only_a_wire_helper() -> None:
    user_id = str(uuid.UUID("abcdefab-cdef-abcd-efab-cdefabcdefab"))
    assert encode_remote_token_request(user_id) == encode_bytes_field(
        1, user_id.encode()
    )
    with pytest.raises(RemoteProtocolError, match="canonical"):
        encode_remote_token_request(user_id.upper())
    with pytest.raises(RemoteProtocolError, match="UUID"):
        encode_remote_token_request("not-an-identifier")


@pytest.mark.parametrize(
    "base_url",
    [
        "https://portal.example.invalid/",
        "wss://name:secret@portal.example.invalid/",
        "wss://portal.example.invalid/unexpected",
        "wss://portal.example.invalid/?redirect=elsewhere",
        "wss://portal.example.invalid/#fragment",
        "wss://portal .example.invalid/",
        "not a URL",
    ],
)
def test_portal_url_builder_rejects_ambiguous_origins(base_url: str) -> None:
    with pytest.raises(RemoteProtocolError):
        build_portal_tunnel_url(base_url)


def test_portal_url_builder_rejects_service_path_injection() -> None:
    with pytest.raises(RemoteProtocolError, match="service"):
        build_portal_tunnel_url("wss://portal.example.invalid/", "../service")


def test_jwt_inspection_is_explicitly_unverified() -> None:
    parsed = decode_unverified_jwt(synthetic_jwt())
    assert parsed.header == {"alg": "synthetic"}
    assert parsed.signature_bytes == len(b"synthetic-signature")
    with pytest.raises(RemoteProtocolError):
        decode_unverified_jwt("not.a.valid+jwt")


def test_control_message_parser_accepts_only_observed_shapes() -> None:
    status = parse_portal_control_message('{"Status":"synthetic status"}')
    assert status.kind is PortalControlKind.STATUS
    assert status.status == "synthetic status"
    assert parse_portal_control_message('"Connected"').kind is (
        PortalControlKind.CONNECTED
    )
    with pytest.raises(RemoteProtocolError):
        parse_portal_control_message('{"Connected":true}')
    with pytest.raises(RemoteProtocolError):
        parse_portal_control_message('{"Status":"ok","extra":1}')


def test_tunnel_gate_keeps_control_and_raw_bytes_separate() -> None:
    gate = PortalTunnelGate()
    assert gate.state is PortalTunnelState.WAITING
    with pytest.raises(RemoteProtocolError, match="before"):
        gate.feed(b"premature TLS")
    assert gate.feed('{"Status":"readying synthetic tunnel"}').kind is (
        PortalControlKind.STATUS
    )
    assert gate.feed('"Connected"').kind is PortalControlKind.CONNECTED
    assert gate.state is PortalTunnelState.CONNECTED
    assert gate.feed(b"synthetic TLS bytes") == b"synthetic TLS bytes"
    with pytest.raises(RemoteProtocolError, match="text"):
        gate.feed('"Connected"')
    gate.close()
    assert gate.state is PortalTunnelState.CLOSED
    with pytest.raises(RemoteProtocolError, match="closed"):
        gate.feed(b"late bytes")
