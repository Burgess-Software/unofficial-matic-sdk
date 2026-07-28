"""Print privacy-aware pose, robot-state, and motor models as JSON Lines."""

from __future__ import annotations

import asyncio
import json
import os

from matic_sdk import (
    MaticClient,
    MaticConfig,
    TlsConfig,
    collection_model_to_dict,
)


async def main() -> None:
    config = MaticConfig(
        host=os.environ["MATIC_HOST"],
        tls=TlsConfig.pinned(os.environ["MATIC_CERT_SHA256"]),
    )
    async with await MaticClient.connect_from_store(
        os.environ["MATIC_DEVICE_ALIAS"],
        config,
    ) as robot:
        async with robot.telemetry() as updates:
            async for update in updates:
                print(
                    json.dumps(
                        collection_model_to_dict(update.model),
                        separators=(",", ":"),
                    )
                )


if __name__ == "__main__":
    asyncio.run(main())
