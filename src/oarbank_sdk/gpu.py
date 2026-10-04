"""The GPU APIs this host provides (spec/runner-protocol.md, "GPU use"), detected as the agent detects them.

    from oarbank_sdk import gpu
    gpu.detect()          # {"host": ["metal", "opencl"], "containers": [], "evidence": {"metal": "Apple M4 Max", ...}}
    gpu.fits(["cuda", "vulkan"], ["metal", "vulkan"])    # True: they share an API
    gpu.unmet(man.gpu_needs("score", "darwin-arm64"), {"host": ["metal"], "containers": []})   # the groups not met

An API counts only when its own runtime enumerates a device that is a GPU: a software rasteriser, a CPU device or
Windows' Basic Render Driver does not. Standard library only (ctypes); every probe that fails names the API absent,
with the reason as its evidence. `oarbank-sdk gpu-apis` prints `detect()`; on a node, `oarbank-agent gpu-apis` prints
the same object (with the containers' APIs, which only an agent knows), and parity tests hold the two to one answer.
"""
import ctypes
import sys

# The APIs a core detects. The set is open (`^[a-z][a-z0-9]*$`): a module may name another, which no node reports yet.
KNOWN_APIS = ("cuda", "directml", "metal", "opencl", "rocm", "vulkan")

_DARWIN, _WINDOWS = sys.platform == "darwin", sys.platform == "win32"
_LINUX = sys.platform.startswith("linux")


def fits(any_of, have) -> bool:
    """Does a host with APIs `have` meet a need for any of `any_of`? (An empty need is always met.)"""
    need = set(any_of or [])
    return not need or bool(need & set(have or []))


def unmet(needs: list[dict], apis: dict) -> list[dict]:
    """The groups of `needs` (manifest.Manifest.gpu_needs: `{apis, where, source}`) that a node with `apis` (`{host,
    containers}`, as its doctor reports them) does not meet. Empty: the node may run the work."""
    return [g for g in needs if not fits(g["apis"], (apis or {}).get(g["where"]) or [])]


def describe(group: dict) -> str:
    """`one of cuda, rocm on the host` / `vulkan in containers`."""
    apis = group["apis"]
    what = apis[0] if len(apis) == 1 else "one of " + ", ".join(apis)
    return f"{what} {'in containers' if group['where'] == 'containers' else 'on the host'}"


class _Absent(Exception):
    """The API is not provided here; the message is the evidence."""


def _load(names: list[str]):
    """The first of `names` the dynamic loader opens (a ctypes library), or _Absent naming them all."""
    loader = ctypes.WinDLL if _WINDOWS else ctypes.CDLL
    for n in names:
        try:
            return loader(n)
        except OSError:
            continue
    raise _Absent(f"no {' or '.join(names)}")


def _fn(lib, name: str, restype, *argtypes):
    f = getattr(lib, name)
    f.restype, f.argtypes = restype, list(argtypes)
    return f


def _cstr(buf) -> str:
    return bytes(buf).split(b"\0", 1)[0].decode("utf-8", "replace").strip()


# ---------------------------------------------------------------------------- probes (each returns its device names)

def _metal() -> list[str]:
    """MTLCopyAllDevices works without a window server, so a LaunchDaemon sees the GPU too."""
    if not _DARWIN:
        raise _Absent("Metal is macOS only")
    mtl = _load(["/System/Library/Frameworks/Metal.framework/Metal"])
    cf = _load(["/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"])
    objc = _load(["/usr/lib/libobjc.A.dylib"])
    vp = ctypes.c_void_p
    devices = _fn(mtl, "MTLCopyAllDevices", vp)()
    if not devices:
        raise _Absent("MTLCopyAllDevices returned no devices")
    count = _fn(cf, "CFArrayGetCount", ctypes.c_long, vp)
    at = _fn(cf, "CFArrayGetValueAtIndex", vp, vp, ctypes.c_long)
    sel = _fn(objc, "sel_registerName", vp, ctypes.c_char_p)
    send = ctypes.cast(objc.objc_msgSend, ctypes.CFUNCTYPE(vp, vp, vp))
    utf8 = ctypes.cast(objc.objc_msgSend, ctypes.CFUNCTYPE(ctypes.c_char_p, vp, vp))
    try:
        names = []
        for i in range(count(devices)):
            name = send(at(devices, i), sel(b"name"))
            names.append((utf8(name, sel(b"UTF8String")) or b"").decode("utf-8", "replace") if name else "a Metal device")
    finally:
        _fn(cf, "CFRelease", None, vp)(devices)
    if not names:
        raise _Absent("MTLCopyAllDevices returned no devices")
    return names


