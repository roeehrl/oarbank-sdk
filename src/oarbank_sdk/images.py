"""Container image sets approved by signature (spec/sandbox.md, "Image sets"): the reference policy.

A set names a registry and repository prefix, a platform and a pinned cosign public key (ECDSA P-256). An image
`<registry>/<repository>@sha256:<hex>` is a member when it lies under the prefix and, without an index, its digest
carries a cosign signature by the key; with an index, the set's signed index lists the digest. The agent implements
the same decisions in Rust (oarbank-core `images.rs`); spec/vectors/image-signatures.json holds both to them.

Two cosign signature formats are read, both offline with the key alone (no transparency log):
- a Sigstore bundle (cosign 3's default): an OCI referrer of the image whose layer is a bundle with a DSSE envelope
  over an in-toto statement (predicate https://sigstore.dev/cosign/sign/v1) naming the digest;
- simple signing (cosign 2's default): the manifest at tag `sha256-<hex>.sig`, a payload layer naming the digest with
  the signature in its `dev.cosignproject.cosign/signature` annotation.

Everything here is the standard library: ECDSA P-256 verification is a few lines of modular arithmetic, and the
registry client speaks the OCI distribution API's reads (or reads an OCI image layout directory).

    pol = images.SetPolicy(man.sandbox.container_sets[0], key_pem)
    v = pol.verify("ghcr.io/org/tasks/t1@sha256:...", "linux/amd64", images.Registry())
    if not v.ok: print(v.reason)
"""
import base64
import hashlib
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
REF = re.compile(r"^(?P<name>[a-z0-9][a-z0-9._/:-]*?)(?::(?P<tag>[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}))?"
                 r"(?:@(?P<digest>sha256:[0-9a-f]{64}))?$")
SIG_ANNOTATION = "dev.cosignproject.cosign/signature"
SIMPLE_SIGNING = "application/vnd.dev.cosign.simplesigning.v1+json"
SIMPLE_TYPE = "cosign container image signature"
BUNDLE_TYPES = ("application/vnd.dev.sigstore.bundle.v0.3+json",)
INTOTO_PAYLOAD = "application/vnd.in-toto+json"
INTOTO_STATEMENT = "https://in-toto.io/Statement/v1"
COSIGN_PREDICATE = "https://sigstore.dev/cosign/sign/v1"
INDEX_TYPE = "application/vnd.oarbank.image-set.v1+json"
INDEX_DOC_TYPE = "oarbank.image-set/v1"
OCI_MANIFEST = "application/vnd.oci.image.manifest.v1+json"
OCI_INDEX = "application/vnd.oci.image.index.v1+json"
OCI_EMPTY = "application/vnd.oci.empty.v1+json"
ACCEPT = ", ".join([OCI_MANIFEST, OCI_INDEX, "application/vnd.docker.distribution.manifest.v2+json",
                    "application/vnd.docker.distribution.manifest.list.v2+json"])
MAX_DOC = 4 << 20                 # manifests, signature payloads, bundles and index documents


class ImageError(ValueError):
    pass


# ---------------------------------------------------------------------------- references

def normalize(reference: str) -> tuple[str, str | None, str | None]:
    """(repository with its registry, tag, digest) as Docker resolves a reference: `org/tool` is
    `docker.io/org/tool`, `tool` is `docker.io/library/tool`, `index.docker.io` is `docker.io`."""
    m = REF.match(reference or "")
    if not m:
        raise ImageError(f"{reference!r} is not an image reference")
    name = m.group("name")
    first = name.split("/", 1)[0]
    if "/" in name and ("." in first or ":" in first or first == "localhost"):
        repo = name
    elif "/" in name:
        repo = "docker.io/" + name
    else:
        repo = "docker.io/library/" + name
    if repo.startswith("index.docker.io/"):
        repo = "docker.io/" + repo[len("index.docker.io/"):]
    return repo, m.group("tag"), m.group("digest")


def signature_tag(digest: str) -> str:
    """The simple-signing tag of a digest: `sha256-<hex>.sig`."""
    return digest.replace(":", "-") + ".sig"


