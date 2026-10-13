---
title: Versions and compatibility
description: Find the Oarbank, agent and module SDK versions you have installed, the latest release, which SDK features need which core, and where the docs for an older version are.
type: reference
platforms: [macos, linux, windows]
core: '2.10.2'
sidebar:
  order: 19
---

Oarbank has two version lines. The **core** (the coordinator and the node agent, in
[roeehrl/oarbank](https://github.com/roeehrl/oarbank)) is at 2.10.2. The **module SDK** (`oarbank-sdk`, in
[roeehrl/oarbank-sdk](https://github.com/roeehrl/oarbank-sdk)) is at 1.7.0. These docs describe those releases.

## Find the installed versions

| What | How |
|---|---|
| Coordinator, macOS | `defaults read "/Applications/Oarbank Coordinator.app/Contents/Info" CFBundleShortVersionString` |
| Coordinator, Linux | `dpkg-query -W oarbank-coordinator` (Debian, Ubuntu) or `rpm -q oarbank-coordinator` (Fedora, RHEL) |
| Coordinator, Windows | Settings, Installed apps, **Oarbank Coordinator**; or in PowerShell: `Get-ItemProperty HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\* \| Where-Object DisplayName -like 'Oarbank*' \| Select-Object DisplayName, DisplayVersion` |
| Every node's agent | `oarbank agent list` on the coordinator shows the version each node runs; `oarbank node show <node>` shows one |
| A node's package | `oarbank-node --version` on the node (the launcher's version) |
| Module SDK | `python -c "import oarbank_sdk; print(oarbank_sdk.__version__)"` in the environment you installed it in |
| A module's needs | its manifest's `requires.core` (for example `">=2.5,<3"`); `oarbank modules` lists every installed module's |

Nodes update their agent through the coordinator ([Updating and removing](/oarbank/operate/update-and-remove)), so a
node's agent version can differ from the version of the package first installed on it.

## Find the latest release

The [releases of roeehrl/oarbank](https://github.com/roeehrl/oarbank/releases) and of
[roeehrl/oarbank-sdk](https://github.com/roeehrl/oarbank-sdk/releases). For a script:

```sh
curl -s https://api.github.com/repos/roeehrl/oarbank/releases/latest | grep '"tag_name"'
curl -s https://api.github.com/repos/roeehrl/oarbank-sdk/releases/latest | grep '"tag_name"'
```

The first line of [llms.txt](/oarbank/llms.txt) names the release these docs describe.

## Releases

| Core | Date | Module SDK |
|---|---|---|
| 2.10.2 | 2026-10-13 | 1.7.0 |
| 2.9.0 (shipped in 2.10.2) | 2026-10-13 | 1.6.0 |
| 2.8.0 | 2026-10-10 | 1.5.0 |
| 2.7.0 | 2026-10-09 | 1.5.0 |
| 2.6.0 | 2026-10-08 | 1.5.0 |
| 2.5.0 | 2026-10-06 | 1.5.0 |

Each release's notes are on its release page, and each repository keeps a changelog: the core's
[CHANGELOG.md](https://github.com/roeehrl/oarbank/blob/main/CHANGELOG.md) (what changes for the people who run a fleet,
and the upgrade steps) and the SDK's [CHANGELOG.md](https://github.com/roeehrl/oarbank-sdk/blob/main/CHANGELOG.md).

## Which SDK features need which core

A module declares the cores it runs on in `requires.core`. The SDK refuses a manifest that uses a key older cores
would silently ignore unless `requires.core` excludes those cores, and a coordinator refuses to install a module
whose `requires.core` excludes it ([Versioning and deprecation](/oarbank/spec/versioning)).

| Manifest features | Added in SDK | Need `requires.core` |
|---|---|---|
| Per-platform keys and placement | 1.1 | `>= 2.2` |
| Stages, tick results, datasets | 1.3 | `>= 2.3` |
| Bootstrap stages and pinned datasets | 1.4 | `>= 2.4` |
| Secrets, container image sets, the container GPU pool, GPU placement by API, service endpoints, service GPU use, folder grants, portable checkpoints, the `artifact_ref` cell type | 1.5 | `>= 2.5` |
| Host tool version constraints and `arch` other than `any` (`[sandbox].tools`), module settings with `x-oarbank` `scope = "node"`, `required` or `campaign` | 1.6 | `>= 2.9` |
| Inbound listeners: `[sandbox].net.inbound` and `services[].listeners` | 1.7 | `>= 2.10` |
| Workloads: `[[workloads]]`, `requires.workload_protocol` and the `workload.report` capability | 1.7 | `>= 2.10` |

Stages that run before certification ([Running modules](/oarbank/operate/modules#which-stages-run-before-certification))
need no new key and no core floor: a 2.9 coordinator applies the rule to modules as they are.

Some 1.7 features need no floor because older cores ignore them harmlessly; they take effect only on 2.10:

| Manifest feature | Added in SDK | On a core before 2.10 |
|---|---|---|
| Network-heavy stages: `stages[].requires.resources.network = "heavy"` (a workload's `resources.network` comes with workloads, which need 2.10) | 1.7 | Ignored: the job runs as any other, with no [site](/oarbank/operate/sites) hold or budget |
| A `cooperative_throttle` runner that holds `gpu_duty` with the SDK's duty governor, and `duty_specs` in `conformance.json` | 1.7 | No `gpu_duty` is ever written, so the runner runs flat out ([Share a GPU with a protected program](/oarbank/build/gpu-duty)) |

A module with a workload or a listener also needs nodes that run the 2.10 agent: an older agent runs neither
([Upgrading](/oarbank/operate/upgrading#upgrade-the-nodes-to-210)).

The `oarbank-sdk` package follows SemVer. Its major equals the highest module protocol major it speaks, and the latest
two majors receive fixes.

## Docs for an older version

This site always describes the current release. For an older installation, read the documents in the repositories at
that version's tag. They are the sources of these pages:

- Core install, update and removal: `https://github.com/roeehrl/oarbank/blob/v<version>/docs/install.md`
- SDK guides and the specification: `https://github.com/roeehrl/oarbank-sdk/tree/v<version>/docs` and `…/spec`

Before following a page here on an older installation, compare the page's version note with yours, or upgrade first.
