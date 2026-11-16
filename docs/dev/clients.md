# Writing a client

A client for a new application is a small subclass of `NyxClient`. Existing ones to read:
[nyx-bridge-blender](https://github.com/sowwic/nyx-bridge-blender) and
[nyx-bridge-maya](https://github.com/sowwic/nyx-bridge-maya).

```python
from nyx_bridge.client import NyxClient


class HoudiniClient(NyxClient):
    CLIENT_KIND = "houdini"

    def client_version(self) -> str:
        import hou
        return hou.applicationVersionString()

    def default_namespace(self) -> dict:
        import hou
        return {"hou": hou}

    def run_on_main_thread(self, func):
        ...
```

## Checklist

1. **`CLIENT_KIND`**: unique and lowercase; servers filter with it (`find_clients(kind=)`).
2. **`default_namespace`**: the modules scripts will expect, imported lazily inside the
   method so importing your package works outside the application.
3. **`run_on_main_thread`**: if the application can't be used from other threads, put the
   function on a queue drained by the application's own timer or idle callback, and block
   until it ran. Return its value and **re-raise** what it raised only if you need to;
   `execute` already catches exceptions and builds the error message, so the function
   passed in never raises for user code.
4. **Where to start it**: `start_in_thread()` from the application's startup (plug-in
   load, add-on `register`), `stop()` from its shutdown. In a headless or batch mode with
   no event loop, call `serve_forever()` and skip `run_on_main_thread`.
5. **Dependencies**: install `sowwic-nyx-bridge` with the application's own Python; keep it the
   only requirement.

## Things to watch

- **Never block the main thread waiting for the server.** `start_in_thread` keeps the
  connecting and retrying off it.
- **A request can run for a long time.** The client answers nothing else meanwhile (see
  [Architecture](architecture.md#client-threads)), so avoid long executions on a client
  that has to stay responsive.
- **Cleanup.** `on_disconnected` is the place to drop references to application objects
  created by scripts. The namespace is replaced on reconnect anyway.
- **Thread safety of captured output.** `redirect_stdout` is process wide.
