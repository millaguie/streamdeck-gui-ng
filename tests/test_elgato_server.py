"""Round trips over a real loopback socket, no mocks in the middle."""

import json

import pytest
from PySide6.QtCore import QEventLoop, QTimer, QUrl
from PySide6.QtNetwork import QAbstractSocket
from PySide6.QtWebSockets import QWebSocket

from streamdeck_ui.elgato_plugin.protocol import ElgatoProtocol, HostActions
from streamdeck_ui.elgato_plugin.server import ElgatoPluginHost

PLUGIN_UUID = "com.example.plugin"
CONTEXT = "ctx-1"


@pytest.fixture
def host(qcore_app):
    server = ElgatoPluginHost()
    server.start()
    yield server
    server.stop()


@pytest.fixture
def client(qcore_app, host):
    socket = QWebSocket()
    received: list[dict] = []
    closed = {"done": False}
    socket.textMessageReceived.connect(lambda text: received.append(json.loads(text)))
    socket.disconnected.connect(lambda: closed.update(done=True))
    socket.open(QUrl(f"ws://127.0.0.1:{host.port}"))
    assert wait_until(lambda: is_open(socket)), "client never connected"
    yield socket, received, closed
    socket.close()


def is_open(socket) -> bool:
    return socket.state() == QAbstractSocket.SocketState.ConnectedState


def wait_until(predicate, timeout_ms=4000, interval_ms=10):
    loop = QEventLoop()
    poll = QTimer()
    poll.setInterval(interval_ms)
    poll.timeout.connect(lambda: predicate() and loop.quit())
    watchdog = QTimer()
    watchdog.setSingleShot(True)
    watchdog.timeout.connect(loop.quit)
    poll.start()
    watchdog.start(timeout_ms)
    loop.exec()
    poll.stop()
    return predicate()


def test_host_listens_on_an_ephemeral_loopback_port(host):
    assert host.port > 0


def test_registered_plugin_receives_settings_back(host, client):
    socket, received, _ = client
    socket.sendTextMessage(json.dumps({"event": "registerPlugin", "uuid": PLUGIN_UUID}))
    assert wait_until(lambda: host.connection_ids)

    connection_id = host.connection_ids[0]
    host.protocol.bind_context(CONTEXT, connection_id, "com.example.plugin.action", "DEV1", settings={"a": 1})

    socket.sendTextMessage(json.dumps({"event": "getSettings", "context": CONTEXT}))

    assert wait_until(lambda: any(message["event"] == "didReceiveSettings" for message in received))
    answer = next(message for message in received if message["event"] == "didReceiveSettings")
    assert answer["context"] == CONTEXT
    assert answer["payload"]["settings"] == {"a": 1}


def test_plugin_without_registration_gets_dropped(host, client):
    socket, _, closed = client

    socket.sendTextMessage(json.dumps({"event": "showAlert", "context": "robado"}))

    assert wait_until(lambda: closed["done"])
    assert host.connection_ids == []


def test_broadcast_reaches_plugins_only(host, qcore_app):
    plugin = QWebSocket()
    inspector = QWebSocket()
    plugin_messages: list[dict] = []
    inspector_messages: list[dict] = []
    plugin.textMessageReceived.connect(lambda text: plugin_messages.append(json.loads(text)))
    inspector.textMessageReceived.connect(lambda text: inspector_messages.append(json.loads(text)))
    plugin.open(QUrl(f"ws://127.0.0.1:{host.port}"))
    inspector.open(QUrl(f"ws://127.0.0.1:{host.port}"))
    assert wait_until(lambda: is_open(plugin) and is_open(inspector))

    plugin.sendTextMessage(json.dumps({"event": "registerPlugin", "uuid": PLUGIN_UUID}))
    inspector.sendTextMessage(json.dumps({"event": "registerPropertyInspector", "uuid": PLUGIN_UUID}))
    assert wait_until(lambda: len(host.connection_ids) == 2)

    sent = host.broadcast(host.protocol.device_did_connect("DEV1", {"name": "Stream Deck MK.2"}))

    assert len(sent) == 1
    assert wait_until(lambda: plugin_messages and not inspector_messages)
    assert plugin_messages[0]["deviceIdentifier"] == "DEV1"

    plugin.close()
    inspector.close()


def test_dropped_socket_forgets_its_contexts(host, client):
    socket, _, closed = client
    socket.sendTextMessage(json.dumps({"event": "registerPlugin", "uuid": PLUGIN_UUID}))
    assert wait_until(lambda: host.connection_ids)
    connection_id = host.connection_ids[0]
    host.protocol.bind_context(CONTEXT, connection_id, "com.example.plugin.action", "DEV1")

    socket.close()

    assert wait_until(lambda: not host.connection_ids)
    assert host.protocol.contexts == {}


def test_server_works_with_an_injected_protocol_and_actions(qcore_app):
    seen: list[dict] = []
    protocol = ElgatoProtocol(HostActions(set_title=lambda **kwargs: seen.append(kwargs)))
    server = ElgatoPluginHost(protocol=protocol)
    server.start()

    socket = QWebSocket()
    socket.open(QUrl(f"ws://127.0.0.1:{server.port}"))
    assert wait_until(lambda: is_open(socket))
    socket.sendTextMessage(json.dumps({"event": "registerPlugin", "uuid": PLUGIN_UUID}))
    assert wait_until(lambda: server.connection_ids)
    server.protocol.bind_context(CONTEXT, server.connection_ids[0], "com.example.plugin.action", "DEV1")
    socket.sendTextMessage(json.dumps({"event": "setTitle", "context": CONTEXT, "payload": {"title": "Hola"}}))

    assert wait_until(lambda: bool(seen))
    assert seen[0]["title"] == "Hola"

    socket.close()
    server.stop()
