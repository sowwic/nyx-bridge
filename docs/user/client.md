# Running code in a process

`NyxClient` connects to a Nyx bridge server and runs the code it sends.

## Serving

There are two ways to run it:

```python
from nyx_bridge.client import NyxClient

client = NyxClient(name="my-script")

client.serve_forever()                 # blocks; the calling thread executes the code
thread = client.start_in_thread()      # returns a daemon thread; code runs there
```

- `serve_forever()` suits batch applications with no event loop (`mayapy`, `blender
  --background`, a plain script). It returns when the client is stopped, the server
  asks it to shut down, or reconnecting is off and the connection ends.
- `start_in_thread()` serves from a background thread, for applications that keep
  running their own loop. Anything the application only allows from its main thread
  needs [`run_on_main_thread`](#subclassing-for-an-application).

`client.stop()` ends either, and is safe to call from any thread.

## Constructor

| Argument | Meaning |
| --- | --- |
| `name` | Name the server shows. `<kind>-<pid>` when omitted. |
| `address` | `"host:port"` or `(host, port)`. See [Finding the server](addresses.md). |
| `token` | Server token. From the environment or the address file when omitted. |
| `reconnect` | Connect again when the connection drops (default `True`). |
| `reconnect_delay` | Seconds between connection attempts (default `1.0`). |

`serve_forever(connect_timeout=None)` keeps trying to reach the server until it works.
Give a number of seconds to give up with `ConnectionError` instead. A server that
**refuses** the client (wrong token, wrong protocol version) raises
`protocol.HandshakeError` straight away, since retrying would not help.

## What a script sees

Code runs in a namespace that lives as long as the connection, so one execution sees the
variables of the previous one. The server picks the mode:

- **exec** (default): the value of the request is whatever the code assigned to
  `result`. `result` is cleared before each run, so it never carries over.
- **eval**: the value is that of a single expression.

Output printed to stdout and stderr is captured and sent back. Names passed as `args`
are set in the namespace first. A script that calls `sys.exit()` does not stop the
client; it is reported as an error of that execution.

Values travel as JSON. Lists, dicts, strings, numbers, booleans and `None` arrive as
they are, tuples and sets become lists, and anything else (an object of the
application) becomes its `repr()`.

```python
# what the server sends...
session.execute("import math\nresult = math.sqrt(x)", args={"x": 16})   # value 4.0
session.execute("math.sqrt(2)", mode="eval")                              # math is still imported
```

Executions on one client run **one at a time**, in the order received, even when the
server sent several at once.

## Errors

`execute` never raises on the client side. An exception in the code is sent back with its
type, message and traceback, and the server re-raises it as
[`RemoteExecutionError`](server.md#errors). The client keeps working afterwards.

## Subclassing for an application

Subclass `NyxClient` for an application and override what differs:

```python
import bpy

from nyx_bridge.client import NyxClient


class BlenderClient(NyxClient):
    CLIENT_KIND = "blender"

    def client_version(self) -> str:
        return bpy.app.version_string

    def default_namespace(self) -> dict:
        return {"bpy": bpy}

    def run_on_main_thread(self, func):
        ...  # queue func for the main thread, wait for and return its result
```

| Member | Purpose |
| --- | --- |
| `CLIENT_KIND` | Label the server can filter on (`"blender"`, `"maya"`). Default `"python"`. |
| `client_version()` | Version string of the application, sent in the hello. |
| `default_namespace()` | Names every fresh namespace starts with. |
| `run_on_main_thread(func)` | Run `func` where the application allows its API to be used and return the result. Default runs it on the serving thread. |
| `on_connected()` / `on_disconnected()` | Called after the handshake and after the connection ended. |

`run_on_main_thread` receives every execution when serving from `start_in_thread`. A
typical implementation puts `func` on a queue that a timer on the main thread drains, and
waits for the result. See [nyx-bridge-blender](https://github.com/sowwic/nyx-bridge-blender)
and [nyx-bridge-maya](https://github.com/sowwic/nyx-bridge-maya) for working versions.

## Logging

The client logs to `nyx_bridge.client`: connections at `INFO`, failed attempts at
`DEBUG`.
