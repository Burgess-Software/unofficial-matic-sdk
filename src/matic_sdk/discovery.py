"""Read-only Hermes endpoint probing and BotInformation decoding."""

from __future__ import annotations

import asyncio
import socket
from dataclasses import dataclass, field

from matic_sdk.config import MaticConfig
from matic_sdk.protocol.wire import first_bytes, first_integer, parse_fields
from matic_sdk.transport.h2 import H2Transport

BOT_INFO_PATH = "/hermes_bot_info.HermesDiscoveryRPC/GetBotInfo"


@dataclass(frozen=True, slots=True)
class BotInformation:
    serial_number: str | None
    ipv4_address: str | None
    ipv6_address: str | None
    encrypted: bool | None
    requires_auth: bool | None
    network_auth: bool | None
    hardware_revision: str | None
    raw: bytes = field(repr=False)

    @classmethod
    def decode(cls, payload: bytes) -> BotInformation:
        fields = parse_fields(payload, max_fields=64)

        def text(number: int) -> str | None:
            value = first_bytes(fields, number)
            return value.decode("utf-8") if value is not None else None

        def boolean(number: int) -> bool | None:
            value = first_integer(fields, number)
            return bool(value) if value is not None else None

        # Field order is retained in the official client's generated
        # BotInformation type and has been confirmed against direct responses.
        return cls(
            serial_number=text(1),
            ipv4_address=text(2),
            ipv6_address=text(3),
            encrypted=boolean(4),
            requires_auth=boolean(5),
            network_auth=boolean(6),
            hardware_revision=text(7),
            raw=payload,
        )


@dataclass(frozen=True, slots=True)
class ResolvedAddress:
    family: socket.AddressFamily
    address: str
    port: int


async def resolve_endpoint(config: MaticConfig) -> tuple[ResolvedAddress, ...]:
    """Resolve the configured hostname without sweeping the local network."""

    loop = asyncio.get_running_loop()
    records = await loop.getaddrinfo(
        config.host,
        config.port,
        type=socket.SOCK_STREAM,
    )
    unique: dict[tuple[socket.AddressFamily, str, int], ResolvedAddress] = {}
    for family, _, _, _, sockaddr in records:
        address, port = sockaddr[:2]
        key = (family, address, port)
        unique[key] = ResolvedAddress(family, address, port)
    return tuple(unique.values())


async def get_bot_info(transport: H2Transport) -> BotInformation:
    response = await transport.unary(BOT_INFO_PATH)
    if len(response.messages) != 1:
        raise RuntimeError(
            f"GetBotInfo returned {len(response.messages)} messages; expected one"
        )
    return BotInformation.decode(response.messages[0])


async def probe(config: MaticConfig) -> BotInformation:
    """Open a read-only connection and retrieve unauthenticated identity data."""

    async with H2Transport(config) as transport:
        return await get_bot_info(transport)