# ---------------------------------------------------------------------------- ECDSA P-256

_P = 0xFFFFFFFF00000001000000000000000000000000FFFFFFFFFFFFFFFFFFFFFFFF
_A = _P - 3
_B = 0x5AC635D8AA3A93E7B3EBBD55769886BC651D06B0CC53B0F63BCE3C3E27D2604B
_N = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551
_G = (0x6B17D1F2E12C4247F8BCE6E563A440F277037D812DEB33A0F4A13945D898C296,
      0x4FE342E2FE1A7F9B8EE7EB4A7C0F9E162BCE33576B315ECECBB6406837BF51F5)
_SPKI_P256 = bytes.fromhex("3059301306072a8648ce3d020106082a8648ce3d030107034200")


def _add(p, q):
    """Affine point addition on P-256 (None is the point at infinity)."""
    if p is None:
        return q
    if q is None:
        return p
    if p[0] == q[0] and (p[1] + q[1]) % _P == 0:
        return None
    if p == q:
        lam = (3 * p[0] * p[0] + _A) * pow(2 * p[1], -1, _P) % _P
    else:
        lam = (q[1] - p[1]) * pow(q[0] - p[0], -1, _P) % _P
    x = (lam * lam - p[0] - q[0]) % _P
    return x, (lam * (p[0] - x) - p[1]) % _P


def _mul(k: int, p):
    out = None
    while k:
        if k & 1:
            out = _add(out, p)
        p = _add(p, p)
        k >>= 1
    return out


def _on_curve(q) -> bool:
    x, y = q
    return 0 <= x < _P and 0 <= y < _P and (y * y - (x * x * x + _A * x + _B)) % _P == 0


def public_key(pem: str) -> tuple[int, int]:
    """The point of a PEM `PUBLIC KEY` (SPKI) holding an uncompressed ECDSA P-256 key, as cosign writes cosign.pub."""
    der = spki_der(pem)
    if len(der) != 91 or not der.startswith(_SPKI_P256) or der[26] != 4:
        raise ImageError("the key is not an uncompressed ECDSA P-256 public key (cosign.pub)")
    q = int.from_bytes(der[27:59], "big"), int.from_bytes(der[59:91], "big")
    if not _on_curve(q):
        raise ImageError("the key's point is not on P-256")
    return q


def spki_der(pem: str) -> bytes:
    m = re.search(r"-----BEGIN PUBLIC KEY-----(.+?)-----END PUBLIC KEY-----", pem or "", re.S)
    if not m:
        raise ImageError("the key is not a PEM PUBLIC KEY")
    try:
        return base64.b64decode("".join(m.group(1).split()), validate=True)
    except ValueError as e:
        raise ImageError(f"the key's PEM body is not base64: {e}") from None


def key_sha256(pem: str) -> str:
    """The key's fingerprint as approval shows it: SHA-256 of its DER (SPKI)."""
    public_key(pem)
    return hashlib.sha256(spki_der(pem)).hexdigest()


def _der_signature(sig: bytes) -> tuple[int, int]:
    """(r, s) of an ASN.1 DER ECDSA signature, strictly: SEQUENCE { INTEGER r, INTEGER s }, minimal lengths."""
    def integer(b: bytes, i: int) -> tuple[int, int]:
        if i + 2 > len(b) or b[i] != 0x02 or b[i + 1] > 33 or b[i + 1] == 0:
            raise ImageError("bad signature encoding")
        n = b[i + 1]
        v = b[i + 2:i + 2 + n]
        if len(v) != n or v[0] & 0x80 or (n > 1 and v[0] == 0 and not v[1] & 0x80):
            raise ImageError("bad signature encoding")
        return int.from_bytes(v, "big"), i + 2 + n
    if len(sig) < 8 or sig[0] != 0x30 or sig[1] != len(sig) - 2:
        raise ImageError("bad signature encoding")
    r, i = integer(sig, 2)
    s, i = integer(sig, i)
    if i != len(sig):
        raise ImageError("bad signature encoding")
    return r, s


