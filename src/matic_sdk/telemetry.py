"""Concurrent telemetry aggregation and privacy-preserving raw capture."""

from __future__ import annotations

import asyncio
import json
import os
import stat
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from matic_sdk.collections import CollectionSubscription
from matic_sdk.protocol.collections import RawCollectionEvent

if TYPE_CHECKING:
    from matic_sdk.client import MaticClient
    from matic_sdk.models.collections import FriendlyCollectionModel


DEFAULT_CONTROL_FEEDBACK_TARGETS = ("latest_pose", "kabuki_state", "motor_status")
_PUMP_FINISHED = object()


@dataclass(frozen=True, slots=True)
class TelemetryUpdate:
    event: RawCollectionEvent
    latest: dict[str, RawCollectionEvent]

    @property
    def model(self) -> FriendlyCollectionModel:
        """Decode the event that produced this update."""

        return self.event.decode()

    @property
    def latest_models(self) -> dict[str, FriendlyCollectionModel]:
        """Decode the most recent event seen for each subscribed target."""

        return {target: event.decode() for target, event in self.latest.items()}


class TelemetrySession(AsyncIterator[TelemetryUpdate]):
    """Merge several independently acknowledged collections into one iterator."""

    def __init__(self, client: MaticClient, targets: Iterable[str]) -> None:
        self.client = client
        self.targets = tuple(dict.fromkeys(targets))
        if not self.targets:
            raise ValueError("at least one telemetry target is required")
        self._queue: asyncio.Queue[RawCollectionEvent | BaseException | object] = (
            asyncio.Queue()
        )
        self._tasks: list[asyncio.Task[None]] = []
        self._subscriptions: list[CollectionSubscription] = []
        self._latest: dict[str, RawCollectionEvent] = {}
        self._started = False
        self._closed = False
        self._completed_pumps = 0

    async def start(self) -> TelemetrySession:
        if self._started:
            return self
        self._started = True
        try:
            for target in self.targets:
                subscription = await self.client.subscribe(target)
                self._subscriptions.append(subscription)
                self._tasks.append(
                    asyncio.create_task(
                        self._pump(subscription),
                        name=f"matic-telemetry-{target}",
                    )
                )
        except BaseException:
            await self.aclose()
            raise
        return self

    async def _pump(self, subscription: CollectionSubscription) -> None:
        try:
            async for event in subscription:
                await self._queue.put(event)
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            await self._queue.put(exc)
        finally:
            await self._queue.put(_PUMP_FINISHED)

    def __aiter__(self) -> TelemetrySession:
        return self

    async def __anext__(self) -> TelemetryUpdate:
        if not self._started:
            await self.start()
        if self._closed:
            raise StopAsyncIteration
        while True:
            value = await self._queue.get()
            if value is _PUMP_FINISHED:
                self._completed_pumps += 1
                if self._completed_pumps == len(self._tasks):
                    await self.aclose()
                    raise StopAsyncIteration
                continue
            break
        if isinstance(value, BaseException):
            await self.aclose()
            raise value
        assert isinstance(value, RawCollectionEvent)
        self._latest[value.target] = value
        return TelemetryUpdate(value, dict(self._latest))

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        for task in self._tasks:
            task.cancel()
        errors: list[BaseException] = []
        if self._tasks:
            task_results = await asyncio.gather(*self._tasks, return_exceptions=True)
            errors.extend(
                result
                for result in task_results
                if isinstance(result, BaseException)
                and not isinstance(result, asyncio.CancelledError)
            )
        for subscription in self._subscriptions:
            try:
                await subscription.aclose()
            except BaseException as error:
                errors.append(error)
        if len(errors) == 1:
            raise errors[0]
        if errors:
            raise BaseExceptionGroup("telemetry cleanup failed", errors)

    async def __aenter__(self) -> TelemetrySession:
        return await self.start()

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()


def _write_private(path: Path, data: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        view = memoryview(data)
        while view:
            count = os.write(descriptor, view)
            view = view[count:]
    finally:
        os.close(descriptor)


async def record_telemetry(
    client: MaticClient,
    output_directory: Path,
    *,
    targets: Iterable[str] = DEFAULT_CONTROL_FEEDBACK_TARGETS,
    duration: float = 10.0,
    max_events: int = 10_000,
) -> Path:
    """Capture synchronized raw events into a new owner-only directory."""

    if duration <= 0 or max_events < 1:
        raise ValueError("duration and max_events must be positive")
    output_directory = Path(output_directory)
    output_directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    os.chmod(output_directory, 0o700)
    started = datetime.now(UTC)
    records: list[dict[str, object]] = []
    session = TelemetrySession(client, targets)
    deadline = asyncio.get_running_loop().time() + duration
    try:
        async with session:
            while len(records) < max_events:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    break
                try:
                    update = await asyncio.wait_for(anext(session), timeout=remaining)
                except TimeoutError:
                    break
                event = update.event
                index = len(records)
                filename = f"{index:06d}-{event.target}.pb"
                _write_private(output_directory / filename, event.raw_response)
                records.append(
                    {
                        "index": index,
                        "target": event.target,
                        "operation": event.operation.value,
                        "received_at": event.received_at.isoformat(),
                        "sequence_no": (
                            event.sequence_id.sequence_no if event.sequence_id else None
                        ),
                        "bytes": len(event.raw_response),
                        "file": filename,
                    }
                )
    finally:
        await session.aclose()
    finished = datetime.now(UTC)
    manifest = {
        "format": "unofficial-matic-sdk-telemetry-v1",
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "targets": list(session.targets),
        "events": records,
    }
    _write_private(
        output_directory / "manifest.json",
        (json.dumps(manifest, indent=2) + "\n").encode("utf-8"),
    )
    mode = stat.S_IMODE(output_directory.stat().st_mode)
    if mode != 0o700:
        raise RuntimeError("telemetry directory did not retain owner-only permissions")
    return output_directory
