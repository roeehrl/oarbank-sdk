---
title: Host tools
description: How nodes find the programs modules need, such as a JDK or Python, what a job waiting with TOOL_NOT_FOUND or TOOL_VERSION_UNMET means, and how to install a tool, re-detect, add search paths or choose an installation per node.
type: how-to
platforms: [macos, linux, windows]
core: '2.9.0'
sidebar:
  order: 7
---

Some modules need a program installed on the node: a JDK, a Python, a tool such as samtools. From Oarbank 2.9 a module
asks for it by **tool id and version** (`jdk >=17`), every node finds its own installations and reports them, and the
coordinator places the module's jobs only on nodes that have a version it accepts. Nobody maps paths per OS by hand.

This replaces the tool registry of 2.8 and earlier, where an admin mapped ids such as `java17` to paths per OS. A
module version that still asks for such an id needs a new version ([Upgrading](/oarbank/operate/upgrading#modules-that-used-tool-registry-ids)).

## Where to look

- The node page's **Host tools** section: every installation the node found (path, version, architecture, vendor,
  how it was found, status), what each module gets, the paths set for the node, and **Re-detect**.
- The module page's **Nodes** tab (for a module that asks for tools): a row per node and tool with what was detected,
  the override, what is in effect and the fix.
- **Settings → Tools**: the tool definitions, their search paths, which modules ask for each, and a warning for a tool
  an approved module version asks for that the fleet does not define.
- The module's readiness checklist step **Host tools on the nodes**: per tool, how many nodes have it, grouped by
  reason, with the install command.

From a terminal:

```sh
oarbank tools                              # definitions and what every node found
oarbank tools --node build-07              # one node, with each module's resolution
oarbank tools --module reel                # a module's node-by-node matrix, with the fixes
oarbank node show build-07                 # also lists the node's tools
```

## When a job waits for a tool

A module's jobs wait on a node, with one of these codes in `oarbank explain job <id>`, until a tool it asks for
resolves there:

| Code | Means | Example |
|---|---|---|
| `TOOL_NOT_FOUND` | No installation of the tool on the node, the node has not reported yet, or the fleet defines no such tool | `jdk >=17: no jdk found on this node` |
| `TOOL_VERSION_UNMET` | Installations exist, but none the module accepts (or the one set for the node is not accepted) | `jdk >=17: found 11.0.2 at /usr/lib/jvm/java-11; needs >=17` |
| `TOOL_REFUSED` | The path set for the node was refused or is not an installation it found | `jdk >=17: the path set for jdk here, /srv/jdk, was refused: /srv/jdk does not exist here` |

Stages that need no certification are spared, because they get no tools ([Running modules](/oarbank/operate/modules#which-stages-run-before-certification));
the golden jobs and every other stage wait. A node running an agent older than 2.9 reports no tools, so it shows
`TOOL_NOT_FOUND` until its agent is updated.

Each failure comes with its fixes, in this order.

### Install it, then re-detect

Oarbank does not install tools itself. The failure shows the exact command for the node's package manager, for a
version the module accepts (for a JDK, the lowest long-term release that fits):

| | Example |
|---|---|
| macOS | `brew install openjdk@17` |
| Debian, Ubuntu | `sudo apt install openjdk-17-jdk-headless` |
| Fedora, RHEL | `sudo dnf install …` (the console names the package) |
| Windows | `winget install EclipseAdoptium.Temurin.17.JDK` |

The machine's administrator runs it. Then press **Re-detect** on the node page, or:

```sh
oarbank tools detect build-07 --wait       # waits (120 s by default) and prints what the node found and each module's resolution
```

Nodes also detect again at startup, after installing a release, when their tool paths change, and every hour. The
node's Host tools section says "waiting for the node's next heartbeat" (or that it is offline) until the node reports a
detection made after you asked, then "Detected again".

### Add a search path

If the tool is installed somewhere the built-in search does not look, add a pattern for the OS:

```sh
oarbank tools define jdk --search darwin=/opt/java/*/Contents/Home
oarbank tools define jdk --search linux=/srv/jdks/*
```

A pattern is an absolute path whose components may hold `*` and `?` (within one component), or `$VAR` with an optional
sub-path. The first directory is literal, and `**`, `[ ]`, `..` and a bare root are refused, so a pattern never scans a
whole disk. A scope is `fleet` (every OS), `darwin`, `linux` or `windows`. Changing a definition rebuilds every
platform's release, which you then sign ([Running modules](/oarbank/operate/modules#sign-each-platforms-release)).

### Choose the installation on a node

Where a node has several installations, it picks the best match: its own architecture first, then the highest version
the module accepts. To choose another one, set its path on the node:

```sh
oarbank settings set tool.jdk.path /Library/Java/JavaVirtualMachines/temurin-17.jdk/Contents/Home --node build-07
oarbank settings set tool.jdk.path /usr/lib/jvm/java-17-openjdk-amd64 --node build-08 --module reel   # for one module only
oarbank settings reset tool.jdk.path --node build-07
```

The node page has **Set path on this node**, with the installations it found. `tool.<id>.path` is an ordinary
setting ([Settings](/oarbank/operate/settings)): a value set for a module beats a plain one at any scope, and the most
specific scope wins. At the fleet or a group, a path is a choice each node makes among its own installations; to add
a place to look everywhere, use a search path instead.

A path the node did not find itself is a grant decision. It goes into the node's signed statement (`oarbank node sign
<node>` in signing mode, on the computer with your owner key), and the node checks it before granting it: it refuses
roots, home directories, Oarbank's data directory and system directories themselves (`/usr/lib/jvm/...` is fine,
`/usr` is not), then verifies it like anything it found. A pinned path that is not an installation the module accepts
does not resolve; the node never falls back silently.

### Define a new tool

`jdk` and `python` are built in. For any other program, define a tool of kind `executable` with where it is and how to
read its version:

```sh
oarbank tools define samtools --kind executable \
  --search darwin=/opt/homebrew/bin/samtools --search linux=/usr/bin/samtools \
  --version-regex 'samtools ([0-9.]+)'            # --version-args defaults to --version
oarbank tools delete samtools                     # a built-in tool loses only your extra patterns
```

The regex's first group is the version; look-around and back-references are not supported.

## How nodes detect tools

- **A JDK is read, never run.** A directory counts when it has `bin/java` and a `release` file; its `JAVA_VERSION`,
  `OS_ARCH` and `IMPLEMENTOR` give the version, architecture and vendor (a JDK 8's `1.8.0_392` is reported as
  `8.0.392`). The built-in search covers Homebrew's `openjdk` formulae and `/Library/Java/JavaVirtualMachines` on
  macOS, `/usr/lib/jvm`, `/usr/lib64/jvm`, `/usr/java`, `/opt/java`, `/opt/jdk*` and Linuxbrew on Linux, the usual
  vendors' folders under `C:\Program Files` on Windows, and `$JAVA_HOME` everywhere.
- **Any other tool runs its version command in the sandbox**: read and execute on that file only, no network, a
  private temporary directory, 10 seconds at most. A node that cannot sandbox reports the tool refused instead. The
  built-in Python search covers Homebrew, `/usr/local/bin`, python.org's framework builds, `/usr/bin` on Linux and the
  usual Windows folders; macOS's `/usr/bin/python3` is left out on purpose (it can open an installer dialog).
- **The node's owner can add candidates** in `tool-hints.json` in the agent's home, for example
  `{"jdk": ["/opt/jdks/zulu-17"]}`. Hints are verified like anything else found.
  `OARBANK_TOOLS_BUILTIN_SEARCH=0` in the agent's environment searches only the fleet's paths, the hints and paths set
  for the node.
- On the node, `oarbank-agent tools detect`, run as the agent's account, prints what it finds.

## For module authors

A module asks for tools in its manifest's `[sandbox].tools`, and its runner reads the granted installation from
`OARBANK_TOOLS_FILE`. See [Ask for host tools](/oarbank/build/host-tools).
