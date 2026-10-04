"""Signed test images without cosign or a registry: OCI image layouts holding tiny images, cosign-format signatures
(both formats `oarbank_sdk.images` reads) and signed image indexes. For module tests and conformance fixtures; a
module publishes real images with `cosign sign --key` and `oras push`.

    key = imagetest.Key.from_seed(b"tasks")
    lay = imagetest.LayoutWriter(tmp / "images")
    digest = lay.image("ghcr.io/org/tasks/t1", b"task one")
    lay.sign(key, "ghcr.io/org/tasks/t1", digest)              # a Sigstore bundle referrer (cosign 3)
    lay.sign(key, "ghcr.io/org/tasks/t1", digest, simple=True) # the sha256-<hex>.sig manifest (cosign 2)
    key.public_pem()                                           # cosign.pub
"""
import base64
import hashlib
import json
from pathlib import Path

from . import images as I


class Key:
    """An ECDSA P-256 key pair for tests (deterministic nonces: never for real signing)."""

    def __init__(self, d: int):
        if not 1 <= d < I._N:
            raise ValueError("bad private scalar")
        self.d = d
        self.q = I._mul(d, I._G)

    @classmethod
    def from_seed(cls, seed: bytes) -> "Key":
        return cls(int.from_bytes(hashlib.sha256(b"oarbank-imagetest-key" + seed).digest(), "big") % (I._N - 1) + 1)

    def public_pem(self) -> str:
        der = I._SPKI_P256 + b"\x04" + self.q[0].to_bytes(32, "big") + self.q[1].to_bytes(32, "big")
        b = base64.b64encode(der).decode()
        return "-----BEGIN PUBLIC KEY-----\n" + "\n".join(b[i:i + 64] for i in range(0, len(b), 64)) + "\n-----END PUBLIC KEY-----\n"

    def sign(self, message: bytes) -> bytes:
        """ASN.1 DER ECDSA P-256 / SHA-256 signature, low-s, with a nonce derived from the key and the message."""
        e = int.from_bytes(hashlib.sha256(message).digest(), "big")
        k = int.from_bytes(hashlib.sha256(self.d.to_bytes(32, "big") + message).digest(), "big") % (I._N - 1) + 1
        r = I._mul(k, I._G)[0] % I._N
        s = pow(k, -1, I._N) * (e + r * self.d) % I._N
        s = min(s, I._N - s)

        def integer(v: int) -> bytes:
            b = v.to_bytes(32, "big").lstrip(b"\0") or b"\0"
            if b[0] & 0x80:
                b = b"\0" + b
            return b"\x02" + bytes([len(b)]) + b
        body = integer(r) + integer(s)
        return b"\x30" + bytes([len(body)]) + body


def _json(doc) -> bytes:
    return json.dumps(doc, sort_keys=True, separators=(",", ":")).encode()


def simple_payload(reference: str, digest: str) -> bytes:
    return _json({"critical": {"identity": {"docker-reference": reference}, "image": {"docker-manifest-digest": digest},
                               "type": I.SIMPLE_TYPE}, "optional": None})


def bundle(key: Key, reference: str, digest: str, predicate: str = I.COSIGN_PREDICATE) -> bytes:
    """A Sigstore bundle v0.3 as cosign 3 writes for `cosign sign --key`: a DSSE envelope over an in-toto statement."""
    st = _json({"_type": I.INTOTO_STATEMENT, "predicateType": predicate, "predicate": {},
                "subject": [{"name": reference, "digest": {"sha256": digest.split(":", 1)[1]}}]})
    sig = key.sign(I.pae(I.INTOTO_PAYLOAD, st))
    return _json({"mediaType": I.BUNDLE_TYPES[0], "verificationMaterial": {"publicKey": {"hint": ""}},
                  "dsseEnvelope": {"payload": base64.b64encode(st).decode(), "payloadType": I.INTOTO_PAYLOAD,
                                   "signatures": [{"sig": base64.b64encode(sig).decode(), "keyid": ""}]}})


