# Android app 1.175.0 compatibility audit

Audited on September 10, 2026 against the August 21 SDK update, commit
`2b9aa1e3bca34088562eb2a48fedd810f5c7eba4`. A live upstream HEAD check still
returned that commit. The baseline Android app was 1.172.1 (version code 86);
Google Play delivered 1.175.0 (version code 92) during this audit.

The new app adds a brush-roll jam diagnostic workflow, expands sweeper
maintenance commands, and changes several telemetry models. These are
confirmed client interfaces. The SDK now implements the exact command formats
recovered from the signed app. Robot acceptance and firmware requirements have
not been tested, and no robot commands were sent.

## New and expanded robot commands

| App interface | Input or operation | Evidence and SDK implementation |
| --- | --- | --- |
| `UserCommand.DiagnoseBrushRollJam` | Begin brush-roll jam diagnosis | Registered as `user.diagnose_brush_roll_jam`; `CommandExecutor.diagnose_brush_roll_jam()` sends the exact nested user-command payload. |
| `sendBrushRollJamResponseCommand` | `BRUSH_ROLL_REMOVED`, `START_MOTOR_TEST`, or `STOP_MOTOR_TEST` | Registered as `device.brush_roll_jam_response` on `cleaning_workflow_response`; the codec includes the app's current timestamp and all three response arms. |
| `sendBrushRollJamDismissCommand` | Dismiss the diagnostic outcome | Registered as `device.brush_roll_jam_outcome_dismiss` on `sweeper_jam_outcome_dismiss` with the app's empty `Unit` value. |
| `sendSweeperMaintenanceCommand` | `Resolve` or `Trigger(MAINTENANCE \| FEEDBACK)` | Existing Resolve behavior remains compatible. `device.sweeper_maintenance_trigger` adds both new trigger arms on `sweeper_maintenance_command`. |

This is three new command intents plus additional variants of an existing
maintenance command. A start-motor-test response is part of a troubleshooting
workflow; its presence does not establish that it works independently of the
robot's current diagnostic state.

The response and dismissal targets were followed through concrete native
send paths rather than inferred from method names. In the new ARM64 library:

- At `0xa2bb38`, the response sender loads the string at `0x31178b` with
  length 26: `cleaning_workflow_response`. It calls `ToProto` at `0xfbe3e0`
  and the `CleaningWorkflowUserResponse` formatter at `0xd577b4`.
- At `0xa2bf20`, the dismissal sender loads the string at `0x3117a5` with
  length 27: `sweeper_jam_outcome_dismiss`. Its client send is typed as
  `Param<Value<Unit>, ProtoFormat>`.
- At `0xa2d0c0`, the maintenance sender loads
  `sweeper_maintenance_command`, then calls the
  `behavior_cleaning_proto::SweeperMaintenanceCommand` formatter at
  `0xd56844`.

These addresses apply only to the library hash recorded below. Further
disassembly established these exact protobuf bodies, now covered by SDK golden
tests:

- Diagnose brush-roll jam: `9201040a020a00`.
- Sweeper Resolve, Maintenance, and Feedback: `0a00`, `1a020a00`, and
  `1a021200`.
- Brush-roll responses end in `1a020a00`, `1a021200`, or `1a021a00`, after
  the encoded timestamp.
- Brush-roll outcome dismissal is an empty protobuf body.

## Telemetry and model changes

| Area | Change from 1.172.1 | SDK implication |
| --- | --- | --- |
| Brush-roll diagnosis | `KabukiOutput.brushRollJamOutcome` is new, with `NOT_JAMMED`, `STILL_JAMMED`, `FAILED_TO_RUN`, and `INSUFFICIENT_CHARGING` values. | `BrushRollJamOutcome` is public. The `RobotStatusCollectionModel` slot remains unset until its nested wire representation is proven; this app field does not establish a collection named `brush_roll_jam_outcome`. |
| Error details | `KabukiDisplayedError` changes from a flat enum to variants; brush-roll jam, brush-roll dislodgement, and mop-roll jam variants carry `isHardwareIssue`. | Preserve this additional diagnostic detail when its payload mapping is established. |
| Gesture status | Adds `FOLLOWING_PAUSED`, `MOVING_CLOSER`, and `MOVING_BACK`; removes `REPOSITIONING`. `KabukiOutput.isFollowingPerson` is also removed. | The three values are public. `REPOSITIONING` and `is_following_person` remain for Stable 172 compatibility. This does not prove that following behavior was removed. |
| Point-to-clean intents | `VoiceGestureIntentKind.PointToClean` now carries `PointToCleanIntentKind.GENERAL` or `STAIN`; `FollowPerson` remains. | `CuesPointToCleanIntentKind` and an optional robot-status slot are public. No new direct command for submitting arbitrary voice text or a follow-person request was established. |
| Recording reasons | `RollingRecordingReason` adds `BOT_LOST` and `TILTED`. | The full app 1.175 enum is public as `RollingRecordingReason`; this is not a new camera stream. |
| Cleaning explanation | `CoveragePlan.orderedBehaviors()` is added, along with cleaning-phase/card helpers. | Useful app-side presentation helpers; no new robot control endpoint was established for these methods. |

