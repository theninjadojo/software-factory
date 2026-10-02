"""CONNECT-only egress proxy on a unix socket. Sandboxes have no network; this socket is
their only way out, and it only tunnels TLS to allowlisted hostnames on port 443."""
import asyncio
import logging
import os
import sys
import time

log = logging.getLogger("factory.proxy")


def host_allowed(host: str, port: int, allow: tuple[str, ...]) -> bool:
    return port == 443 and host.lower().rstrip(".") in {h.lower() for h in allow}


async def _pipe(r, w):
    try:
        while data := await r.read(65536):
            w.write(data)
            await w.drain()
    except Exception:
        pass
    finally:
        w.close()


async def handle(reader, writer, allow_source):
    try:
        parts = (await reader.readline()).decode("latin1").split()
        while (await reader.readline()) not in (b"\r\n", b"\n", b""):
            pass
        if len(parts) < 3 or parts[0] != "CONNECT":
            writer.write(b"HTTP/1.1 405 Method Not Allowed\r\n\r\n")
            return
        host, _, port = parts[1].rpartition(":")
        allow = allow_source() if callable(allow_source) else allow_source
        if not port.isdigit() or not host_allowed(host, int(port), allow):
            log.warning("DENY %s", parts[1])
            writer.write(b"HTTP/1.1 403 Forbidden\r\n\r\n")
            return
        r2, w2 = await asyncio.wait_for(asyncio.open_connection(host, 443), 10)
        writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
        await writer.drain()
        await asyncio.gather(_pipe(reader, w2), _pipe(r2, writer))
    except Exception as e:
        log.info("proxy error: %s", e)
    finally:
        writer.close()


async def serve(sock: str, allow):
    os.makedirs(os.path.dirname(sock), exist_ok=True)
    if os.path.exists(sock):
        os.unlink(sock)
    server = await asyncio.start_unix_server(lambda r, w: handle(r, w, allow), path=sock)
    os.chmod(sock, 0o600)
    log.info("proxy listening on %s, allow=%s", sock, allow() if callable(allow) else allow)
    async with server:
        await server.serve_forever()


def dynamic_allowlist(path: str, first):
    """The allowlist, re-read (at most every few seconds) when the config or its UI overrides change. A bad reload keeps the
    last good list, so a half-saved config can never open the proxy up."""
    from .config import allowed_hosts, load, overrides_path
    state = {"sig": None, "hosts": first, "checked": 0.0}

    def signature():
        ov = overrides_path(path)
        return (os.path.getmtime(path), os.path.getmtime(ov) if ov.exists() else None)

    def current():
        now = time.time()
        if now - state["checked"] >= 5:
            state["checked"] = now
            try:
                sig = signature()
                if sig != state["sig"]:
                    state["sig"], new = sig, allowed_hosts(load(path))
                    if new != state["hosts"]:
                        log.info("allowlist changed: %s", new)
                    state["hosts"] = new
            except Exception:
                log.exception("could not reload the allowlist; keeping the previous one")
        return state["hosts"]

    return current


def main():
    from .config import allowed_hosts, load
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    path = sys.argv[1] if len(sys.argv) > 1 else "/srv/factory/config.toml"
    cfg = load(path)
    asyncio.run(serve(cfg.runner.proxy_socket, dynamic_allowlist(path, allowed_hosts(cfg))))


if __name__ == "__main__":
    main()
