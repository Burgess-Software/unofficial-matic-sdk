# Experimental remote path

The SDK includes strict, offline helpers for the portal-backed remote transport
under `matic_sdk.experimental.remote`. It does **not** currently expose a
networked remote client.

## What has been established

The official client has been observed using this stack:

```text
portal websocket
  -> raw byte tunnel
  -> robot TLS with ALPN h2
  -> HTTP/2
  -> Hermes gRPC
```

The websocket uses a portal bearer JWT and a URL shaped as
`<portal-origin>/connect/client/agent_hermes`. Before raw tunnel bytes begin,
the portal sends JSON text control frames. The two recovered shapes are a
`{"Status":"..."}` object and the JSON string `"Connected"`.

One observed `RequestRemoteToken` response was a protobuf with three
length-delimited strings: device serial in field 1, portal base URL in field 3,
and portal JWT in field 4. Its request was field 1 containing the user's UUID.

## Important limits

- Current firmware has been observed rejecting `RequestRemoteToken` as
  unauthenticated. The encoder documents a recovered wire shape; it does not
  promise that a credential can be minted.
- A portal JWT authenticates the outer websocket. Authenticated collection and
  command RPCs inside the tunnel still require the separate local Hermes
  `BotToken`.
- Historical portal credentials reached the portal authentication layer in a
  later replay, but the robot-side `agent_hermes` service was unavailable. That
  does not establish current end-to-end remote access.
- JWT claim decoding is explicitly unverified. It does not validate a
  signature, issuer, audience, or expiration.
- The module does not open sockets, persist credentials, or implement TLS over
  websocket. A future transport must use normal certificate validation or an
  explicit robot certificate pin; it must not copy the old diagnostic probe's
  certificate-verification bypass.

## Available helpers

- `encode_remote_token_request(user_id)` — strict, offline request encoding.
- `decode_remote_access_token(payload)` — strict parser for the one recovered
  response schema; unknown or duplicate fields fail closed.
- `build_portal_tunnel_url(base_url, service)` — accepts only a credential-free
  `wss` origin and a safe service identifier.
- `decode_unverified_jwt(token)` — diagnostic JSON decoding with an explicit
  trust warning.
- `parse_portal_control_message(text)` — parser for the two observed JSON
  control shapes.
- `PortalTunnelGate` — prevents binary tunnel bytes before `"Connected"` and
  rejects unexpected text after the tunnel opens.

These APIs are experimental because the cloud service and firmware can change
independently of this SDK. Treat any compatibility break as expected until the
path is exercised against a current, owner-authorized device.
