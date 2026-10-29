import pytest

from nyx_bridge.manager import BridgeServerManager
from nyx_bridge.server import BridgeServer


class MayaServer(BridgeServer):
    TYPE_ID = "maya"


def test_add_and_find_servers():
    manager = BridgeServerManager()
    server = manager.add_server(BridgeServer(name="one", port=0))

    assert manager.get_server("one") is server
    assert manager.servers == [server]
    assert manager.get_server("missing") is None


def test_duplicate_names_are_refused():
    manager = BridgeServerManager()
    manager.add_server(BridgeServer(name="one", port=0))

    with pytest.raises(ValueError, match="already exists"):
        manager.add_server(BridgeServer(name="one", port=0))


def test_create_server_uses_the_resolver_and_defaults_the_name_to_the_type():
    manager = BridgeServerManager({"maya": MayaServer}.get)

    server = manager.create_server("maya", port=0)

    assert isinstance(server, MayaServer)
    assert server.name == "maya"
    assert manager.get_server("maya") is server
    assert manager.create_server("maya", name="second", port=0).name == "second"


def test_create_server_of_unknown_type_is_an_error():
    with pytest.raises(KeyError, match="nope"):
        BridgeServerManager({}.get).create_server("nope")
    with pytest.raises(KeyError):
        BridgeServerManager().create_server("maya")


def test_remove_server_stops_it():
    manager = BridgeServerManager()
    server = manager.add_server(BridgeServer(name="one", port=0))
    server.start()

    assert manager.remove_server("one") is server

    assert not server.is_running
    assert manager.servers == []
    assert manager.remove_server("one") is None


def test_stop_all_stops_every_server_even_if_one_fails(monkeypatch):
    manager = BridgeServerManager()
    first = manager.add_server(BridgeServer(name="first", port=0))
    second = manager.add_server(BridgeServer(name="second", port=0))
    first.start()
    second.start()

    def fail():
        raise RuntimeError("cannot stop")

    monkeypatch.setattr(first, "stop", fail)

    manager.stop_all()

    assert not second.is_running
    monkeypatch.undo()
    first.stop()
