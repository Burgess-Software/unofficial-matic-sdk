from __future__ import annotations

import asyncio
import json
import stat
from dataclasses import replace
from pathlib import Path

import pytest

from matic_sdk.client import MaticClient
from matic_sdk.commands import (
    CommandExecutor,
    CommandOutcomeUnknown,
    DirectJoystickUnsupported,
    JsonlAuditLog,
    UnverifiedCommandTransport,
)
from matic_sdk.config import MaticConfig, TlsConfig
from matic_sdk.models.control import (
    CommandFamily,
    JoystickCommand,
    ObservedEffect,
    ObservedEffectStatus,
    RawMotorCommand,
    TransportAcknowledgement,
    TransportAckStatus,
    UserAction,
    UserCommand,
    WifiAction,
    WifiCommand,
)
from matic_sdk.protocol.commands import (
    COMMAND_REGISTRY,
    COMMAND_SPECS,
    CodecEvidenceLevel,
    CommandRegistry,
    EncodedCommand,
    UnsupportedCommandCodec,
    UnsupportedProtocolVersion,
    encode_command,
)
from matic_sdk.protocol.grpc import GrpcProtocolError, GrpcResponse
from matic_sdk.protocol.wire import encode_bytes_field
from matic_sdk.safety import (
    MOTION_CONFIRMATION,
    UNSAFE_CONFIRMATION,
    MotionControlRequired,
    MotionControls,
    UnsafeControlRequired,
    UnsafeControls,
)
from matic_sdk.transport.commands import (
    HERMES_TARGET_HEADER,
    SEND_TO_CHANNEL_PATH,
    _encode_channel_request,
    _HermesCommandTransport,
)


class NeverCalledTransport:
    def __init__(self) -> None:
        self.calls = 0

    async def send_channel(
        self,
        command: EncodedCommand,
    ) -> TransportAcknowledgement:
        del command
        self.calls += 1
        raise AssertionError("transport must not be reached")


class SyntheticStopCodec:
    def encode(self, command: object) -> EncodedCommand:
        assert command == UserCommand(UserAction.STOP)
        return EncodedCommand(b"synthetic-stop", "synthetic-user-command")


class AcknowledgingTransport:
    def __init__(self) -> None:
        self.commands: list[EncodedCommand] = []

    async def send_channel(
        self,
        command: EncodedCommand,
    ) -> TransportAcknowledgement:
        self.commands.append(command)
        return TransportAcknowledgement(
            TransportAckStatus.ACKNOWLEDGED,
            code="synthetic-ok",
        )


class FailingCompletionAudit:
    def record(self, event: str, **values: object) -> None:
        del values
        if event == "command.complete":
            raise OSError("synthetic disk full")


def synthetic_stop_registry() -> CommandRegistry:
    stop_spec = replace(
        COMMAND_REGISTRY.spec_for("user.stop"),
        evidence_level=CodecEvidenceLevel.WIRE_VERIFIED,
        evidence="synthetic unit-test fixture",
    )
    return CommandRegistry(
        (stop_spec,),
        codecs={"user.stop": SyntheticStopCodec()},
    )


def test_registry_documents_every_recovered_command_family() -> None:
    families = {spec.family for spec in COMMAND_SPECS}
    assert families == set(CommandFamily)
    expected_keys = {
        "user.stop",
        "user.joystick",
        "navigation.navigate",
        "coverage.normal",
        "coverage.reprioritize",
        "cleaning.manual",
        "map.build_partition",
        "map.edit_rooms",
        "wifi.connect",
        "device.rename",
        "settings.child_lock",
        "schedule.add_or_modify",
        "media.recording_enable",
        "telemetry.support_ssh_permission",
        "raw_motors.setpoints",
        "lifecycle.update",
        "lifecycle.reboot",
        "lifecycle.shutdown",
    }
    assert expected_keys <= COMMAND_REGISTRY.specs.keys()


def test_default_registry_exposes_only_verified_stop_codec() -> None:
    available = {spec.key for spec in COMMAND_SPECS if spec.codec_available}
    assert available == {"user.stop"}
    assert encode_command(UserCommand(UserAction.STOP), protocol_version=25) == (
        EncodedCommand(bytes.fromhex("7a040a022200"), "user_command")
    )
    with pytest.raises(UnsupportedCommandCodec, match="no evidence-backed"):
        encode_command(JoystickCommand(0.1, 0.2), protocol_version=25)


