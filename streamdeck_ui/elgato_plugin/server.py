"""Loopback websocket server speaking the official plugin protocol.

This is the transport only; every decision about what a message means lives in
:mod:`streamdeck_ui.elgato_plugin.protocol`.
"""

import json
import logging
from itertools import count

from PySide6.QtCore import QObject, Slot
from PySide6.QtNetwork import QHostAddress
from PySide6.QtWebSockets import QWebSocket, QWebSocketServer

from streamdeck_ui.elgato_plugin.protocol import ElgatoProtocol, ProtocolError

logger = logging.getLogger(__name__)


class ElgatoPluginHost(QObject):
    """Accepts plugin and property inspector connections on 127.0.0.1."""

    def __init__(self, protocol: ElgatoProtocol | None = None, parent: QObject | None = None):
        super().__init__(parent)
        self.protocol = protocol or ElgatoProtocol(logger=lambda level, message: logger.warning(message))
        self._server = QWebSocketServer("streamdeck-ui", QWebSocketServer.SslMode.NonSecureMode, self)
        self._sockets: dict[str, QWebSocket] = {}
        self._identifiers = count(1)
        self._server.newConnection.connect(self._on_new_connection)

    @property
    def port(self) -> int:
        """The ephemeral port the official handshake passes to plugins."""
        return self._server.serverPort()

    @property
    def connection_ids(self) -> list[str]:
        return sorted(self._sockets)

    def start(self) -> int:
        """Listens on loopback only, on a port chosen by the kernel."""
        if self._server.isListening():
            return self.port
        if not self._server.listen(QHostAddress(QHostAddress.SpecialAddress.LocalHost), 0):
            raise RuntimeError(f"could not listen on loopback: {self._server.errorString()}")
        logger.info("Plugin host listening on port %d", self.port)
        return self.port

    def stop(self) -> None:
        for socket in list(self._sockets.values()):
            socket.close()
        self._sockets.clear()
        self.protocol.connections.clear()
        self._server.close()

    def send(self, connection_id: str, message: dict) -> bool:
        socket = self._sockets.get(connection_id)
        if socket is None:
            return False
        socket.sendTextMessage(json.dumps(message))
        return True

    def broadcast(self, message: dict) -> list[str]:
        """Sends a device or application level event to every registered plugin."""
        targets = [
            connection.connection_id for connection in self.protocol.connections.values() if connection.kind == "plugin"
        ]
        return [target for target in targets if self.send(target, message)]

    def send_to_context(self, context: str, message: dict) -> list[str]:
        """Sends a key level event to the plugin owning the context, and its inspector."""
        binding = self.protocol.contexts.get(context)
        if binding is None:
            return []
        targets = [binding.connection_id]
        if binding.property_inspector:
            targets.append(binding.property_inspector)
        return [target for target in targets if self.send(target, message)]

    @Slot()
    def _on_new_connection(self) -> None:
        socket = self._server.nextPendingConnection()
        if socket is None:
            return
        connection_id = f"conn-{next(self._identifiers)}"
        self._sockets[connection_id] = socket
        socket.textMessageReceived.connect(lambda text, cid=connection_id: self._on_message(cid, text))
        socket.disconnected.connect(lambda cid=connection_id: self._on_disconnected(cid))
        logger.debug("Plugin connection %s from %s", connection_id, socket.peerAddress().toString())

    def _on_disconnected(self, connection_id: str) -> None:
        self._sockets.pop(connection_id, None)
        self.protocol.close(connection_id)

    def _on_message(self, connection_id: str, text: str) -> None:
        try:
            replies = self.protocol.handle(connection_id, text)
        except ProtocolError as error:
            logger.warning("Closing %s: %s", connection_id, error)
            socket = self._sockets.pop(connection_id, None)
            self.protocol.close(connection_id)
            if socket is not None:
                socket.close()
            return
        for reply in replies:
            self.send(reply.connection_id, reply.message)
