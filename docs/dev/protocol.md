# Wire protocol

`PROTOCOL_VERSION = 1`. Everything is in `nyx_bridge/protocol.py`.

## Framing

Each message is a JSON object (`separators=(",", ":")`, UTF-8) preceded by its length as a
4-byte big-endian unsigned integer.

```text
+----------------+---------------------------+
| length (u32 BE)| JSON payload (UTF-8)      |
+----------------+---------------------------+
```

- A payload larger than `MAX_MESSAGE_SIZE` (64 MiB) is refused when encoding (sender) and
  when reading the header (receiver, before allocating).
- The payload has to be an object with a string `type`, else `ProtocolError`.
- `recv_message` raises `ConnectionClosed` (a `ProtocolError`) when the peer closes
  mid-frame and passes `TimeoutError` through.
- Sockets shared between threads need a lock around `send_message`; the server's
  `ClientSession` has one, the client sends only from its reading thread.

## Messages

| `type` | Direction | Fields |
| --- | --- | --- |
| `hello` | client → server | `protocol_version`, `token`, `client_name`, `client_kind`, `client_version` |
| `ack` | server → client | `protocol_version`, `client_id`, `server_name` |
| `error` | either | `message`, optional `id` of the request it answers |
| `execute` | server → client | `id`, `code`, `mode` (`"exec"` or `"eval"`), `args` (object) |
| `result` | client → server | `id`, `ok`, `value`, `error`, `stdout`, `stderr` |
| `ping` | server → client | `id` |
| `pong` | client → server | `id` |
| `shutdown` | server → client | none |

`result.error` is `{"type", "message", "traceback"}` when `ok` is false, else `null`.
Builders: `make_hello`, `make_ack`, `make_error`, `make_execute`, `make_result`,
`make_ping`, `make_pong`, `make_shutdown`.

## Handshake

```text
client                                   server
  │ ── connect ───────────────────────────▶ │
  │ ── hello(token, kind, name, ...) ─────▶ │  must arrive within HANDSHAKE_TIMEOUT (5 s)
  │                                         │  checks, in order: type == hello,
  │                                         │  protocol_version, token (constant time),
  │                                         │  then validate_client(hello)
  │ ◀──── ack(client_id, server_name) ───── │  accepted
  │ ◀──── error(message) ─────────────────  │  refused, connection closed
```

The client waits up to `HANDSHAKE_TIMEOUT` (10 s) for the reply. An `error` becomes
`HandshakeError`, which `serve_forever` does **not** retry. Network errors and
`ProtocolError` are retried.

## Requests

Request ids are strings chosen by the sender (`"1"`, `"2"`, ... for executions,
`"ping-N"` for pings). An answer carries the id of its request. An `error` with an `id`
fails that request on the server; an answer whose id nobody waits for (a timed-out
request, say) is dropped silently.

An unknown message type received by the client is answered with an `error`. One received
by the server is logged and ignored.

## Changing the protocol

- Bump `PROTOCOL_VERSION` for any change an older peer can't understand. The server
  refuses other versions in the hello, so mixed versions fail loudly at connect.
- Adding an optional field to a message needs no bump: peers read fields with `.get`.
- The clients (`nyx-bridge-maya`, `nyx-bridge-blender`) and Nyx pin a tag of this package,
  so a change lands in each when its pin moves.
