"""oarbank-module.toml, manifest schema 1.

The manifest holds every fact the core (installer, coordinator, agent, console) needs *before* running
module code: identity, compatibility, entry points, stages and resources, services and probes, result
fields, dataset kinds, goldens, declarative UI and the module CLI. Behaviour stays in code (the module
protocol verbs). Rule: if the scheduler, installer or GUI needs a fact to decide something, declare it.

See spec/manifest.md. Every field is labelled [stable], [beta] or [experimental].
"""
import fnmatch
import re
import tomllib
from pathlib import Path
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, Field, field_validator, model_validator

from . import gpu
from . import platform as pf
from . import portable
from ._base import Contract, ModuleId, Name, SemVer, Sha256, Stability, VersionRange
from .module_protocol import CAP_TICK_RESULTS
from .ui import OperationDecl, UISection

PlatformToken = Annotated[str, Field(pattern=portable.PLATFORM_TOKEN.pattern, max_length=40)]
# A key of a per-platform table: a platform token (`linux-amd64`) or an OS name (`linux`); the most specific key wins.
PlatformKey = Annotated[str, Field(pattern=portable.PLATFORM_KEY.pattern, max_length=40)]
BUNDLE_TOKEN = "{bundle}"

# ---------------------------------------------------------------------------- per-platform declarations

# A key older cores would ignore silently (lenient readers) needs the core that understands it (spec/versioning.md,
# "Additive changes within manifest 1"): ignoring it would, for example, mix platforms in a unit of work that must stay
# on one, or dispute an ingestion stage's honest runs. Cores already refuse a module whose core range excludes them.
PLATFORM_KEYS_CORE = (2, 2)       # the per-platform and placement keys (SDK 1.1)
SDK13_KEYS_CORE = (2, 3)          # stage determinism and default, structured tick results, dataset update/delete (SDK 1.3)
BOOTSTRAP_KEYS_CORE = (2, 4)      # bootstrap stages and the pinned dataset table (SDK 1.4)
SDK15_KEYS_CORE = (2, 5)          # SDK 1.5: secrets, signed container image sets, the container GPU pool, service
                                  # endpoints and GPU use, folder grants, portable checkpoints, artifact_ref
KNOWN_FEATURES = ("placement",)          # requires.features this SDK understands (must-understand)
ENV_NAME = r"^[A-Z][A-Z0-9_]*$"
# Variables the agent or the host sets itself (spec/runner-protocol.md, spec/platforms.md "Environment per OS"); a
# module's env never overrides them. Compared case-insensitively (Windows names are).
RESERVED_ENV_PREFIX = "OARBANK_"
RESERVED_ENV = ("PATH", "PATHEXT", "HOME", "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "APPDATA", "LOCALAPPDATA", "TMPDIR",
                "TEMP", "TMP", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "SYSTEMROOT",
                "SYSTEMDRIVE", "WINDIR", "COMSPEC", "PROCESSOR_ARCHITECTURE", "NUMBER_OF_PROCESSORS", "LANG", "LC_ALL",
                "PYTHONUTF8", "HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "NO_PROXY")


def check_env(env: dict) -> dict:
    """Module env names never shadow what the agent or host sets: OARBANK_*, the search path, home, temporary and
    system directories, locale and proxy variables (RESERVED_ENV)."""
    for k in env:
        if k.upper().startswith(RESERVED_ENV_PREFIX) or k.upper() in RESERVED_ENV:
            raise ValueError(f"env {k!r} is reserved (OARBANK_* and {', '.join(RESERVED_ENV)} are set by the host)")
    return env


EnvMap = Annotated[dict[Annotated[str, Field(pattern=ENV_NAME, max_length=64)], Annotated[str, Field(max_length=4096)]],
                   AfterValidator(check_env)]
Mix = Annotated[str, Field(pattern=r"^[a-z][a-z0-9-]*$", max_length=40)]
Reason = Annotated[str, Field(min_length=1, max_length=200)]


def core_lower_bound(rng: str) -> tuple[int, ...] | None:
    """The lowest core version a VersionRange admits, as numbers (None: no lower bound). `>X` counts as X."""
    best = None
    for part in rng.split(","):
        m = re.match(r"\s*(==|>=|>|~=)\s*([0-9][0-9A-Za-z.]*)", part)
        if m:
            nums = tuple(int(x) for x in re.findall(r"\d+", m.group(2).split("rc")[0].split("a")[0].split("b")[0])[:3])
            best = nums if best is None or nums > best else best
    return best


def check_exec(argv: list) -> list:
    """An exec is an argv array, never a shell string (spec/manifest.md, "Exec"). Element 0 is the `python` token or
    `{bundle}/<PortablePath>` (a native executable in the bundle; on Windows the path names its `.exe`). `.bat`, `.cmd`
    and `.ps1` are never valid as element 0. `{bundle}` may appear in any element; nothing else is rewritten."""
    if not argv or not all(isinstance(a, str) for a in argv):
        raise ValueError("exec must be a non-empty list of strings")
    head = argv[0]
    if head != "python":
        if not head.startswith(BUNDLE_TOKEN + "/"):
            raise ValueError(f"exec[0] {head!r}: the `python` token or `{BUNDLE_TOKEN}/<path in the bundle>`")
        rel = head[len(BUNDLE_TOKEN) + 1:]
        portable.check_portable_path(rel)
        if rel.lower().endswith((".bat", ".cmd", ".ps1")):
            raise ValueError(f"exec[0] {head!r}: .bat, .cmd and .ps1 are not valid entry points")
    return argv


Exec = Annotated[list[str], Field(min_length=1)]


def resolve_exec(argv: list[str], bundle, python: str) -> list[str]:
    """The argv a host runs: `python` (element 0) becomes the module's interpreter and `{bundle}` the bundle's absolute
    native path. Nothing else is rewritten."""
    b = str(bundle)
    out = [python if i == 0 and a == "python" else a.replace(BUNDLE_TOKEN, b) for i, a in enumerate(argv)]
    if out[0] != python and not out[0].startswith(b):
        raise ValueError(f"exec[0] {argv[0]!r} does not resolve into the bundle")
    return out

# ---------------------------------------------------------------------------- building blocks


class Runtime(Contract):
    """How an entry point's dependencies are provided. [stable]"""
    kind: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]*$")] = Field(description=(
        "[stable] Open set. `python`: the host's managed CPython with the SDK (plus the bundle's requirements, installed from "
        "wheels); `uv`: a locked uv project (wheels only, resolvable for every declared platform) built offline into a "
        "per-module environment; `native`: argv[0] is a native executable in the bundle (Mach-O, ELF or PE, or a POSIX shebang "
        "script in darwin/linux variants). An unknown kind means the entry point cannot run on this node."))
    lock: str | None = Field(None, description="[stable] Path of uv.lock inside the bundle (kind=uv).")
    python: str | None = Field(None, description="[stable] Exact Python version for the venv (kind=uv), e.g. 3.12.14.")

    def check_argv(self, argv: list[str], where: str):
        if argv[0] == "python" and self.kind == "native":
            raise ValueError(f"{where}: the `python` token needs runtime kind python or uv, not native")
        if argv[0] != "python" and self.kind in ("python", "uv"):
            raise ValueError(f"{where}: runtime kind {self.kind} runs the `python` token, not {argv[0]!r}")

    @model_validator(mode="after")
    def _uv_needs_lock(self):
        if self.kind == "uv" and not (self.lock and self.python):
            raise ValueError("runtime kind 'uv' requires `lock` and `python`")
        return self


class ModuleInfo(Contract):
    id: ModuleId = Field(description="[stable] Namespaced, globally unique module id (reverse DNS). Removed ids are tombstoned, never reused.")
    version: SemVer = Field(description="[stable] SemVer of the module package.")
    compat: Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")] = Field(
        description="[stable] Result-semantics tag. Part of every job_key: bump it only when results for the same inputs change meaning. Patch releases keep it, so caches survive.")
    publisher: str = Field(description="[stable] Publisher identity (today a free-form owner name).")
    license: str = Field(description="[stable] SPDX license expression of the module.")
    codeowners: list[str] = Field(default_factory=list, description="[stable] Maintainers; modules without codeowners cannot be verified.")
    stability: Stability = Field("beta", description="[stable] Maturity of the module itself.")
    description: str = Field("", description="[stable] One-line description for the console and docs.")


class LinuxRequirements(Contract):
    kernel: VersionRange | None = Field(None, description="[stable] Kernel versions, e.g. '>=6.1'.")
    glibc: VersionRange | None = Field(None, description="[stable] glibc versions, e.g. '>=2.35'.")


class OSRequirements(Contract):
    """Per-OS version requirements; keys for undeclared OSes are ignored. [stable]"""
    darwin: VersionRange | None = None
    linux: LinuxRequirements | None = None
    windows: VersionRange | None = Field(None, description="[stable] Windows versions by build, e.g. '>=10.0.19045'.")


class Unsupported(Contract):
    """Reasons a module does not run on some platforms (keys: tokens or OSes). They never contradict the allow-lists: a
    key never names an allowed platform or the OS of one. [beta]"""
    runner: dict[PlatformKey, Reason] = Field(default_factory=dict, description="[beta] Node platforms, beside requires.platforms.")
    coordinator: dict[PlatformKey, Reason] = Field(default_factory=dict, description=(
        "[beta] Coordinator platforms, beside requires.coordinator_platforms (which must then be set: absent means any)."))