def verify_ecdsa(q: tuple[int, int], message: bytes, sig_der: bytes) -> bool:
    """ECDSA P-256 with SHA-256 over `message`, the signature in ASN.1 DER (cosign's encoding)."""
    try:
        r, s = _der_signature(sig_der)
    except ImageError:
        return False
    if not (1 <= r < _N and 1 <= s < _N):
        return False
    e = int.from_bytes(hashlib.sha256(message).digest(), "big")
    w = pow(s, -1, _N)
    pt = _add(_mul(e * w % _N, _G), _mul(r * w % _N, q))
    return pt is not None and pt[0] % _N == r


# ---------------------------------------------------------------------------- signature formats

def _b64(s, what: str) -> bytes:
    try:
        return base64.b64decode(s or "", validate=True)
    except (ValueError, TypeError):
        raise ImageError(f"{what} is not base64") from None


def check_simple_signing(q, payload: bytes, signature_b64: str, digest: str, covers) -> str | None:
    """Why a simple-signing payload and signature do not approve `digest` (None: they do). `covers(repository)` says
    whether the payload's docker-reference lies in the set."""
    try:
        sig = _b64(signature_b64, "the signature")
    except ImageError as e:
        return str(e)
    if not verify_ecdsa(q, payload, sig):
        return "the signature does not verify with the set's key"
    try:
        doc = json.loads(payload)
        crit = doc["critical"]
        got, kind, ref = crit["image"]["docker-manifest-digest"], crit["type"], crit["identity"]["docker-reference"]
    except (ValueError, KeyError, TypeError):
        return "the signed payload is not a cosign simple-signing document"
    if kind != SIMPLE_TYPE:
        return f"the signed payload's type is {kind!r}, not {SIMPLE_TYPE!r}"
    if got != digest:
        return f"the signature is for {got}, not {digest}"
    try:
        repo = normalize(ref)[0]
    except ImageError:
        return f"the signed docker-reference {ref!r} is not a reference"
    if not covers(repo):
        return f"the signed docker-reference {ref!r} is outside the set"
    return None


def pae(payload_type: str, body: bytes) -> bytes:
    """DSSE's pre-authentication encoding: what the signature covers."""
    t = payload_type.encode()
    return b"DSSEv1 %d %s %d %s" % (len(t), t, len(body), body)


def check_bundle(q, bundle: bytes, digest: str) -> str | None:
    """Why a Sigstore bundle does not approve `digest` (None: it does): a DSSE envelope signed by the key over an in-toto
    statement whose predicate is cosign's signature predicate and whose subject names the digest."""
    try:
        env = json.loads(bundle)["dsseEnvelope"]
        ptype, payload, sigs = env["payloadType"], env["payload"], env["signatures"]
    except (ValueError, KeyError, TypeError):
        return "the bundle has no DSSE envelope"
    if not isinstance(ptype, str) or not isinstance(payload, str) or not isinstance(sigs, list):
        return "the bundle has no DSSE envelope"
    try:
        body = _b64(payload, "the DSSE payload")
    except ImageError as e:
        return str(e)
    if ptype != INTOTO_PAYLOAD:
        return f"the DSSE payload type is {ptype!r}, not {INTOTO_PAYLOAD!r}"
    msg = pae(ptype, body)
    def verifies(sig) -> bool:
        try:
            return verify_ecdsa(q, msg, _b64(sig, "a DSSE signature"))
        except ImageError:
            return False
    if not any(verifies(s.get("sig")) for s in sigs if isinstance(s, dict)):
        return "no DSSE signature verifies with the set's key"
    try:
        st = json.loads(body)
        stype, pred, subjects = st["_type"], st["predicateType"], st["subject"]
    except (ValueError, KeyError, TypeError):
        return "the DSSE payload is not an in-toto statement"
    if stype != INTOTO_STATEMENT or pred != COSIGN_PREDICATE:
        return f"the statement is {stype} {pred}, not a cosign signature ({COSIGN_PREDICATE})"
    hexd = digest.split(":", 1)[1]
    if not any(isinstance(s, dict) and (s.get("digest") or {}).get("sha256") == hexd for s in subjects):
        return f"the statement's subject is not {digest}"
    return None


