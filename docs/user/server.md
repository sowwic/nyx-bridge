# Sending code from a server

`BridgeServer` accepts clients and gives you a `ClientSession` for each one.

```python
from nyx_bridge.server import BridgeServer

server = BridgeServer(name="my-server")
server.start()
print(server.port, server.token)

session = server.wait_for_client(kind="blender", timeout=60)
result = session.execute("result = len(bpy.data.objects)")
print(result.value, result.stdout)

server.stop()
```

The server is also a context manager: `with BridgeServer() as server:` starts it and
stops it on exit. `start()` is a no-op on a running server, and a stopped server can be
started again.

## Constructor

| Argument | Meaning |
| --- | --- |
| `name` | Server name, shown to clients. The class name when omitted. |
| `host` | Interface to listen on (default `127.0.0.1`). A warning is logged for anything but a local address. |
| `port` | `None`: try `DEFAULT_PORT` (47770) and the 20 ports after it. `0`: let the system choose. Anything else is used as is. |
| `token` | Shared secret clients must present. Random when omitted. |
| `address_file` | File telling clients where the server is. Written on `start()`, removed on `stop()`. See [Finding the server](addresses.md). |

!!! warning
    Whoever holds the token can have code run in every connected client. Keep the host
    local and treat the token, and the address file that contains it, as a secret.

If no port can be bound, `start()` raises `OSError`.

## Finding clients

```python
server.clients                              # connected sessions, in order of connection
server.find_clients(kind="maya")            # filter by name, kind and/or client_id
server.wait_for_client(name="shot-010", timeout=30)
```

`wait_for_client` returns the first match, waiting until one connects. It raises
`TimeoutError` when none does in time, and `RuntimeError` if the server is stopped while
waiting. Criteria left out match anything.

A `ClientSession` has `id`, `name`, `kind`, `version`, `address` and `is_connected`.

## Executing

```python
result = session.execute(
    "result = [o.name for o in bpy.data.objects if o.type == kind]",
    args={"kind": "MESH"},     # set in the client's namespace first (JSON only)
    timeout=10,
)
result.value     # what the code assigned to `result`
result.stdout    # what it printed
result.stderr
```

`mode="eval"` evaluates one expression instead of running statements. Several threads can
call `execute` on one session; each gets its own answer, although the client runs them
one at a time. See [what a script sees](client.md#what-a-script-sees) for the namespace
and value rules.

`session.ping(timeout=5.0)` checks the client answers. `session.request_shutdown()` asks
it to disconnect and stop without reconnecting. `session.close()` drops the connection.

## Errors

| Raised | When |
| --- | --- |
| `RemoteExecutionError` | The code raised in the client. Has `type_name`, `message`, `remote_traceback`, `stdout` and `stderr`. |
| `ClientDisconnected` | The client is gone, or went away while you waited. A subclass of `ConnectionError`. |
| `TimeoutError` | No answer within `timeout`. A late answer is ignored. |
| `TypeError` | `args` can't be serialized to JSON. Raised before anything is sent. |

```python
from nyx_bridge.server import RemoteExecutionError

try:
    session.execute("1 / 0")
except RemoteExecutionError as err:
    print(err.type_name, err.message)     # ZeroDivisionError division by zero
    print(err.remote_traceback)
```

## Listeners

To follow the server, for a UI for instance, add a listener: any object with some of
these methods (the others are skipped).

```python
class Watcher:
    def on_server_started(self, server): ...
    def on_server_stopped(self, server): ...
    def on_client_connected(self, server, session): ...
    def on_client_disconnected(self, server, session): ...

server.add_listener(Watcher())
```

They run on the server's own threads, so keep them quick and thread safe. A listener that
raises is logged and does not affect the server or the other listeners.
`remove_listener` takes one away.

## Deciding who may connect

The token is checked first. To add your own rule, subclass and override
`validate_client`, returning a reason to refuse or `None` to accept:

```python
class OnlyKnownKinds(BridgeServer):
    def validate_client(self, hello):
        if hello.get("client_kind") not in {"blender", "maya"}:
            return "unknown client kind"
```

`DEFAULT_PORT`, `PORT_FALLBACK_COUNT` and `HANDSHAKE_TIMEOUT` (seconds a new connection
has to send its hello, default 5) are class attributes you can override the same way.