class Requires(Contract):
    core: VersionRange = Field(description="[stable] Supported oarbank core versions, e.g. '>=2.0,<3'.")
    agent: VersionRange | None = Field(None, description="[stable] Supported agent versions.")
    os: "OSRequirements | None" = Field(None, description="[stable] Supported OS versions per OS (only for the declared platforms).")
    platforms: list[PlatformToken] = Field(min_length=1, description=(
        "[stable] Node platforms the module runs on, `<os>-<arch>` (spec/platforms.md). Required; an unknown token means no "
        "node of that platform, never an error."))
    module_protocol: list[int] = Field(description="[stable] Module protocol majors the coordinator side speaks.")
    runner_protocol: list[int] = Field(default_factory=lambda: [1], description="[stable] Runner protocol majors the runner speaks.")
    service_protocol: list[int] = Field(default_factory=list, description="[stable] Service protocol majors (only when services/probes are declared).")
    experimental: list[str] = Field(default_factory=list, description="[stable] Opt-ins to experimental contract items; any opt-in blocks the verified badge.")
    ui_contract: VersionRange | None = Field(None, description="[beta] UI contract versions the module's pages use, e.g. '>=1.0,<2' (spec/ui-contract.md).")
    coordinator_platforms: list[PlatformToken] | None = Field(None, min_length=1, description=(
        "[beta] Platforms the coordinator side runs on (the coordinator host's platform); `platforms` lists node platforms "
        "only. Absent: any platform. A core refuses to install or enable the module on a coordinator host not listed, and "
        "blocks a coordinator move to one. Needs requires.core >= 2.2."))
    unsupported: Unsupported = Field(default_factory=Unsupported, description=(
        "[beta] Why the module does not run somewhere, shown by explain and the console. Needs requires.core >= 2.2."))
    features: list[Annotated[str, Field(pattern=r"^[a-z][a-z0-9._-]*$", max_length=64)]] = Field(default_factory=list, description=(
        "[beta] Must-understand list: a reader that does not know a listed feature refuses the manifest. Known: "
        "`placement`. Needs requires.core >= 2.2."))

    def os_platforms(self) -> set[str]:
        return {portable.split_platform(p)[0] for p in self.platforms}


Permission = Literal["datasets:read", "blobs:stat", "settings:read:self", "store:read:self", "nodes:read", "jobs:read:self",
                     "files:read:self", "secrets:read:self"]
EffectKind = Literal["jobs.enqueue", "jobs.cancel", "campaigns.create", "campaigns.update", "campaigns.cancel",
                     "datasets.create", "datasets.update", "datasets.delete", "module_settings.update",
                     "store.write", "store.delete", "files.write", "files.put", "files.delete", "external"]
MOVE_VERBS = ("move.preflight", "move.postflight", "move.cancelled")
TICK_RESULTS = CAP_TICK_RESULTS                  # campaign.tick sees canonical payloads and artifacts
Capability = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9._-]*$", max_length=64)]


class MoveRule(Contract):
    """What happens to part of a module's coordinator state when the coordinator moves to another machine. [beta]"""
    files: Annotated[str, Field(max_length=512, pattern=r"^([A-Za-z0-9._-]+/)*[A-Za-z0-9._-]*$")] | None = Field(
        None, description="[beta] A path prefix in the module's files ('' = all of them).")
    store: Name | None = Field(None, description="[beta] A store collection.")
    class_: Literal["carry", "rebuild", "drop"] = Field(alias="class", description=(
        "[beta] `carry` (the default for everything): moved and verified by digest; `rebuild`: not transferred, the module "
        "recreates it in move.postflight; `drop`: scratch, deleted on the new coordinator."))

    @model_validator(mode="after")
    def _one_selector(self):
        if (self.files is None) == (self.store is None):
            raise ValueError("a move rule names exactly one of `files` or `store`")
        return self


class MoveSection(Contract):
    """The module's say in a coordinator move (spec/module-protocol.md, "Coordinator moves"). [beta]"""
    rules: list[MoveRule] = Field(default_factory=list, description="[beta] The first matching rule wins; unmatched state is carried.")
    effects: list[EffectKind] = Field(default_factory=list, description="[beta] Effects move.preflight, move.postflight and move.cancelled may request.")


IMAGE_REF = r"^[a-z0-9][a-z0-9._/:-]*@sha256:[0-9a-f]{64}$"
CORE_POOLS = ("containers", "gpu")              # pools the agent itself provides
SERVICE_ENV_PREFIX = "OARBANK_SERVICE_"         # + the endpoint service's name, upper-cased: a job's connector
RESERVED_CAPABILITY_PREFIXES = ("os.", "arch.", "gpu.", "containers.", "oarbank.")
HOST_PATTERN = r"^(\*\.)?[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*(:[0-9]{1,5})?$"


class ContainerImage(Contract):
    """A container image the module's jobs may run through the agent's container broker. [beta]"""
    image: Annotated[str, Field(max_length=300, pattern=IMAGE_REF)] = Field(
        description="[beta] A digest-pinned reference, e.g. `docker.io/org/tool:1.2@sha256:<64 hex>`.")
    platform: Annotated[str, Field(pattern=r"^[a-z0-9]+/[a-z0-9_]+$")] = Field(
        "linux/arm64", description="[beta] OCI platform (open set), e.g. linux/arm64 or linux/amd64 (emulated where the node's arch differs).")


REGISTRY_HOST = r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*(:[0-9]{1,5})?$"
REPOSITORY_PREFIX = r"^[a-z0-9]+([._-][a-z0-9]+)*(/[a-z0-9]+([._-][a-z0-9]+)*)*/?$"
TAGGED_REF = r"^[a-z0-9][a-z0-9._/:-]*:[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$"


class ContainerSet(Contract):
    """Container images approved by signature instead of by digest (spec/sandbox.md, "Image sets"): every digest-pinned
    image under a registry and repository prefix that carries a cosign signature by the pinned key, or that a signed image
    index lists. Approval covers the prefix and the key, so new images need no new module version. [beta]"""
    name: Name = Field(description="[beta] The set's name; unique; shown at approval and in the audit.")
    registry: Annotated[str, Field(max_length=253, pattern=REGISTRY_HOST)] = Field(description=(
        "[beta] The registry host[:port], lowercase (`docker.io` for Docker Hub)."))
    repository: Annotated[str, Field(max_length=255, pattern=REPOSITORY_PREFIX)] = Field(description=(
        "[beta] A repository path; ending with `/` it is a prefix (every repository below it), else exactly that repository."))
    platform: Annotated[str, Field(pattern=r"^[a-z0-9]+/[a-z0-9_]+$")] = Field(
        "linux/arm64", description="[beta] OCI platform of the set's images.")
    key: Annotated[str, Field(max_length=200)] = Field(description=(
        "[beta] Bundle path of the cosign public key: one ECDSA P-256 key, PEM `PUBLIC KEY` (SPKI), as "
        "`cosign generate-key-pair` writes `cosign.pub`."))
    index: Annotated[str, Field(max_length=300, pattern=TAGGED_REF)] | None = Field(None, description=(
        "[beta] A tagged reference of a signed image index (artifact type application/vnd.oarbank.image-set.v1+json): "
        "only the digests it lists are members. Absent: every image signed by the key is."))

    @field_validator("key")
    @classmethod
    def _key_path(cls, v):
        portable.check_portable_path(v)
        return v

    def covers(self, repository: str) -> bool:
        """Whether a normalized repository (`<registry>/<path>`) lies in this set."""
        reg, _, path = repository.partition("/")
        if reg != self.registry:
            return False
        return path.startswith(self.repository) if self.repository.endswith("/") else path == self.repository


class SandboxNet(Contract):
    """Network for the module's node-side processes (spec/sandbox.md, "Network"). [beta]"""
    mode: Annotated[str, Field(pattern=r"^[a-z][a-z0-9-]*$")] = Field("none", description=(
        "[beta] Open set. `none`; `egress-allowlist`: only the `allow` hosts, through the agent's local proxy (the SDK sets "
        "HTTPS_PROXY/ALL_PROXY); `egress-any`: any public address, a separately approved full-trust grant. Never loopback "
        "or link-local, never listening."))
    allow: list[Annotated[str, Field(max_length=253, pattern=HOST_PATTERN)]] = Field(default_factory=list, description=(
        "[beta] `host[:port]` entries (a leading `*.` matches subdomains); never IP addresses or CIDRs. Required with "
        "egress-allowlist; port defaults to 443."))

    @model_validator(mode="after")
    def _allow(self):
        if self.mode == "egress-allowlist" and not self.allow:
            raise ValueError("sandbox.net.mode = 'egress-allowlist' needs at least one `allow` entry")
        if self.mode in ("none", "egress-any") and self.allow:
            raise ValueError(f"sandbox.net.allow only applies to mode 'egress-allowlist' (mode is {self.mode!r})")
        for h in self.allow:
            host = h.rsplit(":", 1)[0] if ":" in h else h
            if all(part.isdigit() for part in host.split(".")):
                raise ValueError(f"sandbox.net.allow {h!r}: host names only, never IP addresses")
        return self


class ToolGrant(Contract):
    """A host tool the module's node-side processes may read and execute: a tool the fleet defines (`jdk`, `python`, or
    one an admin defines), detected and version-checked on each node, never a path (spec/sandbox.md, "Host tools").
    The agent grants the one installation that satisfies `version` and `arch` on each node. [beta]"""
    id: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_.-]{0,63}$")] = Field(description=(
        "[beta] The fleet's tool definition id: `jdk` and `python` are built in; an admin defines others "
        "(`tools.define`)."))
    version: str | None = Field(None, description=(
        "[beta] Versions the module accepts, comma-separated clauses all of which hold: `=`, `!=`, `>`, `>=`, `<`, "
        "`<=`, `~>` (Nomad's version operators), e.g. `\">=17, <22\"`. Leave it out for any version."))
    arch: Literal["any", "native", "arm64", "amd64"] = Field("any", description=(
        "[beta] `any`: any architecture, the node's own preferred; `native`: only the node's own (no x86_64 JDK under "
        "Rosetta on Apple silicon); `arm64` or `amd64`: exactly that one."))
    trust: Literal["read", "code-exec"] = Field("read", description=(
        "[beta] `code-exec`: the tool runs arbitrary code (a JVM, an interpreter, a shell); the approval UI flags it. On "
        "Windows any readable binary is executable."))

    @field_validator("version")
    @classmethod
    def _version(cls, v):
        if v is None:
            return v
        from . import toolversion
        return toolversion.describe(v)


FOLDER_ID = r"^[a-z][a-z0-9_.-]{0,63}$"


class FolderGrant(Contract):
    """A folder on the node, from the operator's folder registry (a logical id the operator maps to a path per node).
    Only runners get folders. [beta]"""
    id: Annotated[str, Field(pattern=FOLDER_ID)] = Field(description="[beta] Logical folder id, e.g. `inputs`.")
    access: Literal["read", "write"] = Field(description=(
        "[beta] `read`: read the folder's files and listings, never write. `write`: an outbox: create files and directories "
        "and write them, never read, list, rename or delete anything there (files it creates may replace files there)."))


