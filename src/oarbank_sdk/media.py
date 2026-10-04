"""Media served to module pages (spec/ui-contract.md, "Media"): which bytes a host may serve for a `media`, `gallery`
or `compare` component, decided from the bytes themselves, never from a file name or a module's claim.

    kind = "image"
    ctype = media.sniff(kind, head)          # head: the file's first HEAD_BYTES bytes
    if ctype is None: refuse (415)
    if size > media.CAPS[kind]: refuse (413)

The allowlist is closed: PNG, JPEG, WebP and AVIF images (thumbnails too), MP4 and WebM video, MP3, M4A, Ogg and WAV
audio, and UTF-8 text (plain text, VTT, SRT and Markdown are all served as text/plain). No SVG, no HTML, nothing a
browser could run. The host serves what this allows with exactly the returned Content-Type, `nosniff` and a sandboxing
CSP, from an origin that is not the console's.
"""

KINDS = ("image", "video", "audio", "text")
THUMBNAIL = "thumbnail"                  # a module-made preview image: images only, small
HEAD_BYTES = 64 * 1024                   # how much of a file sniff() looks at (text needs the most)
MiB = 1 << 20
CAPS = {"image": 64 * MiB, THUMBNAIL: 1 * MiB, "video": 16 * 1024 * MiB, "audio": 1024 * MiB, "text": 4 * MiB}
TEXT = "text/plain; charset=utf-8"

_VIDEO_BRANDS = (b"isom", b"iso2", b"iso4", b"iso5", b"iso6", b"mp41", b"mp42", b"avc1", b"dash", b"M4V ", b"mp4v")
_AUDIO_BRANDS = (b"M4A ", b"M4B ")


def _ftyp(head: bytes) -> bytes | None:
    """The major brand of an ISO base media file (`ftyp` box first), else None."""
    return head[8:12] if len(head) >= 12 and head[4:8] == b"ftyp" else None


def _image(head: bytes) -> str | None:
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    if _ftyp(head) in (b"avif", b"avis"):
        return "image/avif"
    return None


def _video(head: bytes) -> str | None:
    if _ftyp(head) in _VIDEO_BRANDS:
        return "video/mp4"
    if head.startswith(b"\x1a\x45\xdf\xa3") and b"webm" in head[:64]:
        return "video/webm"
    return None


def _audio(head: bytes) -> str | None:
    if head.startswith(b"ID3") or (len(head) >= 2 and head[0] == 0xFF and head[1] & 0xE0 == 0xE0):
        return "audio/mpeg"
    if _ftyp(head) in _AUDIO_BRANDS:
        return "audio/mp4"
    if head.startswith(b"OggS"):
        return "audio/ogg"
    if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "audio/wav"
    return None


def _text(head: bytes) -> str | None:
    if b"\x00" in head:
        return None
    for cut in range(4):                 # a multi-byte character the sample cut in half is fine
        try:
            head[:len(head) - cut].decode("utf-8")
            return TEXT
        except UnicodeDecodeError:
            continue
    return None


def sniff(kind: str, head: bytes) -> str | None:
    """The Content-Type to serve a file of `kind` (one of KINDS, or THUMBNAIL) whose first bytes are `head`, or None when
    the bytes are not an allowed type of that kind."""
    fn = {"image": _image, THUMBNAIL: _image, "video": _video, "audio": _audio, "text": _text}.get(kind)
    return fn(head) if fn else None


def problem(kind: str, head: bytes, size: int) -> str | None:
    """Why a file may not be served as `kind` (None: it may): its type, or its size against the kind's cap."""
    if kind not in CAPS:
        return f"unknown media kind {kind!r}"
    if sniff(kind, head) is None:
        return f"not an allowed {kind} type (allowed: {ALLOWED[kind]})"
    if size > CAPS[kind]:
        return f"{size} bytes is over the {kind} cap of {CAPS[kind]} bytes"
    return None


ALLOWED = {"image": "PNG, JPEG, WebP, AVIF", THUMBNAIL: "PNG, JPEG, WebP, AVIF", "video": "MP4, WebM",
           "audio": "MP3, M4A, Ogg, WAV", "text": "UTF-8 text"}


def byte_range(header: str | None, size: int) -> tuple[int, int] | None | str:
    """A single `Range: bytes=a-b` request against a file of `size` bytes: (start, end inclusive), None for no range
    (the whole file), or "unsatisfiable" (416)."""
    if not header:
        return None
    h = header.strip()
    if not h.startswith("bytes=") or "," in h:
        return None                       # a malformed or multi-range request gets the whole file
    a, _, b = h[6:].partition("-")
    try:
        if a == "":
            n = int(b)
            if n <= 0:
                return "unsatisfiable"
            return max(0, size - n), size - 1
        start = int(a)
        end = int(b) if b else size - 1
    except ValueError:
        return None
    if start >= size or start < 0 or end < start:
        return "unsatisfiable"
    return start, min(end, size - 1)