_VK_LOADERS = (["libvulkan.1.dylib", "/opt/homebrew/lib/libvulkan.1.dylib", "/usr/local/lib/libvulkan.1.dylib"] if _DARWIN
               else ["vulkan-1.dll"] if _WINDOWS else ["libvulkan.so.1"])
_VK_CPU, _VK_COMPUTE = 4, 0x2                       # VK_PHYSICAL_DEVICE_TYPE_CPU, VK_QUEUE_COMPUTE_BIT
_VK_TYPES = {0: "other", 1: "integrated", 2: "discrete", 3: "virtual", 4: "cpu"}
_PORTABILITY = b"VK_KHR_portability_enumeration"     # MoltenVK is a portability driver: listed only when asked for


class _VkAppInfo(ctypes.Structure):
    _fields_ = [("sType", ctypes.c_uint32), ("pNext", ctypes.c_void_p), ("pApplicationName", ctypes.c_char_p),
                ("applicationVersion", ctypes.c_uint32), ("pEngineName", ctypes.c_char_p), ("engineVersion", ctypes.c_uint32),
                ("apiVersion", ctypes.c_uint32)]


class _VkInstanceInfo(ctypes.Structure):
    _fields_ = [("sType", ctypes.c_uint32), ("pNext", ctypes.c_void_p), ("flags", ctypes.c_uint32),
                ("pApplicationInfo", ctypes.POINTER(_VkAppInfo)), ("enabledLayerCount", ctypes.c_uint32),
                ("ppEnabledLayerNames", ctypes.c_void_p), ("enabledExtensionCount", ctypes.c_uint32),
                ("ppEnabledExtensionNames", ctypes.POINTER(ctypes.c_char_p))]


def _vulkan() -> list[str]:
    vk = _load(_VK_LOADERS)
    vp, u32 = ctypes.c_void_p, ctypes.c_uint32
    n = u32(0)
    exts = _fn(vk, "vkEnumerateInstanceExtensionProperties", ctypes.c_int32, vp, ctypes.POINTER(u32), vp)
    exts(None, ctypes.byref(n), None)
    props = (ctypes.c_char * (260 * max(n.value, 1)))()                    # VkExtensionProperties: name[256], version
    exts(None, ctypes.byref(n), props)
    portable = any(_cstr(props[i * 260:i * 260 + 256]).encode() == _PORTABILITY for i in range(n.value))
    app = _VkAppInfo(0, None, b"oarbank-gpu-probe", 1, b"oarbank", 1, 1 << 22)
    names = (ctypes.c_char_p * 1)(_PORTABILITY)
    info = _VkInstanceInfo(1, None, 0x1 if portable else 0, ctypes.pointer(app), 0, None, 1 if portable else 0,
                           names if portable else None)
    inst = vp()
    r = _fn(vk, "vkCreateInstance", ctypes.c_int32, ctypes.POINTER(_VkInstanceInfo), vp, ctypes.POINTER(vp))(
        ctypes.byref(info), None, ctypes.byref(inst))
    if r != 0:
        raise _Absent(f"vkCreateInstance failed ({r})")
    try:
        devs = _fn(vk, "vkEnumeratePhysicalDevices", ctypes.c_int32, vp, ctypes.POINTER(u32), vp)
        n = u32(0)
        devs(inst, ctypes.byref(n), None)
        handles = (vp * max(n.value, 1))()
        devs(inst, ctypes.byref(n), handles)
        get_props = _fn(vk, "vkGetPhysicalDeviceProperties", None, vp, vp)
        get_queues = _fn(vk, "vkGetPhysicalDeviceQueueFamilyProperties", None, vp, ctypes.POINTER(u32), vp)
        out, skipped = [], []
        for i in range(n.value):
            buf = (ctypes.c_uint8 * 2048)()                # VkPhysicalDeviceProperties: deviceType at 16, deviceName at 20
            get_props(handles[i], buf)
            kind = int.from_bytes(bytes(buf[16:20]), sys.byteorder)
            name = _cstr(buf[20:276])
            q = u32(0)
            get_queues(handles[i], ctypes.byref(q), None)
            fams = (u32 * (6 * max(q.value, 1)))()        # VkQueueFamilyProperties: 6 words, queueFlags first
            get_queues(handles[i], ctypes.byref(q), fams)
            compute = any(fams[6 * k] & _VK_COMPUTE for k in range(q.value))
            if kind == _VK_CPU or not compute:
                skipped.append(f"{name} ({_VK_TYPES.get(kind, kind)}{'' if compute else ', no compute queue'})")
            else:
                out.append(f"{name} ({_VK_TYPES.get(kind, kind)})")
    finally:
        _fn(vk, "vkDestroyInstance", None, vp, vp)(inst, None)
    if not out:
        raise _Absent("no Vulkan GPU device" + (f" (only {', '.join(skipped)})" if skipped else ""))
    return out