def index_document(registry: str, repository: str, seq: int, digests) -> bytes:
    """The layer of a signed image index (canonical JSON, digests sorted and unique)."""
    ds = sorted(set(digests))
    bad = [d for d in ds if not DIGEST.match(d)]
    if bad:
        raise ImageError(f"not sha256 digests: {bad[:3]}")
    doc = {"type": INDEX_DOC_TYPE, "registry": registry, "repository": repository, "seq": int(seq), "images": ds}
    return json.dumps(doc, sort_keys=True, separators=(",", ":")).encode()


def parse_index(doc: bytes, registry: str, repository: str) -> tuple[int, frozenset]:
    """(seq, digests) of an image index layer for this set; raises ImageError when it is not one."""
    try:
        d = json.loads(doc)
        kind, reg, repo, seq, imgs = d["type"], d["registry"], d["repository"], d["seq"], d["images"]
    except (ValueError, KeyError, TypeError):
        raise ImageError("the index is not an oarbank image-set document") from None
    if kind != INDEX_DOC_TYPE:
        raise ImageError(f"the index type is {kind!r}, not {INDEX_DOC_TYPE!r}")
    if (reg, repo) != (registry, repository):
        raise ImageError(f"the index is for {reg}/{repo}, not this set ({registry}/{repository})")
    if not isinstance(seq, int) or isinstance(seq, bool) or seq < 0:
        raise ImageError("the index seq is not a non-negative integer")
    if not isinstance(imgs, list) or not all(isinstance(x, str) and DIGEST.match(x) for x in imgs):
        raise ImageError("the index images are not sha256 digests")
    return seq, frozenset(imgs)


# ---------------------------------------------------------------------------- where artifacts come from

class Source:
    """Read access to images and their referrers: a registry or an OCI image layout."""

    def manifest(self, repository: str, ref: str) -> tuple[bytes, str] | None:
        """(manifest bytes, its digest) by tag or digest; None when absent."""
        raise NotImplementedError

    def blob(self, repository: str, digest: str) -> bytes:
        raise NotImplementedError

    def referrers(self, repository: str, digest: str) -> list[dict] | None:
        """The descriptors of manifests whose subject is `digest`; None when the source has no referrers API."""
        raise NotImplementedError


def _checked(data: bytes, digest: str, what: str) -> bytes:
    if len(data) > MAX_DOC:
        raise ImageError(f"{what} is larger than {MAX_DOC} bytes")
    if "sha256:" + hashlib.sha256(data).hexdigest() != digest:
        raise ImageError(f"{what} does not match its digest {digest}")
    return data


