from __future__ import annotations

import pytest

from matic_sdk.models.control import SettingAction
from tools import live_verify_safe_commands as verifier
from tools.live_verify_safe_commands import (
    _decode_binary_state,
    _decode_operational_state,
)


@pytest.mark.parametrize(
    ("payload", "expected"),
    [(b"", False), (b"\x08\x00", False), (b"\x08\x01", True)],
)
def test_binary_state_accepts_default_and_explicit_bool_encodings(
    payload: bytes,
    expected: bool,
) -> None:
    assert _decode_binary_state(payload) is expected


@pytest.mark.parametrize("payload", [b"\x08\x02", b"\x10\x01", b"\x08\x00\x08\x01"])
def test_binary_state_rejects_ambiguous_values(payload: bytes) -> None:
    with pytest.raises(ValueError, match="exactly one boolean"):
        _decode_binary_state(payload)


def test_operational_preflight_requires_a_parked_nonmoving_state() -> None:
    docked = _decode_operational_state(b"\x0a\x01\x6a")
    returning_while_docked = _decode_operational_state(b"\x0a\x02\x68\x6a")
    docked_with_error = _decode_operational_state(b"\x0a\x01\x6a\x10\x01")

    assert docked.activity == "docked"
    assert docked.safely_parked
    assert not returning_while_docked.safely_parked
    assert docked_with_error.activity == "error"
    assert not docked_with_error.safely_parked


@pytest.mark.asyncio
async def test_bounded_verifier_rechecks_parked_state_before_each_send(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    safe = b"\x0a\x01\x6a"
    moving = b"\x0a\x01\x68"
    responses = iter(
        (
            ("kabuki_state", safe),
            ("child_lock_enabled_state", b""),
            ("petwaste_enabled_state", b""),
            ("voice_enabled_state", b""),
            ("kabuki_state", safe),
            ("child_lock_enabled_state", b""),
            ("kabuki_state", moving),
        )
    )

    async def fake_read_state(client: object, target: str) -> bytes:
        del client
        expected_target, payload = next(responses)
        assert target == expected_target
        return payload

    class FakeReceipt:
        transport_acknowledged = True

    class FakeCommands:
        def __init__(self) -> None:
            self.settings: list[SettingAction] = []

        async def set_binary_setting(
            self,
            action: SettingAction,
            enabled: bool,
            *,
            unsafe_controls: object,
        ) -> FakeReceipt:
            del enabled, unsafe_controls
            self.settings.append(action)
            return FakeReceipt()

    class FakeClient:
        def __init__(self) -> None:
            self.commands = FakeCommands()

    client = FakeClient()
    monkeypatch.setattr(verifier, "_read_state", fake_read_state)

    with pytest.raises(RuntimeError, match="not safely parked"):
        await verifier._run_bounded_verification(client)  # type: ignore[arg-type]

    assert client.commands.settings == [SettingAction.CHILD_LOCK]