def test_recovered_user_payloads_remain_offline_evidence() -> None:
    expected = {
        "user.stop": "7a040a022200",
        "user.stay_put": "820100",
        "user.pause": "4801880101",
        "user.resume": "4800880100",
        "user.dock": "12042a020800",
    }
    for key, payload_hex in expected.items():
        spec = COMMAND_REGISTRY.spec_for(key)
        expected_level = (
            CodecEvidenceLevel.WIRE_VERIFIED
            if key == "user.stop"
            else CodecEvidenceLevel.PAYLOAD_VERIFIED
        )
        assert spec.evidence_level is expected_level
        assert spec.known_payload == bytes.fromhex(payload_hex)
        assert spec.known_hermes_target == "user_command"
        assert spec.codec_available is (key == "user.stop")

    joystick = COMMAND_REGISTRY.spec_for("user.joystick")
    assert joystick.known_hermes_target == "user_command"
    assert joystick.known_payload is None


def test_protocol_gate_runs_before_codec_lookup() -> None:
    with pytest.raises(UnsupportedProtocolVersion, match="protocol 24"):
        COMMAND_REGISTRY.encode(
            UserCommand(UserAction.STOP),
            protocol_version=24,
        )


def test_wifi_passphrase_is_not_exposed_by_repr() -> None:
    command = WifiCommand(
        WifiAction.CONNECT,
        ssid="example-network",
        passphrase="do-not-print-this",
    )
    assert "do-not-print-this" not in repr(command)


def test_registry_refuses_codec_without_wire_verified_evidence() -> None:
    unverified_stop = replace(
        COMMAND_REGISTRY.spec_for("user.stop"),
        evidence_level=CodecEvidenceLevel.PAYLOAD_VERIFIED,
    )
    with pytest.raises(ValueError, match="WIRE_VERIFIED"):
        CommandRegistry(
            (unverified_stop,),
            codecs={"user.stop": SyntheticStopCodec()},
        )


def test_stop_channel_request_matches_golden_wire_vector() -> None:
    command = encode_command(UserCommand(UserAction.STOP), protocol_version=25)
    assert _encode_channel_request(command).hex() == (
        "0a0c757365725f636f6d6d616e6412067a040a022200"
    )


def test_stop_codec_rejects_unverified_session_id() -> None:
    with pytest.raises(ValueError, match="session_id is not wire-verified"):
        encode_command(
            UserCommand(UserAction.STOP, session_id="synthetic"),
            protocol_version=25,
        )


class FakeCommandH2:
    def __init__(self, messages: tuple[bytes, ...] = (b"",)) -> None:
        self.messages = messages
        self.calls: list[tuple[str, bytes, object, bool]] = []

    async def unary(
        self,
        path: str,
        payload: bytes,
        *,
        metadata: object,
        mutating: bool,
    ) -> GrpcResponse:
        self.calls.append((path, payload, metadata, mutating))
        return GrpcResponse(self.messages, (), (("grpc-status", "0"),))


