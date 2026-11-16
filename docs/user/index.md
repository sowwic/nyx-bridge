# User Guide

This guide is for people who connect an application to Nyx, or run code in other
processes from Python.

## Installation

```bash
pip install sowwic-nyx-bridge
```

Requires Python 3.11+, with no other dependencies. To put it in the Python of a DCC
that has no pip of its own, install into a folder on its path:

```bash
pip install sowwic-nyx-bridge --target <modules dir>
```

Until a release is on PyPI, use `pip install git+https://github.com/sowwic/nyx-bridge.git`.

## Pages

| Page | What you learn |
| --- | --- |
| [Running code in a process](client.md) | `NyxClient`: serving, what scripts see, results and errors, reconnecting, subclassing for an application. |
| [Sending code from a server](server.md) | `BridgeServer` and `ClientSession`: starting, finding clients, executing, timeouts, listeners. |
| [Finding the server](addresses.md) | How a client learns the address and token: arguments, environment, address file. |

## How it fits together

```text
   Nyx                                    Blender / Maya / Python
┌──────────────┐   client connects out   ┌──────────────┐
│ BridgeServer │ ◀────────────────────── │  NyxClient   │
│              │ ───── execute ────────▶ │  runs code   │
│ ClientSession│ ◀──── result ────────── │              │
└──────────────┘                         └──────────────┘
```

1. The server listens on a local port and holds a secret **token**.
2. A client connects and opens with a hello carrying the token.
3. The server sends `execute` requests; the client runs them and answers with the value
   and the captured output.
