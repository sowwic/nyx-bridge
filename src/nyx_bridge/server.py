"""Server side of the Nyx bridge: accepts clients and sends them code to run.

Everything here runs on plain threads and standard library sockets, no Qt. The
crawler is synchronous, so a request just blocks its caller until the client answers.
Never touch stage objects from the threads of this module.
"""

from __future__ import annotations

import concurrent.futures
import dataclasses
import hmac
import itertools
import logging
import os
import pathlib
import secrets
import socket
import threading
import typing
import uuid

from . import protocol

LOGGER = logging.getLogger(__name__)


class ClientDisconnected(ConnectionError):
    """The client went away before it answered."""


class RemoteExecutionError(Exception):
    """Code run by a client raised an exception.

    Attributes:
        type_name: name of the exception class raised in the client
        message: message of the exception
        remote_traceback: traceback formatted by the client
        stdout: output of the code before it failed
        stderr: error output of the code before it failed
    """

    def __init__(self, error: dict, stdout: str = "", stderr: str = "") -> None:
        """Initialize RemoteExecutionError.

        Args:
            error (dict): `type`, `message` and `traceback` sent by the client.
            stdout (str): output of the code.
            stderr (str): error output of the code.
        """
        self.type_name = error.get("type", "Exception")
        self.message = error.get("message", "")
        self.remote_traceback = error.get("traceback", "")
        self.stdout = stdout
        self.stderr = stderr
        super().__init__(f"{self.type_name}: {self.message}")


@dataclasses.dataclass
class ExecutionResult:
    """What a client answered to an execution.

    Attributes:
        value: value of the code, JSON data (see `protocol.to_jsonable`)
        stdout: output written by the code
        stderr: error output written by the code
    """

    value: typing.Any = None
    stdout: str = ""
    stderr: str = ""


