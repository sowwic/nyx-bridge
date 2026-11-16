# Finding the server

A client needs a host, a port and the token. It looks for them in this order and uses
the first that answers:

1. The `address` (and `token`) given to `NyxClient`.
2. The environment: `NYX_BRIDGE_ADDRESS` (`host:port`, or a bare port for the local
   host) with `NYX_BRIDGE_TOKEN`.
3. The file named by `NYX_BRIDGE_ADDRESS_FILE`.
4. The default address file of a running Nyx.

If none of them says anything, `connect()` raises `ConnectionError`, and `serve_forever`
keeps retrying (until its `connect_timeout`, if you gave one).

A `token` passed as an argument replaces the one in an address file. `NYX_BRIDGE_TOKEN` is
only read together with an address from the arguments or `NYX_BRIDGE_ADDRESS`.

## The address file

The server writes a small JSON file when it starts, with its host, port and token, and
removes it when it stops. On the same machine the client then needs **no configuration**:

```json
{"host": "127.0.0.1", "port": 47770, "token": "..."}
```

| Platform | Default location |
| --- | --- |
| macOS | `~/Library/Application Support/nyx/bridge.json` |
| Windows | `%LOCALAPPDATA%\sowwic\nyx\bridge.json` |
| Linux | `$XDG_DATA_HOME/nyx/bridge.json`, or `~/.local/share/nyx/bridge.json` |

A server only writes the file when given `address_file=...` (Nyx does it for you).
`server.write_address_file(path)` writes one once, on demand. The file is readable by its
owner only where the platform allows it. When a server stops it removes the file only if
it still describes that server, so a newer server that took the file over keeps it.

From code, `protocol.find_server_address()`, `read_address_file()` and
`default_address_file_path()` do the same lookups.

## Several machines, containers

The server listens on `127.0.0.1` by default, so a remote client can't reach it. To allow
that, pass another `host`, set the token yourself, and put both in the client's
environment. Do this only on a network you trust: the token is the only protection, and
traffic is not encrypted.

```bash
export NYX_BRIDGE_ADDRESS=192.168.1.20:47770
export NYX_BRIDGE_TOKEN=...
```