class SandboxDevices(Contract):
    gpu: Annotated[str, Field(pattern=r"^[a-z][a-z0-9-]*$")] = Field("none", description=(
        "[beta] Open set: `none` or `compute` (GPU compute through the platform's APIs, no display server; weakens "
        "isolation, so it is flagged at approval)."))


class SandboxSection(Contract):
    """What the module's node-side processes (runner, services, probes) need beyond their bundle, runtime, data and work
    directories (spec/sandbox.md). Every request is shown to an operator, who approves it for each module version before
    that version can run. The coordinator side never gets these grants. [beta]"""
    contract: Literal[1] = Field(1, description="[beta] Sandbox contract version.")
    net: SandboxNet = Field(default_factory=SandboxNet)
    tools: list[ToolGrant] = Field(default_factory=list, description=(
        "[beta] Host tools runners, services and probes may read and execute: fleet tool ids with version constraints, "
        "resolved per node to one detected installation each (OARBANK_TOOLS_FILE)."))
    devices: SandboxDevices = Field(default_factory=SandboxDevices)
    containers: list[ContainerImage] = Field(default_factory=list, description=(
        "[beta] Images the agent's container broker may run for the module's jobs (a stage that uses them reserves the "
        "`containers` pool)."))
    container_sets: list[ContainerSet] = Field(default_factory=list, description=(
        "[beta] Image sets approved by signature (registry and repository prefix, a pinned cosign key, optionally a signed "
        "index). A job runs a set's images only if it lists them (jobs.enqueue `images`). Needs requires.core >= 2.5."))
    exec_writable: bool = Field(False, description=(
        "[beta] Runners may execute files they wrote into the data or work directory (downloaded tools). Not enforceable "
        "as `false` on Windows without application control; nodes report it."))
    folders: list[FolderGrant] = Field(default_factory=list, description=(
        "[beta] Folders on the node (registry ids the operator maps to a path per node) the runner may read, or write into "
        "as an outbox. Needs requires.core >= 2.5."))

    @field_validator("tools")
    @classmethod
    def _unique_tools(cls, v):
        ids = [t.id for t in v]
        dup = sorted({i for i in ids if ids.count(i) > 1})
        if dup:
            raise ValueError(f"sandbox.tools lists {dup} more than once")
        return v

    @field_validator("folders")
    @classmethod
    def _unique_folders(cls, v):
        ids = [f.id for f in v]
        dup = sorted({i for i in ids if ids.count(i) > 1})
        if dup:
            raise ValueError(f"sandbox.folders lists {dup} more than once")
        return v

    def requests(self) -> bool:
        return bool(self.net.mode != "none" or self.tools or self.devices.gpu != "none" or self.containers
                    or self.container_sets or self.exec_writable or self.folders)

    def runs_containers(self) -> bool:
        return bool(self.containers or self.container_sets)

    def for_bootstrap(self) -> "SandboxSection":
        """The grants a bootstrap stage's jobs get (spec/sandbox.md, "Bootstrap jobs"): the network as approved (none or the
        egress allowlist; a module with a bootstrap stage never asks for egress-any) and nothing else: no host tools, GPU,
        containers or execution of written files. The host also withholds the module data directory and settings."""
        return SandboxSection(contract=self.contract, net=self.net)


class BundleSection(Contract):
    """How the bundle is built (spec/bundles.md). [stable]"""
    executables: list[Annotated[str, Field(max_length=200)]] = Field(default_factory=list, description=(
        "[stable] Globs (bundle paths) of files that get mode 755; every other file is 644. The argv[0] of every native "
        "exec is executable too. Modes never come from the build host's filesystem."))
    platform_files: dict[Annotated[str, Field(max_length=200, pattern=r"^[^/\\:][^\\:]*$")],
                         Annotated[list[PlatformKey], Field(min_length=1)]] = Field(default_factory=dict, description=(
        "[beta] Glob (bundle path) -> the platforms or OSes whose nodes receive the matching files; a file matched by "
        "several globs goes to each one's platforms, and unmatched files go everywhere. The coordinator and the CLI "
        "always have the whole bundle. Needs requires.core >= 2.2."))

    def receives(self, path: str, platform: str) -> bool:
        """Whether a node of `platform` receives bundle file `path`."""
        matched = [keys for glob, keys in self.platform_files.items() if path == glob or fnmatch.fnmatchcase(path, glob)]
        return not matched or any(pf.matches(platform, keys) for keys in matched)

    def subset(self, paths, platform: str) -> list[str]:
        """The bundle files a node of `platform` receives."""
        return [p for p in paths if self.receives(p, platform)]


def _declared(m) -> dict:
    """A variant's fields as written: set fields only (unknown fields never take effect), nested sections as dicts."""
    out = {}
    for name, f in type(m).model_fields.items():
        v = getattr(m, name)
        if v is not None:
            out[f.alias or name] = _declared(v) if isinstance(v, BaseModel) else v
    return out


def _for_platform(section, platform: str):
    """`section` (coordinator, runner or stage) with its variants for `platform` applied, OS first, then the token
    (oarbank_sdk.platform.apply_variants; pinned by spec/vectors/variant-resolution.json)."""
    layers = [_declared(section.variants[k]) for k in pf.variant_keys(platform) if k in section.variants]
    if not layers:
        return section
    return type(section).model_validate(pf.overlay(section.model_dump(by_alias=True), *layers))


Timeout = Annotated[float, Field(gt=0, le=3600)]


class CoordinatorVariant(Contract):
    """Per-platform overrides of [coordinator] for the coordinator host's platform (key: a platform token or an OS name;
    the most specific key wins; `timeouts_s` and `env` merge key by key). [beta]"""
    exec: Exec | None = Field(None, description="[beta] Replaces coordinator.exec.")
    runtime: Runtime | None = Field(None, description="[beta] Replaces coordinator.runtime.")
    timeouts_s: dict[str, Timeout] | None = Field(None, description="[beta] Merged over coordinator.timeouts_s verb by verb.")
    concurrency: Annotated[int, Field(ge=1, le=64)] | None = Field(None, description="[beta] Replaces coordinator.concurrency.")
    env: EnvMap | None = Field(None, description="[beta] Merged over coordinator.env name by name.")


class Coordinator(Contract):
    """The out-of-process coordinator side (module protocol over stdio). [stable]"""
    exec: Exec = Field(description="[stable] argv (spec/manifest.md, \"Exec\"); cwd is the bundle root; the process speaks module protocol JSON-RPC on stdin/stdout.")
    runtime: Runtime
    capabilities: list[Capability] = Field(default_factory=list, description=(
        "[stable] Optional verbs/features implemented, e.g. result.merge, golden.compare, study.metrics, params.distance, "
        "result.upgrade, and campaign.tick.results [beta] (campaign.tick sees each done job's payload and artifacts; the "
        "host validates payloads against results.schema at acceptance; needs requires.core >= 2.3)."))
    concurrency: Annotated[int, Field(ge=1, le=64)] = Field(1, description="[stable] Max in-flight requests the module accepts; 1 = serial.")
    timeouts_s: dict[str, Timeout] = Field(
        default_factory=lambda: {"default": 10.0}, description="[stable] Per-verb timeouts; key `default` applies to unlisted verbs.")
    permissions: list[Permission] = Field(default_factory=list, description="[stable] Host callbacks the module may call; everything else is denied.")
    campaign_effects: list[EffectKind] = Field(default_factory=list, description="[beta] Effects campaign.tick may request (requires the campaign.tick capability).")
    move: MoveSection = Field(default_factory=MoveSection)
    env: EnvMap = Field(default_factory=dict, description=(
        "[beta] Extra environment for the coordinator process (names `^[A-Z][A-Z0-9_]*$`, never a reserved name). The host "
        "also sets OARBANK_PLATFORM. Needs requires.core >= 2.2."))
    variants: dict[PlatformKey, CoordinatorVariant] = Field(default_factory=dict, description=(
        "[beta] Per-platform overrides for the coordinator host, keyed by platform token or OS (checked against "
        "requires.coordinator_platforms when set). Needs requires.core >= 2.2."))

    def for_platform(self, platform: str) -> "Coordinator":
        """This coordinator side with the most specific variant for `platform` applied."""
        return _for_platform(self, platform)


class GPUNeed(Contract):
    """The runner's GPU use. [beta]"""
    use: Literal["none", "shared", "exclusive"] = Field("none", description="[beta] `shared`/`exclusive` jobs are not admitted while a protected process group uses that GPU.")
    apis_any: list[Annotated[str, Field(pattern=r"^[a-z][a-z0-9]*$")]] = Field(default_factory=list, description=(
        "[beta] Any of these GPU APIs; jobs run only on nodes providing one (open set; detected: cuda, directml, metal, "
        "opencl, rocm, vulkan). Needs `use` shared or exclusive and requires.core >= 2.5."))
    min_vram_gb: Annotated[float, Field(ge=0)] | None = Field(None, description="[beta] Minimum device memory where memory is not unified.")
    in_container: bool = Field(False, description="[beta] The GPU is used from inside a broker-run container.")


class RunnerVariant(Contract):
    """Per-platform overrides of [runner] (key: a platform token or an OS name; the most specific key wins). [beta]"""
    exec: Exec | None = None
    runtime: Runtime | None = None
    capabilities: list[Capability] | None = None
    stop_grace_s: Annotated[float, Field(ge=1, le=600)] | None = None
    gpu: GPUNeed | None = None
    env: EnvMap | None = Field(None, description="[beta] Merged over [runner].env name by name. Needs requires.core >= 2.2.")


RUNNER_CAPABILITIES = ("cancellable", "freeze_ok", "cooperative_pause", "resumable", "progress_events",
                       "deterministic_output", "cooperative_throttle", "checkpoint")


