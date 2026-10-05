# Platforms

Oarbank runs modules on macOS, Windows and Linux, each on arm64 and amd64, and fleets may mix them. This page holds
the rules every contract shares. Each rule is pinned by test vectors in [vectors/](vectors/) that every implementation
(SDK, coordinator, agents) must reproduce.

## Platform tokens

A platform is written `<os>-<arch>`:

- `os` ∈ `darwin`, `linux`, `windows`;
- `arch` ∈ `arm64`, `amd64` (the OCI spelling; never `x86_64`, `x64` or `aarch64`);
- `linux-*` means glibc. A musl or other libc gets its own token later.

The set is **open**. A well-formed token nobody knows yet (pattern `^[a-z][a-z0-9]*-[a-z0-9_]+$`) is valid: it means
"no node of this platform here", never a validation error. Container images use the OCI form (`linux/amd64`).

## Portable paths

Every path that crosses the protocol is a **PortablePath**: bundle files, artifact files, mounts, dataset files, module
files and move rules. It is valid on every filesystem Oarbank supports.

- relative, `/`-separated, no leading `/`, no `\`, no `:`, no drive letters;
- ASCII only, NFC (non-NFC input is rejected, not normalised), at most 200 bytes, segments at most 100 bytes;
- segments match `[A-Za-z0-9_+@-][A-Za-z0-9._+@-]*`. Bundles also allow a leading `.` (`.gitattributes`);
- no `.` or `..` segment, no empty segment, no segment ending in `.` or a space;
- no Windows reserved device name, with or without an extension, in any case: `CON PRN AUX NUL CONIN$ CONOUT$
  COM0–COM9 LPT0–LPT9` and the superscript forms `COM¹ COM² COM³ LPT¹ LPT² LPT³`;
- **case-fold unique** within one set: one bundle, one artifact list, one dataset, one module's files.

Hosts convert to native separators at the edge. Writers always produce `/`.

## Encodings and line endings

Every protocol file and stream is UTF-8 without a BOM: specs, results, failures, events, control, phase, doctor and
service output, RPC, broker messages, manifests. Lines end in LF. Readers strip a trailing CR. Data never depends on the
locale: the agent passes `PYTHONUTF8=1` to Python runtimes on every OS, and a UTF-8 `LANG` on POSIX.

## Canonical JSON

Job keys and every digest computed over JSON use **RFC 8785 (JCS)**, plus three refusals:
- NaN and infinities;
- integers beyond ±2^53 (send them as decimal strings);
- non-string object keys.

`oarbank_sdk.keys.canonical_json` is the reference implementation.

## Process containers

Every module process the agent starts lives in a **process container** the agent can stop, kill, freeze and account as
a whole, including every descendant:

| OS | Container |
|---|---|
| macOS | process group plus an escaped-descendant sweep |
| Linux | cgroup v2 leaf |
| Windows | Job Object, assigned at creation, kill-on-close, no breakaway |

A module process never detaches. It does not daemonise, register launchd, systemd, scheduled-task or service jobs, or
break away from its Job.

## Environment per OS

The agent passes exactly the variables in [runner-protocol.md](runner-protocol.md#environment), with the conventional
ones derived per OS:

| Purpose | darwin, linux | windows |
|---|---|---|
| search path | module environment `bin` + `/usr/bin:/bin:/usr/sbin:/sbin` | environment `Scripts` + `%SystemRoot%\System32;%SystemRoot%;%SystemRoot%\System32\Wbem`, `PATHEXT=.COM;.EXE` |
| home | `HOME` | `USERPROFILE`, `HOMEDRIVE`/`HOMEPATH`; `APPDATA` under it; `LOCALAPPDATA` the host account's (an AppContainer start points it at the container's own profile folder, which exists only there) |
| temporary files | `TMPDIR` | `TEMP`, `TMP` (an AppContainer start points them into the container's profile folder) |
| system | none | `SystemRoot`, `SystemDrive`, `windir`, `ComSpec`, `PROCESSOR_ARCHITECTURE`, `NUMBER_OF_PROCESSORS` |
| text | `LANG=C.UTF-8` (Linux), `en_US.UTF-8` (macOS) | none |
| Python | `PYTHONUTF8=1` | `PYTHONUTF8=1` |

Module environments put interpreters in `bin/` on POSIX and in `Scripts\` on Windows. The `python` exec token resolves
to the right one ([manifest.md](manifest.md#exec)).

## What each OS enforces

Nodes report, per capability, whether it is **enforced**, **cooperative** or **unavailable**: sandbox elements, freeze,
CPU caps, memory caps, presence signals. The coordinator places a module only where everything it needs is enforced.
Nothing runs with less isolation than an operator approved.

## Agent duties

- **Executable locations.** Bundles, module environments and data directories sit on filesystems that allow
  execution, with no quarantine or Mark-of-the-Web attributes.
- **Short work roots.** Work roots are short (Windows `C:\ob\w\<id>`), so relative paths keep within MAX_PATH. Socket
  and pipe names stay under 100 bytes.
- **File replacement.** Readers of files that another party replaces (control, phase) close them promptly. Writers
  replace atomically and retry for up to 2 s on a sharing violation (Windows).

## Per-platform declarations

A module says where it runs, why it does not run elsewhere, and what changes per platform, all in its manifest
([manifest.md](manifest.md#platforms)). Every key below is additive to manifest 1 and needs `requires.core >= 2.2`, so
an older core, which would ignore it, refuses the module at install.

**Where it runs.**

- `requires.platforms` lists the **node** platforms: where the runner, services and probes run.
- `requires.coordinator_platforms` lists the platforms of the **coordinator host** the coordinator side runs on.
  Absent means any. A core refuses to install or enable the module on a coordinator host not listed, and a coordinator
  move to such a host is blocked.
- `[requires.unsupported]` gives reasons, shown by explain and the console: `runner = { windows-arm64 = "no arm64
  build" }`, `coordinator = { windows = "uses fork()" }`. A key is a token or an OS and never names an allowed platform
  or its OS; `coordinator` reasons need `coordinator_platforms`.

**What changes per platform.** A per-platform table is keyed by a platform token (`linux-amd64`) or an OS name
(`linux`).

| Table | May set |
|---|---|
| `[runner.variants.<key>]` | `exec`, `runtime`, `capabilities`, `stop_grace_s`, `gpu`, `env` |
| `[coordinator.variants.<key>]` (for the coordinator host's platform) | `exec`, `runtime`, `timeouts_s`, `concurrency`, `env` |
| `[stages.variants.<key>]` | `timeout_s`, `requires.resources`, `retry` |

Resolution, pinned by [vectors/variant-resolution.json](vectors/variant-resolution.json):

- the OS's entry applies first, then the token's: the most specific key wins;
- each field a variant sets replaces the base value whole (an `exec`, a `runtime`, a `gpu`), except the named tables
  `env`, `timeouts_s` and a stage's `requires` and `resources`, which merge key by key;
- `oarbank_sdk.platform.resolve(table, token, default)` picks a table entry (token, then OS, then the default), and
  `Runner.for_platform`, `Coordinator.for_platform` and `Stage.for_platform` give a section's resolved view.

**Environment.** `[runner].env`, `[coordinator].env` and the variants' `env` add variables after the host's own (for
example `MKL_CBWR` or `OMP_NUM_THREADS`, which decide whether BLAS results are reproducible). Names match
`^[A-Z][A-Z0-9_]*$`. Never allowed (compared case-insensitively): `OARBANK_*`, `PATH`, `PATHEXT`, `HOME`, `USERPROFILE`,
`HOMEDRIVE`, `HOMEPATH`, `APPDATA`, `LOCALAPPDATA`, `TMPDIR`, `TEMP`, `TMP`, `SYSTEMROOT`, `SYSTEMDRIVE`, `WINDIR`, `COMSPEC`,
`PROCESSOR_ARCHITECTURE`, `NUMBER_OF_PROCESSORS`, `LANG`, `LC_ALL`, `PYTHONUTF8` and the proxy variables
(`oarbank_sdk.manifest.RESERVED_ENV`).

**Files per platform.** `[bundle.platform_files]` maps a glob to the platforms or OSes whose nodes receive the matching
files; unmatched files go everywhere. The bundle and its digest stay whole: the host builds each platform's release from
its subset (`oarbank_sdk.bundle.subset`), and the coordinator and the CLI always have every file. Every bundle path an
exec names must reach each platform that runs it.

**Goldens per platform.** A `Golden` may list `platforms` (only those nodes get it) and `expected_by_platform` (the
expected value per token or OS, for results that legitimately differ). The host resolves the most specific key, then
`expected`. `oarbank_sdk.goldens.load(root, glob, node_class)` does both for a module that keeps its goldens as files.

**Datasets.** Kinds listed in `[datasets].platform_bound` (an index built by a native tool, say) carry a `platform` at
creation, and the jobs that use one run only there.

**Must-understand features.** `requires.features` lists features a core must understand or refuse the module. This SDK
knows `placement`.

**How code learns its platform.**

| Side | Where |
|---|---|
| Runner, doctor, services | `OARBANK_PLATFORM`; the spec envelope's `platform` |
| Coordinator side | `initialize`'s `host.platform` and `OARBANK_PLATFORM`; `ctx.host_platform` in `oarbank_sdk.server` |
| Any Python code | `oarbank_sdk.platform.current()`: `OARBANK_PLATFORM`, else this machine |

Verbs keep job keys platform-independent: never put a platform in `key_inputs`. A result's platform is recorded by the
host and returned with it (`CampaignJob.platform`).

## Placement

A module may keep each **unit of work** on one **platform class** while other units use other classes: for example,
every job of a campaign on one OS, so that values compared within the campaign come from comparable builds.
`[placement]` in the manifest sets the policy ([manifest-reference.md](manifest-reference.md)); absent, the mix is `any`
and nothing changes.

**Classes.** `class_key(token, mix)`:

| `mix` | Class of `linux-amd64` |
|---|---|
| `any` | `*` (one class for the whole fleet) |
| `same-os` | `linux` |
| `same-arch` | `amd64` |
| `same-platform` | `linux-amd64` |

Strictness: `any` < `same-os`, `same-arch` < `same-platform`; `same-os` combined with `same-arch` is `same-platform`.
`mix` is an open set: an unknown value applies as `same-platform` (fail safe), and `oarbank-sdk check` warns. Pinned by
[vectors/placement-class.json](vectors/placement-class.json); `oarbank_sdk.platform` is the reference implementation.

**Units.** `unit` is `campaign` (the default), `group` (jobs.enqueue `group` within a campaign), `dataset` (the jobs of
one dataset within a campaign) or `pipeline` (a head and tail stage chain, with its replicas and tie-breaks). A stage's
`placement = { mix = … }` constrains it and its `after` stage; the stricter of it and `[placement].mix` applies.

**Feasible classes.** A unit binds only to a class where all its work can run: the classes of `requires.platforms`,
kept where every stage it runs can run (stage `requires.platforms`), where its jobs' and datasets' platforms allow, and
where eligible certified nodes exist. So a head stage never binds to a class where its tail cannot run
(`oarbank_sdk.platform.feasible`).

**Binding.** `bind = "capacity"` (the default for campaigns) binds at creation to the feasible class with the most free
certified CPU; `first-claim` (the default otherwise) binds to the class of the first node that claims one of the unit's
jobs; `explicit` waits for a pin. A binding stays soft until the unit's first accepted result (or result-cache hit)
makes it hard; a soft binding whose first attempt failed is released.

**Stranded units.** When no node of the bound class has been eligible for `stranded_after_s`, `rebind = "never"` (the
default) raises an alert naming the remedy, and `if-stranded` rebinds to the best feasible class and re-queues the
unit's finished jobs.

**Campaigns.** `campaigns.create` may pass `placement {mix, unit, bind, pin}`: stricter than the manifest, never looser
(the host refuses with `placement_looser_than_manifest`). `pin` binds the campaign now, for example one campaign per
platform with `fx.placement("same-platform", pin="linux-amd64")`.

**Determinism scope.** `results.determinism_scope` (`global`, `os`, `arch` or `platform`) decides which replicas
compare; placement decides which results share a unit. A mix coarser than the scope while `results.value` is set means a
campaign may compare values from classes whose results are not comparable, and `oarbank-sdk check` warns.
