# nyx-bridge

![Tests](https://github.com/sowwic/nyx-bridge/actions/workflows/tests.yml/badge.svg)
![Lint](https://github.com/sowwic/nyx-bridge/actions/workflows/lint.yml/badge.svg)

TCP bridge between [Nyx](https://github.com/sowwic/nyx) and external processes
(Blender, Maya, any Python).

Nyx runs a `BridgeServer`. The external process runs a `NyxClient`, connects out,
and executes the code the server sends. Standard library only; the client side
supports Python 3.11+ (Maya 2027, Blender), so it can be installed into a DCC's Python
(e.g. `pip install --target <modules dir>`).

## Install

```bash
pip install sowwic-nyx-bridge
```

## Use

In the external process:

```python
from nyx_bridge.client import NyxClient

NyxClient().serve_forever()  # address from NYX_BRIDGE_ADDRESS / NYX_BRIDGE_TOKEN
```

On the Nyx side:

```python
from nyx_bridge.server import BridgeServer

with BridgeServer() as server:
    session = server.wait_for_client(timeout=30)
    print(session.execute("result = 6 * 7").value)   # 42
```

On the same machine the client finds a running Nyx through its address file, so no
configuration is needed. See the [documentation](https://sowwic.github.io/nyx-bridge/) for the
client and server in detail, the wire protocol and how to write a client for a new
application.

## Development

```bash
uv sync
make check       # ruff + pytest
make mkdocs      # docs
make help        # all targets, including pypi-publish
```
