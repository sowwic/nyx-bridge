# Testing

```bash
make pytest                                              # everything, with coverage
uv run pytest tests/test_bridge.py::test_ping            # one test
make pytest-pdb                                          # verbose, pdb on failure
```

Coverage (`--cov=nyx_bridge`) is on by default. `make clean` and `make pytest-pdb` remove
`test_output/`.

## Layout

| File | Covers |
| --- | --- |
| `tests/test_protocol.py` | Framing, message builders, `to_jsonable`, address parsing and files, the lookup order of `find_server_address`. Plain functions and socket pairs, no threads. |
| `tests/test_bridge.py` | A real server and real clients over loopback: connecting, executing, errors, reconnecting, handshake refusals, listeners, ports, address files. |

## Fixtures (`tests/test_bridge.py`)

- `server`: a `BridgeServer(port=0)` that is stopped afterwards. Port 0 means the system
  picks a free one, so tests can run in parallel with a running Nyx.
- `make_client(**kwargs)`: starts a `NyxClient` thread pointed at the server, with a short
  `reconnect_delay`, and stops and joins it at the end.
- `session`: a client already connected, as the server sees it.
- `wait_until(predicate)`: polls for a condition. Use it instead of `sleep`, since
  connections happen on other threads.

## What to cover

- A change to a message or the framing: a test in `test_protocol.py`, **and** a bridge
  test that crosses a real socket.
- A new failure mode of the connection (refused, closed, timed out): both ends, and that
  the session or client is usable afterwards.
- Anything with threads: avoid fixed sleeps, use `wait_until` or events, and keep the
  timeouts generous (`WAIT = 10`). A flaky test here is almost always a missing wait.
- Python 3.11 compatibility: the project supports 3.11, which `.python-version` pins for
  local development. CI uses the default matrix of the shared workflow (3.12 to 3.14),
  so run `make check` locally before relying on anything newer.
