"""Apply a Seatbelt profile to this process, then exec the module (spec/sandbox.md).

    python -I _sandbox_launch.py PROFILE KEY=VALUE ... -- /abs/argv0 args...

Standalone on purpose (no imports beyond the standard library): it runs before the sandbox exists and must not
read anything the module could have changed. Exit codes: 70 the sandbox could not be applied, 71 exec failed,
64 usage.
"""
import ctypes
import os
import sys


def main():
    try:
        sep = sys.argv.index("--")
    except ValueError:
        sys.stderr.write("usage: _sandbox_launch.py PROFILE K=V ... -- argv\n")
        os._exit(64)
    if sep < 2 or len(sys.argv) <= sep + 1 or not sys.argv[sep + 1].startswith("/"):
        sys.stderr.write("sandbox launch: needs a profile and an absolute argv[0]\n")
        os._exit(64)
    with open(sys.argv[1], encoding="utf-8") as f:
        profile = f.read()
    flat = []
    for kv in sys.argv[2:sep]:
        k, _, v = kv.partition("=")
        flat += [k.encode(), v.encode()]
    params = (ctypes.c_char_p * (len(flat) + 1))(*flat, None)
    lib = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
    init = lib.sandbox_init_with_parameters
    init.argtypes = [ctypes.c_char_p, ctypes.c_uint64, ctypes.POINTER(ctypes.c_char_p), ctypes.POINTER(ctypes.c_char_p)]
    init.restype = ctypes.c_int
    err = ctypes.c_char_p()
    if init(profile.encode(), 0, params, ctypes.byref(err)) != 0:
        sys.stderr.write(f"sandbox launch: sandbox_init failed: {err.value.decode() if err.value else '?'}\n")
        os._exit(70)
    argv = sys.argv[sep + 1:]
    try:
        os.execv(argv[0], argv)
    except OSError as e:
        sys.stderr.write(f"sandbox launch: exec {argv[0]}: {e}\n")
        os._exit(71)


if __name__ == "__main__":
    main()