class Runner(Contract):
    """The node-side job runner (runner protocol). [stable]"""
    exec: Exec = Field(description="[stable] argv (spec/manifest.md, \"Exec\"); the agent appends `run --spec S --workdir W --out R [--events E]` or `doctor --json`.")
    runtime: Runtime
    capabilities: list[Capability] = Field(default_factory=list, description=(
        "[stable] By intent: cancellable (honours a stop request within stop_grace_s), freeze_ok (safe to freeze at any "
        "instruction), cooperative_pause (pauses on control.json), resumable (resumes from its own checkpoint after a "
        "restart), progress_events, deterministic_output, cooperative_throttle, checkpoint (writes portable checkpoints, "
        "honours a checkpoint-then-stop request and resumes from <W>/checkpoint/; needs requires.core >= 2.5)."))
    stop_grace_s: Annotated[float, Field(ge=1, le=600)] = Field(20.0, description="[stable] Seconds between the stop request and forced termination of the process container.")
    checkpoint_grace_s: Annotated[float, Field(ge=1, le=1800)] = Field(120.0, description=(
        "[beta] Seconds between a checkpoint-then-stop request and forced termination of the process container. Needs "
        "requires.core >= 2.5."))
    gpu: GPUNeed = Field(default_factory=GPUNeed)
    bandwidth_class: Literal["low", "medium", "high"] | None = Field(None, description="[experimental] Measured memory-bandwidth appetite relative to the node's memory system (measured on Apple unified memory so far).")
    env: EnvMap = Field(default_factory=dict, description=(
        "[beta] Extra environment for the runner and doctor, applied after the agent's own variables (names "
        "`^[A-Z][A-Z0-9_]*$`, never OARBANK_* or another reserved name), e.g. MKL_CBWR or OMP_NUM_THREADS for "
        "determinism. Needs requires.core >= 2.2."))
    variants: dict[str, RunnerVariant] = Field(default_factory=dict, description="[beta] Per-platform overrides, keyed by platform token (`linux-amd64`) or OS (`linux`).")

    def for_platform(self, platform: str) -> "Runner":
        """This runner with the most specific variant for `platform` applied."""
        return _for_platform(self, platform)


class Resources(Contract):
    cpu: Annotated[float, Field(gt=0, le=256)] = Field(1.0, description="[stable] CPU cores the job uses.")
    mem_gb: Annotated[float, Field(gt=0, le=1024)] = Field(1.0, description="[stable] Peak memory (GB) the job may use; the agent budgets it before admission.")


class StageRequires(Contract):
    capabilities: list[Capability] = Field(default_factory=list, description="[stable] Node capabilities that must be healthy (from probes/services).")
    pools: dict[Name, Annotated[int, Field(ge=1)]] = Field(default_factory=dict, description="[stable] Countable pool tokens reserved for the job's lifetime.")
    needs_pools: list[Name] = Field(default_factory=list, description="[beta] Pools that must exist on the node but are not reserved (a short phase uses them).")
    platforms: list[PlatformToken] = Field(default_factory=list, description="[beta] Only on these platforms (empty: every declared platform).")
    resources: Resources = Field(default_factory=Resources)


class Retry(Contract):
    max_attempts: Annotated[int, Field(ge=1, le=20)] = Field(3, description="[stable] Execution attempts before the job is quarantined.")


class ResourcesPatch(Contract):
    cpu: Annotated[float, Field(gt=0, le=256)] | None = Field(None, description="[beta] Replaces the stage's cpu on this platform.")
    mem_gb: Annotated[float, Field(gt=0, le=1024)] | None = Field(None, description="[beta] Replaces the stage's mem_gb on this platform.")


class StageVariantRequires(Contract):
    resources: ResourcesPatch | None = Field(None, description="[beta] Merged over the stage's resources field by field.")


class StageVariant(Contract):
    """Per-platform adjustments of a stage (key: a platform token or an OS name; the most specific key wins). [beta]"""
    timeout_s: Annotated[float, Field(gt=0, le=86400)] | None = Field(None, description="[beta] Replaces the stage's timeout_s.")
    requires: StageVariantRequires | None = Field(None, description="[beta] Only `resources` may vary per platform.")
    retry: Retry | None = Field(None, description="[beta] Replaces the stage's retry.")


class StagePlacement(Contract):
    mix: Mix = Field(description=(
        "[beta] Between this stage and its `after` stage: `any`, `same-os`, `same-arch` or `same-platform` (open set; an "
        "unknown value applies as `same-platform`). Combined with [placement].mix, the stricter one wins."))


Determinism = Literal["exact", "within_tolerance", "none"]


class StageCheckpoint(Contract):
    """Portable checkpoints for a stage's jobs (spec/runner-protocol.md, "Checkpoints"). [beta]"""
    max_mb: Annotated[int, Field(ge=1, le=65536)] = Field(description="[beta] The largest checkpoint (all its files) the agent uploads, MB.")
    min_interval_s: Annotated[float, Field(ge=30, le=86400)] = Field(600.0, description=(
        "[beta] The agent uploads at most one checkpoint per interval; a checkpoint answering a checkpoint-then-stop "
        "request is always uploaded."))


class Stage(Contract):
    name: Name = Field(description="[stable] Stage name; unique within the manifest.")
    after: Name | None = Field(None, description="[stable] Stage whose output this stage consumes (its artifacts become inputs).")
    determinism: Determinism | None = Field(None, description=(
        "[beta] This stage's determinism (absent: results.determinism). `none`: its results depend on when it ran (an "
        "ingestion job pulling a moving feed), so the host never replicates, compares, caches or golden-tests them; when "
        "the stage also needs no capability and no pool, its jobs run before the module is certified on a node, wherever "
        "its runner starts. Only a standalone stage sets it (a chain compares as results.determinism). Needs "
        "requires.core >= 2.3."))
    default: bool = Field(False, description=(
        "[beta] The default stage: what a job runs when it names no stage (the single-stage form). Only a standalone stage "
        "sets it; exactly one does when several stages are standalone. Needs requires.core >= 2.3."))
    bootstrap: bool = Field(False, description=(
        "[beta] A bootstrap stage: its jobs run on nodes where the module's runner starts, before the goldens pass, with only "
        "the module's egress allowlist (no tools, GPU, containers, module data or settings), and the host registers their "
        "output only when it is exactly datasets of [[datasets.pinned]]. A standalone stage, not the default one, with "
        "determinism none and no pools. Needs requires.core >= 2.4."))
    secrets: list[Name] = Field(default_factory=list, description=(
        "[beta] Declared [[secrets]] this stage's runner receives in OARBANK_SECRETS_FILE (no other stage, service, probe or "
        "doctor does). A job waits until each has a value for its node. Never on a bootstrap stage. Needs requires.core >= 2.5."))
    checkpoint: StageCheckpoint | None = Field(None, description=(
        "[beta] The stage keeps portable checkpoints: the latest one a job's runner wrote is uploaded, and the job's next "
        "attempt, on any node, resumes from it. Needs the runner capability `checkpoint` and requires.core >= 2.5."))
    requires: StageRequires = Field(default_factory=StageRequires)
    timeout_s: Annotated[float, Field(gt=0, le=86400)] = Field(1800.0, description="[stable] Hard wall-clock limit per attempt.")
    retry: Retry = Field(default_factory=Retry)
    variants: dict[PlatformKey, StageVariant] = Field(default_factory=dict, description=(
        "[beta] Per-platform `timeout_s`, `requires.resources` and `retry`, keyed by platform token or OS. Needs "
        "requires.core >= 2.2."))
    placement: StagePlacement | None = Field(None, description="[beta] Needs requires.core >= 2.2.")

    def for_platform(self, platform: str) -> "Stage":
        """This stage with the most specific variant for `platform` applied (resources merge field by field)."""
        return _for_platform(self, platform)


class RestartPolicy(Contract):
    backoff_initial_s: Annotated[float, Field(gt=0)] = 10.0
    backoff_max_s: Annotated[float, Field(gt=0)] = 600.0
    max_failures: Annotated[int, Field(ge=1)] = 5


class ServiceProvides(Contract):
    capabilities: list[Capability] = Field(default_factory=list)
    pools: list[Name] = Field(default_factory=list, description="[stable] Pools whose token counts the service's `fingerprint` reports.")


class ServiceGPU(Contract):
    """A service's GPU use, as `runner.gpu` without the runner-only keys. [beta]"""
    use: Literal["none", "shared", "exclusive"] = Field("none", description=(
        "[beta] A running service that is not `none` is GPU-resident fleet work: host protection stops it, when "
        "yieldable, while GPU work may not run, and a job reserving one of its pools is a GPU job. Needs "
        "sandbox.devices.gpu = 'compute' and requires.core >= 2.5."))
    apis_any: list[Annotated[str, Field(pattern=r"^[a-z][a-z0-9]*$")]] = Field(default_factory=list, description=(
        "[beta] Any of these GPU APIs; jobs run only on nodes providing one (open set; detected: cuda, directml, metal, "
        "opencl, rocm, vulkan). Needs `use` shared or exclusive and requires.core >= 2.5."))


class Service(Contract):
    """A node helper the agent manages generically (service protocol). [beta]"""
    name: Name
    exec: Exec = Field(description="[beta] argv (spec/manifest.md, \"Exec\"); cwd is the bundle root; the agent appends the operation (fingerprint|start|stop|status|ready|list_owned|destroy).")
    platforms: list[PlatformToken] = Field(default_factory=list, description="[beta] Only on these platforms (empty: every declared platform).")
    lifecycle: Literal["on_demand", "always", "manual"] = Field("on_demand", description="[beta] on_demand: refcounted by admitted jobs and stopped after idle_timeout_s.")
    idle_timeout_s: Annotated[float, Field(ge=0)] = 900.0
    start_timeout_s: Annotated[float, Field(gt=0)] = 120.0
    stop_timeout_s: Annotated[float, Field(gt=0)] = 120.0
    restart: RestartPolicy = Field(default_factory=RestartPolicy)
    provides: ServiceProvides = Field(default_factory=ServiceProvides)
    reserves_host_memory: bool = Field(False, description="[beta] The fingerprint's reserve.mem_gb is charged to the host while running.")
    yieldable: bool = Field(True, description=(
        "[beta] The agent may stop it when idle under memory pressure, and host protection may stop it, releasing the "
        "jobs using it, when it evicts or while GPU work may not run (a GPU service)."))
    freeze_ok: bool = Field(False, description="[beta] The agent may freeze the service's process container (freezing returns no memory).")
    endpoint: bool = Field(False, description=(
        "[beta] Jobs reach the service: each attempt whose stage reserves one of its pools gets OARBANK_SERVICE_<NAME>, "
        "and the agent hands the service every connection over its endpoint channel; the service never listens "
        "(spec/service-protocol.md, \"Endpoints\"). Provides at least one pool; lifecycle on_demand or always. Needs "
        "requires.core >= 2.5."))
    gpu: ServiceGPU = Field(default_factory=ServiceGPU, description="[beta] Needs requires.core >= 2.5 when `use` is not none.")

    def env_name(self) -> str:
        """The variable a job finds this endpoint service's connector in."""
        return SERVICE_ENV_PREFIX + self.name.upper()


