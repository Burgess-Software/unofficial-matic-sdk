# Command verification ledger

This is the protocol-25 command boundary implemented by the SDK: 65 documented
intents, 30 exact `wire_verified` formats, 29 registered codecs, and 7 command
intents with live delivery evidence. `Wire / yes` means the protobuf body,
Hermes target, and surrounding channel envelope are exact and the codec is
registered. `Wire / no` records an exact format disabled by safety policy.
`Static type / no` or `Static fields / no` means the target and some
client-side structure are known, but the SDK has no encoder and fails before
network I/O.

The live column is independent of codec evidence. `Yes` records one bounded,
acknowledged send on 2026-07-22, not proof of a physical transition. A safety
gate authorizes an informed attempt; it does not upgrade the evidence. Exact
offline codecs have not been live-tested unless the row explicitly says yes.

| Command key | Hermes target | Evidence / codec | SDK live | Gate / risk | Current limitation or blocker |
| --- | --- | --- | --- | --- | --- |
| `user.stop` | `user_command` | Wire / yes | Yes | None / stationary | Acknowledged while docked; no transition claimed |
| `user.stay_put` | `user_command` | Wire / yes | Yes | None / stationary | Acknowledged while docked; no transition claimed |
| `user.pause` | `user_command` | Wire / yes | Yes | None / stationary | Acknowledged while docked; no transition claimed |
| `user.resume` | `user_command` | Wire / yes | No | `MotionControls` / motion | No motion-changing live test |
| `user.dock` | `user_command` | Wire / yes | No | `MotionControls` / motion | No motion-changing live test |
| `user.explore` | `user_command` | Wire / yes | No | `MotionControls` / motion | No motion-changing live test |
| `user.re_explore` | `user_command` | Wire / yes | No | `MotionControls` / motion | No motion-changing live test |
| `user.redo_coverage` | `user_command` | Wire / yes | No | `MotionControls` / motion | No motion-changing live test |
| `user.resume_coverage` | `user_command` | Wire / yes | No | `MotionControls` / motion | No motion-changing live test |
| `user.trace_calibration` | `user_command` | Wire / yes | No | `MotionControls` + `UnsafeControls` / raw actuation | Hazardous motion; offline wire proof only |
| `user.joystick` | `user_command` | Wire / yes | Yes | `MotionControls` / motion | A bounded forward sequence produced 25 acknowledged sends and a docked-to-ready state transition; zero plus Stop completed |
| `navigation.navigate` | `user_command` | Wire / yes | No | `MotionControls` / motion | Exact offline coordinate transform and envelope; no live test |
| `navigation.navigate_and_wait` | `user_command` | Wire / yes | No | `MotionControls` / motion | Exact fixed 900-second wait envelope; no live test |
| `navigation.navigate_and_explore` | `user_command` | Wire / yes | No | `MotionControls` / motion | Exact NavigateTo plus Explore task envelope; no live test |
| `coverage.normal` | `user_command` | Wire / yes | No | `MotionControls` / motion | Native encoder proof plus official Android 1.167 synthetic golden vector; no live test |
| `coverage.reprioritize` | `user_command` | Wire / yes | No | `MotionControls` / motion | Exact Prioritize and Skip plan transforms; requires the current goal plan and session ID; Add/Redo not exposed |
| `coverage.stain_mode` | `user_command` | Wire / yes | No | `MotionControls` / motion | Exact dry-stain and wet-spill drawn-circle plans; no live test |
| `cleaning.manual` | `user_command` | Wire / yes | No | `MotionControls` / motion | No motion-changing live test |
| `raw_motors.setpoints` | `motor_command` | Wire / no | No | Disabled / raw actuation | Exact fields known; hardware-safe ranges are not, so no registry codec |
| `map.build_partition` | `build_regions` | Static type / no | No | `UnsafeControls` / persistent | Exact inner protobuf tags incomplete |
| `map.edit_rooms` | `rename_area_command` | Static type / no | No | `UnsafeControls` / persistent | Exact edit oneof and tags incomplete |
| `map.edit_no_go_zone` | `nogo_command` | Static type / no | No | `UnsafeControls` / persistent | Exact edit oneof and tags incomplete |
| `map.edit_drive_only_zone` | `nogo_command` | Static type / no | No | `UnsafeControls` / persistent | Exact edit oneof and tags incomplete |
| `map.edit_stairs` | `stair_command` | Static type / no | No | `UnsafeControls` / persistent | Exact edit oneof and tags incomplete |
| `map.edit_semantics_override` | `semantics_override` | Static type / no | No | `UnsafeControls` / persistent | Exact inner protobuf tags incomplete |
| `map.edit_sink_summon_location` | `edit_sink_summon_location` | Static type / no | No | `UnsafeControls` / persistent | Exact variant encoding incomplete |
| `map.canonicalize` | `floor_command` | Static type / no | No | `UnsafeControls` / persistent | Exact floor-command oneof tags incomplete |
| `map.rename` | `floor_command` | Static type / no | No | `UnsafeControls` / persistent | Exact floor-command oneof tags incomplete |
| `map.persistence_clear` | `map_command` | Static type / no | No | `UnsafeControls` / destructive | Destructive variant encoding incomplete |
| `map.clear_map` | `map_command` | Static type / no | No | `UnsafeControls` / destructive | Destructive variant encoding incomplete |
| `map.restore_map` | `map_command` | Static type / no | No | `UnsafeControls` / persistent | Exact variant encoding incomplete |
| `map.upload_map_for_debug` | `map_command` | Static type / no | No | `UnsafeControls` / sensitive | Exact variant encoding incomplete |
| `map.clear_rgb_weights` | `clear_rgb_weights_command` | Wire / yes | No | `UnsafeControls` / destructive | Destructive; offline wire proof only |
| `wifi.scan` | `wifi_scan_command` | Wire / yes | No | `UnsafeControls` / sensitive | Offline wire proof only |
| `wifi.connect` | `wifi_update_command` | Static fields / no | No | `UnsafeControls` / persistent | Exact connect variant and tags incomplete |
| `wifi.forget` | `wifi_update_command` | Static fields / no | No | `UnsafeControls` / destructive | Exact forget variant and tags incomplete |
| `device.rename` | `new_bot_name` | Static fields / no | No | `UnsafeControls` / persistent | Exact protobuf tags incomplete |
| `device.discoverability` | `set_device_discoverable` | Static fields / no | No | `UnsafeControls` / sensitive | Exact enable/disable variant encoding incomplete |
| `device.new_mop_roll` | `new_mop_roll_override_command` | Static fields / no | No | `UnsafeControls` / persistent | Exact protobuf tag incomplete |
| `device.clear_calibration` | `clear_online_calib_command` | Wire / yes | No | `UnsafeControls` / destructive | Destructive; offline wire proof only |
| `device.configure_shipping` | `configure_shipping_command` | Wire / yes | No | `UnsafeControls` / destructive | Destructive; offline wire proof only |
| `settings.child_lock` | `child_lock_enabled_command` | Wire / yes | Yes | `UnsafeControls` / persistent | Same-value write acknowledged; no transition claimed |
| `settings.pet_waste_avoidance` | `petwaste_enabled_command` | Wire / yes | Yes | `UnsafeControls` / persistent | Same-value write acknowledged; no transition claimed |
| `settings.voice` | `voice_enabled_command` | Wire / yes | Yes | `UnsafeControls` / persistent | Same-value write acknowledged; no transition claimed |
| `settings.auto_record_voice` | `auto_record_voice_enabled_command` | Static fields / no | No | `UnsafeControls` / sensitive | Exact protobuf tag incomplete |
| `settings.matter_pairing` | `matter_pairing_command` | Static fields / no | No | `UnsafeControls` / sensitive | Exact protobuf tag incomplete |
| `settings.preview_release` | `request_preview_release_command` | Static fields / no | No | `UnsafeControls` / persistent | Exact protobuf tag incomplete |
| `settings.jukebox` | `jukebox_command` | Static fields / no | No | `UnsafeControls` / persistent | Exact enum and tags incomplete |
| `schedule.add_or_modify` | `edit_schedule` | Static fields / no | No | `UnsafeControls` / persistent | Exact schedule variant encoding incomplete |
| `schedule.remove` | `edit_schedule` | Static fields / no | No | `UnsafeControls` / destructive | Exact schedule variant encoding incomplete |
| `schedule.toggle` | `edit_schedule` | Static fields / no | No | `UnsafeControls` / persistent | Exact schedule variant encoding incomplete |
| `schedule.generate_suggested` | `generate_suggested_schedule` | Wire / yes | No | `UnsafeControls` / persistent | Offline wire proof only |
| `schedule.sink_summon_add_or_modify` | `edit_sink_summon_schedule` | Static fields / no | No | `UnsafeControls` / persistent | Exact schedule variant encoding incomplete |
| `schedule.sink_summon_remove` | `edit_sink_summon_schedule` | Static fields / no | No | `UnsafeControls` / destructive | Exact schedule variant encoding incomplete |
| `media.recording_enable` | `recording_command` | Static fields / no | No | `UnsafeControls` / sensitive | Exact recording variant encoding incomplete |
| `media.rolling_buffer_config` | `toggle_rolling_recordings` | Static fields / no | No | `UnsafeControls` / sensitive | Exact protobuf tags incomplete |
| `media.flush_rolling_buffer` | `recording_command` | Static fields / no | No | `UnsafeControls` / sensitive | Exact recording variant encoding incomplete |
| `media.confirm_save` | `recording_upload_confirmation` | Static fields / no | No | `UnsafeControls` / sensitive | Exact confirmation variant encoding incomplete |
| `media.confirm_delete` | `recording_upload_confirmation` | Static fields / no | No | `UnsafeControls` / destructive | Exact confirmation variant encoding incomplete |
| `telemetry.uploader_config` | `uploader_config_command` | Static fields / no | No | `UnsafeControls` / sensitive | Exact protobuf tags incomplete |
| `telemetry.support_ssh_permission` | `user_tunnel_ssh_permission_command` | Static fields / no | No | `UnsafeControls` / sensitive | Exact protobuf tags incomplete; no shell access implied |
| `telemetry.push_notification_subscription` | `subscribe_push_notifications` | Static fields / no | No | `UnsafeControls` / sensitive | Exact protobuf tags incomplete |
| `lifecycle.update` | `update_command` | Wire / yes | No | `UnsafeControls` / destructive | Destructive; offline wire proof only |
| `lifecycle.reboot` | `reboot_command` | Wire / yes | No | `UnsafeControls` / destructive | Destructive; offline wire proof only |
| `lifecycle.shutdown` | `reboot_command` | Wire / yes | No | `UnsafeControls` / destructive | Destructive; offline wire proof only |

The ledger describes protocol evidence, not a recommendation to send a command.
In particular, the unsafe capability is deliberately required for persistent,
sensitive, raw-actuation, and destructive operations even when their encoder is
exact.
