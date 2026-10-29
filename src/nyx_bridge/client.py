"""Base client connecting an application to a running Nyx.

Standard library only, for Python 3.11+: clients live inside other
applications (DCCs) that install this package without Nyx or Qt.

Subclass `NyxClient` for an application: fill `default_namespace` (e.g. with `bpy`)
and, if the application is not safe to touch from other threads, override
`run_on_main_thread`.
"""

from __future__ import annotations

import contextlib
import io
import logging
import os
import socket
import threading
import time
import traceback
import typing

from . import protocol

LOGGER = logging.getLogger("nyx_bridge.client")


class NyxClient:
    """Connects to a Nyx bridge server and runs the code it sends.

    Two ways to run it:

    * `serve_forever` blocks the calling thread, which is then the thread executing
      the code. Meant for applications run in batch mode, where no event loop exists.
    * `start_in_thread` serves from a background thread and hands every execution to
      `run_on_main_thread`, which a subclass implements for its application.

    Code runs in a namespace that persists for the lifetime of a connection, so
    variables of one execution are visible to the next. A script exposes its return
    value by assigning `result`.

    Attributes:
        CLIENT_KIND: label telling the server what kind of application this is.
    """

    CLIENT_KIND = "python"
    HANDSHAKE_TIMEOUT = 10.0

    def __init__(
        self,
        name: str | None = None,
        address: str | tuple[str, int] | None = None,
        token: str | None = None,
        reconnect: bool = True,
        reconnect_delay: float = 1.0,
    ) -> None:
        """Initialize NyxClient.

        Args:
            name (str | None): name shown by the server. Derived from the kind and the
                process id if omitted.
            address (str | tuple[str, int] | None): `host:port` of the server. The
                environment is used if omitted, see `protocol.find_server_address`.
            token (str | None): server token, from the environment if omitted.
            reconnect (bool): connect again when the connection drops.
            reconnect_delay (float): seconds between connection attempts.
        """
        self.name = name or f"{self.CLIENT_KIND}-{os.getpid()}"
        self._address = address
        self._token = token
        self._reconnect = reconnect
        self._reconnect_delay = reconnect_delay
        self._stop_event = threading.Event()
        self._sock: socket.socket | None = None
        self._namespace: dict[str, typing.Any] = {}
        self.client_id: str | None = None
        self.server_name: str | None = None

    # Hooks for subclasses
    def client_version(self) -> str:
        """str: version of the application. Override in subclasses."""
        return ""

    def default_namespace(self) -> dict[str, typing.Any]:
        """Names available to every execution, e.g. the application's modules.

        Returns:
            dict[str, Any]: names to put in a fresh namespace. Override in subclasses.
        """
        return {}

    def run_on_main_thread(self, func: typing.Callable[[], typing.Any]) -> typing.Any:
        """Run a function on the thread the application wants its API used from.

        Called by the serving thread for every execution. The default runs it right
        there, which suits `serve_forever` on the main thread. Subclasses serving from
        a background thread override it to queue the call and wait for its result.

        Args:
            func (Callable[[], Any]): function to run.

        Returns:
            Any: whatever the function returns.
        """
        return func()

    def on_connected(self) -> None:
        """Called after the handshake. Override in subclasses."""

    def on_disconnected(self) -> None:
        """Called after the connection ended. Override in subclasses."""

    # Connection
    @property
    def is_connected(self) -> bool:
        """bool: a handshaken connection is open."""
        return self._sock is not None

    def connect(self) -> None:
        """Open one connection and handshake.

        Raises:
            ConnectionError: nothing says where the server is.
            OSError: the server can't be reached.
            protocol.HandshakeError: the server refused the hello.
            protocol.ProtocolError: the server broke the protocol.
        """
        address = protocol.find_server_address(self._address, self._token)
        if address is None:
            raise ConnectionError(
                f"Unknown Nyx server address, set {protocol.ADDRESS_ENV_VAR}"
            )
        sock = socket.create_connection(
            (address.host, address.port), timeout=self.HANDSHAKE_TIMEOUT
        )
        try:
            protocol.send_message(
                sock,
                protocol.make_hello(
                    address.token,
                    self.name,
                    self.CLIENT_KIND,
                    self.client_version(),
                ),
            )
            reply = protocol.recv_message(sock)
            if reply["type"] == protocol.MessageType.ERROR:
                raise protocol.HandshakeError(reply.get("message", "Refused"))
            if reply["type"] != protocol.MessageType.ACK:
                raise protocol.ProtocolError(f"Unexpected reply: {reply['type']}")
            sock.settimeout(None)
        except BaseException:
            sock.close()
            raise

        self.client_id = reply.get("client_id")
        self.server_name = reply.get("server_name")
        self._namespace = dict(self.default_namespace())
        self._namespace.setdefault("__name__", "__nyx__")
        self._sock = sock
        LOGGER.info("Connected to %s as %s", self.server_name, self.name)
        self.on_connected()

    def serve_forever(self, connect_timeout: float | None = None) -> None:
        """Connect and run what the server sends, until stopped or told to shut down.

        Reconnects when the connection drops (if enabled). Runs on the calling thread.

        Args:
            connect_timeout (float | None): seconds to keep trying to reach the
                server, per attempt to connect. Forever if omitted.

        Raises:
            ConnectionError: the server was not reached within `connect_timeout`.
            protocol.HandshakeError: the server refused this client, retrying would
                not help.
        """
        self._stop_event.clear()
        while not self._stop_event.is_set():
            self._connect_with_retry(connect_timeout)
            if not self.is_connected:
                break  # stopped while trying to connect
            try:
                # `stop` may have come in between connecting and here, when it had
                # no connection to close yet.
                if not self._stop_event.is_set():
                    self._serve_connection()
            finally:
                self._close_socket()
                self.on_disconnected()
            if not self._reconnect:
                break

    def start_in_thread(self, connect_timeout: float | None = None) -> threading.Thread:
        """Serve from a daemon thread.

        Args:
            connect_timeout (float | None): see `serve_forever`.

        Returns:
            threading.Thread: running thread.
        """
        thread = threading.Thread(
            target=self.serve_forever,
            args=(connect_timeout,),
            name=f"NyxClient-{self.name}",
            daemon=True,
        )
        thread.start()
        return thread

    def stop(self) -> None:
        """Stop serving and drop the connection. Safe to call from any thread."""
        self._stop_event.set()
        self._close_socket()

    def _connect_with_retry(self, connect_timeout: float | None) -> None:
        """Try to connect until it works, the timeout expires or the client stops."""
        deadline = (
            None if connect_timeout is None else time.monotonic() + connect_timeout
        )
        while not self._stop_event.is_set():
            try:
                self.connect()
                return
            except protocol.HandshakeError:
                raise
            except (OSError, protocol.ProtocolError) as err:
                if deadline is not None and time.monotonic() >= deadline:
                    raise ConnectionError(
                        f"Could not connect to Nyx within {connect_timeout}s: {err}"
                    ) from err
                LOGGER.debug("Connecting to Nyx failed: %s", err)
                self._stop_event.wait(self._reconnect_delay)

    def _serve_connection(self) -> None:
        """Handle messages until the connection ends."""
        sock = self._sock
        while True:
            try:
                message = protocol.recv_message(sock)
            except (protocol.ProtocolError, OSError) as err:
                if not self._stop_event.is_set():
                    LOGGER.info("Connection to Nyx ended: %s", err)
                return
            try:
                if not self._handle_message(sock, message):
                    return
            except OSError as err:
                LOGGER.info("Connection to Nyx ended: %s", err)
                return

    def _handle_message(self, sock: socket.socket, message: dict) -> bool:
        """Act on one message.

        Args:
            sock (socket.socket): connection to answer on.
            message (dict): message received.

        Returns:
            bool: keep serving.
        """
        message_type = message["type"]
        if message_type == protocol.MessageType.EXECUTE:
            reply = self.run_on_main_thread(lambda: self._run_request(message))
            protocol.send_message(sock, reply)
        elif message_type == protocol.MessageType.PING:
            protocol.send_message(sock, protocol.make_pong(message.get("id")))
        elif message_type == protocol.MessageType.SHUTDOWN:
            LOGGER.info("Nyx asked the client to shut down")
            self._stop_event.set()
            return False
        else:
            protocol.send_message(
                sock,
                protocol.make_error(
                    f"Unexpected message type: {message_type}", message.get("id")
                ),
            )
        return True

    def _close_socket(self) -> None:
        """Close the connection, unblocking a thread reading from it."""
        sock, self._sock = self._sock, None
        if sock is None:
            return
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        sock.close()

    # Execution
    def _run_request(self, message: dict) -> dict:
        """Run an execute request and build the answer, whatever happens.

        Args:
            message (dict): execute message.

        Returns:
            dict: result message.
        """
        return self.execute(
            message.get("code", ""),
            message.get("mode", protocol.EXEC),
            message.get("args"),
            request_id=message.get("id"),
        )

    def execute(
        self,
        code: str,
        mode: str = protocol.EXEC,
        args: dict | None = None,
        request_id: str | None = None,
    ) -> dict:
        """Run code in the persistent namespace. Never raises.

        Output written to stdout and stderr while it runs is captured. In `exec` mode
        the value is what the code assigned to `result`, in `eval` mode the value of
        the expression.

        Args:
            code (str): source to run.
            mode (str): `protocol.EXEC` or `protocol.EVAL`.
            args (dict | None): names to set in the namespace before running.
            request_id (str | None): id of the request being answered.

        Returns:
            dict: result message, see `protocol.make_result`.
        """
        namespace = self._namespace
        namespace.update(args or {})
        stdout, stderr = io.StringIO(), io.StringIO()
        value, error, ok = None, None, True
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            try:
                if mode == protocol.EVAL:
                    value = eval(compile(code, "<nyx>", "eval"), namespace)
                elif mode == protocol.EXEC:
                    namespace.pop("result", None)
                    exec(compile(code, "<nyx>", "exec"), namespace)
                    value = namespace.get("result")
                else:
                    raise ValueError(f"Unknown execution mode: {mode!r}")
            except (Exception, SystemExit) as err:
                ok, error = False, self._describe_error(err)
        return protocol.make_result(
            request_id,
            ok,
            value=protocol.to_jsonable(value),
            error=error,
            stdout=stdout.getvalue(),
            stderr=stderr.getvalue(),
        )

    @staticmethod
    def _describe_error(err: BaseException) -> dict:
        """Describe an exception raised by executed code.

        The frame of `execute` itself is left out of the traceback.

        Args:
            err (BaseException): raised exception.

        Returns:
            dict: `type`, `message` and `traceback` of the exception.
        """
        tb = err.__traceback__.tb_next if err.__traceback__ else None
        return {
            "type": type(err).__name__,
            "message": str(err),
            "traceback": "".join(traceback.format_exception(type(err), err, tb)),
        }
