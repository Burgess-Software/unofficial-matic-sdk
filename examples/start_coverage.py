"""Start one direct mapped-room coverage request from environment values."""

from __future__ import annotations

import asyncio
import os
from uuid import UUID

from matic_sdk import (
    CoverageCleaningMode,
    CoverageSetting,
    MaticClient,
    MaticConfig,
    TlsConfig,
)


async def main() -> None:
    config = MaticConfig(
        host=os.environ["MATIC_HOST"],
        command_protocol_version=int(os.environ["MATIC_PROTOCOL_VERSION"]),
        tls=TlsConfig.pinned(os.environ["MATIC_CERT_SHA256"]),
    )
    async with await MaticClient.connect_from_store(
        os.environ["MATIC_DEVICE"],
        config,
    ) as robot:
        receipt = await robot.commands.normal_coverage(
            mission_id=int(os.environ["MATIC_MISSION_ID"]),
            partition_id=UUID(os.environ["MATIC_PARTITION_ID"]),
            region_ids=(UUID(os.environ["MATIC_REGION_ID"]),),
            cleaning_mode=CoverageCleaningMode.BOTH,
            coverage_setting=CoverageSetting.STANDARD,
        )
        print(receipt.transport.status.value)


if __name__ == "__main__":
    asyncio.run(main())