class Probe(Contract):
    """A read-only capability check (service protocol `fingerprint`). [beta]"""
    name: Capability
    exec: Exec
    period_s: Annotated[float, Field(ge=10)] = 3600.0
    platforms: list[PlatformToken] = Field(default_factory=list, description="[beta] Only on these platforms (empty: every declared platform).")


class Secret(Contract):
    """A credential the owner sets through the core, write-only (spec/manifest.md, "Secrets"): stored encrypted on the
    coordinator, never shown, delivered only to the runners of stages that list it and, with `secrets:read:self`, to
    coordinator verbs. [beta]"""
    name: Name = Field(description="[beta] The secret's name; unique. Stages list it in `secrets`.")
    description: Annotated[str, Field(max_length=500)] = Field("", description="[beta] What it is for, shown where the owner sets it.")


class Settings(Contract):
    schema_: str | None = Field(None, alias="schema", description="[stable] JSON Schema (bundle path) for the module's settings; the core stores but never interprets them.")


FieldType = Literal["number", "integer", "string", "boolean"]
FORMAT_RE = re.compile(r"^(\.\d{1,2}[fe%]|d|,d|s|\.\d{1,2}s)?$")


class FieldUI(Contract):
    column: str | None = Field(None, max_length=40, description="[stable] Column header in result tables; absent = not shown.")
    format: str | None = Field(None, description="[stable] Restricted format spec: .Nf, .Ne, .N%, d, ,d or s.")
    unit: str | None = Field(None, max_length=16)

    @field_validator("format")
    @classmethod
    def _fmt(cls, v):
        if v is not None and not FORMAT_RE.match(v):
            raise ValueError(f"format {v!r} not in the whitelist (.Nf, .Ne, .N%, d, ,d, s)")
        return v


class ResultField(Contract):
    name: Name
    type: FieldType
    indexed: bool = Field(False, description="[stable] Promote to a generated column with an index (sortable/filterable).")
    unit: str | None = None
    ui: FieldUI = Field(default_factory=FieldUI)


class Digest(Contract):
    version: Annotated[int, Field(ge=1)] = Field(description="[stable] Replica/golden comparisons happen only between equal digest versions.")
    over: list[str] = Field(min_length=1, description="[stable] Result payload fields the digest covers (documentation; the module computes it in result.evaluate).")


class Value(Contract):
    field: Name = Field(description="[stable] Result field studies optimise.")
    direction: Literal["maximize", "minimize"] = "maximize"


class Results(Contract):
    schema_: str = Field(alias="schema", description="[stable] JSON Schema (bundle path) for the result payload.")
    schema_version: Annotated[int, Field(ge=1)]
    determinism: Determinism = Field(description=(
        "[stable] exact: replicas must produce the same digest; `none`: results are not compared (see stages[].determinism)."))
    determinism_scope: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*$")] = Field("global", description=(
        "[stable] Open set. `global`: replicas on any platform must agree; `platform`: replicas and tie-breaks compare only "
        "within one platform (libm, BLAS and GPU differ across operating systems); `os` and `arch` [beta]: within one OS "
        "or one arch (they need requires.core >= 2.2). An unknown scope compares within one platform."))
    digest: Digest
    value: Value | None = None
    max_inline_kb: Annotated[int, Field(ge=1, le=1024)] = 64
    fields: list[ResultField] = Field(default_factory=list)


class DatasetAttr(Contract):
    name: Name
    type: FieldType
    indexed: bool = False


DATASET_ID = r"^[A-Za-z0-9][A-Za-z0-9_.:+-]{0,127}$"


class PinnedFile(Contract):
    """One file of a pinned dataset. [beta]"""
    path: Annotated[str, AfterValidator(portable.check_portable_path)] = Field(description=(
        "[beta] PortablePath inside the dataset (what a job sees under the dataset's mount)."))
    sha256: Sha256 = Field(description="[beta] The file's sha256, 64 lowercase hex digits.")
    size: Annotated[int, Field(ge=0)] = Field(description="[beta] The file's size in bytes.")


class PinnedDataset(Contract):
    """A dataset a bootstrap stage may provide, file by file (spec/manifest.md, "Pinned datasets"). [beta]"""
    dataset_id: Annotated[str, Field(pattern=DATASET_ID)] = Field(description="[beta] The dataset id the host registers.")
    kind: Name = Field(description="[beta] One of [datasets].kinds.")
    meta: dict = Field(default_factory=dict, description="[beta] The registered dataset's meta (its attrs).")
    platform: PlatformToken | None = Field(None, description=(
        "[beta] Set exactly when `kind` is platform-bound: one of requires.platforms."))
    files: list[PinnedFile] = Field(min_length=1, description="[beta] Every file, with unique paths.")

    def contents(self) -> frozenset:
        """The pinned files as (path, sha256, size), the form an artifact is matched by."""
        return frozenset((f.path, f.sha256, f.size) for f in self.files)

    def dataset_files(self) -> list[dict]:
        """The files as a dataset row holds them ({path, digest, size}), sorted by path."""
        return [{"path": f.path, "digest": f.sha256, "size": f.size} for f in sorted(self.files, key=lambda f: f.path)]


class Datasets(Contract):
    kinds: list[Name] = Field(default_factory=list, description=(
        "[stable] Dataset kinds this module registers (datasets.create refuses others). Kinds are short names scoped by the "
        "dataset's owning module, so two modules' kinds never collide; host.datasets.query takes the same short kind."))
    attrs: list[DatasetAttr] = Field(default_factory=list)
    platform_bound: list[Name] = Field(default_factory=list, description=(
        "[beta] Kinds whose datasets only make sense on one platform (an index built by a native tool): datasets.create "
        "must give their `platform`, and jobs using them run only there. Needs requires.core >= 2.2."))
    pinned: list[PinnedDataset] = Field(default_factory=list, description=(
        "[beta] The datasets the module's bootstrap stages may provide, each file with its sha256 and size. The host "
        "registers a bootstrap job's artifact only when its files are exactly one entry's, and datasets.create of a pinned "
        "id only with the pinned contents. Needs a bootstrap stage and requires.core >= 2.4."))

    def pin(self, dataset_id: str) -> PinnedDataset | None:
        return next((p for p in self.pinned if p.dataset_id == dataset_id), None)


class Goldens(Contract):
    fixtures: str = Field(description=(
        "[stable] Glob (bundle path) of golden fixtures: inputs plus the expected digest, optionally per platform "
        "(`Golden.platforms`, `Golden.expected_by_platform`; oarbank_sdk.goldens.load reads them)."))
    compare: Literal["digest", "verb"] = Field("digest", description="[stable] `verb` calls golden.compare instead of digest equality.")


class Placement(Contract):
    """Keeps each unit of work on one platform class while other units use other classes (spec/platforms.md,
    "Placement"). Absent: mix `any`, today's behaviour. [beta]"""
    mix: Mix = Field("any", description=(
        "[beta] Open set: `any`, `same-os`, `same-arch` or `same-platform`. An unknown value applies as `same-platform` "
        "(fail safe) and `oarbank-sdk check` warns."))
    unit: Literal["campaign", "group", "dataset", "pipeline"] = Field("campaign", description=(
        "[beta] What stays together: a campaign, a job group within a campaign (jobs.enqueue `group`), the jobs of one "
        "dataset within a campaign, or one pipeline (head and tail stages, replicas and tie-breaks)."))
    bind: Literal["capacity", "first-claim", "explicit"] | None = Field(None, description=(
        "[beta] How a unit gets its class: `capacity` (the feasible class with the most free certified CPU, at creation), "
        "`first-claim` (the class of the first node that claims one of its jobs) or `explicit` (only a pin). Absent: "
        "`capacity` for campaigns, `first-claim` otherwise."))
    rebind: Literal["never", "if-stranded"] = Field("never", description=(
        "[beta] When no node of the bound class has been eligible for `stranded_after_s`: `never` raises an alert naming "
        "the remedy; `if-stranded` rebinds to the best feasible class and re-queues the unit's finished jobs."))
    stranded_after_s: Annotated[float, Field(ge=60, le=604800)] = Field(1800.0, description="[beta] Seconds before a bound unit counts as stranded.")

    def effective_bind(self) -> str:
        return self.bind or ("capacity" if self.unit == "campaign" else "first-claim")


class CLI(Contract):
    exec: Exec = Field(description="[beta] Module CLI; `oarbank cli <module> ...` runs it on the coordinator, sandboxed, with a token scoped to the module (OARBANKD_URL, OARBANK_TOKEN).")


