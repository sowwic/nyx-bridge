# Developer Guide

This guide is for people who work on nyx-bridge itself, or write a client for a new
application.

## Setup

```bash
uv sync
make check        # ruff + pytest
make mkdocs       # serve these docs
```

`make help` lists every target. Ruff uses a line length of 89 and the `E`, `F` and `UP`
rules with `target-version = "py311"`. Docstrings are Google style: the API reference is
generated from them.

!!! important
    The package must stay **standard library only** and work on **Python 3.11**. Clients
    are installed into the Python of other applications, where nothing else can be
    assumed. Don't import a third-party package, and don't use syntax newer than 3.11.

## Pages

| Page | What you learn |
| --- | --- |
| [Architecture](architecture.md) | The three modules, the threads on each side, and why the client connects out. |
| [Wire protocol](protocol.md) | Framing, message types, the handshake and the rules both sides follow. |
| [Writing a client](clients.md) | What to implement to bring a new application to Nyx. |
| [Testing](testing.md) | The fixtures and what to cover. |
| [Releasing](releasing.md) | Versions, the changelog, `make release` and publishing to PyPI. |
| API reference | Generated from the docstrings (see the navigation). |