class Registry(Source):
    """The OCI distribution API's reads, anonymously (a bearer token when the registry asks for one). `https`, except
    for registries on localhost or a loopback address. Redirects are followed only on the same host or to https."""

    def __init__(self, timeout: float = 30):
        self.timeout = timeout
        self.tokens: dict[str, str] = {}

    @staticmethod
    def _base(registry: str) -> str:
        host = registry.rsplit(":", 1)[0] if registry.count(":") == 1 else registry
        scheme = "http" if host in ("localhost", "127.0.0.1", "[::1]") else "https"
        return f"{scheme}://{'registry-1.docker.io' if registry == 'docker.io' else registry}"

    def _get(self, registry: str, path: str, accept: str | None = None, retry: bool = True) -> tuple[int, bytes, dict]:
        req = urllib.request.Request(self._base(registry) + path, headers={"Accept": accept} if accept else {})
        if registry in self.tokens:
            req.add_header("Authorization", f"Bearer {self.tokens[registry]}")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return r.status, r.read(MAX_DOC + 1), dict(r.headers)
        except urllib.error.HTTPError as e:
            if e.code == 401 and retry and self._login(registry, e.headers.get("WWW-Authenticate") or ""):
                return self._get(registry, path, accept, retry=False)
            return e.code, b"", dict(e.headers or {})

    def _login(self, registry: str, challenge: str) -> bool:
        if not challenge.lower().startswith("bearer "):
            return False
        params = dict(re.findall(r'(\w+)="([^"]*)"', challenge))
        realm = params.pop("realm", None)
        if not realm or not realm.startswith("https://") and not realm.startswith(self._base(registry)):
            return False
        try:
            with urllib.request.urlopen(realm + "?" + urllib.parse.urlencode(params), timeout=self.timeout) as r:
                doc = json.loads(r.read(1 << 20))
        except (urllib.error.URLError, ValueError):
            return False
        tok = doc.get("token") or doc.get("access_token")
        if tok:
            self.tokens[registry] = tok
        return bool(tok)

    @staticmethod
    def _split(repository: str) -> tuple[str, str]:
        reg, _, path = repository.partition("/")
        return reg, path

    def manifest(self, repository, ref):
        reg, path = self._split(repository)
        code, body, headers = self._get(reg, f"/v2/{path}/manifests/{ref}", ACCEPT)
        if code == 404:
            return None
        if code != 200:
            raise ImageError(f"{repository}:{ref}: the registry answered {code}")
        digest = "sha256:" + hashlib.sha256(body).hexdigest()
        if DIGEST.match(ref) and digest != ref:
            raise ImageError(f"{repository}@{ref}: the registry served other content")
        return _checked(body, digest, f"manifest {repository}:{ref}"), digest

    def blob(self, repository, digest):
        reg, path = self._split(repository)
        code, body, _ = self._get(reg, f"/v2/{path}/blobs/{digest}")
        if code != 200:
            raise ImageError(f"blob {repository}@{digest}: the registry answered {code}")
        return _checked(body, digest, f"blob {digest}")

    def referrers(self, repository, digest):
        reg, path = self._split(repository)
        code, body, _ = self._get(reg, f"/v2/{path}/referrers/{digest}", OCI_INDEX)
        if code != 200:
            return None
        try:
            return list(json.loads(body).get("manifests") or [])
        except ValueError:
            return None


class Layout(Source):
    """An OCI image layout directory (index.json, blobs/sha256/…), such as `oras copy --to-oci-layout` or
    `cosign save` write. Tags are the `org.opencontainers.image.ref.name` annotations, as `<repository>:<tag>` or a
    bare tag; referrers are found by scanning manifests for a subject."""

    def __init__(self, root):
        self.root = Path(root)
        self.index = json.loads((self.root / "index.json").read_text())

    def _read(self, digest: str) -> bytes:
        p = self.root / "blobs" / "sha256" / digest.split(":", 1)[1]
        if not p.is_file():
            raise ImageError(f"blob {digest} is not in the layout")
        return _checked(p.read_bytes(), digest, f"blob {digest}")

    def manifest(self, repository, ref):
        if DIGEST.match(ref):
            p = self.root / "blobs" / "sha256" / ref.split(":", 1)[1]
            return (self._read(ref), ref) if p.is_file() else None
        for d in self.index.get("manifests") or []:
            name = (d.get("annotations") or {}).get("org.opencontainers.image.ref.name")
            if name in (ref, f"{repository}:{ref}"):
                return self._read(d["digest"]), d["digest"]
        return None

    def blob(self, repository, digest):
        return self._read(digest)

    def referrers(self, repository, digest):
        out = []
        for p in sorted((self.root / "blobs" / "sha256").iterdir()):
            if p.stat().st_size > MAX_DOC:
                continue
            try:
                m = json.loads(p.read_bytes())
            except ValueError:
                continue
            if isinstance(m, dict) and (m.get("subject") or {}).get("digest") == digest:
                out.append({"mediaType": m.get("mediaType"), "digest": "sha256:" + p.name,
                            "artifactType": m.get("artifactType") or (m.get("config") or {}).get("mediaType")})
        return out


# ---------------------------------------------------------------------------- the policy

@dataclass
class Verdict:
    ok: bool
    reason: str = ""
    seq: int | None = None          # the index seq this verdict relied on

    @property
    def code(self) -> str:
        return "" if self.ok else "image_not_approved"