class Manifest(Contract):
    """oarbank-module.toml. [stable]"""
    manifest: Literal[1] = Field(1, description="[stable] Manifest schema major.")
    module: ModuleInfo
    requires: Requires
    coordinator: Coordinator
    runner: Runner
    stages: list[Stage] = Field(min_length=1)
    services: list[Service] = Field(default_factory=list)
    probes: list[Probe] = Field(default_factory=list)
    settings: Settings = Field(default_factory=Settings)
    secrets: list[Secret] = Field(default_factory=list, description="[beta] Write-only credentials. Needs requires.core >= 2.5.")
    results: Results
    datasets: Datasets = Field(default_factory=Datasets)
    goldens: Goldens | None = None
    ui: UISection = Field(default_factory=UISection)
    operations: list[OperationDecl] = Field(default_factory=list, description="[beta] Module operations, registered as mod.<module>.<verb>.")
    cli: CLI | None = None
    sandbox: SandboxSection = Field(default_factory=SandboxSection)
    bundle: BundleSection = Field(default_factory=BundleSection)
    placement: Placement | None = Field(None, description="[beta] Needs requires.core >= 2.2.")

    def execs(self) -> list[list[str]]:
        out = [self.coordinator.exec] + [v.exec for v in self.coordinator.variants.values() if v.exec]
        out += [self.runner.exec] + [v.exec for v in self.runner.variants.values() if v.exec]
        out += [s.exec for s in self.services] + [p.exec for p in self.probes] + ([self.cli.exec] if self.cli else [])
        return out

    def core_keys_used(self) -> list[tuple[str, tuple[int, int]]]:
        """The keys this manifest uses that older cores would ignore, each with the lowest core that understands it."""
        r = self.requires
        checks = [
            ("requires.coordinator_platforms", r.coordinator_platforms is not None),
            ("requires.unsupported", bool(r.unsupported.runner or r.unsupported.coordinator)),
            ("requires.features", bool(r.features)),
            ("coordinator.env", bool(self.coordinator.env)),
            ("coordinator.variants", bool(self.coordinator.variants)),
            ("runner.env", bool(self.runner.env) or any(v.env for v in self.runner.variants.values())),
            ("stages[].variants", any(s.variants for s in self.stages)),
            ("stages[].placement", any(s.placement for s in self.stages)),
            ("placement", self.placement is not None),
            (f"results.determinism_scope = {self.results.determinism_scope!r}", self.results.determinism_scope not in ("global", "platform")),
            ("bundle.platform_files", bool(self.bundle.platform_files)),
            ("datasets.platform_bound", bool(self.datasets.platform_bound)),
        ]
        out = [(k, PLATFORM_KEYS_CORE) for k, on in checks if on]
        sdk13 = [
            ("stages[].determinism", any(s.determinism for s in self.stages)),
            ("stages[].default", any(s.default for s in self.stages)),
            ("coordinator.capabilities campaign.tick.results", TICK_RESULTS in self.coordinator.capabilities),
            ("the datasets.update and datasets.delete effects", bool({"datasets.update", "datasets.delete"} & set(
                self.coordinator.campaign_effects + self.coordinator.move.effects + [k for o in self.operations for k in o.effects]))),
        ]
        sdk14 = [
            ("stages[].bootstrap", any(s.bootstrap for s in self.stages)),
            ("datasets.pinned", bool(self.datasets.pinned)),
        ]
        sdk15 = [
            ("secrets", bool(self.secrets)),
            ("stages[].secrets", any(s.secrets for s in self.stages)),
            ("the secrets:read:self permission", "secrets:read:self" in self.coordinator.permissions),
            ("sandbox.container_sets", bool(self.sandbox.container_sets)),
            ("a stage reserving the gpu pool", any("gpu" in s.requires.pools for s in self.stages)),
            ("services[].endpoint", any(s.endpoint for s in self.services)),
            ("services[].gpu", any(s.gpu.use != "none" or s.gpu.apis_any for s in self.services)),
            ("runner.gpu.apis_any", any(r.gpu and r.gpu.apis_any for r in [self.runner, *self.runner.variants.values()])),
            ("sandbox.folders", bool(self.sandbox.folders)),
            ("stages[].checkpoint", any(s.checkpoint for s in self.stages)),
            ("runner.checkpoint_grace_s", "checkpoint_grace_s" in self.runner.model_fields_set),
            ("runner capability checkpoint", "checkpoint" in self.runner.capabilities),
            ("ui.views.columns[].type artifact_ref", any(c.type == "artifact_ref" for v in self.ui.views.values() for c in v.columns)),
        ]
        return (out + [(k, SDK13_KEYS_CORE) for k, on in sdk13 if on] + [(k, BOOTSTRAP_KEYS_CORE) for k, on in sdk14 if on]
                + [(k, SDK15_KEYS_CORE) for k, on in sdk15 if on])

    # ------------------------------------------------------------------------ stages

    def stage(self, name: str) -> Stage | None:
        return next((s for s in self.stages if s.name == name), None)

    def chain_stages(self) -> set[str]:
        """Stages that are part of a chain: `after` another, or the stage another is `after`."""
        return {s.name for s in self.stages if s.after} | {s.after for s in self.stages if s.after}

    def standalone_stages(self) -> list[str]:
        """Stages that run a job in one go: neither `after` another nor depended on (declaration order)."""
        chain = self.chain_stages()
        return [s.name for s in self.stages if s.name not in chain]

    def default_stage(self) -> str | None:
        """The single-stage form: the stage a job runs when it names none (and the pipeline is not split): the stage that
        sets `default`, else the only standalone stage."""
        marked = [s.name for s in self.stages if s.default]
        alone = self.standalone_stages()
        return marked[0] if marked else (alone[0] if len(alone) == 1 else None)

    def determinism_of(self, stage: str | None) -> str:
        """A stage's effective determinism (None: the default stage)."""
        st = self.stage(stage or self.default_stage() or "")
        return (st.determinism if st else None) or self.results.determinism

    def compares(self, stage: str | None) -> bool:
        """Whether the host compares this stage's results (replicas, disputes, the result cache, goldens)."""
        return self.determinism_of(stage) != "none"

    def certification_exempt(self, stage: str | None) -> bool:
        """Whether jobs of this stage run on a node before the module is certified there (spec/manifest.md, "Stages that
        run before certification"; None: the default stage): a bootstrap stage, or a stage that compares nothing
        (effective determinism `none`) and needs no node capability and no pool. Such jobs run on any node where the
        module's runner starts (its doctor ran), unless a failed doctor check proves a capability the stage requires."""
        st = self.stage(stage or self.default_stage() or "")
        if st is None:
            return False
        if st.bootstrap:
            return True
        r = st.requires
        return not self.compares(st.name) and not (r.capabilities or r.pools or r.needs_pools)

    def capability_names(self) -> set[str]:
        """Every node capability the module names: its probes and what its services provide (rule 2: a stage requires
        only these). A doctor check of the same name proves the capability (runner_protocol.DoctorCheck)."""
        return {c for svc in self.services for c in svc.provides.capabilities} | {p.name for p in self.probes}

    def secrets_of(self, stage: str | None) -> list[str]:
        """The secrets a stage's runner receives (None: the default stage)."""
        st = self.stage(stage or self.default_stage() or "")
        return list(st.secrets) if st else []

    def container_set(self, repository: str, platform: str) -> ContainerSet | None:
        """The image set a normalized repository (`<registry>/<path>`) and platform belong to."""
        return next((c for c in self.sandbox.container_sets if c.platform == platform and c.covers(repository)), None)

    def is_bootstrap(self, stage: str | None) -> bool:
        """Whether jobs of this stage are bootstrap jobs (None: the default stage, which never is)."""
        st = self.stage(stage) if stage else None
        return bool(st and st.bootstrap)

    def bootstrap_problem(self, payload, artifacts: list) -> str | None:
        """Why a bootstrap job's result is not exactly pinned datasets (None: it is): its payload must be empty, and each
        of its artifacts (at least one; files as {path, digest, size}) must hold exactly one pin's files."""
        if payload:
            return "a bootstrap result carries no payload"
        if not artifacts:
            return "a bootstrap result carries at least one artifact (a pinned dataset)"
        for a in artifacts:
            if self.pin_of(a.get("files") or []):
                continue
            paths = {f.get("path") for f in a.get("files") or []}
            near = max(self.datasets.pinned, key=lambda p: len(paths & {f.path for f in p.files}), default=None)
            name = a.get("name")
            if near is None or not paths & {f.path for f in near.files}:
                return f"artifact {name!r}: its files {sorted(paths)[:3]} are no pinned dataset's"
            want = {f.path: (f.sha256, f.size) for f in near.files}
            have = {f.get("path"): (f.get("digest"), f.get("size")) for f in a.get("files") or []}
            for path in sorted(set(want) | set(have)):
                if path not in have:
                    return f"artifact {name!r}: pinned dataset {near.dataset_id} has {path}, which the artifact lacks"
                if path not in want:
                    return f"artifact {name!r}: {path} is not a file of pinned dataset {near.dataset_id}"
                if have[path] != want[path]:
                    return (f"artifact {name!r}: {path} is sha256 {have[path][0]} ({have[path][1]} bytes); pinned dataset "
                            f"{near.dataset_id} pins {want[path][0]} ({want[path][1]} bytes)")
        return None

    def pin_of(self, files: list) -> PinnedDataset | None:
        """The pinned dataset whose files are exactly these ({path, digest, size})."""
        got = frozenset((f.get("path"), f.get("digest"), f.get("size")) for f in files)
        return next((p for p in self.datasets.pinned if p.contents() == got), None)

    def mix(self) -> str:
        """The placement mix the scheduler applies to the module's units (absent: `any`; unknown: `same-platform`)."""
        return pf.normalize(self.placement.mix if self.placement else None)

    @model_validator(mode="after")
    def _consistency(self):
        for argv in self.execs():
            check_exec(argv)
        self.coordinator.runtime.check_argv(self.coordinator.exec, "coordinator.exec")
        self.runner.runtime.check_argv(self.runner.exec, "runner.exec")
        for key, v in self.runner.variants.items():
            (v.runtime or self.runner.runtime).check_argv(v.exec or self.runner.exec, f"runner.variants.{key}")
        for key, v in self.coordinator.variants.items():
            (v.runtime or self.coordinator.runtime).check_argv(v.exec or self.coordinator.exec, f"coordinator.variants.{key}")
        declared = set(self.requires.platforms)
        declared_os = self.requires.os_platforms()
        for key in self.runner.variants:
            if key not in declared and key not in declared_os:
                raise ValueError(f"runner variant {key!r} is neither a declared platform nor an OS of one")
        self._platform_rules(declared)
        for what, items in (("service", self.services), ("probe", self.probes), ("stage", self.stages)):
            for it in items:
                plats = it.requires.platforms if what == "stage" else it.platforms
                bad = sorted(set(plats) - declared)
                if bad:
                    raise ValueError(f"{what} {it.name!r}: platforms {bad} are not in requires.platforms")
        for cap in [c for svc in self.services for c in svc.provides.capabilities] + [p.name for p in self.probes]:
            if cap.startswith(RESERVED_CAPABILITY_PREFIXES):
                raise ValueError(f"capability {cap!r}: the prefixes {', '.join(RESERVED_CAPABILITY_PREFIXES)} are provided by the agent")
        names = [s.name for s in self.stages]
        if len(set(names)) != len(names):
            raise ValueError("stage names must be unique")
        for s in self.stages:
            if s.after and s.after not in names:
                raise ValueError(f"stage {s.name!r}: after={s.after!r} is not a stage")
            if s.after == s.name:
                raise ValueError(f"stage {s.name!r} cannot depend on itself")
        # dependency cycles
        after = {s.name: s.after for s in self.stages}
        for s in names:
            seen, cur = set(), s
            while cur:
                if cur in seen:
                    raise ValueError(f"stage dependency cycle through {cur!r}")
                seen.add(cur)
                cur = after.get(cur)
        provided_pools = {p for svc in self.services for p in svc.provides.pools}
        clash = provided_pools & set(CORE_POOLS)
        if clash:
            raise ValueError(f"pools {sorted(clash)} are provided by the agent itself; a service cannot provide them")
        if self.sandbox.runs_containers():
            provided_pools |= set(CORE_POOLS)
        provided_caps = {c for svc in self.services for c in svc.provides.capabilities} | {p.name for p in self.probes}
        for s in self.stages:
            for p in list(s.requires.pools) + s.requires.needs_pools:
                if p not in provided_pools:
                    raise ValueError(f"stage {s.name!r} needs pool {p!r} that no declared service provides")
            for c in s.requires.capabilities:
                if c not in provided_caps:
                    raise ValueError(f"stage {s.name!r} needs capability {c!r} that no probe or service provides")
        if (self.services or self.probes) and not self.requires.service_protocol:
            raise ValueError("services/probes are declared, so requires.service_protocol must list a major")
        self._endpoint_rules()
        field_names = {f.name for f in self.results.fields}
        if self.results.value and self.results.value.field not in field_names:
            raise ValueError(f"results.value.field {self.results.value.field!r} is not a declared result field")
        chain = self.chain_stages()
        marked = [s.name for s in self.stages if s.default]
        if len(marked) > 1:
            raise ValueError(f"stages {marked} all set default; at most one stage is the default")
        if marked and marked[0] in chain:
            raise ValueError(f"stage {marked[0]!r}: only a standalone stage can be the default (not `after` another nor "
                             "depended on)")
        if len(self.standalone_stages()) > 1 and not marked:
            raise ValueError(f"stages {self.standalone_stages()} are all standalone: mark the one a job runs when it names "
                             "no stage with default = true")
        for s in self.stages:
            if s.determinism and s.name in chain:
                raise ValueError(f"stage {s.name!r}: determinism applies to standalone stages; a chain compares as "
                                 "results.determinism")
        if not any(self.compares(s.name) for s in self.stages):
            raise ValueError("no stage compares (every stage's determinism is none): goldens need one, and every module "
                             "is certified on golden evidence")
        self._bootstrap_rules(chain)
        self._trust_rules()
        self._checkpoint_rules()
        self._gpu_api_rules()
        if len(self.stages) > 1 and "result.merge" not in self.coordinator.capabilities:
            raise ValueError("a multi-stage module must implement result.merge (coordinator.capabilities)")
        if self.goldens and self.goldens.compare == "verb" and "golden.compare" not in self.coordinator.capabilities:
            raise ValueError("goldens.compare='verb' requires the golden.compare capability")
        if (self.ui.pages or self.ui.panels or self.ui.views or self.ui.iframes) and not self.requires.ui_contract:
            raise ValueError("pages, panels, views or iframes are declared, so requires.ui_contract must be set")
        if self.ui.views and "ui.view.compute" not in self.coordinator.capabilities:
            raise ValueError("[ui.views] requires the ui.view.compute capability")
        verbs = [o.verb for o in self.operations]
        if len(verbs) != len(set(verbs)):
            raise ValueError("operation verbs must be unique")
        for o in self.operations:
            if o.effective_tier() in ("T2", "T3") and not o.preview:
                raise ValueError(f"operation {o.verb}: effective tier {o.effective_tier()} requires preview (op.plan)")
        if self.coordinator.campaign_effects and "campaign.tick" not in self.coordinator.capabilities:
            raise ValueError("coordinator.campaign_effects requires the campaign.tick capability")
        if TICK_RESULTS in self.coordinator.capabilities and "campaign.tick" not in self.coordinator.capabilities:
            raise ValueError(f"the {TICK_RESULTS} capability requires the campaign.tick capability")
        caps = set(self.coordinator.capabilities)
        mv = self.coordinator.move
        if any(r.class_ == "rebuild" for r in mv.rules) and "move.postflight" not in caps:
            raise ValueError("a `rebuild` move rule requires the move.postflight capability (it rebuilds what was skipped)")
        if mv.effects and not caps & set(MOVE_VERBS):
            raise ValueError("coordinator.move.effects requires move.preflight, move.postflight or move.cancelled")
        if self.operations and "op.apply" not in self.coordinator.capabilities:
            raise ValueError("[[operations]] require the op.apply capability")
        low = core_lower_bound(self.requires.core)
        short = [(k, f) for k, f in self.core_keys_used() if low is None or low < f]
        if short:
            floor = ".".join(map(str, max(f for _, f in short)))
            raise ValueError(f"{', '.join(k for k, _ in short)} need requires.core >= {floor} (older cores ignore them); "
                             f"core is {self.requires.core!r}")
        return self

    def endpoint_services_of(self, stage: str | None) -> list[Service]:
        """The endpoint services a job of this stage reaches (None: the default stage): those providing a pool the stage
        reserves (`requires.pools`; `needs_pools` gives no endpoint)."""
        st = self.stage(stage or self.default_stage() or "")
        pools = set(st.requires.pools) if st else set()
        return [s for s in self.services if s.endpoint and pools & set(s.provides.pools)]

    def runner_gpu_need(self, platform: str) -> dict | None:
        """The runner's GPU API group on `platform` (its variant applied), or None when it names no API: `where` is
        `host`, a need of every stage, or `containers` (`in_container`), a need of stages reserving the `gpu` pool."""
        g = self.runner.for_platform(platform).gpu
        if g.use == "none" or not g.apis_any:
            return None
        return {"apis": sorted(set(g.apis_any)), "where": "containers" if g.in_container else "host", "source": "runner"}

    def gpu_needs(self, stage: str | None, platform: str) -> list[dict]:
        """The GPU APIs a job of `stage` (None: the default stage) needs on a node of `platform`, as "any of" groups
        `{apis, where, source}` (spec/runner-protocol.md, "GPU use"): the runner's `gpu` for the platform (its variant
        applied) when it uses a GPU and names APIs, checked against the node's host APIs, or with `in_container` its
        containers' APIs and only for a stage reserving the agent's `gpu` pool; and each GPU service on the platform
        that names APIs and provides a pool the stage reserves, checked against the host's. A node meets the need when
        every group shares an API with its list for that place."""
        st = self.stage(stage or self.default_stage() or "")
        pools = set(st.requires.pools) if st else set()
        run = self.runner_gpu_need(platform)
        out = [run] if run and (run["where"] == "host" or "gpu" in pools) else []
        for s in self.services:
            if s.gpu.use != "none" and s.gpu.apis_any and pools & set(s.provides.pools) and pf.matches(platform, s.platforms):
                out.append({"apis": sorted(set(s.gpu.apis_any)), "where": "host", "source": f"service {s.name}"})
        return out

    def gpu_pools(self) -> set[str]:
        """Pools provided by services that use a GPU: a job reserving one is a GPU job."""
        return {p for s in self.services if s.gpu.use != "none" for p in s.provides.pools}

    def _endpoint_rules(self):
        """Service endpoints and service GPU use (spec/manifest.md, rule 18)."""
        for s in self.services:
            if s.endpoint and not s.provides.pools:
                raise ValueError(f"service {s.name!r}: an endpoint service provides at least one pool (a job reaches it "
                                 "through a pool its stage reserves)")
            if s.endpoint and s.lifecycle == "manual":
                raise ValueError(f"service {s.name!r}: an endpoint service is on_demand or always (the agent never starts "
                                 "a manual service, so it could never hand it its endpoint channel)")
            if s.gpu.use != "none" and self.sandbox.devices.gpu != "compute":
                raise ValueError(f"service {s.name!r}: gpu.use = {s.gpu.use!r} needs sandbox.devices.gpu = 'compute'")

    def _bootstrap_rules(self, chain: set):
        """Bootstrap stages and the pinned dataset table (spec/manifest.md, rule 15)."""
        boot = [s for s in self.stages if s.bootstrap]
        for s in boot:
            if s.name in chain:
                raise ValueError(f"stage {s.name!r}: a bootstrap stage is standalone (neither `after` another nor depended on)")
            if s.name == self.default_stage():
                raise ValueError(f"stage {s.name!r}: a bootstrap stage is never the default stage (a job runs it only by "
                                 "naming it)")
            if self.determinism_of(s.name) != "none":
                raise ValueError(f"stage {s.name!r}: a bootstrap stage has determinism none (its results are never compared, "
                                 "cached or golden-tested)")
            if s.requires.pools or s.requires.needs_pools:
                raise ValueError(f"stage {s.name!r}: a bootstrap stage reserves and needs no pools (its jobs get no container "
                                 "broker, GPU or module services)")
        pins = self.datasets.pinned
        if boot and not pins:
            raise ValueError(f"bootstrap stages {[s.name for s in boot]} need [[datasets.pinned]]: the host registers a "
                             "bootstrap job's output only when it is exactly a pinned dataset")
        if pins and not boot:
            raise ValueError("[[datasets.pinned]] needs a bootstrap stage (pins say what bootstrap jobs may provide)")
        if boot and self.sandbox.net.mode == "egress-any":
            raise ValueError("a module with a bootstrap stage cannot request sandbox.net.mode = 'egress-any': bootstrap jobs "
                             "get the egress allowlist or no network")
        ids = [p.dataset_id for p in pins]
        dup = sorted({i for i in ids if ids.count(i) > 1})
        if dup:
            raise ValueError(f"[[datasets.pinned]] lists {dup} more than once")
        seen = {}
        for p in pins:
            where = f"pinned dataset {p.dataset_id!r}"
            if p.kind not in self.datasets.kinds:
                raise ValueError(f"{where}: kind {p.kind!r} is not in datasets.kinds")
            if p.kind in self.datasets.platform_bound and p.platform is None:
                raise ValueError(f"{where}: kind {p.kind!r} is platform-bound, so the pin sets `platform`")
            if p.platform is not None and p.kind not in self.datasets.platform_bound:
                raise ValueError(f"{where}: `platform` is only for platform-bound kinds (datasets.platform_bound)")
            if p.platform is not None and p.platform not in self.requires.platforms:
                raise ValueError(f"{where}: platform {p.platform!r} is not in requires.platforms")
            paths = [f.path for f in p.files]
            if len(set(paths)) != len(paths):
                raise ValueError(f"{where}: file paths must be unique")
            if p.contents() in seen:
                raise ValueError(f"{where} holds the same files as {seen[p.contents()]!r}: an artifact names its pin by its files")
            seen[p.contents()] = p.dataset_id

    def _trust_rules(self):
        """Secrets (spec/manifest.md, rule 16), and container image sets and the container GPU pool (rule 17)."""
        names = [s.name for s in self.secrets]
        dup = sorted({n for n in names if names.count(n) > 1})
        if dup:
            raise ValueError(f"[[secrets]] lists {dup} more than once")
        used = set()
        for st in self.stages:
            if len(set(st.secrets)) != len(st.secrets):
                raise ValueError(f"stage {st.name!r}: secrets lists a name more than once")
            unknown = [n for n in st.secrets if n not in names]
            if unknown:
                raise ValueError(f"stage {st.name!r}: secrets {unknown} are not declared in [[secrets]]")
            if st.secrets and st.bootstrap:
                raise ValueError(f"stage {st.name!r}: a bootstrap stage receives no secrets (bootstrap jobs get only the "
                                 "egress allowlist)")
            used |= set(st.secrets)
        if "secrets:read:self" not in self.coordinator.permissions:
            unused = [n for n in names if n not in used]
            if unused:
                raise ValueError(f"secrets {unused} reach nothing: list them in a stage's `secrets`, or give the "
                                 "coordinator the secrets:read:self permission")
        sets = [c.name for c in self.sandbox.container_sets]
        dup = sorted({n for n in sets if sets.count(n) > 1})
        if dup:
            raise ValueError(f"[[sandbox.container_sets]] lists {dup} more than once")
        for st in self.stages:
            if "gpu" not in st.requires.pools:
                continue
            gpu = self.runner.gpu
            if "containers" not in st.requires.pools:
                raise ValueError(f"stage {st.name!r}: the gpu pool is GPU passthrough to containers, so the stage also "
                                 "reserves the containers pool")
            if not gpu.in_container or gpu.use == "none":
                raise ValueError(f"stage {st.name!r} reserves the gpu pool: declare runner.gpu.in_container = true and "
                                 "runner.gpu.use = 'shared' or 'exclusive' (GPU admission applies to its jobs)")

    def _gpu_api_rules(self):
        """GPU APIs select nodes only for GPU work (spec/manifest.md, rule 21)."""
        needs = [("runner.gpu", self.runner.gpu)] + [(f"runner.variants.{k}.gpu", v.gpu) for k, v in self.runner.variants.items() if v.gpu]
        needs += [(f"services.{s.name}.gpu", s.gpu) for s in self.services]
        for where, g in needs:
            if g.apis_any and g.use == "none":
                raise ValueError(f"{where}.apis_any needs use = 'shared' or 'exclusive': it places GPU work, and with "
                                 "use = 'none' the work uses no GPU")

    def _checkpoint_rules(self):
        """Portable checkpoints (spec/manifest.md, rule 20)."""
        stages = [s.name for s in self.stages if s.checkpoint]
        cap = "checkpoint" in self.runner.capabilities
        if stages and not cap:
            raise ValueError(f"stages {stages} keep checkpoints, so runner.capabilities lists `checkpoint` (the runner "
                             "honours a checkpoint-then-stop request and resumes from <W>/checkpoint/)")
        if cap and not stages:
            raise ValueError("runner capability `checkpoint` needs a stage with `checkpoint` (its limits)")
        for s in self.stages:
            if s.checkpoint and s.bootstrap:
                raise ValueError(f"stage {s.name!r}: a bootstrap stage keeps nothing, so it never checkpoints")

    def checkpoint_of(self, stage: str | None) -> "StageCheckpoint | None":
        """A stage's checkpoint limits (None: the default stage), or None when it keeps no portable checkpoints."""
        st = self.stage(stage or self.default_stage() or "")
        return st.checkpoint if st else None

    def _platform_rules(self, declared: set):
        """The cross-field rules of the per-platform declarations (spec/manifest.md, rules 9-11 and 13)."""
        r = self.requires
        cplats = r.coordinator_platforms
        for s in self.stages:
            for key in s.variants:
                if not pf.declared(key, declared):
                    raise ValueError(f"stage {s.name!r}: variant {key!r} is neither a declared platform nor an OS of one")
        if cplats is not None:
            for key in self.coordinator.variants:
                if not pf.declared(key, cplats):
                    raise ValueError(f"coordinator variant {key!r} is neither a coordinator platform nor an OS of one")
        for key in r.unsupported.runner:
            if pf.declared(key, declared):
                raise ValueError(f"requires.unsupported.runner {key!r} contradicts requires.platforms")
        if r.unsupported.coordinator and cplats is None:
            raise ValueError("requires.unsupported.coordinator needs requires.coordinator_platforms (absent means every platform)")
        for key in r.unsupported.coordinator:
            if pf.declared(key, cplats):
                raise ValueError(f"requires.unsupported.coordinator {key!r} contradicts requires.coordinator_platforms")
        unknown = [f for f in r.features if f not in KNOWN_FEATURES]
        if unknown:
            raise ValueError(f"requires.features {unknown}: not understood by this SDK (must-understand; known: {list(KNOWN_FEATURES)})")
        bad = sorted(set(self.datasets.platform_bound) - set(self.datasets.kinds))
        if bad:
            raise ValueError(f"datasets.platform_bound {bad} are not in datasets.kinds")
        targets = declared | set(cplats or [])
        for glob, keys in self.bundle.platform_files.items():
            bad = [k for k in keys if not pf.declared(k, targets)]
            if bad:
                raise ValueError(f"bundle.platform_files {glob!r}: {bad} are neither declared platforms nor OSes of one")
        if self.bundle.platform_files:
            for p in sorted(declared):
                execs = [("runner", self.runner.for_platform(p).exec)]
                execs += [(f"service {s.name}", s.exec) for s in self.services if pf.matches(p, s.platforms)]
                execs += [(f"probe {x.name}", x.exec) for x in self.probes if pf.matches(p, x.platforms)]
                for what, argv in execs:               # argv[0], and the script a `python` exec runs
                    for a in argv:
                        if a.startswith(BUNDLE_TOKEN + "/") and not self.bundle.receives(a[len(BUNDLE_TOKEN) + 1:], p):
                            raise ValueError(f"{p}: {what} runs {a!r}, which bundle.platform_files does not send to {p}")


