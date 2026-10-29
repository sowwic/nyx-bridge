"""Wire protocol shared by the Nyx bridge server and its clients.

Standard library only, for Python 3.11+: clients living in other
applications (DCCs) install this package, so it must have no dependencies.

Messages are JSON objects with a ``type`` key, sent over TCP with a 4-byte big-endian
length prefix.
"""

from __future__ import annotations

import collections
import json
import os
import socket
import struct
import typing

PROTOCOL_VERSION = 1
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 47770
MAX_MESSAGE_SIZE = 64 * 1024 * 1024

ADDRESS_ENV_VAR = "NYX_BRIDGE_ADDRESS"
TOKEN_ENV_VAR = "NYX_BRIDGE_TOKEN"
ADDRESS_FILE_ENV_VAR = "NYX_BRIDGE_ADDRESS_FILE"

EXEC = "exec"
EVAL = "eval"

_HEADER = struct.Struct(">I")
_MAX_JSON_DEPTH = 32

Address = collections.namedtuple("Address", ["host", "port", "token"])


class MessageType:
    """Values of the ``type`` key of messages."""

    HELLO = "hello"
    ACK = "ack"
    ERROR = "error"
    EXECUTE = "execute"
    RESULT = "result"
    PING = "ping"
    PONG = "pong"
    SHUTDOWN = "shutdown"


class ProtocolError(Exception):
    """Malformed message or a peer breaking the protocol."""


class ConnectionClosed(ProtocolError):
    """The peer closed the connection (or it broke) while reading."""


class HandshakeError(ProtocolError):
    """The server refused the hello message."""


# Framing
def encode_message(message: dict) -> bytes:
    """Serialize a message and prefix it with its length.

    Args:
        message (dict): JSON serializable message.

    Raises:
        TypeError: message can't be serialized to JSON.
        ProtocolError: message is larger than `MAX_MESSAGE_SIZE`.

    Returns:
        bytes: frame to send.
    """
    payload = json.dumps(message, separators=(",", ":")).encode("utf-8")
    if len(payload) > MAX_MESSAGE_SIZE:
        raise ProtocolError(f"Message too large: {len(payload)} bytes")
    return _HEADER.pack(len(payload)) + payload


def send_message(sock: socket.socket, message: dict) -> None:
    """Send a message. Callers sharing a socket between threads have to lock.

    Args:
        sock (socket.socket): connected socket.
        message (dict): JSON serializable message.
    """
    sock.sendall(encode_message(message))


def recv_message(sock: socket.socket) -> dict:
    """Block until a whole message is read.

    Args:
        sock (socket.socket): connected socket.

    Raises:
        ConnectionClosed: connection closed or broken.
        ProtocolError: invalid frame, size or payload.
        TimeoutError: socket timeout expired.

    Returns:
        dict: message.
    """
    (size,) = _HEADER.unpack(_recv_exact(sock, _HEADER.size))
    if size > MAX_MESSAGE_SIZE:
        raise ProtocolError(f"Message too large: {size} bytes")
    payload = _recv_exact(sock, size)
    try:
        message = json.loads(payload.decode("utf-8"))
    except ValueError as err:
        raise ProtocolError(f"Invalid message payload: {err}") from err
    if not isinstance(message, dict) or not isinstance(message.get("type"), str):
        raise ProtocolError("Message has to be an object with a string `type`")
    return message


def _recv_exact(sock: socket.socket, size: int) -> bytes:
    """Read exactly `size` bytes.

    Args:
        sock (socket.socket): connected socket.
        size (int): number of bytes.

    Raises:
        ConnectionClosed: connection closed before all bytes arrived.

    Returns:
        bytes: data read.
    """
    chunks = []
    remaining = size
    while remaining:
        try:
            chunk = sock.recv(min(remaining, 1 << 20))
        except TimeoutError:
            raise
        except OSError as err:
            raise ConnectionClosed(str(err)) from err
        if not chunk:
            raise ConnectionClosed("Connection closed by peer")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


# Messages
def make_hello(
    token: str, client_name: str, client_kind: str, client_version: str
) -> dict:
    """Build the message a client opens a connection with."""
    return {
        "type": MessageType.HELLO,
        "protocol_version": PROTOCOL_VERSION,
        "token": token,
        "client_name": client_name,
        "client_kind": client_kind,
        "client_version": client_version,
    }


def make_ack(client_id: str, server_name: str) -> dict:
    """Build the server's answer to an accepted hello."""
    return {
        "type": MessageType.ACK,
        "protocol_version": PROTOCOL_VERSION,
        "client_id": client_id,
        "server_name": server_name,
    }


