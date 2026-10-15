"""A websocket client built only with the standard library.

Test plugins run as separate processes and cannot import PySide6 reliably, so they talk to
the plugin host with this. It implements only what those fixtures need: a text frame client
with masked requests, which is what RFC 6455 requires from a client anyway.
"""

import base64
import hashlib
import json
import os
import socket
import struct

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def accept_key(key: str) -> str:
    return base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()


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
            raise ConnectionError("websocket handshake closed")
        response += chunk
    expected = accept_key(key)
    header = response.split(b"\r\n\r\n")[0].decode(errors="replace")
    if "101" not in header.splitlines()[0]:
        raise ConnectionError(f"unexpected handshake response: {header.splitlines()[0]}")
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
    masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
    sock.sendall(bytes(header) + masked)


def read_json(sock: socket.socket):
    """Returns the next text frame as a dict; None when the peer closes."""
    while True:
        first = _recv_exact(sock, 1)
        if first is None:
            return None
        second = _recv_exact(sock, 1)
        if second is None:
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
            return json.loads(payload.decode())


def _send_raw(sock: socket.socket, opcode: int, payload: bytes) -> None:
    mask = os.urandom(4)
    header = bytes([0x80 | opcode, 0x80 | len(payload)]) + mask
    masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
    sock.sendall(header + masked)


def _recv_exact(sock: socket.socket, count: int):
    data = b""
    while len(data) < count:
        chunk = sock.recv(count - len(data))
        if not chunk:
            return None
        data += chunk
    return data