# ---------------------------------------------------------------------------- loading + strict check

def load(path: str | Path) -> Manifest:
    """Lenient load (the core's view): unknown fields are tolerated and preserved."""
    return Manifest.model_validate(tomllib.loads(Path(path).read_text(encoding="utf-8")))


def unknown_fields(model, prefix: str = "") -> list[str]:
    """Every field present in the document but not in the contract (typos, or items from a newer
    schema). `oarbank-sdk check` treats these as errors; the core ignores them."""
    out = []
    for k in (model.model_extra or {}):
        out.append(f"{prefix}{k}")
    for name, value in model:
        items = value if isinstance(value, list) else [value]
        for i, v in enumerate(items):
            if hasattr(v, "model_extra"):
                sub = f"{prefix}{name}" + (f"[{i}]" if isinstance(value, list) else "") + "."
                out.extend(unknown_fields(v, sub))
    return out


def lint(man: Manifest) -> list[str]:
    """Warnings `oarbank-sdk check` prints without failing: placement settings that probably do not mean what they say."""
    out = []
    mixes = [("placement.mix", man.placement.mix)] if man.placement else []
    mixes += [(f"stages.{s.name}.placement.mix", s.placement.mix) for s in man.stages if s.placement]
    for where, mix in mixes:
        if not pf.known_mix(mix):
            out.append(f"{where} {mix!r} is not known to this SDK; it applies as {pf.STRICTEST!r}")
    for s in man.stages:
        if s.placement and not s.after:
            out.append(f"stages.{s.name}.placement constrains a stage and its `after` stage, but {s.name!r} has no `after`")
    for where, runner in [("runner", man.runner)] + [(f"runner.variants.{k}", v) for k, v in man.runner.variants.items()]:
        for c in runner.capabilities or []:
            if c not in RUNNER_CAPABILITIES:
                out.append(f"{where}.capabilities {c!r} is not known to this SDK; the agent ignores it")
    if man.sandbox.net.mode == "egress-any" and any(s.secrets for s in man.stages):
        out.append("stages receive secrets while sandbox.net.mode is 'egress-any': a leaked key can reach any host; an "
                   "egress-allowlist naming only the service the key is for limits that")
    gpus = [("runner.gpu", man.runner.gpu)] + [(f"runner.variants.{k}.gpu", v.gpu) for k, v in man.runner.variants.items() if v.gpu]
    gpus += [(f"services.{s.name}.gpu", s.gpu) for s in man.services]
    for where, g in gpus:
        unknown = [a for a in g.apis_any if a not in gpu.KNOWN_APIS]
        if unknown:
            out.append(f"{where}.apis_any {unknown}: no core detects {'it' if len(unknown) == 1 else 'them'} yet, so a node "
                       "is never found to provide " + ("it" if len(unknown) == 1 else "them"))
    # a Windows node's container runtime runs its own architecture only (spec/sandbox.md, "Containers")
    images = {c.platform for c in man.sandbox.containers} | {c.platform for c in man.sandbox.container_sets}
    for plat in man.requires.platforms:
        os_, arch = plat.split("-", 1) if "-" in plat else (plat, "")
        if images and os_ == "windows" and f"linux/{arch}" not in images:
            out.append(f"requires.platforms lists {plat}, but no container image or set is for linux/{arch}: Windows nodes run "
                       f"containers of their own architecture only, so its containers cannot run there")
    scope = man.results.determinism_scope
    if scope not in pf.SCOPE_MIX:
        out.append(f"results.determinism_scope {scope!r} is not known to this SDK; replicas compare within one platform")
    if man.results.value and pf.looser(man.mix(), pf.scope_mix(scope)):
        out.append(f"placement mix {man.mix()!r} is coarser than results.determinism_scope {scope!r}: values in one "
                   f"campaign may come from platforms whose results are not comparable (set [placement].mix = "
                   f"{pf.scope_mix(scope)!r} to keep each campaign in one class)")
    return out
