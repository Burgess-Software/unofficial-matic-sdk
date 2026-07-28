# SDK recipes

These recipes assume one completed Bluetooth enrollment and these environment
values:

```bash
export MATIC_DEVICE_ALIAS=my-matic
export MATIC_HOST=ROBOT_IP_OR_HOSTNAME
export MATIC_CERT_SHA256=VERIFIED_64_CHARACTER_SHA256
export MATIC_PROTOCOL_VERSION=25
```

See [Troubleshooting](troubleshooting.md) for finding the host, checking the
unauthenticated identity, pinning TLS, and reading the robot's command protocol
version.

## Read one friendly model

```python
import asyncio
import os

from matic_sdk import MaticClient, MaticConfig, TlsConfig
from matic_sdk.models.collections import RobotStatusCollectionModel


async def main() -> None:
    config = MaticConfig(
        host=os.environ["MATIC_HOST"],
        tls=TlsConfig.pinned(os.environ["MATIC_CERT_SHA256"]),
    )
    async with await MaticClient.connect_from_store(
        os.environ["MATIC_DEVICE_ALIAS"], config
    ) as robot:
        event = await robot.first("kabuki_state")
        state = event.decode()
        if isinstance(state, RobotStatusCollectionModel):
            print(state.activity, state.battery_percentage, state.error_codes)


asyncio.run(main())
```

For a stream, iterate `robot.collections.subscribe(target)`. For synchronized
pose, state, and motor feedback, iterate `robot.telemetry()` and use
`update.model` or `update.latest_models`.

Runnable examples:

- [`stream_pose.py`](../examples/stream_pose.py)
- [`decoded_telemetry.py`](../examples/decoded_telemetry.py)

## Dock

```python
receipt = await robot.commands.dock()
print(receipt.transport.status.value)
```

The complete program is [`dock.py`](../examples/dock.py).

## Direct joystick control

Each call sends exactly one linear/angular velocity request:

```python
await robot.commands.joystick(linear_mps=0.05, angular_rad_s=0.0)
await asyncio.sleep(0.5)
await robot.commands.joystick(linear_mps=0.0, angular_rad_s=0.0)
```

The zero is an explicit caller command. The SDK does not repeat velocity,
expire it, send zero, or Stop automatically. See
[`joystick.py`](../examples/joystick.py).

## Navigate

Coordinates use the mission-relative frame returned by `latest_pose`. Zero yaw
points along positive X.

```python
from matic_sdk import MissionPosture

destination = MissionPosture(
    mission_id=42,
    x_meters=1.0,
    y_meters=2.0,
    yaw_radians=0.0,
)
await robot.commands.navigate(destination)
```

`navigate_and_wait()` and `navigate_and_explore()` accept the same destination.

## Start room coverage

```python
from uuid import UUID

from matic_sdk import CoverageCleaningMode, CoverageSetting

await robot.commands.normal_coverage(
    mission_id=42,
    partition_id=UUID("10213243-5465-7687-98a9-babcbddcedfe"),
    region_ids=(UUID("00112233-4455-6677-8899-aabbccddeeff"),),
    cleaning_mode=CoverageCleaningMode.BOTH,
    coverage_setting=CoverageSetting.STANDARD,
)
```

Use identifiers from your robot's decoded collections, never the placeholders
above. The complete environment-driven program is
[`start_coverage.py`](../examples/start_coverage.py).

## Clean a drawn stain area

```python
from matic_sdk import DrawnCircle, StainMode

await robot.commands.stain_mode(
    mission_id=42,
    stain_mode=StainMode.WET_SPILL,
    circles=(DrawnCircle(1.0, 2.0, 0.25),),
)
```

The SDK supports the official dry-stain and wet-spill plan shapes.

## Reprioritize active coverage

```python
from matic_sdk import ReprioritizeAction

snapshot = await robot.reprioritization_snapshot(timeout=5.0)
if snapshot is not None:
    selected = snapshot.region_ids[-1]
    await robot.commands.reprioritize_coverage(
        action=ReprioritizeAction.PRIORITIZE,
        mission_id=snapshot.mission_id,
        goals=snapshot.goals,
        current_region_id=snapshot.current_region_id,
        selected_region_id=selected,
        current_session_id=snapshot.current_session_id,
    )
```

Use `ReprioritizeAction.SKIP` without `selected_region_id` to remove the current
region block. An idle robot has no active snapshot.

## Edit one room or map zone

```python
from uuid import UUID

from matic_sdk import DrawnCircle, RoomLabel

await robot.commands.rename_room(
    mission_id=42,
    partition_id=UUID("10213243-5465-7687-98a9-babcbddcedfe"),
    region_id=UUID("00112233-4455-6677-8899-aabbccddeeff"),
    label=RoomLabel.KITCHEN,
)

await robot.commands.add_no_go_zones(
    mission_id=42,
    circles=(DrawnCircle(1.0, 2.0, 0.4),),
)
```

Other typed methods cover partition building, room merge/split, drive-only
zones, stairs, semantic overrides, and sink-summon locations.

## Create a cleaning schedule

Regular schedules require an IANA timezone ID and the UTC offset that applies
to the scheduled local time.

```python
from uuid import UUID, uuid4

from matic_sdk import (
    CoverageCleaningMode,
    ScheduleCoverageSetting,
    ScheduleEvent,
    ScheduleEventKey,
    ScheduleTime,
    StandardScheduleTarget,
    Weekday,
)

key = ScheduleEventKey(mission_id=42, event_id=uuid4())
event = ScheduleEvent(
    name="Weekday morning",
    weekdays=(Weekday.MONDAY, Weekday.TUESDAY, Weekday.WEDNESDAY),
    time=ScheduleTime(
        seconds_since_midnight=8 * 60 * 60,
        timezone_id="America/Chicago",
        utc_offset_seconds=-5 * 60 * 60,
    ),
    target=StandardScheduleTarget(
        (UUID("00112233-4455-6677-8899-aabbccddeeff"),)
    ),
    partition_id=UUID("10213243-5465-7687-98a9-babcbddcedfe"),
    cleaning_mode=CoverageCleaningMode.BOTH,
    vacuum_setting=ScheduleCoverageSetting.STANDARD,
)
await robot.commands.add_or_modify_schedule(key=key, event=event)
```

Use `toggle_schedule(key)` and `remove_schedule(key)` for existing events.
Sink-summon schedules have their own typed event and methods. See the complete
[`create_schedule.py`](../examples/create_schedule.py).

## Change a boolean preference

```python
from matic_sdk.models.control import SettingAction

await robot.commands.set_binary_setting(SettingAction.CHILD_LOCK, True)
```

Child lock, pet-waste avoidance, preview releases, voice, and automatic voice
recording have exact typed variants where supported.

## Send cleaning-mechanism setpoints

```python
await robot.commands.set_raw_motors(
    vacuum_rpm=100.0,
    sweeper_duty=0.5,
    side_brush_duty=0.25,
)
```

This sends one direct `motor_command`. The codec is wire-verified, has not been
exercised live, and applies no device-specific range limits.

## Evidence for additional commands

All 65 documented command codecs are callable through typed models. Use the
[command verification ledger](command-verification.md) to distinguish live
delivery checks from offline native-serializer wire proof.