class ClientSession:
    """A connected client, with the requests waiting for its answers."""

    def __init__(
        self,
        sock: socket.socket,
        client_id: str,
        name: str,
        kind: str,
        version: str,
        address: tuple,
    ) -> None:
        """Initialize ClientSession.

        Args:
            sock (socket.socket): connection, already handshaken.
            client_id (str): id given to the client by the server.
            name (str): client name.
            kind (str): client kind, e.g. "blender".
            version (str): client application version.
            address (tuple): remote address of the connection.
        """
        self._sock = sock
        self._send_lock = threading.Lock()
        self._pending_lock = threading.Lock()
        self._pending: dict[str, concurrent.futures.Future] = {}
        self._request_ids = itertools.count(1)
        self._closed = threading.Event()
        self.id = client_id
        self.name = name
        self.kind = kind
        self.version = version
        self.address = address

    def __repr__(self) -> str:
        """str: client with its name and kind."""
        return f"ClientSession({self.name!r}, kind={self.kind!r}, id={self.id!r})"

    @property
    def is_connected(self) -> bool:
        """bool: the connection is open."""
        return not self._closed.is_set()

    def execute(
        self,
        code: str,
        args: dict | None = None,
        mode: str = protocol.EXEC,
        timeout: float | None = None,
    ) -> ExecutionResult:
        """Run code in the client and wait for the answer.

        Args:
            code (str): source to run.
            args (dict | None): names to set in the client's namespace first, has to
                be JSON serializable.
            mode (str): `protocol.EXEC`, the value is what the code assigned to
                `result`, or `protocol.EVAL`, the value of the expression.
            timeout (float | None): seconds to wait for the answer, forever if None.

        Raises:
            TypeError: args are not JSON serializable.
            ClientDisconnected: the client is gone or went away while waiting.
            TimeoutError: no answer within the timeout.
            RemoteExecutionError: the code raised in the client.

        Returns:
            ExecutionResult: value and output.
        """
        request_id = str(next(self._request_ids))
        frame = protocol.encode_message(
            protocol.make_execute(request_id, code, mode, args)
        )
        reply = self._request(request_id, frame, timeout)
        if not reply.get("ok"):
            raise RemoteExecutionError(
                reply.get("error") or {},
                reply.get("stdout", ""),
                reply.get("stderr", ""),
            )
        return ExecutionResult(
            reply.get("value"), reply.get("stdout", ""), reply.get("stderr", "")
        )

    def ping(self, timeout: float | None = 5.0) -> None:
        """Check the client answers.

        Args:
            timeout (float | None): seconds to wait for the pong.

        Raises:
            ClientDisconnected: the client is gone.
            TimeoutError: no answer within the timeout.
        """
        request_id = f"ping-{next(self._request_ids)}"
        self._request(
            request_id, protocol.encode_message(protocol.make_ping(request_id)), timeout
        )

    def request_shutdown(self) -> None:
        """Ask the client to disconnect and stop serving."""
        try:
            self._send(protocol.encode_message(protocol.make_shutdown()))
        except ClientDisconnected:
            pass

    def close(self) -> None:
        """Drop the connection. Pending requests fail with `ClientDisconnected`."""
        if self._closed.is_set():
            return
        self._closed.set()
        try:
            self._sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self._sock.close()
        with self._pending_lock:
            pending, self._pending = self._pending, {}
        for future in pending.values():
            if not future.done():
                future.set_exception(ClientDisconnected(f"{self.name} disconnected"))

    def run(self) -> None:
        """Read messages until the connection ends. Runs on the connection's thread."""
        try:
            while True:
                self._dispatch(protocol.recv_message(self._sock))
        except protocol.ConnectionClosed as err:
            LOGGER.info("%s disconnected: %s", self.name, err)
        except (protocol.ProtocolError, OSError) as err:
            LOGGER.warning("Dropping %s: %s", self.name, err)
        finally:
            self.close()

    def _request(self, request_id: str, frame: bytes, timeout: float | None) -> dict:
        """Send a frame and wait for the message answering `request_id`.

        Args:
            request_id (str): id the answer carries.
            frame (bytes): encoded request.
            timeout (float | None): seconds to wait.

        Returns:
            dict: the answer.
        """
        future: concurrent.futures.Future = concurrent.futures.Future()
        with self._pending_lock:
            if self._closed.is_set():
                raise ClientDisconnected(f"{self.name} is not connected")
            self._pending[request_id] = future
        try:
            self._send(frame)
            return future.result(timeout)
        except concurrent.futures.TimeoutError as err:
            raise TimeoutError(f"{self.name} did not answer within {timeout}s") from err
        finally:
            with self._pending_lock:
                self._pending.pop(request_id, None)

    def _send(self, frame: bytes) -> None:
        """Send an encoded frame.

        Raises:
            ClientDisconnected: the frame couldn't be sent.
        """
        try:
            with self._send_lock:
                self._sock.sendall(frame)
        except OSError as err:
            self.close()
            raise ClientDisconnected(f"{self.name} disconnected: {err}") from err

    def _dispatch(self, message: dict) -> None:
        """Hand an incoming message to the request waiting for it."""
        message_type = message["type"]
        if message_type not in (
            protocol.MessageType.RESULT,
            protocol.MessageType.PONG,
            protocol.MessageType.ERROR,
        ):
            LOGGER.warning("%s sent unexpected %s message", self.name, message_type)
            return

        with self._pending_lock:
            future = self._pending.pop(str(message.get("id")), None)
        if future is None:
            LOGGER.debug("%s answered a request nobody waits for", self.name)
        elif message_type == protocol.MessageType.ERROR:
            future.set_exception(
                RuntimeError(f"{self.name}: {message.get('message', 'error')}")
            )
        else:
            future.set_result(message)


