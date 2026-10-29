# nyx-bridge

TCP bridge between [Nyx](https://github.com/sowwic/nyx) and external processes
(Blender, Maya, any Python).

Nyx runs a `BridgeServer`. The external process runs a `NyxClient`, connects out,
and executes the code the server sends. Standard library only; the client side
supports Python 3.9+, so it can be installed into a DCC's Python
(e.g. `pip install --target <modules dir>`).

```python
from nyx_bridge.client import NyxClient

NyxClient().serve_forever()  # address from NYX_BRIDGE_ADDRESS / NYX_BRIDGE_TOKEN
```
