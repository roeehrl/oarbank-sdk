"""Origins: public https URLs a dataset file can be fetched from (spec/module-protocol.md, `datasets.create`).

A dataset file names its content by sha256 and size, so an origin can only ever serve those bytes: whoever fetches it
(the agent, or the coordinator when every origin failed for a node) checks the digest. Who may be fetched from is the
URL rule below, plus the operator's origin host policy (`patterns`, as in `[sandbox].net.allow`); at fetch time the
fetcher also refuses names that resolve to non-public addresses.
"""
import fnmatch
import ipaddress
import re
from urllib.parse import urlsplit

DIGEST = re.compile(r"^[0-9a-f]{64}$")
MAX_ORIGINS = 8
MAX_URL = 2048
_LABEL = re.compile(r"^[A-Za-z0-9-]{1,63}$")


def url_problem(url) -> str | None:
    """Why `url` is not an origin (None: it is): https only, a DNS name with a dot (never an IP literal, never
    localhost), no user info, at most 2048 characters."""
    if not isinstance(url, str) or not url or len(url) > MAX_URL:
        return f"an origin is an https URL of at most {MAX_URL} characters"
    try:
        u = urlsplit(url)
        port = u.port
    except ValueError as e:
        return f"not a URL: {e}"
    if u.scheme != "https":
        return "only https origins"
    if u.username is not None or u.password is not None:
        return "no user info in an origin"
    host = (u.hostname or "").rstrip(".")
    if not host:
        return "an origin needs a host"
    try:
        ipaddress.ip_address(host.strip("[]"))
        return "an origin names a host, never an IP address"
    except ValueError:
        pass
    labels = host.split(".")
    if len(labels) < 2 or not all(_LABEL.match(x) for x in labels) or host.lower().endswith(("localhost", ".local")):
        return f"{host!r} is not a public DNS name"
    if port == 0:
        return "port 0"
    return None


def host_port(url: str) -> str:
    """`host` or `host:port` (a non-443 port) of an origin, lowercase: what the origin host policy matches."""
    u = urlsplit(url)
    host = (u.hostname or "").rstrip(".").lower()
    return host if u.port in (None, 443) else f"{host}:{u.port}"


def allowed(url: str, patterns: list[str]) -> bool:
    """Whether the operator's origin host policy admits `url`: no patterns admit every origin the URL rule admits; else a
    pattern `host`, `*.domain` (subdomains of it) or either with `:port` (port 443 when absent) must match."""
    if not patterns:
        return True
    hp = host_port(url)
    host, _, port = hp.partition(":")
    port = port or "443"
    for p in patterns:
        ph, _, pport = p.lower().partition(":")
        if (pport or "443") != port:
            continue
        if ph == host or (ph.startswith("*.") and fnmatch.fnmatchcase(host, ph) and host.count(".") >= ph.count(".")):
            return True
    return False


def file_problem(f) -> str | None:
    """Why a dataset file entry `{path, digest, size, origins?}` is malformed (None: it is not). Whether its blob is held
    and visible is the host's question."""
    from . import portable
    if not isinstance(f, dict):
        return "a file is an object {path, digest, size, origins?}"
    try:
        portable.check_portable_path(f.get("path"))
    except (ValueError, TypeError) as e:
        return f"path {f.get('path')!r}: {e}"
    if not DIGEST.match(str(f.get("digest") or "")):
        return f"{f.get('path')}: digest must be 64 lowercase hex digits"
    size = f.get("size")
    if not isinstance(size, int) or isinstance(size, bool) or size < 0:
        return f"{f.get('path')}: size (bytes, an integer) is required"
    origins = f.get("origins")
    if origins is None:
        return None
    if not isinstance(origins, list) or not 1 <= len(origins) <= MAX_ORIGINS:
        return f"{f.get('path')}: origins is a list of 1 to {MAX_ORIGINS} URLs"
    for o in origins:
        why = url_problem(o)
        if why:
            return f"{f.get('path')}: origin {o!r}: {why}"
    return None
