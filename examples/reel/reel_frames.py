"""reel's frames, shared by its runner and its coordinator side: a deterministic picture per (seed, index), and the
digest a render's result carries. Stdlib only.

The digest covers the raw pixels, never the PNG bytes: zlib's output differs between zlib builds (zlib, zlib-ng), and
a result must compare across every platform the module declares.
"""
import hashlib
import struct
import zlib

WIDTH, HEIGHT = 64, 48
THUMB = 4                                  # thumbnails are WIDTH/4 x HEIGHT/4


def pixels(seed: int, index: int) -> bytes:
    """RGB rows of frame `index`: a gradient tinted by the seed, with a square that moves across it."""
    rng = (seed * 2654435761 + 1) & 0xFFFFFFFF
    tint = (rng & 0xFF, (rng >> 8) & 0xFF, (rng >> 16) & 0xFF)
    sx = (index * 5) % (WIDTH - 12)
    sy = (index * 3 + (rng >> 24) % 20) % (HEIGHT - 12)
    out = bytearray()
    for y in range(HEIGHT):
        for x in range(WIDTH):
            if sx <= x < sx + 12 and sy <= y < sy + 12:
                out += bytes((255 - tint[0], 255 - tint[1], 255 - tint[2]))
            else:
                out += bytes(((x * 4 + tint[0]) & 0xFF, (y * 5 + tint[1]) & 0xFF, ((x + y) * 2 + tint[2]) & 0xFF))
    return bytes(out)


def thumbnail(rgb: bytes) -> bytes:
    """Every THUMB-th pixel of every THUMB-th row."""
    out = bytearray()
    for y in range(0, HEIGHT, THUMB):
        row = rgb[y * WIDTH * 3:(y + 1) * WIDTH * 3]
        for x in range(0, WIDTH, THUMB):
            out += row[x * 3:x * 3 + 3]
    return bytes(out)


def png(rgb: bytes, width: int = WIDTH, height: int = HEIGHT) -> bytes:
    """An 8-bit RGB PNG of `rgb`."""
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + rgb[y * width * 3:(y + 1) * width * 3] for y in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def frame_digest(seed: int, index: int) -> str:
    return hashlib.sha256(pixels(seed, index)).hexdigest()


def render_digest(frame_digests: list[str]) -> str:
    """A render's digest: sha256 over its frames' pixel digests, in order."""
    return hashlib.sha256("\n".join(frame_digests).encode()).hexdigest()


def expected(seed: int, frames: int) -> str:
    return render_digest([frame_digest(seed, i) for i in range(frames)])


def frame_name(index: int) -> str:
    return f"frame-{index:04d}.png"
