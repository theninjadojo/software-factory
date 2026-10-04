"""A minimal WebSocket client (RFC 6455) on the standard library, for Slack's Socket Mode.
Text messages only: control frames are answered, fragments are joined, and anything oversized, masked by the server or
using reserved bits is refused. One thread reads and answers; `send` is locked so it may be called from elsewhere."""
import base64
import hashlib
import os
import socket
import ssl
import struct
import threading
from urllib.parse import urlsplit

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
MAX_MESSAGE = 1 << 20                     # Socket Mode envelopes are a few KB; refuse anything near a megabyte
TEXT, BINARY, CLOSE, PING, PONG = 0x1, 0x2, 0x8, 0x9, 0xA


class WebSocketError(Exception):
    pass


class Closed(WebSocketError):
    """The server closed the connection (or it dropped)."""


def encode_frame(opcode: int, payload: bytes = b"", fin: bool = True, mask: bool = True) -> bytes:
    """One frame. Clients must mask; `mask=False` exists for tests that play the server."""
    head = bytes([(0x80 if fin else 0) | opcode])
    n = len(payload)
    mbit = 0x80 if mask else 0
    if n < 126:
        head += bytes([mbit | n])
    elif n < 1 << 16:
        head += bytes([mbit | 126]) + struct.pack(">H", n)
    else:
        head += bytes([mbit | 127]) + struct.pack(">Q", n)
    if not mask:
        return head + payload
    key = os.urandom(4)
    return head + key + bytes(b ^ key[i % 4] for i, b in enumerate(payload))


class WebSocket:
    def __init__(self, sock):
        self.sock = sock
        self._send_lock = threading.Lock()

    def _read(self, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise Closed("connection closed")
            buf += chunk
        return buf

    def _send_frame(self, opcode: int, payload: bytes = b"") -> None:
        with self._send_lock:
            self.sock.sendall(encode_frame(opcode, payload))

    def send(self, text: str) -> None:
        self._send_frame(TEXT, text.encode())

    def close(self) -> None:
        try:
            self._send_frame(CLOSE, struct.pack(">H", 1000))
        except Exception:
            pass
        try:
            self.sock.close()
        except Exception:
            pass

    def recv(self) -> str:
        """The next complete text message. Raises Closed when the server closes, WebSocketError on a protocol violation,
        and socket.timeout if the connection is silent for the socket's timeout."""
        parts: list[bytes] = []
        size = 0
        started = False
        while True:
            b0, b1 = self._read(2)
            fin, rsv, opcode = bool(b0 & 0x80), b0 & 0x70, b0 & 0x0F
            if rsv:
                raise WebSocketError("reserved bits set")
            if b1 & 0x80:
                raise WebSocketError("server frame is masked")
            n = b1 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._read(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._read(8))[0]
            if opcode >= 0x8:                                  # control frame: short, never fragmented
                if n > 125 or not fin:
                    raise WebSocketError("bad control frame")
            elif size + n > MAX_MESSAGE:
                raise WebSocketError("message too large")
            payload = self._read(n) if n else b""
            if opcode == PING:
                self._send_frame(PONG, payload)
            elif opcode == PONG:
                pass
            elif opcode == CLOSE:
                try:
                    self._send_frame(CLOSE, payload[:2])
                except Exception:
                    pass
                raise Closed("closed by server")
            elif opcode == TEXT or opcode == 0:
                if opcode == TEXT:
                    if started:
                        raise WebSocketError("new message inside a fragmented one")
                    started = True
                elif not started:
                    raise WebSocketError("continuation without a start")
                parts.append(payload)
                size += n
                if fin:
                    try:
                        return b"".join(parts).decode()
                    except UnicodeDecodeError:
                        raise WebSocketError("message is not UTF-8")
            else:
                raise WebSocketError(f"unsupported opcode {opcode}")


def handshake(sock, host: str, path: str) -> WebSocket:
    """Upgrade an open (TLS) socket and check the server's answer."""
    key = base64.b64encode(os.urandom(16)).decode()
    sock.sendall((f"GET {path} HTTP/1.1\r\nHost: {host}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                  f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = sock.recv(4096)
        if not chunk or len(buf) > 16384:
            raise WebSocketError("no handshake response")
        buf += chunk
    head, _, rest = buf.partition(b"\r\n\r\n")
    lines = head.decode("latin-1").split("\r\n")
    if len(lines[0].split()) < 2 or lines[0].split()[1] != "101":
        raise WebSocketError(f"handshake refused: {lines[0][:80]}")
    headers = {k.strip().lower(): v.strip() for k, _, v in (ln.partition(":") for ln in lines[1:])}
    want = base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()
    if headers.get("sec-websocket-accept") != want:
        raise WebSocketError("handshake answer does not match the key")
    if rest:                                                    # bytes the server sent right behind its answer
        sock = _Prefixed(sock, rest)
    return WebSocket(sock)


class _Prefixed:
    """A socket that first returns bytes already read past the handshake."""
    def __init__(self, sock, prefix: bytes):
        self._sock, self._prefix = sock, prefix

    def recv(self, n: int) -> bytes:
        if self._prefix:
            out, self._prefix = self._prefix[:n], self._prefix[n:]
            return out
        return self._sock.recv(n)

    def sendall(self, data: bytes) -> None:
        self._sock.sendall(data)

    def close(self) -> None:
        self._sock.close()


def connect(url: str, timeout: float = 90) -> WebSocket:
    """Open a wss:// URL. `timeout` is how long a silent connection is tolerated (Slack pings far more often)."""
    u = urlsplit(url)
    if u.scheme != "wss" or not u.hostname:
        raise WebSocketError("only wss:// URLs are supported")
    raw = socket.create_connection((u.hostname, u.port or 443), timeout=15)
    try:
        sock = ssl.create_default_context().wrap_socket(raw, server_hostname=u.hostname)
        sock.settimeout(timeout)
        return handshake(sock, u.netloc, (u.path or "/") + ("?" + u.query if u.query else ""))
    except Exception:
        raw.close()
        raise
