# Stable 172 client-surface audit

This audit records what changed in Matic's signed Android client between app
versions 1.151.0 and 1.172.1. It separates names and types established from the
client from protobuf formats proven well enough to publish in this SDK.

No command was sent during the static APK audit, and no robot was connected
over ADB. A separate owner-authorized live follow-up supplied the delivery
evidence described below. No household payload, credential, map, image, or
recording was added to the repository.

## Artifacts and method

The audited release is `com.maticrobots.maticapp` 1.172.1, version code 86.
Its signing certificate has the same SHA-256 digest as the previously audited
1.151.0 release:

```text
45e1591a92aaaa09773f95a434843ea7a699da55ca0f2a9210446691a378a20b
```

The local APK copies used for the audit have these SHA-256 digests:

```text
base.apk                     4731603ca842b6862a901e0204cb25542a29c446b16d111e02f72f0dd6167144
split_config.arm64_v8a.apk   a7081d40e289b5ca78314c2e20b5ee526432f5c53b35dd21dc7b2269bc02fea4
libmegazord.so               8c53d0df1464e39b995d8daf5c5525d0e3104dbb505b16e5bd95f1bc4ee6b144
```

The comparison used Android manifest metadata, generated Kotlin UniFFI
bindings, retained Rust symbols and type names, native string tables, and
targeted AArch64 disassembly. A name or app-facing type is not treated as an
exact protobuf codec. Registration still requires the concrete Hermes target
and byte-for-byte serialization evidence.

## Read-side additions

Stable 172's Kabuki subscription surface adds six property targets.

| Hermes target | App-facing value | SDK decision |
| --- | --- | --- |
| `voice_available` | `BinaryState(enabled: bool)` | Live-verified typed telemetry |
| `user_audio_recording_state` | `Idle` or `Recording(AudioRecordingMode)` | Live-verified typed state with exact native mode mapping |
| `deep_mop_override_setting_state` | `DeepMopOverrideEnabledState(enabled: bool)` | Live-verified typed disabled/enabled oneof |
| `water_flow_override_state` | `WaterFlowOverrideState(factor: float32)` | Live-verified typed nested float32 state |
| `time_zone` | `TimeZoneState(TimeZone)` | Live-verified typed identifier and signed offset |
| `bag_pass_status` | `NotOwned` or an active ownership interval; app status is derived from expiry | Exact native typed schema; app-static pending a live account record |

All six targets are accepted because their names and app-facing classifications
are exact and every decoder preserves the raw payload and fields. Five are now
live-verified. `bag_pass_status` remains app-static because its stream was
accepted but this account delivered no record.

### Owner-authorized live capture

On 2026-08-21, firmware `v172.12`/protocol 25 delivered five targets over a
certificate-pinned authenticated `FetchCollection` connection. The SDK sent
sequence acknowledgements and retained the raw envelopes outside the source
repository with owner-only permissions.

| Target | Exact application payload evidence |
| --- | --- |
| `voice_available` | `08 01` (`enabled = true`) |
| `user_audio_recording_state` | Initial/Idle `12 00`; bounded command observations added Ambient `08 01` and Direction-of-Arrival `08 02` |
| `deep_mop_override_setting_state` | Disabled `0a 00`; bounded transition captured enabled `12 00` and restored disabled |
| `water_flow_override_state` | Initial empty/default scalar; bounded neutral `1.0` transition captured `0a 05 0d 00 00 80 3f` |
| `time_zone` | Outer field 1 contained identifier `America/Chicago` in field 2 and signed offset `-21600` seconds in field 3 |

`bag_pass_status` opened successfully but delivered zero events during both the
concurrent capture and a separate 120-second subscription. Its native parser
is exact: outer field 1 carries an active pass with start and expiry Timestamp
messages in fields 1 and 2. The app derives Active, ExpiringSoon, and Expired
from the expiry rather than separate wire variants.

Stable 172 also removes `requested_preview_release_state` and
`fcm_device_group` from its compact app subscription table. That establishes
an app-client surface change, not a promise that older robot endpoints stopped
serving those targets. The SDK therefore keeps its earlier live-verified read
support.

## Cues voice and gesture telemetry

The current `KabukiOutput` binding adds these fields to the existing robot
status view:

- `isRecording: bool`
- `isFollowingPerson: bool`
- `voiceStatus: KabukiVoiceStatus?`
- `gestureStatus: KabukiGestureStatus?`

Stable 172 exposes the voice states `Disabled`, `ListeningForWakeWord`,
`ListeningForIntent`, `ThinkingForIntent`, `Classified`, and `Rejected`. Its
gesture workflow exposes awaiting/accepting a pointed target, no target found,
facing or following a person, person not found, repositioning, re-awaiting a
target, and awaiting a step back.

