# Architecture

```text
nyx_bridge
├── protocol.py   framing, message builders, JSON conversion, address lookup
├── client.py     NyxClient: connects out, runs code
└── server.py     BridgeServer, ClientSession: accepts clients, sends code
```

`client.py` and `server.py` both depend on `protocol.py` and not on each other. A
client-only install (inside a DCC) only ever runs `protocol` and `client`.

## Direction of the connection

The application that **runs** code is the one that connects. Nyx listens; Blender or Maya
dial in. That way a DCC never opens a port, Nyx never has to find or launch an
application, and the DCC can start before or after Nyx and just retry.

## Server threads

```text
BridgeServer-<name>              accept loop (listener has a 0.2 s timeout to see stop())
 └─ BridgeServer-<name>-connection   one per connection:
      handshake, then ClientSession.run() reading messages until the socket ends
caller threads                    ClientSession.execute / ping, blocking on a Future
```

- `ClientSession._request` registers a `concurrent.futures.Future` under the request id,
  sends the frame under a send lock, and waits on the future. The connection thread's
  `_dispatch` completes the future when a `result`, `pong` or `error` carrying that id
  arrives. That is what lets several threads share one session.
- `close()` fails every pending future with `ClientDisconnected`.
- `_condition` guards the client list, the listeners and `wait_for_client`. Listener
  callbacks are called **outside** the lock, so a slow listener can't stall the server.
- Servers hand out ports from `DEFAULT_PORT` upward (`PORT_FALLBACK_COUNT` tries) because
  several Nyx instances may run at once.

## Client threads

A client has one reading loop, `_serve_connection`, on whichever thread called
`serve_forever` (the calling thread, or the daemon thread of `start_in_thread`). For an
`execute` message it calls `run_on_main_thread(lambda: self._run_request(message))` and
sends the reply. Consequences:

- Executions are **serial**: the next message isn't read until the current one finishes.
- A `ping` queued behind a long execution is answered late, so a server's `ping` timeout
  measures responsiveness, not only liveness.
- `stop()` sets an event and shuts the socket down, which unblocks the blocking read.
- A `shutdown` message sets the same event, so the client leaves without reconnecting.

## The execution namespace

`NyxClient.execute` keeps one dict per connection (`_namespace`), seeded from
`default_namespace()` and `__name__ = "__nyx__"`, and replaced on reconnect. `exec` mode
pops `result` before running and reads it afterwards. stdout/stderr are redirected with
`contextlib.redirect_*`, which is process-wide: output of other threads running at the
same time can end up in the captured text.

`SystemExit` is caught together with `Exception`, so `sys.exit()` in a script is an
error of that request, not the end of the host application. Other `BaseException`s
(`KeyboardInterrupt`) are not caught.

## Values

`to_jsonable` keeps JSON scalars, turns tuples and sets into lists, dicts into dicts with
string keys, and everything else into `repr()`. Nesting deeper than 32 levels is also
turned into a `repr`, which keeps cyclic structures from recursing forever.