class LayoutWriter:
    """Writes an OCI image layout that `images.Layout` (and the core's test registry) serves."""

    def __init__(self, root):
        self.root = Path(root)
        (self.root / "blobs" / "sha256").mkdir(parents=True, exist_ok=True)
        (self.root / "oci-layout").write_text('{"imageLayoutVersion":"1.0.0"}')
        p = self.root / "index.json"
        self.index = json.loads(p.read_text()) if p.exists() else {"schemaVersion": 2, "manifests": []}

    def put(self, data: bytes) -> dict:
        d = "sha256:" + hashlib.sha256(data).hexdigest()
        (self.root / "blobs" / "sha256" / d.split(":", 1)[1]).write_bytes(data)
        return {"digest": d, "size": len(data)}

    def _tag(self, repository: str, tag: str, desc: dict):
        name = f"{repository}:{tag}"
        self.index["manifests"] = [m for m in self.index["manifests"]
                                   if (m.get("annotations") or {}).get("org.opencontainers.image.ref.name") != name]
        self.index["manifests"].append({**desc, "annotations": {"org.opencontainers.image.ref.name": name}})
        (self.root / "index.json").write_text(json.dumps(self.index, indent=1))

    def manifest(self, doc: dict, repository: str | None = None, tag: str | None = None) -> str:
        desc = {"mediaType": doc.get("mediaType", I.OCI_MANIFEST), **self.put(_json(doc))}
        if tag:
            self._tag(repository, tag, desc)
        return desc["digest"]

    def _empty(self) -> dict:
        return {"mediaType": I.OCI_EMPTY, **self.put(b"{}")}

    def image(self, repository: str, content: bytes, tag: str | None = "latest", platform: str = "linux/amd64") -> str:
        """A one-layer image whose layer is `content`; returns its manifest digest."""
        os_, arch = platform.split("/")
        cfg = self.put(_json({"architecture": arch, "os": os_, "rootfs": {"type": "layers", "diff_ids": []}}))
        layer = self.put(content)
        return self.manifest({"schemaVersion": 2, "mediaType": I.OCI_MANIFEST,
                              "config": {"mediaType": "application/vnd.oci.image.config.v1+json", **cfg},
                              "layers": [{"mediaType": "application/vnd.oci.image.layer.v1.tar", **layer}]},
                             repository, tag)

    def sign(self, key: Key, repository: str, digest: str, simple: bool = False, reference: str | None = None,
             predicate: str = I.COSIGN_PREDICATE):
        """Attach a cosign signature of `digest` to `repository`: a bundle referrer, or with `simple` the .sig manifest."""
        ref = reference or f"{repository}@{digest}"
        if simple:
            payload = simple_payload(ref, digest)
            sig = base64.b64encode(key.sign(payload)).decode()
            layer = {"mediaType": I.SIMPLE_SIGNING, **self.put(payload), "annotations": {I.SIG_ANNOTATION: sig}}
            self.manifest({"schemaVersion": 2, "mediaType": I.OCI_MANIFEST,
                           "config": {"mediaType": "application/vnd.oci.image.config.v1+json", **self.put(b"{}")},
                           "layers": [layer]}, repository, I.signature_tag(digest))
            return
        b = bundle(key, ref, digest, predicate)
        self.manifest({"schemaVersion": 2, "mediaType": I.OCI_MANIFEST, "artifactType": I.BUNDLE_TYPES[0],
                       "config": self._empty(),
                       "layers": [{"mediaType": I.BUNDLE_TYPES[0], **self.put(b)}],
                       "subject": {"mediaType": I.OCI_MANIFEST, "digest": digest, "size": 0}})

    def set_index(self, key: Key | None, reference: str, registry: str, repository: str, seq: int, digests) -> str:
        """Publish a signed image index at a tagged reference (key None: unsigned); returns its manifest digest."""
        repo, tag, _ = I.normalize(reference)
        doc = I.index_document(registry, repository, seq, digests)
        d = self.manifest({"schemaVersion": 2, "mediaType": I.OCI_MANIFEST, "artifactType": I.INDEX_TYPE,
                           "config": self._empty(), "layers": [{"mediaType": I.INDEX_TYPE, **self.put(doc)}]},
                          repo, tag or "latest")
        if key:
            self.sign(key, repo, d)
        return d