def _cuda() -> list[str]:
    if _DARWIN:
        raise _Absent("CUDA does not run on macOS")
    cu = _load(["nvcuda.dll"] if _WINDOWS else ["libcuda.so.1"])
    i32 = ctypes.c_int
    r = _fn(cu, "cuInit", i32, ctypes.c_uint)(0)
    if r != 0:
        raise _Absent(f"cuInit failed ({r})")
    n = i32(0)
    r = _fn(cu, "cuDeviceGetCount", i32, ctypes.POINTER(i32))(ctypes.byref(n))
    if r != 0 or n.value <= 0:
        raise _Absent(f"no CUDA device ({r})")
    get, get_name, out = _fn(cu, "cuDeviceGet", i32, ctypes.POINTER(i32), i32), _fn(cu, "cuDeviceGetName", i32, ctypes.c_char_p, i32, i32), []
    for i in range(n.value):
        d, buf = i32(0), ctypes.create_string_buffer(256)
        out.append(_cstr(buf.raw) if get(ctypes.byref(d), i) == 0 and get_name(buf, 256, d.value) == 0 else f"CUDA device {i}")
    return out


def _rocm() -> list[str]:
    if _DARWIN:
        raise _Absent("ROCm does not run on macOS")
    hip = _load(["amdhip64_7.dll", "amdhip64_6.dll", "amdhip64.dll"] if _WINDOWS else
                ["libamdhip64.so", "libamdhip64.so.7", "libamdhip64.so.6", "/opt/rocm/lib/libamdhip64.so"])
    i32 = ctypes.c_int
    n = i32(0)
    r = _fn(hip, "hipGetDeviceCount", i32, ctypes.POINTER(i32))(ctypes.byref(n))
    if r != 0 or n.value <= 0:
        raise _Absent(f"no HIP device ({r})")
    get_name, out = _fn(hip, "hipDeviceGetName", i32, ctypes.c_char_p, i32, i32), []
    for i in range(n.value):
        buf = ctypes.create_string_buffer(256)
        out.append(_cstr(buf.raw) if get_name(buf, 256, i) == 0 else f"HIP device {i}")
    return out


_CL_GPU_OR_ACCELERATOR, _CL_DEVICE_NAME, _CL_DEVICE_NOT_FOUND = 4 | 8, 0x102B, -1


def _opencl() -> list[str]:
    cl = _load(["/System/Library/Frameworks/OpenCL.framework/OpenCL"] if _DARWIN else ["OpenCL.dll"] if _WINDOWS
               else ["libOpenCL.so.1"])
    vp, u32, i32 = ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int32
    plats = _fn(cl, "clGetPlatformIDs", i32, u32, vp, ctypes.POINTER(u32))
    n = u32(0)
    r = plats(0, None, ctypes.byref(n))
    if r != 0 or n.value == 0:
        raise _Absent(f"no OpenCL platform ({r})")
    ids = (vp * n.value)()
    plats(n.value, ids, None)
    devs = _fn(cl, "clGetDeviceIDs", i32, vp, ctypes.c_uint64, u32, vp, ctypes.POINTER(u32))
    info = _fn(cl, "clGetDeviceInfo", i32, vp, u32, ctypes.c_size_t, vp, vp)
    out = []
    for p in ids:
        nd = u32(0)
        if devs(p, _CL_GPU_OR_ACCELERATOR, 0, None, ctypes.byref(nd)) != 0 or nd.value == 0:
            continue                                         # CL_DEVICE_NOT_FOUND: this platform has CPU devices only
        handles = (vp * nd.value)()
        devs(p, _CL_GPU_OR_ACCELERATOR, nd.value, handles, None)
        for h in handles:
            buf = ctypes.create_string_buffer(256)
            name = _cstr(buf.raw) if info(h, _CL_DEVICE_NAME, 256, buf, None) == 0 else "an OpenCL device"
            if not _software(name):
                out.append(name)
    if not out:
        raise _Absent("no OpenCL GPU or accelerator device")
    return out


