"""Cross-platform foundations (spec/platforms.md): platform tokens and portable paths.

Every path that crosses the protocol (bundle files, artifacts, mounts, dataset files, module files, move rules) is a
**PortablePath**: valid on macOS, Linux and Windows filesystems alike, so a bundle built on one OS unpacks safely on
another and no name can escape its directory or collide on a case-insensitive filesystem.
"""
import re
import unicodedata

# ---------------------------------------------------------------------------- platform tokens

OSES = ("darwin", "linux", "windows")
ARCHES = ("arm64", "amd64")
KNOWN_PLATFORMS = tuple(f"{o}-{a}" for o in OSES for a in ARCHES)
PLATFORM_TOKEN = re.compile(r"^[a-z][a-z0-9]*-[a-z0-9_]+$")
PLATFORM_KEY = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9_]+)?$")     # a token, or an OS name (per-platform table keys)


def is_platform_token(s: str) -> bool:
    """`<os>-<arch>`. Unknown but well-formed tokens are valid: they mean "no node of this platform here"."""
    return isinstance(s, str) and bool(PLATFORM_TOKEN.fullmatch(s))


def split_platform(token: str) -> tuple[str, str]:
    os_, _, arch = token.partition("-")
    return os_, arch


def host_platform() -> str:
    """This machine's platform token: the machine's, not the interpreter's (an x64 Python under Windows on Arm's
    emulation runs on windows-arm64)."""
    import platform
    import sys
    os_ = {"darwin": "darwin", "win32": "windows"}.get(sys.platform, "linux" if sys.platform.startswith("linux") else sys.platform)
    m = _native_machine() if sys.platform == "win32" else None
    m = m or platform.machine().lower()
    arch = {"x86_64": "amd64", "amd64": "amd64", "arm64": "arm64", "aarch64": "arm64"}.get(m, m)
    return f"{os_}-{arch}"


def _native_machine() -> str | None:
    """Windows: the machine's architecture (IsWow64Process2), whatever the interpreter was built for."""
    import ctypes
    from ctypes import wintypes
    k = ctypes.WinDLL("kernel32")
    process, native = wintypes.USHORT(), wintypes.USHORT()
    fn = getattr(k, "IsWow64Process2", None)
    if fn is None or not fn(wintypes.HANDLE(-1), ctypes.byref(process), ctypes.byref(native)):
        return None
    return {0xAA64: "arm64", 0x8664: "amd64"}.get(native.value)


def os_env(home, tmp, locale: str = "C.UTF-8") -> dict:
    """The conventional variables a module process gets from its host, for this host's OS (spec/platforms.md,
    "Environment per OS"): the search path, `home` as the home directory and `tmp` for temporary files, plus on Windows
    the system variables programs need to start at all. There LOCALAPPDATA stays the host account's: starting a process
    in an AppContainer points LOCALAPPDATA, TEMP and TMP at the container's own profile folder under it, which exists
    only there (and the start fails without the variable)."""
    import os
    if os.name == "nt":
        root = os.environ.get("SystemRoot", r"C:\Windows")
        return {"SystemRoot": root, "windir": root, "SystemDrive": os.environ.get("SystemDrive", root[:2]),
                "ComSpec": rf"{root}\System32\cmd.exe", "PATHEXT": ".COM;.EXE",
                "PATH": rf"{root}\System32;{root};{root}\System32\Wbem", "USERPROFILE": str(home), "TEMP": str(tmp),
                "TMP": str(tmp), "APPDATA": os.path.join(str(home), "AppData", "Roaming"),
                "LOCALAPPDATA": os.environ.get("LOCALAPPDATA") or os.path.join(str(home), "AppData", "Local"),
                "PROCESSOR_ARCHITECTURE": os.environ.get("PROCESSOR_ARCHITECTURE", "AMD64"),
                "NUMBER_OF_PROCESSORS": os.environ.get("NUMBER_OF_PROCESSORS", "1"), "PYTHONUTF8": "1"}
    return {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": str(home), "TMPDIR": str(tmp), "LANG": locale,
            "LC_ALL": locale, "PYTHONUTF8": "1"}


def oci_platform(token: str) -> str:
    """`linux-amd64` -> `linux/amd64` (container image platforms use the OCI spelling)."""
    o, a = split_platform(token)
    return f"{o}/{a}"


# ---------------------------------------------------------------------------- portable paths

MAX_PATH_BYTES = 200
MAX_SEGMENT = 100
SEGMENT = re.compile(r"^[A-Za-z0-9_+@-][A-Za-z0-9._+@-]*$")        # leading dot handled separately
RESERVED = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"} | {f"{p}{n}" for p in ("COM", "LPT")
                                                                 for n in list("0123456789") + ["¹", "²", "³"]}


class PathError(ValueError):
    pass


def check_portable_path(path: str, allow_dotfiles: bool = False) -> str:
    """Raise PathError unless `path` is a PortablePath (spec/platforms.md, "Portable paths"); returns it."""
    if not isinstance(path, str) or not path:
        raise PathError("empty path")
    if unicodedata.normalize("NFC", path) != path:
        raise PathError(f"{path!r}: not NFC-normalised Unicode")
    if not path.isascii():
        raise PathError(f"{path!r}: only ASCII names are portable")
    if len(path.encode()) > MAX_PATH_BYTES:
        raise PathError(f"{path!r}: longer than {MAX_PATH_BYTES} bytes")
    if path.startswith("/") or "\\" in path or ":" in path:
        raise PathError(f"{path!r}: must be relative and '/'-separated (no '\\', ':' or drive letters)")
    for seg in path.split("/"):
        if seg in ("", ".", ".."):
            raise PathError(f"{path!r}: empty, '.' or '..' segment")
        if len(seg) > MAX_SEGMENT:
            raise PathError(f"{path!r}: segment longer than {MAX_SEGMENT}")
        body = seg[1:] if (allow_dotfiles and seg.startswith(".") and len(seg) > 1) else seg
        if not SEGMENT.fullmatch(body):
            raise PathError(f"{path!r}: segment {seg!r} has a character that is not portable")
        if seg.endswith(".") or seg.endswith(" "):
            raise PathError(f"{path!r}: segment {seg!r} ends with '.' or a space")
        if seg.split(".", 1)[0].upper() in RESERVED:
            raise PathError(f"{path!r}: {seg!r} is a reserved device name on Windows")
    return path


def is_portable_path(path: str, allow_dotfiles: bool = False) -> bool:
    try:
        check_portable_path(path, allow_dotfiles)
        return True
    except PathError:
        return False


def casefold_collisions(paths) -> list[tuple[str, str]]:
    """Pairs of paths that would collide on a case-insensitive filesystem (NTFS, APFS by default)."""
    seen, out = {}, []
    for p in paths:
        k = p.casefold()
        if k in seen and seen[k] != p:
            out.append((seen[k], p))
        seen.setdefault(k, p)
    return out
