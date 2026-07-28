"""Send a brief direct velocity request followed by an explicit zero."""

from __future__ import annotations

import asyncio
import os

from matic_sdk import MaticClient, MaticConfig, TlsConfig


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
        await robot.commands.joystick(linear_mps=0.05, angular_rad_s=0.0)
        await asyncio.sleep(0.5)
        await robot.commands.joystick(linear_mps=0.0, angular_rad_s=0.0)


if __name__ == "__main__":
    asyncio.run(main())
