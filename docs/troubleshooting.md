# Troubleshooting

## `matic` is not found

Install the CLI into the active Python environment:

```bash
python -m pip install --pre "unofficial-matic-sdk[all]"
matic --version
```

With `uv`, install it as a tool instead:

```bash
uv tool install --python 3.13 --prerelease allow \
  "unofficial-matic-sdk[all]"
matic --version
```

The public CLI command is `matic`. `uv run matic` is only needed when running
the repository's development checkout.

## Bluetooth enrollment finds no robot

Enrollment requires Linux, BlueZ, the `ble` package extra, and Matic's pairing
mode.

1. Put the robot in **Settings → Connectivity → Add another user** pairing
   mode.
2. Keep the Bluetooth adapter close to the robot.
3. Confirm Bluetooth is enabled and not blocked by the operating system.
4. Increase the scan window:

   ```bash
   matic enroll --device-alias my-matic --scan-timeout 30
   ```

If several robots are nearby, use the exact advertised name or address shown
during your own Bluetooth inspection:

```bash
matic enroll --device-alias my-matic --name matic-EXACT-NAME
matic enroll --device-alias my-matic --address BLUETOOTH_ADDRESS
```

Enrollment refuses to overwrite an existing alias. Check the selected store
without printing its token:

```bash
matic credentials status --device-alias my-matic
```

## Finding `MATIC_HOST`

The SDK deliberately does not sweep the local network. Find the robot's current
IP address or local hostname in your router's DHCP lease/client list, then set:

```bash
export MATIC_HOST=ROBOT_IP_OR_HOSTNAME
```

Before sending credentials, query the unauthenticated identity endpoint:

```bash
matic probe --host "$MATIC_HOST" --insecure-read-only
```

Confirm the returned serial number and hardware revision belong to the intended
robot using a trusted record or physical device label. If the endpoint does not
respond, confirm that the computer and robot are on the same LAN and that TCP
port `16320` is reachable.

## Pinning the robot certificate

Fetch the certificate without sending the BotToken:

```bash
matic tls fingerprint --host "$MATIC_HOST"
```

This is a trust-on-first-use observation, not proof supplied by Matic. First
identify the endpoint with the unauthenticated probe and your router's lease
information. For stronger confidence, repeat the fingerprint check from a
second trusted machine or trusted network path. Then pin the exact value:

```bash
export MATIC_CERT_SHA256=VERIFIED_64_CHARACTER_SHA256
matic status --device-alias my-matic --host "$MATIC_HOST"
```

If the certificate later changes, do not replace the pin until you have
re-identified the endpoint. A pin mismatch prevents the SDK from sending the
reusable BotToken to a different TLS peer.

## Finding the command protocol version

Reads do not need a command protocol selection. After authenticating, decode
one retained version event:

```bash
export MATIC_DEVICE_ALIAS=my-matic
matic collections stream current_version \
  --count 1 \
  --duration 10 \
  --decode \
  --json
```

Use the model's `protocol_version` for command configuration:

```bash
export MATIC_PROTOCOL_VERSION=25
```

Protocol 25 is the verified codec baseline. Another positive version is
accepted and emits `UnverifiedProtocolVersionWarning`; the SDK then uses the
protocol-25 protobuf encoders as a compatibility attempt.

## A decoded collection is empty or contains `null`

Some collections legitimately represent an idle/default state, and delete
events have no application value. Every known target still returns its named
model. Optional fields remain `null` when the robot did not emit that variant.
The in-memory model retains `raw_payload` and parsed `fields` for analysis.

Live JSON output redacts names, network/account data, pairing codes, and stable
media hashes by default:

```bash
matic collections stream wifi_status --decode --json
```

Use `--include-sensitive` only when that information is intentionally going to
the current terminal or output consumer.

## Decoding a saved capture

A directory created by `matic collections record` includes a manifest, so its
targets are recovered automatically:

```bash
matic collections decode captures/telemetry --json
```

An individual response file does not contain the Hermes target and therefore
requires one:

```bash
matic collections decode event.pb --target latest_pose --pretty
```

The command never prints raw protobuf or retained media bytes. It applies the
same default redaction as live decoded output.

## Map or voxel dependencies are missing

Install the map extra:

```bash
python -m pip install --pre "unofficial-matic-sdk[maps]"
```

The `all` extra includes both map and Bluetooth dependencies.

## Development tests load an unrelated system plugin

Some Linux/ROS installations expose global pytest plugins whose own optional
dependencies are incomplete. The repository and CI isolate the SDK tests:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  uv run pytest -p pytest_asyncio.plugin
```

## Remote access

Normal SDK operation is local-network only. The portal-backed transport under
`matic_sdk.experimental` is not a reliable remote-control path.
