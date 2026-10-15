"""Lo que comparten los plugins de net.millaguie.minino para hablar con el host.

Un bundle oficial se ejecuta como proceso aparte y recibe en argv el puerto del host, el uuid de
la sesión y el nombre del evento de registro. Elgato los pasa posiciónales y OpenAction los pasa
como flags, así que esta capa lee las dos formas. El websocket está hecho sólo con el estándar:
un bundle no puede depender de qué venv tenga el usuario instalado.
"""

import base64
import hashlib
import json
import os
import select
import socket
import struct
import sys
import time
from typing import Any, Callable, Optional

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
FLAG_NAMES = ("port", "pluginUuid", "registerEvent")


class Handshake:
    def __init__(self, port: int, session: str, register_event: str, info: dict, action_info: dict):
        self.port = port
        self.session = session
        self.register_event = register_event
        self.info = info
        self.action_info = action_info

    @property
    def plugin(self) -> str:
        return str((self.info.get("plugin") or {}).get("uuid", ""))


def handshake(argv=None) -> Handshake:
    args = list(sys.argv[1:] if argv is None else argv)
    flags: dict[str, str] = {}
    positional: list[str] = []
    index = 0
    while index < len(args):
        item = args[index]
        if item.startswith("-") and item[1:] in FLAG_NAMES and index + 1 < len(args):
            flags[item.lstrip("-")] = args[index + 1]
            index += 2
        else:
            positional.append(item)
            index += 1
    if not positional and not flags:
        raise SystemExit("this program is a Stream Deck plugin: it needs the host handshake")
    return Handshake(
        port=int(flags.get("port") or positional[0]),
        session=flags.get("pluginUuid") or positional[1],
        register_event=flags.get("registerEvent") or (positional[2] if len(positional) > 2 else "registerPlugin"),
        info=_json(positional[3] if len(positional) > 3 else "{}"),
        action_info=_json(positional[4] if len(positional) > 4 else "{}"),
    )


def _json(text: str) -> dict:
    try:
        parsed = json.loads(text)
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def connect(port: int, host: str = "127.0.0.1", timeout: float = 10.0) -> socket.socket:
    sock = socket.create_connection((host, port), timeout=timeout)
    key = base64.b64encode(os.urandom(16)).decode()
    request = (
        f"GET / HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
    )
    sock.sendall(request.encode())
    response = b""
    while b"\r\n\r\n" not in response:
        chunk = sock.recv(4096)
        if not chunk:
            raise ConnectionError("the host closed the handshake")
        response += chunk
    header = response.split(b"\r\n\r\n")[0].decode(errors="replace")
    if "101" not in header.splitlines()[0]:
        raise ConnectionError(f"unexpected handshake response: {header.splitlines()[0]}")
    expected = base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()
    if expected not in header:
        raise ConnectionError("handshake accept key does not match")
    return sock


def send_json(sock: socket.socket, message: dict) -> None:
    payload = json.dumps(message).encode()
    header = bytearray([0x81])
    length = len(payload)
    if length < 126:
        header.append(0x80 | length)
    elif length < 65536:
        header.append(0x80 | 126)
        header += struct.pack(">H", length)
    else:
        header.append(0x80 | 127)
        header += struct.pack(">Q", length)
    mask = os.urandom(4)
    header += mask
    sock.sendall(bytes(header) + bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload)))


def read_json(sock: socket.socket):
    while True:
        first = _recv_exact(sock, 1)
        second = _recv_exact(sock, 1) if first else None
        if first is None or second is None:
            return None
        opcode = first[0] & 0x0F
        length = second[0] & 0x7F
        if length == 126:
            length = struct.unpack(">H", _recv_exact(sock, 2))[0]
        elif length == 127:
            length = struct.unpack(">Q", _recv_exact(sock, 8))[0]
        payload = _recv_exact(sock, length) if length else b""
        if opcode == 0x8:
            return None
        if opcode == 0x9:
            _send_raw(sock, 0xA, payload)
            continue
        if opcode == 0x1:
            try:
                return json.loads(payload.decode())
            except ValueError:
                continue


def _send_raw(sock: socket.socket, opcode: int, payload: bytes) -> None:
    mask = os.urandom(4)
    header = bytes([0x80 | opcode, 0x80 | len(payload)]) + mask
    sock.sendall(header + bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload)))


def _recv_exact(sock: socket.socket, count: int):
    data = b""
    while len(data) < count:
        chunk = sock.recv(count - len(data))
        if not chunk:
            return None
        data += chunk
    return data


