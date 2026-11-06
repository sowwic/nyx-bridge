import contextlib
import socket
import threading
import time

import pytest

from nyx_bridge import protocol
from nyx_bridge.client import NyxClient
from nyx_bridge.server import (
    BridgeServer,
    ClientDisconnected,
    RemoteExecutionError,
)

WAIT = 10


def wait_until(predicate, timeout=WAIT):
    """Poll until the predicate holds, returns whether it did."""
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.01)
    return predicate()


@pytest.fixture
def server():
    with BridgeServer(name="test", port=0) as running_server:
        yield running_server


@pytest.fixture
def make_client(server):
    """Create clients pointed at the server, stopped when the test ends."""
    started: list[tuple[NyxClient, threading.Thread]] = []

    def _make_client(target=None, **kwargs) -> NyxClient:
        target = target or server
        kwargs.setdefault("reconnect_delay", 0.05)
        client = NyxClient(
            address=("127.0.0.1", target.port), token=target.token, **kwargs
        )
        started.append((client, client.start_in_thread()))
        return client

    yield _make_client
    for client, thread in started:
        client.stop()
        thread.join(WAIT)


@pytest.fixture
def session(server, make_client):
    make_client(name="blender-test")
    return server.wait_for_client(name="blender-test", timeout=WAIT)


def test_client_connects_and_is_registered(server, make_client):
    class BlenderClient(NyxClient):
        CLIENT_KIND = "blender"

        def client_version(self):
            return "5.2.3"

    client = BlenderClient(
        name="b1", address=("127.0.0.1", server.port), token=server.token
    )
    thread = client.start_in_thread()
    try:
        session = server.wait_for_client(kind="blender", timeout=WAIT)

        assert (session.name, session.kind, session.version) == (
            "b1",
            "blender",
            "5.2.3",
        )
        assert session.is_connected
        assert server.find_clients(client_id=session.id) == [session]
        assert wait_until(lambda: client.client_id == session.id)
        assert client.server_name == "test"
    finally:
        client.stop()
        thread.join(WAIT)


def test_exec_returns_the_result_variable_and_output(session):
    result = session.execute("print('hi')\nresult = 6 * 7", timeout=WAIT)

    assert result.value == 42
    assert result.stdout == "hi\n"
    assert result.stderr == ""


def test_eval_returns_the_expression(session):
    assert session.execute("1 + 2", mode=protocol.EVAL, timeout=WAIT).value == 3


def test_args_are_set_in_the_namespace(session):
    result = session.execute("result = a + b", args={"a": 1, "b": 2}, timeout=WAIT)

    assert result.value == 3


def test_namespace_persists_between_executions(session):
    session.execute("counter = 1", timeout=WAIT)
    session.execute("counter += 1", timeout=WAIT)

    assert session.execute("counter", mode=protocol.EVAL, timeout=WAIT).value == 2


def test_result_does_not_leak_into_the_next_execution(session):
    session.execute("result = 1", timeout=WAIT)

    assert session.execute("pass", timeout=WAIT).value is None


def test_subclass_namespace_is_available(server):
    class ToolClient(NyxClient):
        def default_namespace(self):
            return {"answer": 42}

    client = ToolClient(
        name="tool", address=("127.0.0.1", server.port), token=server.token
    )
    thread = client.start_in_thread()
    try:
        session = server.wait_for_client(name="tool", timeout=WAIT)

        assert session.execute("answer", mode=protocol.EVAL, timeout=WAIT).value == 42
    finally:
        client.stop()
        thread.join(WAIT)


def test_values_json_cannot_carry_come_back_as_repr(session):
    value = session.execute("result = object", timeout=WAIT).value

    assert value == "<class 'object'>"


def test_error_in_code_raises_with_remote_traceback_and_output(session):
    with pytest.raises(RemoteExecutionError) as error_info:
        session.execute("print('before')\nraise ValueError('boom')", timeout=WAIT)

    error = error_info.value
    assert (error.type_name, error.message) == ("ValueError", "boom")
    assert "ValueError: boom" in error.remote_traceback
    assert "<nyx>" in error.remote_traceback
    assert "client.py" not in error.remote_traceback
    assert error.stdout == "before\n"
    assert str(error) == "ValueError: boom"


def test_session_keeps_working_after_an_error(session):
    with pytest.raises(RemoteExecutionError):
        session.execute("1/0", timeout=WAIT)
    with pytest.raises(RemoteExecutionError, match="SyntaxError"):
        session.execute("def (", timeout=WAIT)

    assert session.execute("1", mode=protocol.EVAL, timeout=WAIT).value == 1