The app's nine generated subscription-wrapper interfaces were unchanged.
The inspected Kabuki property-target table also retained the same target
names. New nested fields should not be promoted to new standalone collection
targets without further evidence.

## Cleaning history: a new presentation, an existing SDK gap

The [iOS 1.175.0 release notes](https://apps.apple.com/us/app/matic-robots/id1587770267)
announce that history now displays why a cleaning session stopped.

The binary comparison makes the scope more precise:

- The older `HistoryEvent` already had `stopReason`, `completionKind`, and
  `completionStatus` fields. Its twelve `SessionStopReason` variants are
  unchanged, including cancellation, prolonged pause, loss of localization,
  schedule replacement, and deleted map/partition cases.
- The new binding replaces those three fields with
  `outcome: CoverageSessionOutcome`, which contains `completionKind` and
  `stopReason`. It adds `SessionStopReason.title()` and a `StopReasonKind`
  classification for incomplete, intentional-stop, and unexpected-stop cases.
- The SDK's `CoverageHistoryCollectionModel` now exposes
  `CoverageSessionOutcome`. Captured and statically confirmed `Finished` and
  `UserCancelled` oneof tags decode to typed stop reasons; other tags remain
  explicit `unknown_variant_N` values, and completion kind remains unset until
  its areas-summary derivation is fully proven. Raw payload and fields remain
  available. See
  [the model](../src/matic_sdk/models/collections.py) and
  [the decoder](../src/matic_sdk/collection_models.py).

The underlying stop-reason capability should not be described as newly
introduced in 1.175.

## Public release context

[Matic's firmware release notes](https://maticrobots.com/blog/matic-release-notes)
list Stable 173 on August 31 and Stable 174 on September 8. They describe
improved low-battery docking, pet-waste detection, coverage reporting,
under-furniture recovery, wake-word orientation, and faster cleaning.
Firmware release numbers and app version numbers are distinct.

The cached [Google Play listing](https://play.google.com/store/apps/details?id=com.maticrobots.maticapp)
showed an August 29 update with UI fixes, while direct Play delivery returned
1.175.0. This audit uses the delivered APK manifest as the Android version
evidence rather than assuming the cached listing is current.

## Artifacts and verification

Both versions' base and ARM64 split APKs passed `apksigner verify`. All four
have the same signing-certificate SHA-256 digest:

```text
45e1591a92aaaa09773f95a434843ea7a699da55ca0f2a9210446691a378a20b
```

| Artifact | SHA-256 |
| --- | --- |
| 1.175.0 base APK | `8dcafc1fce830876d2b0556c78db84ba0c318d77241134a78e4aaeb6a32fe9ee` |
| 1.175.0 ARM64 split APK | `e519199a10087ce995c37102caac692c7198bac4c0e52b32caedfb53ce25309f` |
| 1.175.0 `libmegazord.so` | `ca84c1b50682f303a788a8fee1ffccdb23b3ec3129e831284ee969302559ede5` |
| 1.172.1 base APK | `4731603ca842b6862a901e0204cb25542a29c446b16d111e02f72f0dd6167144` |
| 1.172.1 ARM64 split APK | `a7081d40e289b5ca78314c2e20b5ee526432f5c53b35dd21dc7b2269bc02fea4` |
| 1.172.1 `libmegazord.so` | `8c53d0df1464e39b995d8daf5c5525d0e3104dbb505b16e5bd95f1bc4ee6b144` |

The comparison used APK manifests, generated DEX bindings, native exported
symbols, retained Rust symbols, string tables, and targeted ARM64 disassembly.
Temporary artifacts and comparison files are in
`/tmp/matic-audit-20260910.L08DKh`; APKs and native binaries were not added to
the repository. The prior audit is [Stable 172](stable-172-audit.md).

Remaining work is the nested Kabuki mapping for brush/error detail, completion
kind derivation, and the unobserved history stop-reason tags. Live behavior and
installed robot firmware remain unverified by this static audit.