class Plugin:
    """Bucle de evento de un plugin: recibe mensajes, y opcionalmente se despierta cada X segundos."""

    def __init__(self):
        self.sock: Optional[socket.socket] = None
        self.info: dict = {}
        self.action_info: dict = {}
        self.devices: dict[str, dict] = {}
        self.contexts: dict[str, dict] = {}
        self._deadline = 0.0

    def run(self) -> None:
        handshake_info = handshake()
        self.sock = connect(handshake_info.port)
        self.info = handshake_info.info
        self.action_info = handshake_info.action_info
        send_json(self.sock, {"event": handshake_info.register_event, "uuid": handshake_info.session})
        self.on_started(handshake_info)
        while self.sock is not None:
            timeout = None
            if self._deadline:
                timeout = max(0.0, self._deadline - time.monotonic())
            if select.select([self.sock], [], [], timeout)[0]:
                message = read_json(self.sock)
                if message is None:
                    return
                if not self._receive(message):
                    return
            if self._deadline and time.monotonic() >= self._deadline:
                self._deadline = 0.0
                self.on_tick()

    def _receive(self, message: dict) -> bool:
        name = message.get("event")
        payload = message.get("payload") if isinstance(message.get("payload"), dict) else {}
        if name == "deviceDidConnect":
            info = message.get("deviceInfo") if isinstance(message.get("deviceInfo"), dict) else {}
            identifier = message.get("deviceIdentifier") or message.get("device") or ""
            self.devices[str(identifier)] = {
                "name": info.get("name", ""),
                "rows": int(info.get("rows") or (info.get("size") or {}).get("rows") or 0),
                "columns": int(info.get("columns") or (info.get("size") or {}).get("columns") or 0),
            }
            self.on_device(message)
        elif name == "deviceDidDisconnect":
            identifier = message.get("deviceIdentifier") or message.get("device") or ""
            self.devices.pop(str(identifier), None)
            self.on_device(message)
        elif name == "willAppear":
            self.contexts[message.get("context", "")] = {
                "settings": payload.get("settings") or {},
                "device": message.get("device", ""),
                "coordinates": payload.get("coordinates") or {},
                "state": payload.get("state", 0),
            }
            self.on_appear(message)
        elif name == "willDisappear":
            self.contexts.pop(message.get("context", ""), None)
            self.on_disappear(message)
        elif name in ("keyDown", "keyUp", "touchTap"):
            self.on_press(message)
        elif name in ("dialRotate", "dialDown", "dialUp"):
            self.on_dial(message)
        elif name == "didReceiveSettings":
            context = message.get("context", "")
            if context in self.contexts:
                self.contexts[context]["settings"] = payload.get("settings") or {}
            self.on_settings(message)
        elif name == "systemDidSleep":
            self.on_sleep(message)
        return self.on_message(message) is not False

    # Los plugins que necesitan algo concreto de un evento lo sobreescriben.

    def on_started(self, handshake: Handshake) -> None: ...
    def on_device(self, message: dict) -> None: ...
    def on_appear(self, message: dict) -> None: ...
    def on_disappear(self, message: dict) -> None: ...
    def on_press(self, message: dict) -> None: ...
    def on_dial(self, message: dict) -> None: ...
    def on_settings(self, message: dict) -> None: ...
    def on_sleep(self, message: dict) -> None: ...
    def on_message(self, message: dict) -> Optional[bool]: ...
    def on_tick(self) -> None: ...

    # Salidas hacia el host.

    def set_image(self, context: str, png: bytes) -> None:
        self._send(
            {"event": "setImage", "context": context, "payload": {"image": "data:image/png;base64," + _b64(png)}}
        )

    def set_title(self, context: str, title: str = "", state: int = 0, position=None) -> None:
        payload: dict[str, Any] = {"title": title, "state": state}
        if position is not None:
            payload["position"] = position
        self._send({"event": "setTitle", "context": context, "payload": payload})

    def set_state(self, context: str, state: int) -> None:
        self._send({"event": "setState", "context": context, "payload": {"position": state}})

    def set_settings(self, context: str, settings: dict) -> None:
        self._send({"event": "setSettings", "context": context, "payload": settings})

    def alert(self, context: str) -> None:
        self._send({"event": "alert", "context": context})

    def show_alert(self, context: str) -> None:
        self._send({"event": "showAlert", "context": context})

    def log(self, message: str, level: str = "info") -> None:
        self._send({"event": "logMessage", "payload": {"message": message, "type": level}})

    def every(self, seconds: float) -> None:
        self._deadline = time.monotonic() + seconds

    def visible(self) -> dict[str, dict]:
        return dict(self.contexts)

    def _send(self, message: dict) -> None:
        if self.sock is not None:
            try:
                send_json(self.sock, message)
            except OSError:
                self.sock = None


def _b64(payload: bytes) -> str:
    return base64.b64encode(payload).decode()


def png_of(image) -> bytes:
    import io

    buffer = io.BytesIO()
    image.convert("RGBA").save(buffer, "PNG")
    return buffer.getvalue()


def run(plugin: Plugin, on_error: Optional[Callable[[BaseException], None]] = None) -> None:
    try:
        plugin.run()
    except BrokenPipeError:
        pass
    except BaseException as error:  # un bundle no debe morir silenciosamente: lo contamos
        if on_error is not None:
            on_error(error)
        else:
            print(f"minino plugin stopped: {error}", file=sys.stderr, flush=True)
