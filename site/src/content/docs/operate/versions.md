---
title: Versions and compatibility
description: Find the Oarbank, agent and module SDK versions you have installed, the latest release, which SDK features need which core, and where the docs for an older version are.
type: reference
platforms: [macos, linux, windows]
core: '2.7.0'
sidebar:
  order: 4
---

Oarbank has two version lines. The **core** (the coordinator and the node agent, in
[roeehrl/oarbank](https://github.com/roeehrl/oarbank)) is at 2.7.0. The **module SDK** (`oarbank-sdk`, in
[roeehrl/oarbank-sdk](https://github.com/roeehrl/oarbank-sdk)) is at 1.5.0. These docs describe those releases.

## Find the installed versions

| What | How |
|---|---|
| Coordinator, macOS | `defaults read "/Applications/Oarbank Coordinator.app/Contents/Info" CFBundleShortVersionString` |
| Coordinator, Linux | `dpkg-query -W oarbank-coordinator` (Debian, Ubuntu) or `rpm -q oarbank-coordinator` (Fedora, RHEL) |
| Coordinator, Windows | Settings, Installed apps, **Oarbank Coordinator**; or in PowerShell: `Get-ItemProperty HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\* \| Where-Object DisplayName -like 'Oarbank*' \| Select-Object DisplayName, DisplayVersion` |
| Every node's agent | `oarbank agent list` on the coordinator shows the version each node runs; `oarbank node show <node>` shows one |
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
| 2.7.0 | 2026-10-09 | 1.5.0 |
| 2.6.0 | 2026-10-08 | 1.5.0 |
| 2.5.0 | 2026-10-06 | 1.5.0 |

Each release's notes are on its release page.

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

The `oarbank-sdk` package follows SemVer. Its major equals the highest module protocol major it speaks, and the latest
two majors receive fixes.

## Docs for an older version

This site always describes the current release. For an older installation, read the documents in the repositories at
that version's tag. They are the sources of these pages:

- Core install, update and removal: `https://github.com/roeehrl/oarbank/blob/v<version>/docs/install.md`
- SDK guides and the specification: `https://github.com/roeehrl/oarbank-sdk/tree/v<version>/docs` and `…/spec`

Before following a page here on an older installation, compare the page's version note with yours, or upgrade first.
