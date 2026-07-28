#!/usr/bin/env python3
"""Run a bounded, state-preserving live command verification.

This tool intentionally has no arbitrary channel or payload option. It sends
only stationary user commands and writes each supported boolean preference
back to its already-observed value. Every command is attempted exactly once.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import dataclass
from pathlib import Path

from matic_sdk.client import MaticClient
from matic_sdk.config import DEFAULT_HERMES_PORT, MaticConfig, TlsConfig
from matic_sdk.credentials import BotToken
from matic_sdk.models.control import SettingAction
from matic_sdk.protocol.wire import WireType, decode_varint, parse_fields
from matic_sdk.safety import UNSAFE_CONFIRMATION, UnsafeControls

_LIVE_CONFIRMATION = "verify bounded stationary and idempotent commands"
_SETTING_READ_TARGETS = {
    SettingAction.CHILD_LOCK: "child_lock_enabled_state",
    SettingAction.PET_WASTE_AVOIDANCE: "petwaste_enabled_state",
    SettingAction.VOICE: "voice_enabled_state",
}
_PARKED_CODES = frozenset({106, 107})
_MOVING_CODES = frozenset({104, 119})
_PAUSED_CODES = frozenset({120, 200, 302})


@dataclass(frozen=True, slots=True)
class OperationalState:
    codes: frozenset[int]
    errors: tuple[int, ...]

    @property
    def safely_parked(self) -> bool:
        return (
            not self.errors
            and bool(self.codes & _PARKED_CODES)
            and not bool(self.codes & _MOVING_CODES)
        )

    @property
    def activity(self) -> str:
        if self.errors:
            return "error"
        if self.codes & _PAUSED_CODES:
            return "paused"
        if 119 in self.codes:
            return "cleaning"
        if 104 in self.codes:
            return "returning"
        if 107 in self.codes:
            return "charging"
        if 106 in self.codes:
            return "docked"
        return "ready"


def _packed_varints(payload: bytes) -> tuple[int, ...]:
    values: list[int] = []
    offset = 0
    while offset < len(payload):
        value, offset = decode_varint(payload, offset)
        values.append(value)
    return tuple(values)


def _decode_operational_state(payload: bytes) -> OperationalState:
    codes: list[int] = []
    errors: list[int] = []
    for field in parse_fields(payload, max_fields=256):
        destination = codes if field.number == 1 else errors
        if field.number not in {1, 2}:
            continue
        if field.wire_type is WireType.VARINT and isinstance(field.value, int):
            destination.append(field.value)
        elif field.wire_type is WireType.LENGTH_DELIMITED and isinstance(
            field.value, bytes
        ):
            destination.extend(_packed_varints(field.value))
    return OperationalState(frozenset(codes), tuple(errors))


def _decode_binary_state(payload: bytes) -> bool:
    # Prost omits a scalar bool at its default false value, so a BinaryState
    # property can legitimately be an empty message.
    if not payload:
        return False
    matches = [
        field.value
        for field in parse_fields(payload, max_fields=16)
        if field.number == 1
        and field.wire_type is WireType.VARINT
        and isinstance(field.value, int)
    ]
    if len(matches) != 1 or matches[0] not in {0, 1}:
        raise ValueError("binary state did not contain exactly one boolean field")
    return bool(matches[0])


async def _read_state(client: MaticClient, target: str) -> bytes:
    event = await client.first(target, timeout=10)
    if event.payload is None:
        raise ValueError(f"{target} returned no current value")
    return event.payload


async def _require_safely_parked(
    client: MaticClient,
    *,
    stage: str,
) -> OperationalState:
    state = _decode_operational_state(await _read_state(client, "kabuki_state"))
    if not state.safely_parked:
        raise RuntimeError(
            f"refusing live command verification because the robot is not "
            f"safely parked {stage}"
        )
    return state


def _emit(**values: object) -> None:
    print(json.dumps(values, sort_keys=True), flush=True)


async def _run_bounded_verification(client: MaticClient) -> None:
    initial = await _require_safely_parked(client, stage="at initial preflight")
    _emit(event="preflight", activity=initial.activity, safely_parked=True)

    setting_values: dict[SettingAction, bool] = {}
    for action, target in _SETTING_READ_TARGETS.items():
        setting_values[action] = _decode_binary_state(await _read_state(client, target))

    capability = UnsafeControls.arm(UNSAFE_CONFIRMATION)
    try:
        for action, before in setting_values.items():
            await _require_safely_parked(
                client,
                stage=f"immediately before settings.{action.value}",
            )
            receipt = await client.commands.set_binary_setting(
                action,
                before,
                unsafe_controls=capability,
            )
            after = _decode_binary_state(
                await _read_state(client, _SETTING_READ_TARGETS[action])
            )
            _emit(
                command=f"settings.{action.value}",
                attempts=1,
                acknowledged=receipt.transport_acknowledged,
                before=before,
                after=after,
                unchanged=before is after,
            )
            if before is not after:
                raise RuntimeError(
                    f"{action.value} changed during an idempotent verification"
                )
    finally:
        capability.disarm()

    for command_name, sender in (
        ("user.pause", client.commands.pause),
        ("user.stay_put", client.commands.stay_put),
    ):
        await _require_safely_parked(
            client,
            stage=f"immediately before {command_name}",
        )
        receipt = await sender()
        after = _decode_operational_state(await _read_state(client, "kabuki_state"))
        _emit(
            command=command_name,
            attempts=1,
            acknowledged=receipt.transport_acknowledged,
            activity=after.activity,
            safely_parked=after.safely_parked,
        )
        if not after.safely_parked:
            raise RuntimeError(
                f"{command_name} left the robot outside the parked state"
            )


async def _verify(args: argparse.Namespace) -> None:
    if args.confirm != _LIVE_CONFIRMATION:
        raise ValueError(f"--confirm must equal {_LIVE_CONFIRMATION!r}")

    token = BotToken.decode(args.token_file.read_bytes())
    config = MaticConfig(
        host=args.host,
        port=args.port,
        authority=args.authority,
        sni=args.server_name,
        command_protocol_version=25,
        tls=TlsConfig.pinned(args.certificate_sha256),
    )
    async with await MaticClient.connect(config, token=token) as client:
        await _run_bounded_verification(client)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, default=DEFAULT_HERMES_PORT)
    parser.add_argument("--authority")
    parser.add_argument("--server-name")
    parser.add_argument("--certificate-sha256", required=True)
    parser.add_argument("--token-file", required=True, type=Path)
    parser.add_argument("--confirm", required=True)
    return parser


def main() -> None:
    asyncio.run(_verify(_parser().parse_args()))


if __name__ == "__main__":
    main()
