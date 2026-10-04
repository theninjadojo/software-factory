import base64
import hashlib
import socket
import struct
import threading
import unittest

from factory import wsclient as w


def server_frame(opcode, payload=b"", fin=True):
    return w.encode_frame(opcode, payload, fin=fin, mask=False)


class Pair:
    """A WebSocket on one end of a socketpair; the test plays the server on the other end."""
    def __init__(self):
        self.client, self.server = socket.socketpair()
        self.client.settimeout(2)
        self.server.settimeout(2)
        self.ws = w.WebSocket(self.client)

    def close(self):
        self.client.close()
        self.server.close()


class Frames(unittest.TestCase):
    def test_client_frames_are_masked_and_roundtrip(self):
        for n in (0, 5, 125, 126, 70000):
            payload = b"x" * n
            frame = w.encode_frame(w.TEXT, payload)
            self.assertTrue(frame[1] & 0x80)                                   # masked, as RFC 6455 requires of clients
            head = 2 + (0 if n < 126 else 2 if n < 1 << 16 else 8)
            key = frame[head:head + 4]
            self.assertEqual(bytes(b ^ key[i % 4] for i, b in enumerate(frame[head + 4:])), payload)

    def test_reads_a_text_message_of_each_length_class(self):
        p = Pair()
        try:
            for n in (3, 200, 70000):
                p.server.sendall(server_frame(w.TEXT, b"a" * n))
                self.assertEqual(p.ws.recv(), "a" * n)
        finally:
            p.close()

    def test_fragments_are_joined_and_a_ping_between_them_is_answered(self):
        p = Pair()
        try:
            p.server.sendall(server_frame(w.TEXT, b"hel", fin=False) + server_frame(w.PING, b"hi")
                             + server_frame(0, b"lo", fin=False) + server_frame(0, b"!"))
            self.assertEqual(p.ws.recv(), "hello!")
            reply = p.server.recv(100)
            self.assertEqual(reply[0], 0x80 | w.PONG)                          # the pong echoes the ping's payload
            self.assertEqual(reply[1] & 0x7F, 2)
        finally:
            p.close()

    def test_close_raises_closed(self):
        p = Pair()
        try:
            p.server.sendall(server_frame(w.CLOSE, struct.pack(">H", 1000)))
            with self.assertRaises(w.Closed):
                p.ws.recv()
        finally:
            p.close()

    def test_dropped_connection_raises_closed(self):
        p = Pair()
        p.server.close()
        with self.assertRaises(w.Closed):
            p.ws.recv()
        p.client.close()

    def test_protocol_violations_are_refused(self):
        bad = {
            "masked server frame": w.encode_frame(w.TEXT, b"x", mask=True),
            "reserved bits": bytes([0xC1, 1]) + b"x",
            "oversized message": bytes([0x81, 127]) + struct.pack(">Q", w.MAX_MESSAGE + 1),
            "continuation with no start": server_frame(0, b"x"),
            "fragmented control frame": server_frame(w.PING, b"x", fin=False),
            "oversized control frame": bytes([0x89, 126]) + struct.pack(">H", 200),
            "binary message": server_frame(w.BINARY, b"x"),
            "invalid utf-8": server_frame(w.TEXT, b"\xff\xfe"),
        }
        for name, frame in bad.items():
            with self.subTest(name):
                p = Pair()
                try:
                    p.server.sendall(frame)
                    with self.assertRaises(w.WebSocketError):
                        p.ws.recv()
                finally:
                    p.close()

    def test_fragments_together_may_not_exceed_the_limit(self):
        p = Pair()
        try:
            half = w.MAX_MESSAGE // 2 + 1

            def push():
                try:
                    p.server.sendall(server_frame(w.TEXT, b"a" * half, fin=False) + server_frame(0, b"a" * half))
                except OSError:
                    pass                                        # the client gave up first, which is the point
            threading.Thread(target=push, daemon=True).start()
            with self.assertRaises(w.WebSocketError):
                p.ws.recv()
        finally:
            p.close()


class Handshake(unittest.TestCase):
    def serve(self, srv, status="101 Switching Protocols", accept=None, extra=b""):
        req = b""
        while b"\r\n\r\n" not in req:
            req += srv.recv(4096)
        key = next(ln.split(b":", 1)[1].strip() for ln in req.split(b"\r\n") if ln.lower().startswith(b"sec-websocket-key"))
        good = base64.b64encode(hashlib.sha1(key + w.GUID.encode()).digest())
        srv.sendall(f"HTTP/1.1 {status}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                    f"Sec-WebSocket-Accept: {(accept or good).decode()}\r\n\r\n".encode() + extra)

    def run_handshake(self, **kw):
        client, srv = socket.socketpair()
        self.addCleanup(client.close)
        self.addCleanup(srv.close)
        client.settimeout(2)
        srv.settimeout(2)
        t = threading.Thread(target=self.serve, args=(srv,), kwargs=kw, daemon=True)
        t.start()
        try:
            return w.handshake(client, "example.test", "/link")
        finally:
            t.join()

    def test_accepts_a_correct_answer_and_keeps_bytes_sent_right_behind_it(self):
        ws = self.run_handshake(extra=server_frame(w.TEXT, b'{"type":"hello"}'))
        self.assertEqual(ws.recv(), '{"type":"hello"}')

    def test_refuses_a_wrong_accept_key(self):
        with self.assertRaises(w.WebSocketError):
            self.run_handshake(accept=b"AAAA")

    def test_refuses_a_non_101_answer(self):
        with self.assertRaises(w.WebSocketError):
            self.run_handshake(status="403 Forbidden")

    def test_only_wss_urls(self):
        for url in ("ws://example.test/x", "https://example.test/x", "wss:///x"):
            with self.assertRaises(w.WebSocketError):
                w.connect(url)


if __name__ == "__main__":
    unittest.main()