def test_script_calling_sys_exit_does_not_stop_the_client(session):
    with pytest.raises(RemoteExecutionError, match="SystemExit"):
        session.execute("import sys; sys.exit(3)", timeout=WAIT)

    assert session.is_connected
    assert session.execute("1", mode=protocol.EVAL, timeout=WAIT).value == 1


def test_unknown_mode_is_an_error(session):
    with pytest.raises(RemoteExecutionError, match="Unknown execution mode"):
        session.execute("1", mode="nope", timeout=WAIT)


def test_args_that_are_not_json_are_refused_before_sending(session):
    with pytest.raises(TypeError):
        session.execute("pass", args={"a": object()}, timeout=WAIT)

    assert session.execute("1", mode=protocol.EVAL, timeout=WAIT).value == 1


def test_execution_times_out_and_the_late_answer_is_ignored(session):
    with pytest.raises(TimeoutError):
        session.execute("import time; time.sleep(0.5)", timeout=0.1)

    # The client answers the next request once it is done with the slow one.
    assert session.execute("2", mode=protocol.EVAL, timeout=WAIT).value == 2


def test_concurrent_requests_get_their_own_answers(session):
    results = {}

    def run(index):
        results[index] = session.execute(f"result = {index}", timeout=WAIT).value

    threads = [threading.Thread(target=run, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(WAIT)

    assert results == {i: i for i in range(8)}


def test_large_values_round_trip(session):
    size = 3 * 1024 * 1024

    result = session.execute("result = 'x' * n", args={"n": size}, timeout=WAIT)

    assert len(result.value) == size


def test_ping(session):
    session.ping(timeout=WAIT)


def test_closing_the_session_fails_requests_in_flight(session):
    errors = []

    def run():
        try:
            session.execute("import time; time.sleep(2)", timeout=WAIT)
        except Exception as err:
            errors.append(err)

    thread = threading.Thread(target=run)
    thread.start()
    time.sleep(0.2)
    session.close()
    thread.join(WAIT)

    assert len(errors) == 1
    assert isinstance(errors[0], ClientDisconnected)
    with pytest.raises(ClientDisconnected):
        session.execute("1", timeout=1)


def test_client_leaving_removes_it_from_the_server(server, make_client):
    client = make_client(name="short-lived", reconnect=False)
    session = server.wait_for_client(name="short-lived", timeout=WAIT)

    client.stop()

    assert wait_until(lambda: not server.find_clients(name="short-lived"))
    assert not session.is_connected


def test_shutdown_request_ends_the_client_without_reconnecting(server):
    client = NyxClient(
        name="quitter",
        address=("127.0.0.1", server.port),
        token=server.token,
        reconnect_delay=0.05,
    )
    thread = client.start_in_thread()
    session = server.wait_for_client(name="quitter", timeout=WAIT)

    session.request_shutdown()

    thread.join(WAIT)
    assert not thread.is_alive()
    assert wait_until(lambda: not server.find_clients(name="quitter"))


def test_blocking_serve_forever_runs_code_on_the_calling_thread(server):
    seen = []

    class ThreadClient(NyxClient):
        def default_namespace(self):
            return {"record": lambda: seen.append(threading.current_thread())}

    client = ThreadClient(
        name="batch", address=("127.0.0.1", server.port), token=server.token
    )

    def drive():
        session = server.wait_for_client(name="batch", timeout=WAIT)
        session.execute("record()", timeout=WAIT)
        session.request_shutdown()

    driver = threading.Thread(target=drive)
    driver.start()
    client.serve_forever(connect_timeout=WAIT)
    driver.join(WAIT)

    assert seen == [threading.current_thread()]


def test_run_on_main_thread_is_used_for_executions(server):
    calls = []

    class QueueClient(NyxClient):
        def run_on_main_thread(self, func):
            calls.append(func)
            return func()

    client = QueueClient(
        name="queue", address=("127.0.0.1", server.port), token=server.token
    )
    thread = client.start_in_thread()
    try:
        session = server.wait_for_client(name="queue", timeout=WAIT)
        session.execute("1", timeout=WAIT)

        assert len(calls) == 1
    finally:
        client.stop()
        thread.join(WAIT)


def test_client_reconnects_to_a_restarted_server():
    first = BridgeServer(name="first", port=0, token="shared")
    first.start()
    port = first.port
    client = NyxClient(
        name="persistent",
        address=("127.0.0.1", port),
        token="shared",
        reconnect_delay=0.05,
    )
    thread = client.start_in_thread()
    second = BridgeServer(name="second", port=port, token="shared")
    try:
        first.wait_for_client(name="persistent", timeout=WAIT)
        first.stop()
        second.start()

        session = second.wait_for_client(name="persistent", timeout=WAIT)

        assert session.execute("1", mode=protocol.EVAL, timeout=WAIT).value == 1
    finally:
        client.stop()
        thread.join(WAIT)
        first.stop()
        second.stop()


def test_client_waits_for_a_server_that_starts_later():
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    late = BridgeServer(name="late", port=port, token="t")
    client = NyxClient(
        name="early", address=("127.0.0.1", port), token="t", reconnect_delay=0.05
    )
    thread = client.start_in_thread()
    try:
        time.sleep(0.2)
        late.start()

        late.wait_for_client(name="early", timeout=WAIT)
    finally:
        client.stop()
        thread.join(WAIT)
        late.stop()


def test_connect_timeout_gives_up():
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    client = NyxClient(address=("127.0.0.1", port), token="t", reconnect_delay=0.05)

    with pytest.raises(ConnectionError, match="Could not connect"):
        client.serve_forever(connect_timeout=0.3)


def test_missing_address_is_an_error(monkeypatch):
    for name in (
        protocol.ADDRESS_ENV_VAR,
        protocol.TOKEN_ENV_VAR,
        protocol.ADDRESS_FILE_ENV_VAR,
    ):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(ConnectionError, match=protocol.ADDRESS_ENV_VAR):
        NyxClient().connect()


def test_wrong_token_is_refused(server):
    client = NyxClient(address=("127.0.0.1", server.port), token="wrong")

    with pytest.raises(protocol.HandshakeError, match="Invalid token"):
        client.connect()
    assert server.clients == []
    assert not client.is_connected


def test_serve_forever_does_not_retry_a_refused_handshake(server):
    client = NyxClient(
        address=("127.0.0.1", server.port), token="wrong", reconnect_delay=0.05
    )

    with pytest.raises(protocol.HandshakeError):
        client.serve_forever(connect_timeout=WAIT)


def test_other_protocol_version_is_refused(server):
    with socket.create_connection(("127.0.0.1", server.port), timeout=WAIT) as sock:
        hello = protocol.make_hello(server.token, "old", "python", "")
        hello["protocol_version"] = 999
        protocol.send_message(sock, hello)

        reply = protocol.recv_message(sock)

    assert reply["type"] == protocol.MessageType.ERROR
    assert "protocol version" in reply["message"]
    assert server.clients == []


def test_first_message_has_to_be_a_hello(server):
    with socket.create_connection(("127.0.0.1", server.port), timeout=WAIT) as sock:
        protocol.send_message(sock, protocol.make_ping("1"))

        reply = protocol.recv_message(sock)

    assert reply["type"] == protocol.MessageType.ERROR
    assert server.clients == []


def test_garbage_does_not_take_the_server_down(server, make_client):
    with socket.create_connection(("127.0.0.1", server.port), timeout=WAIT) as sock:
        sock.sendall(b"\xff\xff\xff\xff garbage")
        sock.settimeout(WAIT)
        with contextlib.suppress(OSError):  # the server may reset the connection
            sock.recv(16)

    make_client(name="after-garbage")

    server.wait_for_client(name="after-garbage", timeout=WAIT)


def test_silent_connection_times_out_during_handshake():
    class QuickServer(BridgeServer):
        HANDSHAKE_TIMEOUT = 0.2

    with QuickServer(port=0) as quick:
        with socket.create_connection(("127.0.0.1", quick.port), timeout=WAIT) as sock:
            assert sock.recv(16) == b""


def test_validate_client_hook_can_refuse_clients():
    class PickyServer(BridgeServer):
        def validate_client(self, hello):
            return None if hello["client_kind"] == "maya" else "Maya only"

    with PickyServer(port=0) as picky:
        refused = NyxClient(address=("127.0.0.1", picky.port), token=picky.token)
        with pytest.raises(protocol.HandshakeError, match="Maya only"):
            refused.connect()

        class MayaClient(NyxClient):
            CLIENT_KIND = "maya"

        accepted = MayaClient(address=("127.0.0.1", picky.port), token=picky.token)
        accepted.connect()
        try:
            assert picky.wait_for_client(kind="maya", timeout=WAIT)
        finally:
            accepted.stop()


class Recorder:
    def __init__(self):
        self.events = []

    def on_server_started(self, server):
        self.events.append(("started", server.port))

    def on_server_stopped(self, server):
        self.events.append(("stopped",))

    def on_client_connected(self, server, session):
        self.events.append(("connected", session.name))

    def on_client_disconnected(self, server, session):
        self.events.append(("disconnected", session.name))


def connect_and_close(server, name):
    client = NyxClient(name=name, address=("127.0.0.1", server.port), token=server.token)
    client.connect()
    server.wait_for_client(name=name, timeout=WAIT).close()
    client.stop()


def test_listeners_hear_about_the_server_and_its_clients_in_order():
    recorder = Recorder()
    listening = BridgeServer(port=0)
    listening.add_listener(recorder)
    listening.start()
    port = listening.port
    connect_and_close(listening, "h")
    wait_until(lambda: len(recorder.events) >= 3)
    listening.stop()

    assert recorder.events == [
        ("started", port),
        ("connected", "h"),
        ("disconnected", "h"),
        ("stopped",),
    ]


def test_removed_and_repeated_listeners_are_handled(server):
    recorder = Recorder()
    server.add_listener(recorder)
    server.add_listener(recorder)
    server.remove_listener(recorder)
    server.remove_listener(recorder)

    connect_and_close(server, "quiet")

    assert recorder.events == []


def test_listener_without_methods_or_failing_does_not_break_the_server():
    recorder = Recorder()

    class Failing:
        def on_client_connected(self, server, session):
            raise RuntimeError("listener failure must not break the connection")

    with BridgeServer(port=0) as listening:
        listening.add_listener(object())
        listening.add_listener(Failing())
        listening.add_listener(recorder)
        connect_and_close(listening, "h")
        wait_until(lambda: len(recorder.events) >= 2)

    assert [event[0] for event in recorder.events] == [
        "connected",
        "disconnected",
        "stopped",
    ]


def test_wait_for_client_times_out(server):
    with pytest.raises(TimeoutError):
        server.wait_for_client(name="nobody", timeout=0.1)


def test_wait_for_client_fails_when_the_server_stops():
    waiting = BridgeServer(port=0)
    waiting.start()
    errors = []

    def wait():
        try:
            waiting.wait_for_client(timeout=WAIT)
        except Exception as err:
            errors.append(err)

    thread = threading.Thread(target=wait)
    thread.start()
    time.sleep(0.1)
    waiting.stop()
    thread.join(WAIT)

    assert [type(err) for err in errors] == [RuntimeError]


def test_stopping_the_server_disconnects_clients(server, make_client):
    make_client(name="victim", reconnect=False)
    session = server.wait_for_client(name="victim", timeout=WAIT)

    server.stop()

    assert not session.is_connected
    assert not server.is_running
    server.stop()  # idempotent


def test_server_can_be_started_again_after_stop():
    again = BridgeServer(port=0)
    again.start()
    again.stop()

    again.start()
    try:
        assert again.is_running
        NyxClient(address=("127.0.0.1", again.port), token=again.token).connect()
        assert again.wait_for_client(timeout=WAIT)
    finally:
        again.stop()


def test_default_port_falls_back_to_the_next_free_one():
    blocker = socket.socket()
    blocker.bind(("127.0.0.1", 0))
    blocker.listen()
    taken = blocker.getsockname()[1]

    class FallbackServer(BridgeServer):
        DEFAULT_PORT = taken

    try:
        with FallbackServer() as fallback:
            assert taken < fallback.port < taken + FallbackServer.PORT_FALLBACK_COUNT
    finally:
        blocker.close()


def test_explicit_port_in_use_is_an_error():
    blocker = socket.socket()
    blocker.bind(("127.0.0.1", 0))
    blocker.listen()

    try:
        with pytest.raises(OSError, match="Could not bind"):
            BridgeServer(port=blocker.getsockname()[1]).start()
    finally:
        blocker.close()


def test_server_token_is_random_by_default():
    assert BridgeServer(port=0).token != BridgeServer(port=0).token


def test_address_file_lets_a_client_find_the_server(server, tmp_path, monkeypatch):
    address_file = tmp_path / "bridge.json"
    server.write_address_file(address_file)
    monkeypatch.delenv(protocol.ADDRESS_ENV_VAR, raising=False)
    monkeypatch.setenv(protocol.ADDRESS_FILE_ENV_VAR, str(address_file))

    client = NyxClient(name="from-file")
    client.connect()
    try:
        assert server.wait_for_client(name="from-file", timeout=WAIT)
    finally:
        client.stop()


def test_address_file_is_written_on_start_and_removed_on_stop(tmp_path):
    address_file = tmp_path / "nested" / "bridge.json"

    with BridgeServer(name="test", port=0, address_file=address_file) as running:
        found = protocol.read_address_file(address_file)
        assert (found.host, found.port, found.token) == (
            running.host,
            running.port,
            running.token,
        )

    assert not address_file.exists()


def test_stopping_a_server_leaves_an_address_file_that_another_took_over(tmp_path):
    address_file = tmp_path / "bridge.json"
    first = BridgeServer(name="first", port=0, address_file=address_file)
    first.start()
    try:
        protocol.write_address_file(address_file, "127.0.0.1", 1, "other-token")
    finally:
        first.stop()

    assert protocol.read_address_file(address_file).token == "other-token"


def test_unwritable_address_file_does_not_stop_the_server(tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("not a folder")

    with BridgeServer(name="test", port=0, address_file=blocker / "bridge.json") as s:
        assert s.is_running
