"""Create one enabled weekday room-cleaning schedule."""

from __future__ import annotations

import asyncio
import os
from uuid import UUID, uuid4

from matic_sdk import (
    CoverageCleaningMode,
    MaticClient,
    MaticConfig,
    ScheduleCoverageSetting,
    ScheduleEvent,
    ScheduleEventKey,
    ScheduleTime,
    StandardScheduleTarget,
    TlsConfig,
    Weekday,
)


async def main() -> None:
    mission_id = int(os.environ["MATIC_MISSION_ID"])
    config = MaticConfig(
        host=os.environ["MATIC_HOST"],
        command_protocol_version=int(os.environ["MATIC_PROTOCOL_VERSION"]),
        tls=TlsConfig.pinned(os.environ["MATIC_CERT_SHA256"]),
    )
    event = ScheduleEvent(
        name="Weekday morning",
        weekdays=(
            Weekday.MONDAY,
            Weekday.TUESDAY,
            Weekday.WEDNESDAY,
            Weekday.THURSDAY,
            Weekday.FRIDAY,
        ),
        time=ScheduleTime(
            seconds_since_midnight=8 * 60 * 60,
            timezone_id=os.environ["MATIC_TIMEZONE"],
            utc_offset_seconds=int(os.environ["MATIC_UTC_OFFSET_SECONDS"]),
        ),
        target=StandardScheduleTarget((UUID(os.environ["MATIC_REGION_ID"]),)),
        partition_id=UUID(os.environ["MATIC_PARTITION_ID"]),
        cleaning_mode=CoverageCleaningMode.BOTH,
        vacuum_setting=ScheduleCoverageSetting.STANDARD,
    )
    async with await MaticClient.connect_from_store(
        os.environ["MATIC_DEVICE_ALIAS"],
        config,
    ) as robot:
        receipt = await robot.commands.add_or_modify_schedule(
            key=ScheduleEventKey(mission_id=mission_id, event_id=uuid4()),
            event=event,
        )
        print(receipt.transport.status.value)


if __name__ == "__main__":
    asyncio.run(main())
