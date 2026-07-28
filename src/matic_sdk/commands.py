"""Guarded command execution and redacted command auditing."""

from __future__ import annotations

import asyncio
import json
import os
import stat
import threading
from collections.abc import Awaitable, Callable, Iterable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Protocol, cast
from uuid import UUID, uuid4

from matic_sdk.models.control import (
    AddZones,
    CommandReceipt,
    ControlCommand,
    CoverageAction,
    CoverageCleaningMode,
    CoverageCommand,
    CoverageGoals,
    CoverageSetting,
    DrawnCircle,
    JoystickCommand,
    MapEnvironmentAction,
    MapEnvironmentCommand,
    MapPoint,
    MergeRooms,
    MissionPosture,
    NavigationCommand,
    NavigationMode,
    ObservedEffect,
    ObservedEffectStatus,
    RawMotorCommand,
    RemoveZones,
    RenameRoom,
    ReprioritizeAction,
    ReprioritizeCoverageCommand,
    RoomLabel,
    ScheduleAction,
    ScheduleCommand,
    ScheduleEvent,
    ScheduleEventKey,
    SemanticsOverride,
    SemanticsOverrideKind,
    SettingAction,
    SettingsCommand,
    SinkSummonLocation,
    SinkSummonScheduleEvent,
    SplitRoom,
    StainMode,
    TransportAcknowledgement,
    UserAction,
    UserCommand,
    utc_now,
)
from matic_sdk.protocol.commands import (
    COMMAND_REGISTRY,
    CommandRegistry,
    EncodedCommand,
)

_REDACTED = "[REDACTED]"
_SENSITIVE_KEY_PARTS = (
    "authorization",
    "credential",
    "passphrase",
    "password",
    "payload",
    "secret",
    "ssid",
    "token",
    "wire",
)


class CommandExecutionError(RuntimeError):
    """Base class for errors at the guarded execution layer."""


class UnverifiedCommandTransport(CommandExecutionError):
    """Raised when a command is attempted without verified TLS identity."""


class CommandOutcomeUnknown(CommandExecutionError):
    """Transport failed after execution began; callers must not auto-retry."""


class CommandTransport(Protocol):
    """Minimal transport seam consumed by the guarded command executor."""

    async def send_channel(
        self,
        command: EncodedCommand,
    ) -> TransportAcknowledgement:
        """Send one encoded request without automatic retry."""


class CommandAuditLog(Protocol):
    """Synchronous append-only audit sink."""

    def record(self, event: str, **values: object) -> None:
        """Record a redacted event before returning."""


class NullAuditLog:
    """Explicit no-op audit sink for callers that do not configure a file."""

    def record(self, event: str, **values: object) -> None:
        del event, values


