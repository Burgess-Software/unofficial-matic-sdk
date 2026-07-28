"""Stream the robot's friendly mission-relative pose model."""

from __future__ import annotations

import asyncio
import os

from matic_sdk import MaticClient, MaticConfig, TlsConfig
from matic_sdk.models.collections import PoseCollectionModel


async def main() -> None:
    config = MaticConfig(
        host=os.environ["MATIC_HOST"],
        tls=TlsConfig.pinned(os.environ["MATIC_CERT_SHA256"]),
    )
    async with await MaticClient.connect_from_store(
        os.environ["MATIC_DEVICE_ALIAS"], config
    ) as robot:
        async with await robot.collections.subscribe("latest_pose") as events:
            async for event in events:
                pose = event.decode()
                if isinstance(pose, PoseCollectionModel) and pose.pose is not None:
                    translation = pose.pose.translation
                    rotation = pose.pose.rotation
                    print(
                        pose.observed_at or event.received_at,
                        f"mission={pose.mission_id}",
                        f"x={translation.x:.3f}",
                        f"y={translation.y:.3f}",
                        f"z={translation.z:.3f}",
                        f"quaternion=({rotation.x:.3f}, {rotation.y:.3f}, "
                        f"{rotation.z:.3f}, {rotation.w:.3f})",
                    )


if __name__ == "__main__":
    asyncio.run(main())