def _software(name: str) -> bool:
    """Windows' Basic Render Driver (WARP), which the D3D12 mapping layers (OpenCLOn12, Dozen) expose as a GPU."""
    return "basic render driver" in name.lower()


class _GUID(ctypes.Structure):
    _fields_ = [("a", ctypes.c_uint32), ("b", ctypes.c_uint16), ("c", ctypes.c_uint16), ("d", ctypes.c_uint8 * 8)]


def _guid(a, b, c, d: bytes) -> _GUID:
    return _GUID(a, b, c, (ctypes.c_uint8 * 8)(*d))


def _directml() -> list[str]:
    """DirectML runs on any Direct3D 12 hardware adapter at feature level 11_0; D3D12CreateDevice with no output pointer
    answers S_FALSE when the adapter could create one, without creating it."""
    if not _WINDOWS:
        raise _Absent("DirectML is Windows only")
    _load(["DirectML.dll"])
    dxgi, d3d12 = _load(["dxgi.dll"]), _load(["d3d12.dll"])
    vp, hr = ctypes.c_void_p, ctypes.c_long
    factory = vp()
    iid_factory1 = _guid(0x770AAE78, 0xF26F, 0x4DBA, bytes([0xA8, 0x29, 0x25, 0x3C, 0x83, 0xD1, 0xB3, 0x87]))
    if _fn(dxgi, "CreateDXGIFactory1", hr, ctypes.POINTER(_GUID), ctypes.POINTER(vp))(ctypes.byref(iid_factory1), ctypes.byref(factory)) < 0:
        raise _Absent("CreateDXGIFactory1 failed")
    iid_device = _guid(0x189819F1, 0x1DB6, 0x4B57, bytes([0xBE, 0x54, 0x18, 0x21, 0x33, 0x9B, 0x85, 0xF7]))
    create = _fn(d3d12, "D3D12CreateDevice", hr, vp, ctypes.c_int, ctypes.POINTER(_GUID), vp)

    def method(obj, index, restype, *argtypes):
        table = ctypes.cast(ctypes.cast(obj, ctypes.POINTER(vp))[0], ctypes.POINTER(vp))
        return ctypes.WINFUNCTYPE(restype, vp, *argtypes)(table[index])

    out, skipped = [], []
    try:
        i = 0
        while True:
            adapter = vp()
            if method(factory, 12, hr, ctypes.c_uint, ctypes.POINTER(vp))(factory, i, ctypes.byref(adapter)) < 0:
                break                                        # DXGI_ERROR_NOT_FOUND: no more adapters (EnumAdapters1)
            i += 1
            desc = (ctypes.c_uint8 * 512)()                   # DXGI_ADAPTER_DESC1
            method(adapter, 10, hr, vp)(adapter, desc)        # GetDesc1
            name = bytes(desc[:256]).decode("utf-16-le", "replace").split("\0", 1)[0]
            off = 256 + 16 + 3 * ctypes.sizeof(ctypes.c_size_t) + 8
            flags = int.from_bytes(bytes(desc[off:off + 4]), "little")
            ok = not flags & 2 and create(adapter, 0xB000, ctypes.byref(iid_device), None) >= 0    # not SOFTWARE; 11_0
            (out if ok else skipped).append(name)
            method(adapter, 2, ctypes.c_ulong)(adapter)       # Release
    finally:
        method(factory, 2, ctypes.c_ulong)(factory)
    if not out:
        raise _Absent("no Direct3D 12 hardware adapter" + (f" (only {', '.join(skipped)})" if skipped else ""))
    return out


PROBES = {"cuda": _cuda, "directml": _directml, "metal": _metal, "opencl": _opencl, "rocm": _rocm, "vulkan": _vulkan}


def detect() -> dict:
    """{host, containers, evidence}: the APIs this host provides (sorted), none in containers (only an agent knows its
    container runtime), and per API the devices found or why it is absent."""
    host, evidence = [], {}
    for api, probe in PROBES.items():
        try:
            evidence[api] = ", ".join(probe())
            host.append(api)
        except _Absent as e:
            evidence[api] = str(e)
        except (OSError, AttributeError, ValueError) as e:      # a library without the entry point, a broken driver
            evidence[api] = f"probe failed: {e}"
    return {"host": sorted(host), "containers": [], "evidence": evidence}
