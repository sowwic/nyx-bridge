# nyx-bridge

TCP bridge between [Nyx](https://github.com/sowwic/nyx) and external processes such as
Blender, Maya or any Python interpreter.

Nyx runs a `BridgeServer`. The external process runs a `NyxClient`, **connects out** to
the server and executes the code the server sends. The side that runs code never
accepts connections. The package uses the standard library only and supports Python
3.11+, so it can be installed into the Python of a DCC.

```python
# in Blender, Maya, mayapy, a script ...
from nyx_bridge.client import NyxClient

NyxClient().serve_forever()  # address from NYX_BRIDGE_ADDRESS / NYX_BRIDGE_TOKEN
```

```python
# in Nyx (or any Python)
from nyx_bridge.server import BridgeServer

with BridgeServer() as server:
    session = server.wait_for_client(timeout=30)
    print(session.execute("result = 6 * 7").value)   # 42
```

| If you want to... | Read |
| --- | --- |
| Connect an application to Nyx, or run code in one | [User Guide](user/index.md) |
| Work on nyx-bridge, or on a client for a new application | [Developer Guide](dev/index.md) |
| Look up a class or function | API reference (see the navigation) |

Ready-made clients: [nyx-bridge-maya](https://github.com/sowwic/nyx-bridge-maya) and
[nyx-bridge-blender](https://github.com/sowwic/nyx-bridge-blender).
