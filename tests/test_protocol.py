import ast
import pathlib
import socket
import struct
import subprocess
import sys
import threading
import time

import pytest

import nyx_bridge
from nyx_bridge import protocol

BRIDGE_DIR = pathlib.Path(nyx_bridge.__file__).parent
SOURCE_FILES = sorted(BRIDGE_DIR.glob("*.py"))


@pytest.fixture
def socket_pair():
    left, right = socket.socketpair()
    yield left, right
    left.close()
    right.close()


def test_message_round_trip(socket_pair):
    left, right = socket_pair
    message = protocol.make_execute("1", "x = 1", args={"a": [1, 2], "b": "ü"})

    protocol.send_message(left, message)

    assert protocol.recv_message(right) == message


def test_message_split_over_many_reads_is_reassembled(socket_pair):
    left, right = socket_pair
    frame = protocol.encode_message(protocol.make_ping("p1"))

    def send_in_pieces():
        for index in range(len(frame)):
            left.sendall(frame[index : index + 1])
            time.sleep(0.001)

    thread = threading.Thread(target=send_in_pieces)
    thread.start()
    try:
        assert protocol.recv_message(right)["id"] == "p1"
    finally:
        thread.join()


def test_two_messages_in_one_chunk_are_read_separately(socket_pair):
    left, right = socket_pair
    left.sendall(
        protocol.encode_message(protocol.make_ping("1"))
        + protocol.encode_message(protocol.make_ping("2"))
    )

    assert protocol.recv_message(right)["id"] == "1"
    assert protocol.recv_message(right)["id"] == "2"


def test_closed_connection_raises_connection_closed(socket_pair):
    left, right = socket_pair
    left.close()

    with pytest.raises(protocol.ConnectionClosed):
        protocol.recv_message(right)


def test_connection_closed_in_the_middle_of_a_frame_raises(socket_pair):
    left, right = socket_pair
    left.sendall(protocol.encode_message(protocol.make_ping("1"))[:-2])
    left.close()

    with pytest.raises(protocol.ConnectionClosed):
        protocol.recv_message(right)


def test_oversized_frame_is_rejected(socket_pair):
    left, right = socket_pair
    left.sendall(struct.pack(">I", protocol.MAX_MESSAGE_SIZE + 1))

    with pytest.raises(protocol.ProtocolError, match="too large"):
        protocol.recv_message(right)


@pytest.mark.parametrize(
    "payload", [b"not json", b"[1, 2]", b'{"no": "type"}', b'{"type": 5}']
)
def test_invalid_payload_is_rejected(socket_pair, payload):
    left, right = socket_pair
    left.sendall(struct.pack(">I", len(payload)) + payload)

    with pytest.raises(protocol.ProtocolError):
        protocol.recv_message(right)


def test_encode_rejects_values_json_cannot_carry():
    with pytest.raises(TypeError):
        protocol.encode_message(protocol.make_execute("1", "x", args={"a": object()}))


def test_to_jsonable_keeps_json_data_and_reprs_the_rest():
    class Thing:
        def __repr__(self):
            return "<Thing>"

    value = {"a": (1, 2), "b": {3}, 4: Thing(), "c": None}

    assert protocol.to_jsonable(value) == {
        "a": [1, 2],
        "b": [3],
        "4": "<Thing>",
        "c": None,
    }


def test_to_jsonable_survives_self_referencing_containers():
    loop = []
    loop.append(loop)

    protocol.encode_message({"type": "x", "value": protocol.to_jsonable(loop)})


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("10.0.0.2:5000", ("10.0.0.2", 5000)),
        ("localhost:1", ("localhost", 1)),
        ("4000", (protocol.DEFAULT_HOST, 4000)),
        (":4000", (protocol.DEFAULT_HOST, 4000)),
    ],
)
def test_parse_address(text, expected):
    assert protocol.parse_address(text) == expected


def test_find_server_address_prefers_arguments_then_environment(monkeypatch, tmp_path):
    for name in (
        protocol.ADDRESS_ENV_VAR,
        protocol.TOKEN_ENV_VAR,
        protocol.ADDRESS_FILE_ENV_VAR,
    ):
        monkeypatch.delenv(name, raising=False)
    assert protocol.find_server_address() is None

    address_file = tmp_path / "address.json"
    protocol.write_address_file(address_file, "127.0.0.1", 5001, "file-token")
    monkeypatch.setenv(protocol.ADDRESS_FILE_ENV_VAR, str(address_file))
    assert protocol.find_server_address() == ("127.0.0.1", 5001, "file-token")
    assert protocol.find_server_address(token="other") == ("127.0.0.1", 5001, "other")

    monkeypatch.setenv(protocol.ADDRESS_ENV_VAR, "127.0.0.1:5002")
    monkeypatch.setenv(protocol.TOKEN_ENV_VAR, "env-token")
    assert protocol.find_server_address() == ("127.0.0.1", 5002, "env-token")

    assert protocol.find_server_address(("10.0.0.1", 5003), "arg") == (
        "10.0.0.1",
        5003,
        "arg",
    )


@pytest.mark.parametrize("path", SOURCE_FILES, ids=lambda path: path.name)
def test_source_files_parse_as_python_3_11(path):
    """The client side is installed into applications running Maya's Python (3.11)."""
    ast.parse(path.read_text(), feature_version=(3, 11))


def test_package_imports_without_pyside_or_nyx():
    code = (
        "import sys\n"
        "sys.modules['PySide6'] = None\n"
        "sys.modules['shiboken6'] = None\n"
        f"sys.path.insert(0, {str(BRIDGE_DIR.parent.parent)!r})\n"
        "import nyx_bridge.protocol, nyx_bridge.client, nyx_bridge.manager\n"
        "loaded = [m for m in sys.modules if m == 'nyx' or m.startswith('nyx.')]\n"
        "assert not loaded, loaded\n"
        "assert 'PySide6' not in [m for m in sys.modules if sys.modules[m]], 'qt'\n"
    )

    result = subprocess.run(
        [sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=30
    )

    assert result.returncode == 0, result.stderr