def make_error(message: str, request_id: str | None = None) -> dict:
    """Build an error message, optionally answering a request."""
    error = {"type": MessageType.ERROR, "message": message}
    if request_id is not None:
        error["id"] = request_id
    return error


def make_execute(
    request_id: str, code: str, mode: str = EXEC, args: dict | None = None
) -> dict:
    """Build a request to run code in the client."""
    return {
        "type": MessageType.EXECUTE,
        "id": request_id,
        "code": code,
        "mode": mode,
        "args": args or {},
    }


def make_result(
    request_id: str | None,
    ok: bool,
    value: typing.Any = None,
    error: dict | None = None,
    stdout: str = "",
    stderr: str = "",
) -> dict:
    """Build the answer to an execute request.

    On failure `error` is `{"type", "message", "traceback"}`.
    """
    return {
        "type": MessageType.RESULT,
        "id": request_id,
        "ok": ok,
        "value": value,
        "error": error,
        "stdout": stdout,
        "stderr": stderr,
    }


def make_ping(request_id: str) -> dict:
    """Build a ping, answered by a pong with the same id."""
    return {"type": MessageType.PING, "id": request_id}


def make_pong(request_id: str | None) -> dict:
    """Build the answer to a ping."""
    return {"type": MessageType.PONG, "id": request_id}


def make_shutdown() -> dict:
    """Build the message asking a client to disconnect and stop serving."""
    return {"type": MessageType.SHUTDOWN}


# Values
def to_jsonable(value: typing.Any, _depth: int = 0) -> typing.Any:
    """Convert a value to something JSON can carry.

    Containers are converted item by item. Anything else that JSON can't carry, like
    an object of the application, becomes its `repr`.

    Args:
        value (Any): value to convert.

    Returns:
        Any: JSON serializable value.
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if _depth < _MAX_JSON_DEPTH:
        if isinstance(value, (list, tuple, set, frozenset)):
            return [to_jsonable(item, _depth + 1) for item in value]
        if isinstance(value, dict):
            return {
                str(key): to_jsonable(item, _depth + 1) for key, item in value.items()
            }
    try:
        return repr(value)
    except Exception:
        return f"<unrepresentable {type(value).__name__}>"


# Addresses
def parse_address(text: str) -> tuple[str, int]:
    """Split `host:port`.

    Args:
        text (str): address, a bare port means the default host.

    Raises:
        ValueError: text is not an address.

    Returns:
        tuple[str, int]: host and port.
    """
    host, separator, port = text.strip().rpartition(":")
    if not separator:
        host, port = DEFAULT_HOST, text.strip()
    return host or DEFAULT_HOST, int(port)


def write_address_file(
    path: str | os.PathLike, host: str, port: int, token: str
) -> None:
    """Write where a server listens, so clients on the machine can find it.

    The file holds the token, so it is made readable by the owner only where possible.

    Args:
        path (str | os.PathLike): file to write.
        host (str): server host.
        port (int): server port.
        token (str): server token.
    """
    with open(path, "w", encoding="utf-8") as address_file:
        json.dump({"host": host, "port": port, "token": token}, address_file)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def read_address_file(path: str | os.PathLike) -> Address:
    """Read a file written by `write_address_file`.

    Args:
        path (str | os.PathLike): address file.

    Returns:
        Address: host, port and token.
    """
    with open(path, encoding="utf-8") as address_file:
        data = json.load(address_file)
    return Address(data["host"], int(data["port"]), data.get("token", ""))


def find_server_address(
    address: str | tuple[str, int] | None = None, token: str | None = None
) -> Address | None:
    """Work out where the server is.

    Looked up in order: the arguments, `NYX_BRIDGE_ADDRESS` (`host:port`) with
    `NYX_BRIDGE_TOKEN`, then the file named by `NYX_BRIDGE_ADDRESS_FILE`.

    Args:
        address (str | tuple[str, int] | None): `host:port` or (host, port).
        token (str | None): token, taken from the environment if omitted.

    Returns:
        Address | None: None when nothing says where the server is.
    """
    if address is None:
        address = os.environ.get(ADDRESS_ENV_VAR)
    if address:
        if isinstance(address, str):
            host, port = parse_address(address)
        else:
            host, port = address
        if token is None:
            token = os.environ.get(TOKEN_ENV_VAR, "")
        return Address(host, int(port), token)

    address_file = os.environ.get(ADDRESS_FILE_ENV_VAR)
    if address_file and os.path.isfile(address_file):
        found = read_address_file(address_file)
        return found if token is None else found._replace(token=token)
    return None