The UniFFI discriminators are exact: voice uses 1 through 6 in that order and
gesture uses 1 through 9 in the order listed by the public enum. They describe
the app/native boundary, not unproven network protobuf tags.

The SDK publishes stable string enums and optional model fields for those
app-facing values. It does not guess voice or gesture fields in the
`kabuki_state` protobuf: those statuses are assembled from separate native
events, so fields without a matching source remain `None`, while the original
payload and parsed fields remain available.

`isRecording` has independent wire evidence. An owner-authorized capture
changed the packed robot state codes from `106, 209, 212` to
`106, 209, 211, 212` while recording was active, then removed 211 when it
stopped. The SDK therefore derives `is_recording` from state code 211.
`isFollowingPerson` has no equivalent captured transition and remains unknown.

The Cues classifier metadata also names task, gesture, unknown, and recording
intent categories. Task intents include cleaning, docking, navigation,
pause/resume/stop, go-away, redo-last-clean, and sink summon. Gesture intents
include follow-person and point-to-clean. Recording intents include rolling,
ambient-audio, and direction-of-arrival recording. These are app-facing names,
not proof that arbitrary speech text or microphone samples are exposed by the
robot collection API.

## Command-side additions and replacements

Stable 172 adds these exact native sender surfaces and target strings.

| Sender operation | Hermes target | App-facing input | Publication decision |
| --- | --- | --- | --- |
| `sendUserAudioRecordingCommand` | `user_audio_recording_command` | `Idle` or `Recording(Ambient \| DirectionOfArrival \| WakeWord)` | Registered with four exact native goldens |
| `sendDeepMopOverrideEnableCommand` | `deep_mop_override_setting_command` | Boolean enable | Registered with exact false/true oneof mapping |
| `sendWaterFlowOverrideCommand` | `water_flow_override_command` | `float32` factor | Registered for the recovered `0.5`-`2.0` app range; `1.0` is neutral |
| `sendSweeperMaintenanceResolveCommand` | `sweeper_maintenance_command` | Unit/empty resolve request | Registered with exact Resolve-arm envelope |
| `sendLiveActivityRegistrationCommand` | `live_activity_registration` | Device ID and notification start/update tokens | Registered as sensitive notification plumbing with hidden token representations |

The cleaning-motor sender moved from the removed
`MechanicalCommandSender` to `KabukiCommandSender`; the existing
`motor_command` target remains present. The schedule API method changed from
`sendEditScheduleCommand` to `sendEditCleaningScheduleCommand`, while the
native target table still contains `edit_schedule`. Stable 172 removes the
app-facing preview-release command method and replaces the old push
notification sender with live-activity registration. These are client API
changes, not enough evidence by themselves to retire an existing robot codec.

Live-activity registration contains cloud notification credentials: a start
token carries FCM and push-to-start tokens, while an update token carries a
live-activity ID and push token. The SDK accepts them only as an explicit
sensitive command: token fields are excluded from representations and command
audit records, and the SDK does not persist them.

## `user_audio_recording_command` boundary

The public shape is now well established:

```text
UserAudioRecording = Idle
                   | Recording(Ambient)
                   | Recording(DirectionOfArrival)
                   | Recording(WakeWord)
```

The app binding fixes the mode order as ambient, direction of arrival, and
wake word, with zero-based UniFFI discriminants 0, 1, and 2. Stable 172 retains
both the high-level-to-protobuf conversion and the concrete generated
`ProtoFormat::value_to_bytes` implementation. Tracing the sender through those
functions proves the exact bodies:

```text
Idle                 12 00
Ambient              08 01
DirectionOfArrival   08 02
WakeWord             08 03
```

The matching high-level decoder rejects enum zero and accepts protobuf modes
1 through 3 in that order, independently confirming the mode mapping.

The command changes recording control state; it does not carry audio samples.
The matching property can report the requested mode, but neither binding nor
the existing media collections proves that ambient, direction-of-arrival, or
wake-word microphone data becomes downloadable. Publication must not claim an
audio retrieval feature until a separate owner-authorized capture demonstrates
one.

The SDK reproduces each inner protobuf and canonical `ChannelRequest`, pins all
four variants in golden tests, and labels the command sensitive. A bounded
2026-08-21 run added live acknowledgement and retained-state evidence for Idle,
Ambient, and Direction-of-Arrival. Wake Word was acknowledged but did not
produce an `08 03` state within ten seconds. No microphone bytes were captured,
and live delivery remains separate from any returned-audio claim.

## Result

This release provides typed models for all six read-side additions, with five
promoted to live-verified and `bag_pass_status` kept app-static pending an
account record. Exact retained serializers prove all five command bodies;
bounded owner-authorized tests added live delivery for user-audio recording,
deep-mop override, and water-flow override. Sweeper maintenance and live
activity registration remain offline-only.