class BridgeServer:
    """TCP server clients connect to.

    Nyx listens and the client applications connect out, so the side executing code
    never accepts connections. A client has to open with a hello carrying the
    server's token. Requests are then sent to its `ClientSession`.

    Subclass it to change the class attributes and `validate_client`. To follow what
    happens to the server, add a listener, see `add_listener`.

    Attributes:
        DEFAULT_PORT: first port tried when no port is requested
        PORT_FALLBACK_COUNT: number of ports tried from the default one
        HANDSHAKE_TIMEOUT: seconds a new connection has to send its hello
    """

    DEFAULT_PORT = protocol.DEFAULT_PORT
    PORT_FALLBACK_COUNT = 20
    HANDSHAKE_TIMEOUT = 5.0

    def __init__(
        self,
        name: str | None = None,
        host: str = protocol.DEFAULT_HOST,
        port: int | None = None,
        token: str | None = None,
        address_file: str | os.PathLike | None = None,
    ) -> None:
        """Initialize BridgeServer.

        Args:
            name (str | None): server name, the class name if omitted.
            host (str): interface to listen on. Keep it local: clients can have code
                run on them by whoever holds the token.
            port (int | None): port to listen on. None tries `DEFAULT_PORT` and the
                ports after it, 0 lets the system choose, anything else is used as is.
            token (str | None): shared secret clients have to present, random if
                omitted.
            address_file (str | os.PathLike | None): file that tells clients where
                the server is. Written when the server starts and removed when it
                stops.
        """
        self.address_file = address_file
        self.name = name or type(self).__name__
        self.host = host
        self.token = token or secrets.token_urlsafe(16)
        self._requested_port = port
        self._listener: socket.socket | None = None
        self._accept_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._clients: list[ClientSession] = []
        self._condition = threading.Condition()
        self._listeners: list[typing.Any] = []
        self._port = port
        if host not in ("127.0.0.1", "localhost", "::1"):
            LOGGER.warning("Bridge server %s is listening on %s", self.name, host)

    def __repr__(self) -> str:
        """str: server with its name and address."""
        return f"{type(self).__name__}({self.name!r}, {self.host}:{self.port})"

    def __enter__(self) -> typing.Self:
        """Start the server."""
        self.start()
        return self

    def __exit__(self, *exc_info) -> None:
        """Stop the server."""
        self.stop()

    @property
    def port(self) -> int | None:
        """int | None: port the server listens on, the requested one before start."""
        return self._port

    @property
    def is_running(self) -> bool:
        """bool: the server accepts connections."""
        return self._listener is not None

    @property
    def clients(self) -> list[ClientSession]:
        """list[ClientSession]: connected clients."""
        with self._condition:
            return list(self._clients)

    # Hooks for subclasses
    def validate_client(self, hello: dict) -> str | None:
        """Decide whether a client may connect, once its token has been checked.

        Args:
            hello (dict): hello message of the client.

        Returns:
            str | None: reason to refuse the client, None to accept it.
        """
        return None

    # Listeners
    def add_listener(self, listener: typing.Any) -> None:
        """Get told about what happens to the server.

        A listener is any object with some of these methods, the others are skipped:
        `on_server_started(server)`, `on_server_stopped(server)`,
        `on_client_connected(server, session)` and
        `on_client_disconnected(server, session)`.

        They are called on the threads of the server, not on the one that added the
        listener, so they have to be quick and thread safe. A failing listener is
        logged and does not affect the server or the other listeners.

        Args:
            listener (Any): object to call, ignored if it is already added.
        """
        with self._condition:
            if listener not in self._listeners:
                self._listeners.append(listener)

    def remove_listener(self, listener: typing.Any) -> None:
        """Stop telling a listener about the server.

        Args:
            listener (Any): listener to remove, ignored if it is not added.
        """
        with self._condition:
            if listener in self._listeners:
                self._listeners.remove(listener)

    def _notify(self, event: str, *args: typing.Any) -> None:
        """Call `event` on every listener having it, outside of the server lock."""
        with self._condition:
            listeners = list(self._listeners)
        for listener in listeners:
            callback = getattr(listener, event, None)
            if callback is None:
                continue
            try:
                callback(self, *args)
            except Exception:
                LOGGER.exception("Bridge server listener %s failed", event)

    # Lifecycle
    def start(self) -> None:
        """Start listening and accepting clients.

        Raises:
            OSError: no port could be bound.
        """
        if self.is_running:
            return
        self._listener = self._bind()
        self._port = self._listener.getsockname()[1]
        self._stop_event.clear()
        self._accept_thread = threading.Thread(
            target=self._accept_loop, name=f"BridgeServer-{self.name}", daemon=True
        )
        self._accept_thread.start()
        if self.address_file is not None:
            self._write_own_address_file()
        LOGGER.info(
            "Bridge server %s listening on %s:%s", self.name, self.host, self.port
        )
        self._notify("on_server_started")

    def stop(self) -> None:
        """Stop listening and disconnect every client."""
        if not self.is_running:
            return
        self._stop_event.set()
        listener, self._listener = self._listener, None
        listener.close()
        if self._accept_thread is not None:
            self._accept_thread.join(timeout=2)
            self._accept_thread = None
        for session in self.clients:
            session.close()
        with self._condition:
            self._condition.notify_all()
        if self.address_file is not None:
            self._remove_own_address_file()
        LOGGER.info("Bridge server %s stopped", self.name)
        self._notify("on_server_stopped")

    def write_address_file(self, path: str | os.PathLike) -> None:
        """Write where this server listens, see `protocol.write_address_file`.

        Args:
            path (str | os.PathLike): file to write.
        """
        protocol.write_address_file(path, self.host, self.port, self.token)

    def _write_own_address_file(self) -> None:
        """Write `address_file`, a failure is logged and does not stop the server."""
        try:
            path = pathlib.Path(self.address_file)
            path.parent.mkdir(parents=True, exist_ok=True)
            self.write_address_file(path)
        except OSError:
            LOGGER.warning("Could not write address file %s", self.address_file)

    def _remove_own_address_file(self) -> None:
        """Remove `address_file` unless another server has taken it over."""
        try:
            found = protocol.read_address_file(self.address_file)
            if (found.port, found.token) == (self._port, self.token):
                os.remove(self.address_file)
        except (OSError, ValueError, KeyError):
            pass

    # Clients
    def find_clients(
        self,
        name: str | None = None,
        kind: str | None = None,
        client_id: str | None = None,
    ) -> list[ClientSession]:
        """Find connected clients. Criteria left out match anything.

        Args:
            name (str | None): client name.
            kind (str | None): client kind.
            client_id (str | None): client id.

        Returns:
            list[ClientSession]: matching clients, in order of connection.
        """
        return [
            session
            for session in self.clients
            if (name is None or session.name == name)
            and (kind is None or session.kind == kind)
            and (client_id is None or session.id == client_id)
        ]

    def wait_for_client(
        self,
        name: str | None = None,
        kind: str | None = None,
        client_id: str | None = None,
        timeout: float | None = None,
    ) -> ClientSession:
        """Wait until a matching client is connected.

        Args:
            name (str | None): client name.
            kind (str | None): client kind.
            client_id (str | None): client id.
            timeout (float | None): seconds to wait, forever if None.

        Raises:
            TimeoutError: no matching client within the timeout.
            RuntimeError: the server was stopped while waiting.

        Returns:
            ClientSession: first matching client.
        """

        def find() -> ClientSession | None:
            matches = [
                session
                for session in self._clients
                if (name is None or session.name == name)
                and (kind is None or session.kind == kind)
                and (client_id is None or session.id == client_id)
            ]
            return matches[0] if matches else None

        with self._condition:
            self._condition.wait_for(
                lambda: find() is not None or self._stop_event.is_set(), timeout
            )
            session = find()
        if session is not None:
            return session
        if self._stop_event.is_set():
            raise RuntimeError(f"Bridge server {self.name} was stopped")
        raise TimeoutError(
            f"No client (name={name!r}, kind={kind!r}) connected to {self.name} "
            f"within {timeout}s"
        )

    # Internals
    def _bind(self) -> socket.socket:
        """Bind a listening socket following the port rules of the constructor.

        Raises:
            OSError: no port could be bound.

        Returns:
            socket.socket: listening socket.
        """
        if self._requested_port is None:
            ports = range(
                self.DEFAULT_PORT, self.DEFAULT_PORT + self.PORT_FALLBACK_COUNT
            )
        else:
            ports = [self._requested_port]

        last_error = None
        for port in ports:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            if os.name != "nt":
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((self.host, port))
                sock.listen()
            except OSError as err:
                sock.close()
                last_error = err
                continue
            sock.settimeout(0.2)
            return sock
        raise OSError(f"Could not bind {self.host}:{list(ports)}: {last_error}")

    def _accept_loop(self) -> None:
        """Accept connections until stopped."""
        listener = self._listener
        while not self._stop_event.is_set():
            try:
                conn, address = listener.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            threading.Thread(
                target=self._serve_connection,
                args=(conn, address),
                name=f"BridgeServer-{self.name}-connection",
                daemon=True,
            ).start()

    def _serve_connection(self, conn: socket.socket, address: tuple) -> None:
        """Handshake with a new connection, then serve its client until it ends."""
        try:
            conn.settimeout(self.HANDSHAKE_TIMEOUT)
            session = self._handshake(conn, address)
        except (protocol.ProtocolError, OSError) as err:
            LOGGER.warning("Handshake with %s failed: %s", address, err)
            conn.close()
            return
        if session is None:
            conn.close()
            return

        conn.settimeout(None)
        self._add_client(session)
        try:
            session.run()
        finally:
            self._remove_client(session)

    def _handshake(self, conn: socket.socket, address: tuple) -> ClientSession | None:
        """Read the hello of a connection and answer it.

        Args:
            conn (socket.socket): new connection.
            address (tuple): remote address.

        Returns:
            ClientSession | None: session, None if the client was refused.
        """
        hello = protocol.recv_message(conn)
        reason = self._check_hello(hello)
        if reason is None:
            reason = self.validate_client(hello)
        if reason is not None:
            LOGGER.warning("Refused client %s: %s", address, reason)
            protocol.send_message(conn, protocol.make_error(reason))
            return None

        session = ClientSession(
            conn,
            uuid.uuid4().hex[:8],
            str(hello.get("client_name") or "client"),
            str(hello.get("client_kind") or ""),
            str(hello.get("client_version") or ""),
            address,
        )
        protocol.send_message(conn, protocol.make_ack(session.id, self.name))
        return session

    def _check_hello(self, hello: dict) -> str | None:
        """Check the parts of a hello every client has to get right.

        Args:
            hello (dict): first message of a connection.

        Returns:
            str | None: reason to refuse it.
        """
        if hello["type"] != protocol.MessageType.HELLO:
            return "Expected a hello message"
        if hello.get("protocol_version") != protocol.PROTOCOL_VERSION:
            return (
                f"Unsupported protocol version {hello.get('protocol_version')}, "
                f"server speaks {protocol.PROTOCOL_VERSION}"
            )
        if not hmac.compare_digest(str(hello.get("token", "")), self.token):
            return "Invalid token"
        return None

    def _add_client(self, session: ClientSession) -> None:
        """Track a handshaken client and tell the waiting threads."""
        with self._condition:
            self._clients.append(session)
            self._condition.notify_all()
        LOGGER.info("%s connected to %s", session, self.name)
        self._notify("on_client_connected", session)

    def _remove_client(self, session: ClientSession) -> None:
        """Forget a client whose connection ended."""
        with self._condition:
            if session in self._clients:
                self._clients.remove(session)
            self._condition.notify_all()
        self._notify("on_client_disconnected", session)
