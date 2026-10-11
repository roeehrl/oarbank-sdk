"""publicecho's `echo` service (service protocol 1, an endpoint service that serves the `echo` listener). Its state is
files in the module's data directory: echo.up (running), echo.ready (the daemon said hello and accepts), echo.log (the
daemon's output).

`start` leaves a daemon running (`serve`) that inherits the endpoint channel and says hello. The agent then hands it
every connection the `echo` listener accepts from the internet, and announces the listener's external address on the
same channel. The daemon answers each line a client sends with `echo <line> <external>` (`-` while no address is
announced) and never listens itself. When the agent closes the channel (it stops the service) the daemon exits.

The manifest's listener has handover `connections`. Switched to `listen_fd` (macOS and Linux), the agent passes the
daemon a listening socket instead (OARBANK_LISTEN_FD_ECHO): the daemon accepts on it, reads the PROXY v2 header the agent
writes first, and answers the same way. Stdlib and the SDK only."""
import json
import os
import socket
import subprocess
import sys
import threading
from pathlib import Path

from oarbank_sdk import service_endpoint as ep

DATA = Path(os.environ.get("OARBANK_MODULE_DATA", "."))
UP, READY, LOG = (DATA / n for n in ("echo.up", "echo.ready", "echo.log"))
MAX_LINE = 1024


def log(text: str):
    print(text, flush=True)                              # stdout is echo.log


def lines(conn):
    """Each line a client sends, without its end of line; stops at end of file or a line that is too long."""
    buf = b""
    while True:
        while b"\n" not in buf:
            if len(buf) > MAX_LINE:
                return
            try:
                chunk = conn.recv(4096)
            except OSError:
                return
            if not chunk:
                return
            buf += chunk
        line, _, buf = buf.partition(b"\n")
        yield line.rstrip(b"\r").decode("utf-8", "replace")


def serve_one(acc: ep.Acceptor, conn, origin: ep.Origin):
    try:
        if origin.listener != "echo":                    # this service provides no pool, so jobs never connect
            return
        for line in lines(conn):
            conn.sendall(f"echo {line} {acc.external('echo') or '-'}\n".encode())
    except OSError:
        pass                                             # the client went away
    finally:
        try:
            conn.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        conn.close()


def accept_inherited(acc: ep.Acceptor, sock):
    """handover = "listen_fd": accept on the socket the agent listens on; each connection starts with a PROXY v2 header."""
    while True:
        try:
            conn, _ = sock.accept()
        except OSError:
            return

        def one(conn=conn):
            try:
                src, _ = ep.read_proxy_v2(conn)
            except (OSError, ep.EndpointError):
                conn.close()
                return
            serve_one(acc, conn, ep.Origin(conn=0, listener="echo", peer=f"{src[0]}:{src[1]}" if src else None))
        threading.Thread(target=one, daemon=True).start()


def serve() -> int:
    acc = ep.Acceptor(on_listener=lambda name, ann: log(f"listener {name}: {ann.get('state')} at {ann.get('external')}"))
    if os.environ.get("OARBANK_LISTEN_FD_ECHO"):
        threading.Thread(target=accept_inherited, args=(acc, ep.listen_socket("echo")), daemon=True).start()
    READY.write_text(str(os.getpid()), encoding="utf-8")
    try:
        for conn, origin in acc:                         # ends when the agent closes the channel
            threading.Thread(target=serve_one, args=(acc, conn, origin), daemon=True).start()
    finally:
        for p in (READY, UP):
            p.unlink(missing_ok=True)
    return 0


def emit(doc: dict) -> int:
    print(json.dumps(doc))
    return 0


def main(op: str) -> int:
    if op == "serve":
        return serve()
    if op == "fingerprint":
        return emit({"service_protocol": {"supported": [1]}, "health": "healthy", "pools": {}, "running": UP.exists()})
    if op == "start":
        if not UP.exists():
            UP.write_text("1", encoding="utf-8")
            READY.unlink(missing_ok=True)
            # stdin stays the one `start` inherited from the agent: some Windows builds refuse an AppContainer the null
            # device, so subprocess.DEVNULL fails there
            with LOG.open("ab") as out:
                subprocess.Popen([sys.executable, "-I", os.path.abspath(__file__), "serve"], stdout=out, stderr=out,
                                 **ep.inherit_channel())
        return emit({"ok": True})
    if op == "stop":
        for p in (UP, READY):
            p.unlink(missing_ok=True)
        return emit({"ok": True})                      # the agent then closes the channel and ends the daemon's group
    if op == "status":
        return emit({"running": UP.exists(), "ready": READY.exists()})
    if op == "ready":
        return emit({"running": UP.exists(), "ready": UP.exists() and READY.exists()})
    if op == "list_owned":
        return emit({"objects": []})
    if op == "destroy":
        return emit({"ok": True})
    return 64


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else ""))