@pytest.mark.asyncio
async def test_hermes_transport_sends_one_verified_stop_request() -> None:
    h2 = FakeCommandH2()
    transport = _HermesCommandTransport(h2)  # type: ignore[arg-type]
    command = encode_command(UserCommand(UserAction.STOP), protocol_version=25)

    acknowledgement = await transport.send_channel(command)

    assert acknowledgement.status is TransportAckStatus.ACKNOWLEDGED
    assert acknowledgement.code == "grpc-status-0"
    assert h2.calls == [
        (
            SEND_TO_CHANNEL_PATH,
            bytes.fromhex("0a0c757365725f636f6d6d616e6412067a040a022200"),
            ((HERMES_TARGET_HEADER, "user_command"),),
            True,
        )
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("messages", [(), (b"", b"")])
async def test_hermes_transport_rejects_invalid_response_shape(
    messages: tuple[bytes, ...],
) -> None:
    transport = _HermesCommandTransport(FakeCommandH2(messages))  # type: ignore[arg-type]
    command = encode_command(UserCommand(UserAction.STOP), protocol_version=25)

    with pytest.raises(GrpcProtocolError):
        await transport.send_channel(command)


@pytest.mark.asyncio
async def test_hermes_transport_accepts_nonempty_response_value() -> None:
    response_value = b"synthetic-response-value"
    transport = _HermesCommandTransport(  # type: ignore[arg-type]
        FakeCommandH2((encode_bytes_field(1, response_value),))
    )
    command = encode_command(UserCommand(UserAction.STOP), protocol_version=25)

    acknowledgement = await transport.send_channel(command)

    assert acknowledgement.status is TransportAckStatus.ACKNOWLEDGED
    assert "synthetic-response-value" not in repr(acknowledgement)


@pytest.mark.asyncio
async def test_hermes_transport_rejects_malformed_channel_response() -> None:
    transport = _HermesCommandTransport(  # type: ignore[arg-type]
        FakeCommandH2((b"\x0a\x05bad",))
    )
    command = encode_command(UserCommand(UserAction.STOP), protocol_version=25)

    with pytest.raises(ValueError, match="truncated"):
        await transport.send_channel(command)


@pytest.mark.asyncio
async def test_unverified_tls_blocks_before_codec_or_transport() -> None:
    transport = NeverCalledTransport()
    executor = CommandExecutor(
        transport,
        protocol_version=25,
        tls_identity_verified=False,
    )
    with pytest.raises(UnverifiedCommandTransport):
        await executor.execute(UserCommand(UserAction.STOP))
    assert transport.calls == 0


@pytest.mark.asyncio
async def test_client_exposes_fail_closed_command_executor() -> None:
    config = MaticConfig(
        "robot.invalid",
        tls=TlsConfig.pinned("00" * 32),
    )
    client = MaticClient(config, NeverCalledTransport(), credentials=None)  # type: ignore[arg-type]

    with pytest.raises(UnsupportedProtocolVersion):
        await client.commands.stop()


@pytest.mark.asyncio
async def test_client_sends_wire_verified_stop_through_hermes() -> None:
    config = MaticConfig(
        "robot.invalid",
        command_protocol_version=25,
        tls=TlsConfig.pinned("00" * 32),
    )
    h2 = FakeCommandH2()
    client = MaticClient(config, h2, credentials=None)  # type: ignore[arg-type]

    receipt = await client.commands.stop()

    assert receipt.transport_acknowledged
    assert len(h2.calls) == 1


@pytest.mark.asyncio
async def test_unsafe_command_needs_explicit_capability() -> None:
    transport = NeverCalledTransport()
    executor = CommandExecutor(
        transport,
        protocol_version=25,
        tls_identity_verified=True,
    )
    command = RawMotorCommand(vacuum_rpm=100.0)
    with pytest.raises(UnsafeControlRequired):
        await executor.execute(command)
    assert transport.calls == 0

    capability = UnsafeControls.arm(UNSAFE_CONFIRMATION)
    with pytest.raises(UnsupportedCommandCodec):
        await executor.execute(command, unsafe_controls=capability)
    assert transport.calls == 0


@pytest.mark.asyncio
async def test_motion_command_needs_explicit_capability() -> None:
    transport = NeverCalledTransport()
    executor = CommandExecutor(
        transport,
        protocol_version=25,
        tls_identity_verified=True,
    )
    command = UserCommand(UserAction.RESUME)

    with pytest.raises(MotionControlRequired):
        await executor.execute(command)
    capability = MotionControls.arm(MOTION_CONFIRMATION)
    with pytest.raises(UnsupportedCommandCodec):
        await executor.execute(command, motion_controls=capability)
    assert transport.calls == 0


@pytest.mark.asyncio
async def test_direct_joystick_cannot_bypass_teleop_watchdog() -> None:
    transport = NeverCalledTransport()
    executor = CommandExecutor(
        transport,
        protocol_version=25,
        tls_identity_verified=True,
    )
    capability = MotionControls.arm(MOTION_CONFIRMATION)

    with pytest.raises(DirectJoystickUnsupported, match="TeleopSession"):
        await executor.execute(
            JoystickCommand(0.1, 0.2),
            motion_controls=capability,
        )
    assert transport.calls == 0


@pytest.mark.asyncio
async def test_receipt_separates_transport_ack_from_observed_effect() -> None:
    transport = AcknowledgingTransport()
    executor = CommandExecutor(
        transport,
        protocol_version=25,
        tls_identity_verified=True,
        registry=synthetic_stop_registry(),
    )

    async def observe(
        command: object,
        acknowledgement: TransportAcknowledgement,
    ) -> ObservedEffect:
        assert command == UserCommand(UserAction.STOP)
        assert acknowledgement.status is TransportAckStatus.ACKNOWLEDGED
        return ObservedEffect(
            ObservedEffectStatus.OBSERVED,
            collection="kabuki_state",
        )

    receipt = await executor.execute(UserCommand(UserAction.STOP), observe=observe)
    assert receipt.transport_acknowledged
    assert receipt.effect_observed
    assert receipt.transport.code == "synthetic-ok"
    assert receipt.observed_effect is not None
    assert receipt.observed_effect.collection == "kabuki_state"
    assert transport.commands == [
        EncodedCommand(b"synthetic-stop", "synthetic-user-command")
    ]


@pytest.mark.asyncio
async def test_post_send_audit_failure_does_not_mask_acknowledged_command() -> None:
    transport = AcknowledgingTransport()
    executor = CommandExecutor(
        transport,
        protocol_version=25,
        tls_identity_verified=True,
        registry=synthetic_stop_registry(),
        audit_log=FailingCompletionAudit(),
    )

    receipt = await executor.stop()

    assert receipt.transport_acknowledged
    assert len(transport.commands) == 1


@pytest.mark.asyncio
async def test_transport_exception_is_unknown_and_never_retried() -> None:
    class BrokenTransport:
        def __init__(self) -> None:
            self.calls = 0

        async def send_channel(
            self,
            command: EncodedCommand,
        ) -> TransportAcknowledgement:
            del command
            self.calls += 1
            raise ConnectionError("synthetic disconnect")

    transport = BrokenTransport()
    executor = CommandExecutor(
        transport,
        protocol_version=25,
        tls_identity_verified=True,
        registry=synthetic_stop_registry(),
    )
    with pytest.raises(CommandOutcomeUnknown, match="must not be retried"):
        await executor.stop()
    assert transport.calls == 1


@pytest.mark.asyncio
async def test_transport_cancellation_surfaces_unknown_outcome() -> None:
    class CancelledTransport:
        async def send_channel(
            self,
            command: EncodedCommand,
        ) -> TransportAcknowledgement:
            del command
            raise asyncio.CancelledError

    executor = CommandExecutor(
        CancelledTransport(),
        protocol_version=25,
        tls_identity_verified=True,
        registry=synthetic_stop_registry(),
    )

    with pytest.raises(CommandOutcomeUnknown, match="cancelled") as raised:
        await executor.stop()

    assert isinstance(raised.value.__cause__, asyncio.CancelledError)


@pytest.mark.asyncio
async def test_jsonl_audit_is_mode_0600_and_contains_no_command_secrets(
    tmp_path: Path,
) -> None:
    audit_path = tmp_path / "private" / "commands.jsonl"
    audit = JsonlAuditLog(audit_path)
    audit.record(
        "direct-test",
        token="bot-token-value",
        nested={
            "ssid": "household-network",
            "passphrase": "network-password",
            "safe": "kept",
        },
        payload=b"protobuf bytes",
    )

    transport = NeverCalledTransport()
    executor = CommandExecutor(
        transport,
        protocol_version=25,
        tls_identity_verified=True,
        audit_log=audit,
    )
    command = WifiCommand(
        WifiAction.CONNECT,
        ssid="household-network",
        passphrase="network-password",
    )
    capability = UnsafeControls.arm(UNSAFE_CONFIRMATION)
    with pytest.raises(UnsupportedCommandCodec):
        await executor.execute(command, unsafe_controls=capability)

    assert stat.S_IMODE(audit_path.stat().st_mode) == 0o600
    contents = audit_path.read_text(encoding="utf-8")
    assert "bot-token-value" not in contents
    assert "household-network" not in contents
    assert "network-password" not in contents
    assert "protobuf bytes" not in contents
    records = [json.loads(line) for line in contents.splitlines()]
    assert records[0]["nested"]["safe"] == "kept"
    assert records[0]["nested"]["ssid"] == "[REDACTED]"
    assert records[-1]["error_type"] == "UnsupportedCommandCodec"
