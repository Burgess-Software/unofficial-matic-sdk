"""Strict helpers for the experimental portal-backed remote path.

This module intentionally stops below the network layer.  It implements only
the message shapes and websocket control framing supported by the available
observations.  It does not mint credentials, open a websocket, or weaken the
robot TLS policy.

The portal JWT authenticates the outer websocket only.  Authenticated Hermes
RPCs inside the tunnel still require the robot's separate ``BotToken``.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from matic_sdk.protocol.wire import (
    ProtoWireError,
    WireField,
    WireType,
    encode_bytes_field,
    parse_fields,
)

REQUEST_REMOTE_TOKEN_RPC = "/hermes_bot_info.HermesDiscoveryRPC/RequestRemoteToken"
DEFAULT_PORTAL_SERVICE = "agent_hermes"

_SERVICE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_SERIAL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}")
_JWT_SEGMENT_RE = re.compile(r"[A-Za-z0-9_-]+")
_MAX_TOKEN_BYTES = 32 * 1024
_MAX_RESPONSE_BYTES = 64 * 1024
_MAX_CONTROL_CHARS = 4 * 1024


class RemoteProtocolError(ValueError):
    """An experimental remote-protocol value was malformed or unsupported."""


@dataclass(frozen=True, slots=True)
class UnverifiedJwt:
    """Decoded JWT metadata with no signature or trust validation.

    Parsing a JWT is useful for diagnostics, but does not establish who issued
    it or whether its claims are trustworthy.
    """

    header: dict[str, Any]
    claims: dict[str, Any]
    signature_bytes: int


def _decode_jwt_segment(segment: str, label: str) -> bytes:
    if not _JWT_SEGMENT_RE.fullmatch(segment):
        raise RemoteProtocolError(f"JWT {label} is not canonical base64url")
    if len(segment) % 4 == 1:
        raise RemoteProtocolError(f"JWT {label} has an invalid base64url length")
    padded = segment + "=" * (-len(segment) % 4)
    try:
        return base64.b64decode(padded, altchars=b"-_", validate=True)
    except (binascii.Error, ValueError) as exc:
        raise RemoteProtocolError(f"JWT {label} is invalid base64url") from exc


def decode_unverified_jwt(token: str) -> UnverifiedJwt:
    """Inspect a compact JWT without validating its signature.

    The deliberately explicit name is a warning: callers must not authorize a
    decision based on the returned claims.
    """

    if not isinstance(token, str) or not token or len(token) > _MAX_TOKEN_BYTES:
        raise RemoteProtocolError("portal JWT has an invalid length")
    if not token.isascii() or any(character.isspace() for character in token):
        raise RemoteProtocolError("portal JWT must be whitespace-free ASCII")
    parts = token.split(".")
    if len(parts) != 3:
        raise RemoteProtocolError("portal credential is not a three-part JWT")

    decoded_objects: list[dict[str, Any]] = []
    for segment, label in zip(parts[:2], ("header", "claims"), strict=True):
        encoded = _decode_jwt_segment(segment, label)
        try:
            value = json.loads(encoded.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RemoteProtocolError(
                f"JWT {label} is not a UTF-8 JSON object"
            ) from exc
        if not isinstance(value, dict):
            raise RemoteProtocolError(f"JWT {label} must be a JSON object")
        decoded_objects.append(value)

    signature = _decode_jwt_segment(parts[2], "signature")
    if not signature:
        raise RemoteProtocolError("JWT signature cannot be empty")
    return UnverifiedJwt(
        header=decoded_objects[0],
        claims=decoded_objects[1],
        signature_bytes=len(signature),
    )


def build_portal_tunnel_url(
    portal_base_url: str, service: str = DEFAULT_PORTAL_SERVICE
) -> str:
    """Build the observed portal client URL after strict origin validation."""

    if not isinstance(service, str) or not _SERVICE_RE.fullmatch(service):
        raise RemoteProtocolError("portal service name contains unsupported characters")
    if (
        not isinstance(portal_base_url, str)
        or not portal_base_url
        or len(portal_base_url) > 2048
        or not portal_base_url.isascii()
        or any(character.isspace() for character in portal_base_url)
    ):
        raise RemoteProtocolError("portal base URL must be whitespace-free ASCII")
    try:
        parsed = urlsplit(portal_base_url)
        # Accessing ``port`` also validates malformed port syntax and range.
        _ = parsed.port
    except ValueError as exc:
        raise RemoteProtocolError("portal base URL is malformed") from exc
    if parsed.scheme.casefold() != "wss":
        raise RemoteProtocolError("portal base URL must use wss")
    has_credentials = parsed.username is not None or parsed.password is not None
    if not parsed.hostname or has_credentials:
        raise RemoteProtocolError(
            "portal base URL must be an origin without credentials"
        )
    if parsed.query or parsed.fragment or parsed.path not in ("", "/"):
        raise RemoteProtocolError(
            "portal base URL must not contain a path, query, or fragment"
        )
    return urlunsplit(("wss", parsed.netloc, f"/connect/client/{service}", "", ""))


@dataclass(frozen=True, slots=True)
class RemoteAccessToken:
    """Observed robot response for a portal access credential.

    The raw credential is excluded from ``repr``.  Reveal it only at the point
    where a websocket client constructs its Authorization header.
    """

    device_serial: str
    portal_base_url: str
    _portal_jwt: str = field(repr=False, compare=False)

    @property
    def portal_tunnel_url(self) -> str:
        return build_portal_tunnel_url(self.portal_base_url)

    def reveal_portal_jwt(self) -> str:
        """Return the bearer credential; never log or persist this value casually."""

        return self._portal_jwt

    def authorization_header(self) -> tuple[str, str]:
        """Build the observed websocket Authorization header."""

        return "Authorization", f"Bearer {self._portal_jwt}"

    def inspect_unverified_claims(self) -> UnverifiedJwt:
        return decode_unverified_jwt(self._portal_jwt)


def _single_text_field(fields: tuple[WireField, ...], number: int, label: str) -> str:
    matched = [
        field
        for field in fields
        if field.number == number and field.wire_type is WireType.LENGTH_DELIMITED
    ]
    if len(matched) != 1:
        raise RemoteProtocolError(f"remote token response requires one {label} field")
    value = matched[0].value
    if not isinstance(value, bytes):
        raise RemoteProtocolError(f"remote token {label} field is not bytes")
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RemoteProtocolError(f"remote token {label} is not UTF-8") from exc


def decode_remote_access_token(payload: bytes) -> RemoteAccessToken:
    """Decode the one observed ``RemoteAccessToken`` response schema.

    Supported fields are device serial (1), portal base URL (3), and portal JWT
    (4), each length-delimited and present exactly once.  Unknown or duplicate
    fields are rejected because their meaning has not been established.
    """

    if (
        not isinstance(payload, bytes)
        or not payload
        or len(payload) > _MAX_RESPONSE_BYTES
    ):
        raise RemoteProtocolError("remote token response has an invalid length")
    try:
        fields = parse_fields(payload)
    except ProtoWireError as exc:
        raise RemoteProtocolError(
            "remote token response is malformed protobuf"
        ) from exc
    for item in fields:
        unsupported = (
            item.number not in (1, 3, 4)
            or item.wire_type is not WireType.LENGTH_DELIMITED
        )
        if unsupported:
            raise RemoteProtocolError(
                "remote token response contains an unsupported field"
            )

    serial = _single_text_field(fields, 1, "device serial")
    portal_base_url = _single_text_field(fields, 3, "portal URL")
    portal_jwt = _single_text_field(fields, 4, "JWT")
    if not _SERIAL_RE.fullmatch(serial):
        raise RemoteProtocolError("remote token device serial has an invalid format")
    build_portal_tunnel_url(portal_base_url)
    decode_unverified_jwt(portal_jwt)
    return RemoteAccessToken(serial, portal_base_url, portal_jwt)


def encode_remote_token_request(user_id: str) -> bytes:
    """Encode the observed request body containing a canonical user UUID.

    Current firmware has been observed rejecting this RPC as unauthenticated;
    this helper only represents the recovered wire shape and performs no I/O.
    """

    try:
        parsed = uuid.UUID(user_id)
    except (AttributeError, TypeError, ValueError) as exc:
        raise RemoteProtocolError("remote token user ID must be a UUID") from exc
    if str(parsed) != user_id:
        raise RemoteProtocolError(
            "remote token user ID must be canonical lowercase UUID"
        )
    return encode_bytes_field(1, user_id.encode("ascii"))


class PortalControlKind(StrEnum):
    """Known portal websocket control-plane message kinds."""

    STATUS = "status"
    CONNECTED = "connected"


@dataclass(frozen=True, slots=True)
class PortalControlMessage:
    kind: PortalControlKind
    status: str | None = None


def parse_portal_control_message(text: str) -> PortalControlMessage:
    """Parse one of the two observed portal text-control message shapes."""

    if not isinstance(text, str) or not text or len(text) > _MAX_CONTROL_CHARS:
        raise RemoteProtocolError("portal control message has an invalid length")
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RemoteProtocolError("portal control message is not valid JSON") from exc
    if value == "Connected":
        return PortalControlMessage(PortalControlKind.CONNECTED)
    if isinstance(value, dict) and set(value) == {"Status"}:
        status = value["Status"]
        if isinstance(status, str) and status and len(status) <= 1024:
            return PortalControlMessage(PortalControlKind.STATUS, status)
    raise RemoteProtocolError("portal control message has an unsupported shape")


class PortalTunnelState(StrEnum):
    WAITING = "waiting"
    CONNECTED = "connected"
    CLOSED = "closed"


class PortalTunnelGate:
    """Fail-closed state machine separating portal text control from tunnel bytes."""

    def __init__(self) -> None:
        self._state = PortalTunnelState.WAITING

    @property
    def state(self) -> PortalTunnelState:
        return self._state

    def feed(self, frame: str | bytes) -> PortalControlMessage | bytes:
        """Accept a websocket frame in the currently valid protocol phase."""

        if self._state is PortalTunnelState.CLOSED:
            raise RemoteProtocolError("portal tunnel is already closed")
        if self._state is PortalTunnelState.WAITING:
            if not isinstance(frame, str):
                raise RemoteProtocolError(
                    "received tunnel bytes before portal reported Connected"
                )
            message = parse_portal_control_message(frame)
            if message.kind is PortalControlKind.CONNECTED:
                self._state = PortalTunnelState.CONNECTED
            return message
        if isinstance(frame, str):
            raise RemoteProtocolError(
                "received an unexpected portal text frame after Connected"
            )
        if not isinstance(frame, bytes):
            raise TypeError("websocket frame must be str or bytes")
        return frame

    def close(self) -> None:
        self._state = PortalTunnelState.CLOSED
