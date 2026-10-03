"""The reference egress proxy for `net.mode = "egress-allowlist"` (spec/sandbox.md, "Network"). Stdlib only.

The module's process may connect only to this proxy (the sandbox enforces that); the proxy lets through only the
approved hosts:
- `CONNECT host:port` (HTTPS and anything tunnelled) and absolute-URI HTTP requests (`GET http://host/...`);
- a host matches an `allow` entry `host[:port]` (port 443 by default) or `*.domain[:port]` (subdomains only);
- IP literals are refused, and so is a name that resolves to any address that is not globally routable (loopback,
  link-local, private, multicast, reserved): a DNS answer cannot turn an allowed name into a local service.

The agent runs one per job; the conformance kit runs one for the runner suite.
"""
import ipaddress
import re
import select
import socket
import threading

_HOST = re.compile(r"^(\*\.)?([a-z0-9-]+\.)*[a-z0-9-]+$")


def _split(entry: str) -> tuple[str, int]:
    host, _, port = entry.strip().lower().rpartition(":") if ":" in entry else (entry.strip().lower(), "", "443")
    return host, int(port)


def allowed(allow: list[str], host: str, port: int) -> bool:
    host = (host or "").lower().rstrip(".")
    try:
        ipaddress.ip_address(host.strip("[]"))
        return False                                   # never an IP literal
    except ValueError:
        pass
    for e in allow:
        h, p = _split(e)
        if p != port:
            continue
        if h.startswith("*.") and host.endswith(h[1:]) and host != h[2:]:
            return True
        if h == host:
            return True
    return False


def resolve_public(host: str, port: int) -> list:
    """getaddrinfo results, refused when any address is not globally routable."""
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    for *_, sa in infos:
        ip = ipaddress.ip_address(sa[0].split("%")[0])
        if not ip.is_global or ip.is_multicast:          # Python counts multicast as global; the proxy never does
            raise PermissionError(f"{host} resolves to {sa[0]}, which is not a public address")
    return infos


class AllowlistProxy:
    def __init__(self, allow: list[str], host: str = "127.0.0.1", port: int = 0):
        self.allow = list(allow)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((host, port))
        self.port = self.sock.getsockname()[1]
        self.refused: list[str] = []
        self._stop = threading.Event()

    def start(self) -> "AllowlistProxy":
        self.sock.listen(64)
        threading.Thread(target=self._accept, daemon=True, name="egress-proxy").start()
        return self

    def stop(self):
        self._stop.set()
        try:
            self.sock.close()
        except OSError:
            pass

    def __enter__(self):
        return self.start()

    def __exit__(self, *a):
        self.stop()

    def _accept(self):
        while not self._stop.is_set():
            try:
                c, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(c,), daemon=True).start()

    def _deny(self, c, why: str):
        self.refused.append(why)
        try:
            c.sendall(f"HTTP/1.1 403 Forbidden\r\nContent-Length: {len(why)}\r\nConnection: close\r\n\r\n{why}".encode())
        finally:
            c.close()

    def _serve(self, c):
        c.settimeout(30)
        try:
            head = b""
            while b"\r\n\r\n" not in head and len(head) < 16384:
                chunk = c.recv(4096)
                if not chunk:
                    return c.close()
                head += chunk
            line = head.split(b"\r\n", 1)[0].decode("latin-1")
            method, target, _ = (line.split(" ") + ["", ""])[:3]
            if method == "CONNECT":
                host, _, port = target.rpartition(":")
                port, rest = int(port or 443), head.split(b"\r\n\r\n", 1)[1]
                prefix = b"HTTP/1.1 200 Connection established\r\n\r\n"
            elif target.startswith("http://"):
                hostport = target[7:].split("/", 1)[0]
                host, _, port = hostport.partition(":")
                port, rest, prefix = int(port or 80), head, b""
            else:
                return self._deny(c, "only CONNECT and absolute http:// requests")
            host = host.strip("[]")
            if not allowed(self.allow, host, port):
                return self._deny(c, f"{host}:{port} is not in the module's allow list")
            try:
                infos = resolve_public(host, port)
                up = socket.create_connection(infos[0][4][:2], timeout=30)
            except (OSError, PermissionError) as e:
                return self._deny(c, str(e)[:200])
            if prefix:
                c.sendall(prefix)
            if rest:
                up.sendall(rest)
            self._pipe(c, up)
        except (OSError, ValueError):
            c.close()

    @staticmethod
    def _pipe(a, b):
        a.settimeout(None)
        b.settimeout(None)
        socks = [a, b]
        try:
            while True:
                r, _, _ = select.select(socks, [], [], 300)
                if not r:
                    return
                for s in r:
                    data = s.recv(65536)
                    if not data:
                        return
                    (b if s is a else a).sendall(data)
        finally:
            a.close()
            b.close()
