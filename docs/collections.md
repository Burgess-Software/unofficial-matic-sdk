# Collection models

`FetchCollection` delivers a typed envelope whose value is a protobuf message.
Call `event.decode()` to turn that value into an immutable friendly model:

```python
from matic_sdk.models.collections import RobotStatusCollectionModel

async with await robot.collections.subscribe("kabuki_state") as events:
    async for event in events:
        state = event.decode()
        if isinstance(state, RobotStatusCollectionModel):
            print(state.activity, state.battery_percentage, state.error_codes)
```

The same decoder is available for saved payloads:

```python
from matic_sdk import decode_collection_payload

state = decode_collection_payload("kabuki_state", saved_payload)
```

For a merged `TelemetrySession`, `update.model` decodes the new event and
`update.latest_models` returns the most recent friendly model for every target
seen so far.

Every model includes `target`, `operation`, `deleted`, `raw_payload`, and
`fields`. The last two make decoding lossless: if firmware adds a protobuf
field before this SDK names it, the original bytes and parsed wire value remain
available. Raw payloads, map geometry, media bytes, network identifiers, account
details, and pairing codes are omitted from model representations.

## Registered targets

All 43 known collection targets have a stable model type:

| Target | Model | Friendly values |
| --- | --- | --- |
| `map_compressed_rgb` | `MapTileCollectionModel` | Mission/page coordinates, decoded layers and tiles |
| `map_compressed_rgb_higher` | `MapTileCollectionModel` | Mission/page coordinates, decoded layers and tiles |
| `map_integrated` | `MapTileCollectionModel` | Mission/page coordinates, decoded layers and tiles |
| `map_combined_coverage` | `MapTileCollectionModel` | Mission/page coordinates, decoded layers and tiles |
| `map_semantics` | `MapTileCollectionModel` | Mission/page coordinates, decoded layers and tiles |
| `map_semantics_override` | `MapTileCollectionModel` | Mission/page coordinates, decoded layers and tiles |
| `latest_pose` | `PoseCollectionModel` | Mission, translation, quaternion, monotonic time, timestamp |
| `dock_detections` | `DockDetectionCollectionModel` | Mission, dock ID, pose, method, timestamp |
| `displayed_mission` | `MissionCollectionModel` | Active/displayed missions, explored state, floor labels |
| `active_session_key` | `ActiveSessionCollectionModel` | Mission and coverage-session IDs |
| `zones` | `ZoneCollectionModel` | Mission, zone ID/class, border and holes |
| `coverage_plan` | `CoveragePlanCollectionModel` | Mission, goal summary and command-compatible active plan |
| `sink_summon_locations` | `SinkSummonLocationCollectionModel` | Mission-relative location and heading |
| `coverage_session_history` | `CoverageHistoryCollectionModel` | Mission/session, start/end time, resumability |
| `recap_history` | `RecapCollectionModel` | Month, area, session count, favorite room and durations |
| `approximate_trajectory` | `PathCollectionModel` | Mission-relative path points |
| `coverage_corridor` | `CoverageLineCollectionModel` | Current mission-relative corridor |
| `coverage_marker` | `CoverageLineCollectionModel` | Current mission-relative marker |
| `flythrough` | `FlythroughCollectionModel` | Retained camera locations and targets |
| `wifi_status` | `WifiStatusCollectionModel` | State, current network/IP and scan counts |
| `motor_status` | `MotorStatusCollectionModel` | Voltage, current and RPM for six app-facing motors |
| `kabuki_state` | `RobotStatusCollectionModel` | Activity, state/error codes, battery and control feedback flags |
| `current_version` | `VersionCollectionModel` | Software/profile names and command protocol version |
| `coverage_time` | `CoverageTimeCollectionModel` | Session timing and progress |
| `update_state` | `UpdateStateCollectionModel` | Updater state, progress, size and release |
| `petwaste_enabled_state` | `BinarySettingCollectionModel` | Named boolean setting |
| `child_lock_enabled_state` | `BinarySettingCollectionModel` | Named boolean setting |
| `requested_preview_release_state` | `BinarySettingCollectionModel` | Named boolean setting |
| `voice_enabled_state` | `BinarySettingCollectionModel` | Named boolean setting |
| `auto_record_voice_enabled_state` | `BinarySettingCollectionModel` | Named boolean setting |
| `matter_pairing_state` | `MatterPairingCollectionModel` | Pairing state and hidden codes |
| `rolling_recordings_config_state` | `RollingRecordingCollectionModel` | Rolling-recording policy |
| `uploader_config_state` | `UploaderConfigCollectionModel` | Uploader baseline and opt-in |
| `user_tunnel_ssh_permission` | `SshPermissionCollectionModel` | Customer-granted support permission |
| `schedule_events` | `ScheduleEventCollectionModel` | Mission/event, weekdays, local time and enabled state |
| `schedule_event_previews` | `MediaCollectionModel` | Validated retained image/video assets |
| `sink_summons` | `SinkSummonScheduleCollectionModel` | Mission/event, weekdays, time, duration and enabled state |
| `coverage_session_thumbnails` | `MediaCollectionModel` | Validated retained image/video assets |
| `scratch_recordings` | `RecordingsCollectionModel` | Recording count |
| `recording_thumbnails` | `MediaCollectionModel` | Validated retained image/video assets |
| `recording_videos` | `MediaCollectionModel` | Validated retained image/video assets |
| `app_customer_info` | `CustomerInfoCollectionModel` | Hidden customer email |
| `jukebox_state` | `JukeboxCollectionModel` | Selected seasonal track |

The public mapping `matic_sdk.COLLECTION_MODEL_TYPES` lets applications inspect
the expected type for a target without decoding an event.

## Evidence boundary

Fields are named only when their meaning is supported by the official client
bindings and captured wire data. Some activity-dependent targets were observed
only while empty or at their default value. Those targets still return their
specific model, but optional values remain absent until a matching event is
received. This avoids replacing missing evidence with guessed protobuf tags
while keeping the complete wire fields available for later analysis.
