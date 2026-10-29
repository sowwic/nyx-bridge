from __future__ import annotations

import collections
import logging
import threading
import typing

from .server import BridgeServer

LOGGER = logging.getLogger(__name__)


class BridgeServerManager:
    """The bridge servers of the application, by name.

    Owned by the session: servers hold ports and threads, so they belong to the
    application, not to the stages that happen to be open.
    """

    def __init__(
        self,
        type_resolver: typing.Callable[[str], type[BridgeServer] | None] | None = None,
    ) -> None:
        """Initialize BridgeServerManager.

        Args:
            type_resolver (Callable[[str], type[BridgeServer] | None] | None): finds a
                server class by type id, e.g. the one registered by plugins. Needed by
                `create_server` only.
        """
        self._type_resolver = type_resolver
        self._servers: collections.OrderedDict[str, BridgeServer] = (
            collections.OrderedDict()
        )
        self._lock = threading.Lock()

    @property
    def servers(self) -> list[BridgeServer]:
        """list[BridgeServer]: managed servers, in order of addition."""
        with self._lock:
            return list(self._servers.values())

    def get_server(self, name: str) -> BridgeServer | None:
        """Find a server by name.

        Args:
            name (str): server name.

        Returns:
            BridgeServer | None: server, None if there is none with that name.
        """
        with self._lock:
            return self._servers.get(name)

    def add_server(self, server: BridgeServer) -> BridgeServer:
        """Manage a server. It is not started.

        Args:
            server (BridgeServer): server to add.

        Raises:
            ValueError: a server with the same name is already managed.

        Returns:
            BridgeServer: the server.
        """
        with self._lock:
            if server.name in self._servers:
                raise ValueError(f"Bridge server {server.name!r} already exists")
            self._servers[server.name] = server
        return server

    def create_server(
        self, type_id: str, name: str | None = None, **kwargs: typing.Any
    ) -> BridgeServer:
        """Create a server of a registered type and manage it. It is not started.

        Args:
            type_id (str): type id of the server class.
            name (str | None): server name, the type id if omitted.
            **kwargs: passed on to the server class (host, port, token).

        Raises:
            KeyError: no server class is registered under the type id.
            ValueError: a server with the same name is already managed.

        Returns:
            BridgeServer: new server.
        """
        server_cls = self._type_resolver(type_id) if self._type_resolver else None
        if server_cls is None:
            raise KeyError(f"Unknown bridge server type: {type_id!r}")
        return self.add_server(server_cls(name=name or type_id, **kwargs))

    def remove_server(self, name: str, stop: bool = True) -> BridgeServer | None:
        """Stop managing a server.

        Args:
            name (str): server name.
            stop (bool): stop the server too.

        Returns:
            BridgeServer | None: removed server, None if there was none.
        """
        with self._lock:
            server = self._servers.pop(name, None)
        if server is not None and stop:
            server.stop()
        return server

    def stop_all(self) -> None:
        """Stop every server, e.g. when the application quits."""
        for server in self.servers:
            try:
                server.stop()
            except Exception:
                LOGGER.exception("Failed to stop bridge server %s", server.name)
