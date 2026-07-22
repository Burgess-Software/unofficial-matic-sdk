"""Stream live pose envelopes without interpreting private payload fields."""

from __future__ import annotations

import asyncio
import os

from matic_sdk import MaticClient, MaticConfig, TlsConfig


async def main() -> None:
    config = MaticConfig(
        host=os.environ["MATIC_HOST"],
        tls=TlsConfig.pinned(os.environ["MATIC_CERT_SHA256"]),
    )
    async with await MaticClient.connect_from_store(
        os.environ["MATIC_DEVICE"], config
    ) as robot:
        async with await robot.collections.subscribe("latest_pose") as events:
            async for event in events:
                print(event.received_at, len(event.payload or b""))


if __name__ == "__main__":
    asyncio.run(main())