def _signatures(src: Source, repository: str, digest: str):
    """Yield ("bundle", bytes) and ("simple", (payload, signature)) candidates for `digest` in `repository`."""
    refs = src.referrers(repository, digest)
    if refs is None:                                  # no referrers API: the referrers tag scheme
        got = src.manifest(repository, digest.replace(":", "-"))
        refs = list(json.loads(got[0]).get("manifests") or []) if got else []
    for d in refs:
        if d.get("artifactType") in BUNDLE_TYPES:
            got = src.manifest(repository, d["digest"])
            if not got:
                continue
            for layer in json.loads(got[0]).get("layers") or []:
                if layer.get("mediaType") in BUNDLE_TYPES:
                    yield "bundle", src.blob(repository, layer["digest"])
    got = src.manifest(repository, signature_tag(digest))
    if got:
        for layer in json.loads(got[0]).get("layers") or []:
            sig = (layer.get("annotations") or {}).get(SIG_ANNOTATION)
            if layer.get("mediaType") == SIMPLE_SIGNING and sig:
                yield "simple", (src.blob(repository, layer["digest"]), sig)


class SetPolicy:
    """One container set with its key: decides whether an image reference is a member."""

    def __init__(self, cset, key_pem: str):
        self.set = cset
        self.q = public_key(key_pem)

    def signed(self, src: Source, repository: str, digest: str) -> str | None:
        """Why `digest` in `repository` carries no signature by the key (None: it does)."""
        why = []
        for kind, art in _signatures(src, repository, digest):
            r = check_bundle(self.q, art, digest) if kind == "bundle" else \
                check_simple_signing(self.q, art[0], art[1], digest, lambda repo: self.set.covers(repo))
            if r is None:
                return None
            why.append(r)
        return why[0] if why else f"{repository}@{digest} has no cosign signature"

    def verify(self, reference: str, platform: str, src: Source, highest_seq: int | None = None) -> Verdict:
        try:
            repo, _, digest = normalize(reference)
        except ImageError as e:
            return Verdict(False, str(e))
        if not digest:
            return Verdict(False, f"{reference} is not pinned by digest")
        if platform != self.set.platform or not self.set.covers(repo):
            return Verdict(False, f"{reference} ({platform}) is outside set {self.set.name}")
        try:
            if not self.set.index:
                why = self.signed(src, repo, digest)
                return Verdict(why is None, why or "")
            irepo, itag, _ = normalize(self.set.index)
            got = src.manifest(irepo, itag or "latest")
            if not got:
                return Verdict(False, f"the set's index {self.set.index} is not in the registry")
            body, idigest = got
            why = self.signed(src, irepo, idigest)
            if why:
                return Verdict(False, f"the set's index: {why}")
            layers = [x for x in json.loads(body).get("layers") or [] if x.get("mediaType") == INDEX_TYPE]
            if len(layers) != 1:
                return Verdict(False, "the set's index has no image-set layer")
            seq, members = parse_index(src.blob(irepo, layers[0]["digest"]), self.set.registry, self.set.repository)
        except ImageError as e:
            return Verdict(False, str(e))
        if highest_seq is not None and seq < highest_seq:
            return Verdict(False, f"the set's index is seq {seq}, older than seq {highest_seq} already accepted", seq)
        if digest not in members:
            return Verdict(False, f"{digest} is not in the set's index (seq {seq})", seq)
        return Verdict(True, "", seq)


def load_key(bundle_root, cset) -> str:
    """A set's key from the bundle, checked to be one ECDSA P-256 public key."""
    p = Path(bundle_root) / cset.key
    if not p.is_file():
        raise ImageError(f"container set {cset.name}: key {cset.key} is not in the bundle")
    pem = p.read_text(encoding="utf-8")
    public_key(pem)
    return pem


def source_for(spec: str | os.PathLike | None) -> Source:
    """A Layout for a directory path, else the network Registry."""
    return Layout(spec) if spec else Registry()