class JsonlAuditLog:
    """Append-only JSONL command log kept at mode ``0600``.

    Command arguments are intentionally excluded by :class:`CommandExecutor`.
    Recursive key-based redaction provides a second boundary for direct audit
    calls and future metadata additions.
    """

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)

    def record(self, event: str, **values: object) -> None:
        record = {
            "timestamp": utc_now().isoformat(),
            "event": event,
            **values,
        }
        redacted = _redact(record)
        line = (
            json.dumps(redacted, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        flags = os.O_APPEND | os.O_CREAT | os.O_WRONLY
        flags |= getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        with self._lock:
            descriptor = os.open(self.path, flags, 0o600)
            try:
                file_stat = os.fstat(descriptor)
                if not stat.S_ISREG(file_stat.st_mode):
                    raise OSError("audit path is not a regular file")
                os.fchmod(descriptor, 0o600)
                view = memoryview(line)
                while view:
                    written = os.write(descriptor, view)
                    view = view[written:]
            finally:
                os.close(descriptor)


def _redact(value: object, *, key: str = "") -> object:
    lowered_key = key.casefold()
    if any(part in lowered_key for part in _SENSITIVE_KEY_PARTS):
        return _REDACTED
    if isinstance(value, Mapping):
        return {
            str(item_key): _redact(item, key=str(item_key))
            for item_key, item in value.items()
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_redact(item) for item in value]
    if isinstance(value, bytes):
        return _REDACTED
    if isinstance(value, datetime):
        return value.isoformat()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return type(value).__name__


ObservationCallback = Callable[
    [ControlCommand, TransportAcknowledgement],
    Awaitable[ObservedEffect | None],
]


class CommandExecutor:
    """Apply protocol, TLS, risk, codec, audit, and no-retry boundaries."""

    def __init__(
        self,
        transport: CommandTransport,
        *,
        protocol_version: object = None,
        tls_identity_verified: bool,
        registry: CommandRegistry = COMMAND_REGISTRY,
        audit_log: CommandAuditLog | None = None,
    ) -> None:
        self._transport = transport
        self._protocol_version = protocol_version
        self._tls_identity_verified = tls_identity_verified
        self._registry = registry
        self._audit = audit_log if audit_log is not None else NullAuditLog()

    @property
    def protocol_version(self) -> object:
        return self._protocol_version

    async def execute(
        self,
        command: ControlCommand,
        *,
        observe: ObservationCallback | None = None,
    ) -> CommandReceipt:
        """Send exactly once and return delivery/effect results separately."""

        return await self._execute_guarded(
            command,
            observe=observe,
        )

    async def _execute_guarded(
        self,
        command: ControlCommand,
        *,
        observe: ObservationCallback | None = None,
    ) -> CommandReceipt:
        spec = self._registry.spec_for(command)
        command_id = str(uuid4())
        issued_at = utc_now()
        self._audit.record(
            "command.attempt",
            command_id=command_id,
            command=spec.key,
            family=spec.family.value,
            risk=spec.risk.value,
            protocol_version=self._protocol_version,
        )
        try:
            if not self._tls_identity_verified:
                raise UnverifiedCommandTransport(
                    "commands require verified robot TLS identity"
                )
            encoded = self._registry.encode(
                command,
                protocol_version=self._protocol_version,
            )
            # CommandRegistry.encode validated the value before returning.
            protocol_version = cast(int, self._protocol_version)
            try:
                acknowledgement = await self._transport.send_channel(encoded)
            except asyncio.CancelledError as error:
                raise CommandOutcomeUnknown(
                    "command execution was cancelled after transport began; the "
                    "outcome is unknown and the command must not be retried"
                ) from error
            except Exception as error:
                raise CommandOutcomeUnknown(
                    "command transport failed; outcome is unknown and the command "
                    "must not be retried automatically"
                ) from error
            if not isinstance(acknowledgement, TransportAcknowledgement):
                raise TypeError(
                    "command transport must return TransportAcknowledgement"
                )

            observed_effect: ObservedEffect | None = None
            if observe is not None:
                try:
                    observed_effect = await observe(command, acknowledgement)
                except Exception as error:
                    observed_effect = ObservedEffect(
                        status=ObservedEffectStatus.NOT_CHECKED,
                        detail=f"observer failed: {type(error).__name__}",
                    )

            receipt = CommandReceipt(
                command_key=spec.key,
                protocol_version=protocol_version,
                transport=acknowledgement,
                observed_effect=observed_effect,
                command_id=command_id,
                issued_at=issued_at,
            )
            try:
                self._audit.record(
                    "command.complete",
                    command_id=command_id,
                    command=spec.key,
                    transport_status=acknowledgement.status.value,
                    observed_effect=(
                        observed_effect.status.value
                        if observed_effect is not None
                        else ObservedEffectStatus.NOT_CHECKED.value
                    ),
                )
            except Exception:
                # A local audit sink cannot turn a delivered command into an
                # apparent failure that an operator might retry.
                pass
            return receipt
        except BaseException as error:
            try:
                self._audit.record(
                    "command.failed",
                    command_id=command_id,
                    command=spec.key,
                    error_type=type(error).__name__,
                )
            except Exception:
                # Preserve the real pre-send or unknown-outcome exception.
                pass
            raise

    async def stop(self) -> CommandReceipt:
        """Convenience wrapper for the stationary Stop intent."""

        return await self.execute(UserCommand(UserAction.STOP))

    async def stay_put(self) -> CommandReceipt:
        """Ask the robot to remain stationary at its current location."""

        return await self.execute(UserCommand(UserAction.STAY_PUT))

    async def pause(self) -> CommandReceipt:
        return await self.execute(UserCommand(UserAction.PAUSE))

    async def resume(self) -> CommandReceipt:
        return await self.execute(UserCommand(UserAction.RESUME))

    async def dock(self) -> CommandReceipt:
        return await self.execute(UserCommand(UserAction.DOCK))

    async def joystick(
        self,
        linear_mps: float,
        angular_rad_s: float,
    ) -> CommandReceipt:
        """Send one robot-relative velocity command without a background sender."""

        return await self.execute(
            JoystickCommand(linear_mps, angular_rad_s),
        )

    async def navigate(
        self,
        destination: MissionPosture,
    ) -> CommandReceipt:
        """Navigate to one mission-relative coordinate."""

        return await self.execute(
            NavigationCommand(NavigationMode.NAVIGATE, destination),
        )

    async def navigate_and_wait(
        self,
        destination: MissionPosture,
    ) -> CommandReceipt:
        """Navigate and install the official fixed 900-second wait condition."""

        return await self.execute(
            NavigationCommand(NavigationMode.NAVIGATE_AND_WAIT, destination),
        )

    async def navigate_and_explore(
        self,
        destination: MissionPosture,
    ) -> CommandReceipt:
        """Navigate to a coordinate and then start exploration."""

        return await self.execute(
            NavigationCommand(
                NavigationMode.NAVIGATE_AND_EXPLORE,
                destination,
            ),
        )

    async def normal_coverage(
        self,
        *,
        mission_id: int,
        partition_id: UUID,
        region_ids: Iterable[UUID],
        cleaning_mode: CoverageCleaningMode = CoverageCleaningMode.BOTH,
        coverage_setting: CoverageSetting = CoverageSetting.STANDARD,
        ordered: bool = False,
    ) -> CommandReceipt:
        """Start a normal coverage plan for one or more mapped regions."""

        return await self.execute(
            CoverageCommand(
                CoverageAction.NORMAL,
                mission_id=mission_id,
                partition_id=partition_id,
                region_ids=tuple(region_ids),
                cleaning_mode=cleaning_mode,
                coverage_setting=coverage_setting,
                ordered=ordered,
            ),
        )

    async def reprioritize_coverage(
        self,
        *,
        action: ReprioritizeAction,
        mission_id: int,
        goals: CoverageGoals,
        current_region_id: UUID,
        current_session_id: UUID,
        selected_region_id: UUID | None = None,
    ) -> CommandReceipt:
        """Prioritize another region or skip the current coverage region."""

        return await self.execute(
            ReprioritizeCoverageCommand(
                action,
                mission_id=mission_id,
                goals=goals,
                current_region_id=current_region_id,
                current_session_id=current_session_id,
                selected_region_id=selected_region_id,
            ),
        )

    async def stain_mode(
        self,
        *,
        mission_id: int,
        stain_mode: StainMode,
        circles: Iterable[DrawnCircle],
    ) -> CommandReceipt:
        """Start the official dry-stain or wet-spill localized program."""

        return await self.execute(
            CoverageCommand(
                CoverageAction.STAIN_MODE,
                mission_id=mission_id,
                stain_mode=stain_mode,
                circles=tuple(circles),
            ),
        )

    async def build_partition(
        self,
        *,
        mission_id: int,
        overwrite: bool = False,
    ) -> CommandReceipt:
        """Ask the robot to rebuild room partitions for a mapped mission."""

        return await self.execute(
            MapEnvironmentCommand(
                MapEnvironmentAction.BUILD_PARTITION,
                mission_id=mission_id,
                overwrite=overwrite,
            )
        )

    async def rename_room(
        self,
        *,
        mission_id: int,
        partition_id: UUID,
        region_id: UUID,
        label: RoomLabel | str,
    ) -> CommandReceipt:
        """Assign a built-in or custom label to one mapped room."""

        return await self._edit_rooms(
            mission_id=mission_id,
            partition_id=partition_id,
            change=RenameRoom(region_id, label),
        )

    async def merge_rooms(
        self,
        *,
        mission_id: int,
        partition_id: UUID,
        first_region_id: UUID,
        second_region_id: UUID,
        label: RoomLabel | str,
    ) -> CommandReceipt:
        """Merge two mapped rooms and label the resulting room."""

        return await self._edit_rooms(
            mission_id=mission_id,
            partition_id=partition_id,
            change=MergeRooms(first_region_id, second_region_id, label),
        )

    async def split_room(
        self,
        *,
        mission_id: int,
        partition_id: UUID,
        region_id: UUID,
        start: MapPoint,
        end: MapPoint,
    ) -> CommandReceipt:
        """Split a mapped room along a mission-relative line."""

        return await self._edit_rooms(
            mission_id=mission_id,
            partition_id=partition_id,
            change=SplitRoom(region_id, start, end),
        )

    async def _edit_rooms(
        self,
        *,
        mission_id: int,
        partition_id: UUID,
        change: RenameRoom | MergeRooms | SplitRoom,
    ) -> CommandReceipt:
        return await self.execute(
            MapEnvironmentCommand(
                MapEnvironmentAction.EDIT_ROOMS,
                mission_id=mission_id,
                partition_id=partition_id,
                change=change,
            )
        )

    async def add_no_go_zones(
        self,
        *,
        mission_id: int,
        circles: Iterable[DrawnCircle],
    ) -> CommandReceipt:
        return await self._edit_zones(
            action=MapEnvironmentAction.EDIT_NO_GO_ZONE,
            mission_id=mission_id,
            change=AddZones(tuple(circles)),
        )

    async def remove_no_go_zones(
        self,
        *,
        mission_id: int,
        region_ids: Iterable[int | UUID],
    ) -> CommandReceipt:
        return await self._edit_zones(
            action=MapEnvironmentAction.EDIT_NO_GO_ZONE,
            mission_id=mission_id,
            change=RemoveZones(tuple(region_ids)),
        )

    async def add_drive_only_zones(
        self,
        *,
        mission_id: int,
        circles: Iterable[DrawnCircle],
    ) -> CommandReceipt:
        return await self._edit_zones(
            action=MapEnvironmentAction.EDIT_DRIVE_ONLY_ZONE,
            mission_id=mission_id,
            change=AddZones(tuple(circles)),
        )

    async def remove_drive_only_zones(
        self,
        *,
        mission_id: int,
        region_ids: Iterable[int | UUID],
    ) -> CommandReceipt:
        return await self._edit_zones(
            action=MapEnvironmentAction.EDIT_DRIVE_ONLY_ZONE,
            mission_id=mission_id,
            change=RemoveZones(tuple(region_ids)),
        )

    async def add_stairs(
        self,
        *,
        mission_id: int,
        circles: Iterable[DrawnCircle],
    ) -> CommandReceipt:
        return await self._edit_zones(
            action=MapEnvironmentAction.EDIT_STAIRS,
            mission_id=mission_id,
            change=AddZones(tuple(circles)),
        )

    async def remove_stairs(
        self,
        *,
        mission_id: int,
        region_ids: Iterable[int | UUID],
    ) -> CommandReceipt:
        return await self._edit_zones(
            action=MapEnvironmentAction.EDIT_STAIRS,
            mission_id=mission_id,
            change=RemoveZones(tuple(region_ids)),
        )

    async def _edit_zones(
        self,
        *,
        action: MapEnvironmentAction,
        mission_id: int,
        change: AddZones | RemoveZones,
    ) -> CommandReceipt:
        return await self.execute(
            MapEnvironmentCommand(
                action,
                mission_id=mission_id,
                change=change,
            )
        )

    async def set_semantics_override(
        self,
        *,
        mission_id: int,
        kind: SemanticsOverrideKind,
        circles: Iterable[DrawnCircle],
    ) -> CommandReceipt:
        """Assign surface/wire semantics to one or more drawn map areas."""

        return await self.execute(
            MapEnvironmentCommand(
                MapEnvironmentAction.EDIT_SEMANTICS_OVERRIDE,
                mission_id=mission_id,
                change=SemanticsOverride(tuple(circles), kind),
            )
        )

    async def set_sink_summon_location(
        self,
        *,
        mission_id: int,
        location: SinkSummonLocation,
    ) -> CommandReceipt:
        return await self.execute(
            MapEnvironmentCommand(
                MapEnvironmentAction.EDIT_SINK_SUMMON_LOCATION,
                mission_id=mission_id,
                change=location,
            )
        )

    async def remove_sink_summon_location(
        self,
        *,
        mission_id: int,
    ) -> CommandReceipt:
        return await self.execute(
            MapEnvironmentCommand(
                MapEnvironmentAction.EDIT_SINK_SUMMON_LOCATION,
                mission_id=mission_id,
            )
        )

    async def add_or_modify_schedule(
        self,
        *,
        key: ScheduleEventKey,
        event: ScheduleEvent,
    ) -> CommandReceipt:
        """Create or replace one regular cleaning schedule."""

        return await self.execute(
            ScheduleCommand(
                ScheduleAction.ADD_OR_MODIFY,
                key=key,
                event=event,
            )
        )

    async def remove_schedule(self, key: ScheduleEventKey) -> CommandReceipt:
        return await self.execute(ScheduleCommand(ScheduleAction.REMOVE, key=key))

    async def toggle_schedule(self, key: ScheduleEventKey) -> CommandReceipt:
        return await self.execute(ScheduleCommand(ScheduleAction.TOGGLE, key=key))

    async def generate_suggested_schedule(self) -> CommandReceipt:
        return await self.execute(ScheduleCommand(ScheduleAction.GENERATE_SUGGESTED))

    async def add_or_modify_sink_summon_schedule(
        self,
        *,
        key: ScheduleEventKey,
        event: SinkSummonScheduleEvent,
    ) -> CommandReceipt:
        return await self.execute(
            ScheduleCommand(
                ScheduleAction.SINK_SUMMON_ADD_OR_MODIFY,
                key=key,
                sink_event=event,
            )
        )

    async def remove_sink_summon_schedule(
        self,
        key: ScheduleEventKey,
    ) -> CommandReceipt:
        return await self.execute(
            ScheduleCommand(ScheduleAction.SINK_SUMMON_REMOVE, key=key)
        )

    async def set_binary_setting(
        self,
        action: SettingAction,
        enabled: bool,
    ) -> CommandReceipt:
        """Set an exact, allowlisted boolean preference."""

        return await self.execute(SettingsCommand(action, enabled))

    async def set_raw_motors(
        self,
        *,
        vacuum_rpm: float | None = None,
        sweeper_duty: float | None = None,
        mopper_duty: float | None = None,
        head_position: float | None = None,
        side_brush_duty: float | None = None,
    ) -> CommandReceipt:
        """Send one direct setpoint command to the cleaning mechanisms."""

        return await self.execute(
            RawMotorCommand(
                vacuum_rpm=vacuum_rpm,
                sweeper_duty=sweeper_duty,
                mopper_duty=mopper_duty,
                head_position=head_position,
                side_brush_duty=side_brush_duty,
            )
        )


__all__ = [
    "CommandAuditLog",
    "CommandExecutionError",
    "CommandExecutor",
    "CommandOutcomeUnknown",
    "CommandTransport",
    "JsonlAuditLog",
    "NullAuditLog",
    "ObservationCallback",
    "UnverifiedCommandTransport",
]
