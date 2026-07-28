# Command verification ledger

This is the protocol-25 command boundary implemented by the SDK: 65 documented
intents, 65 exact `wire_verified` formats and registered codecs, and 10 command
intents with live delivery evidence. `Wire / yes` means the protobuf body,
Hermes target, and surrounding channel envelope are exact and the codec is
registered.

The live column is independent of codec evidence. `Yes` records a bounded,
acknowledged send on the date stated in that row; observed physical effects
are described separately. Risk is descriptive metadata and does not gate a
command. Exact offline codecs have not been live-tested unless the row
explicitly says yes.

| Command key | Hermes target | Evidence / codec | SDK live | Risk | Live evidence or notes |
| --- | --- | --- | --- | --- | --- |
| `user.stop` | `user_command` | Wire / yes | Yes | stationary | Acknowledged while docked; no transition claimed |
| `user.stay_put` | `user_command` | Wire / yes | Yes | stationary | Acknowledged while docked; no transition claimed |
| `user.pause` | `user_command` | Wire / yes | Yes | stationary | Acknowledged while docked; no transition claimed |
| `user.resume` | `user_command` | Wire / yes | No | motion | No motion-changing live test |
| `user.dock` | `user_command` | Wire / yes | Yes | motion | On 2026-07-28 an acknowledged command transitioned ready → returning → charging in about eight seconds with no robot errors |
| `user.explore` | `user_command` | Wire / yes | No | motion | No motion-changing live test |
| `user.re_explore` | `user_command` | Wire / yes | No | motion | No motion-changing live test |
| `user.redo_coverage` | `user_command` | Wire / yes | No | motion | No motion-changing live test |
| `user.resume_coverage` | `user_command` | Wire / yes | No | motion | No motion-changing live test |
| `user.trace_calibration` | `user_command` | Wire / yes | No | raw actuation | Hazardous motion; offline wire proof only |
| `user.joystick` | `user_command` | Wire / yes | Yes | motion | Each call sends once with no SDK watchdog; a bounded forward sequence produced 25 acknowledged sends and a docked-to-ready state transition; explicit zero plus Stop completed |
| `navigation.navigate` | `user_command` | Wire / yes | Yes | motion | On 2026-07-28 a corrected canonical-frame command reached the requested pose within 0.012 m and 0.078 rad; Stop was acknowledged and no robot errors appeared |
| `navigation.navigate_and_wait` | `user_command` | Wire / yes | No | motion | Exact fixed 900-second wait envelope; no live test |
| `navigation.navigate_and_explore` | `user_command` | Wire / yes | No | motion | Exact NavigateTo plus Explore task envelope; no live test |
| `coverage.normal` | `user_command` | Wire / yes | Yes | motion | On 2026-07-28 a bounded one-room Quick Vacuum run was acknowledged; its active mission, session, goals, and current region decoded successfully before Stop and Dock |
| `coverage.reprioritize` | `user_command` | Wire / yes | No | motion | Exact Prioritize and Skip plan transforms; requires the current goal plan and session ID; Add/Redo not exposed |
| `coverage.stain_mode` | `user_command` | Wire / yes | No | motion | Exact dry-stain and wet-spill drawn-circle plans; no live test |
| `cleaning.manual` | `user_command` | Wire / yes | No | motion | No motion-changing live test |
| `raw_motors.setpoints` | `motor_command` | Wire / yes | No | raw actuation | Direct codec with no device-specific range limits; not live-tested |
| `map.build_partition` | `build_regions` | Wire / yes | No | persistent | Offline native-serializer golden proof only |
| `map.edit_rooms` | `rename_area_command` | Wire / yes | No | persistent | Rename, merge, and split variants proven offline |
| `map.edit_no_go_zone` | `nogo_command` | Wire / yes | No | persistent | Add and remove variants proven offline |
| `map.edit_drive_only_zone` | `nogo_command` | Wire / yes | No | persistent | Add and remove variants proven offline |
| `map.edit_stairs` | `stair_command` | Wire / yes | No | persistent | Add and remove variants proven offline |
| `map.edit_semantics_override` | `semantics_override` | Wire / yes | No | persistent | All five semantic kinds proven offline |
| `map.edit_sink_summon_location` | `edit_sink_summon_location` | Wire / yes | No | persistent | Add/modify and remove variants proven offline |
| `map.canonicalize` | `floor_command` | Wire / yes | No | persistent | Both mission and next-noncanonical variants proven offline |
| `map.rename` | `floor_command` | Wire / yes | No | persistent | Offline native-serializer golden proof only |
| `map.persistence_clear` | `map_command` | Wire / yes | No | destructive | Destructive; offline wire proof only |
| `map.clear_map` | `map_command` | Wire / yes | No | destructive | Destructive; offline wire proof only |
| `map.restore_map` | `map_command` | Wire / yes | No | persistent | Offline native-serializer golden proof only |
| `map.upload_map_for_debug` | `map_command` | Wire / yes | No | sensitive | Offline native-serializer golden proof only |
| `map.clear_rgb_weights` | `clear_rgb_weights_command` | Wire / yes | No | destructive | Destructive; offline wire proof only |
| `wifi.scan` | `wifi_scan_command` | Wire / yes | No | sensitive | Offline wire proof only |
| `wifi.connect` | `wifi_update_command` | Wire / yes | No | persistent | Offline native-serializer golden proof only |
| `wifi.forget` | `wifi_update_command` | Wire / yes | No | destructive | Destructive; offline wire proof only |
| `device.rename` | `new_bot_name` | Wire / yes | No | persistent | Offline native-serializer golden proof only |
| `device.discoverability` | `set_device_discoverable` | Wire / yes | No | sensitive | Enable and disable variants proven offline |
| `device.new_mop_roll` | `new_mop_roll_override_command` | Wire / yes | No | persistent | Offline native-serializer golden proof only |
| `device.clear_calibration` | `clear_online_calib_command` | Wire / yes | No | destructive | Destructive; offline wire proof only |
| `device.configure_shipping` | `configure_shipping_command` | Wire / yes | No | destructive | Destructive; offline wire proof only |
| `settings.child_lock` | `child_lock_enabled_command` | Wire / yes | Yes | persistent | Same-value write acknowledged; no transition claimed |
| `settings.pet_waste_avoidance` | `petwaste_enabled_command` | Wire / yes | Yes | persistent | Same-value write acknowledged; no transition claimed |
| `settings.voice` | `voice_enabled_command` | Wire / yes | Yes | persistent | Same-value write acknowledged; no transition claimed |
| `settings.auto_record_voice` | `auto_record_voice_enabled_command` | Wire / yes | No | sensitive | Offline native-serializer golden proof only |
| `settings.matter_pairing` | `matter_pairing_command` | Wire / yes | No | sensitive | Offline native-serializer golden proof only |
| `settings.preview_release` | `request_preview_release_command` | Wire / yes | No | persistent | Offline native-serializer golden proof only |
| `settings.jukebox` | `jukebox_command` | Wire / yes | No | persistent | All optional enum variants proven offline |
| `schedule.add_or_modify` | `edit_schedule` | Wire / yes | No | persistent | Full 1,905-byte standard event and custom-area structures proven offline |
| `schedule.remove` | `edit_schedule` | Wire / yes | No | destructive | Destructive; offline wire proof only |
| `schedule.toggle` | `edit_schedule` | Wire / yes | No | persistent | Offline native-serializer golden proof only |
| `schedule.generate_suggested` | `generate_suggested_schedule` | Wire / yes | No | persistent | Offline wire proof only |
| `schedule.sink_summon_add_or_modify` | `edit_sink_summon_schedule` | Wire / yes | No | persistent | Timing, duration, and enabled variants proven offline |
| `schedule.sink_summon_remove` | `edit_sink_summon_schedule` | Wire / yes | No | destructive | Destructive; offline wire proof only |
| `media.recording_enable` | `recording_command` | Wire / yes | No | sensitive | Enabled and disabled variants proven offline |
| `media.rolling_buffer_config` | `toggle_rolling_recordings` | Wire / yes | No | sensitive | Enabled/confirm-each/disabled variants proven offline |
| `media.flush_rolling_buffer` | `recording_command` | Wire / yes | No | sensitive | Offline native-serializer golden proof only |
| `media.confirm_save` | `recording_upload_confirmation` | Wire / yes | No | sensitive | Offline native-serializer golden proof only |
| `media.confirm_delete` | `recording_upload_confirmation` | Wire / yes | No | destructive | Destructive; offline wire proof only |
| `telemetry.uploader_config` | `uploader_config_command` | Wire / yes | No | sensitive | Offline native-serializer golden proof only |
| `telemetry.support_ssh_permission` | `user_tunnel_ssh_permission_command` | Wire / yes | No | sensitive | Offline wire proof only; no shell access implied |
| `telemetry.push_notification_subscription` | `subscribe_push_notifications` | Wire / yes | No | sensitive | Offline native-serializer golden proof only |
| `lifecycle.update` | `update_command` | Wire / yes | No | destructive | Destructive; offline wire proof only |
| `lifecycle.reboot` | `reboot_command` | Wire / yes | No | destructive | Destructive; offline wire proof only |
| `lifecycle.shutdown` | `reboot_command` | Wire / yes | No | destructive | Destructive; offline wire proof only |

The ledger describes protocol evidence, not a recommendation to send a command.
All registered codecs are callable directly; risk labels are informational and
safe operation is the caller's responsibility.
