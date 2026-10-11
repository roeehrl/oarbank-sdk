# Ask for host tools

Some modules need a program the node already has: a JDK, a Python, a tool such as samtools. A module asks for each one
by the fleet's **tool id** and the **versions** it accepts; every node finds its own installations, and the agent grants
the module one installation per tool. The module never names a path. Host tool versions and architectures need
`requires.core >= 2.9`. The contract is [sandbox.md](../spec/sandbox.md#host-tools); this page shows the steps.

## Ask for the tool

1. Declare the tools in `[sandbox].tools` and raise `requires.core`:

   ```toml
   [requires]
   core = ">=2.9,<3"

   [sandbox]
   tools = [
     { id = "jdk", version = ">=17, <22", arch = "native", trust = "code-exec" },
     { id = "python", version = "~> 3.12" },
     { id = "samtools", version = ">=1.19" },
   ]
   ```

   | Key | Default | Meaning |
   |---|---|---|
   | `id` | required | A fleet tool definition. `jdk` and `python` are built into Oarbank; the fleet's admin defines any other (an executable with its version command). Each id at most once per manifest. |
   | `version` | any version | Comma-separated clauses, all of which must hold (see below). |
   | `arch` | `any` | `any`: the node's own architecture preferred, another accepted. `native`: only the node's own (no x86_64 JDK under Rosetta on Apple silicon). `arm64` or `amd64`: only that one. |
   | `trust` | `read` | `code-exec` for a tool that runs arbitrary code (a JVM, an interpreter, a shell); the owner sees it flagged when approving. |

2. Read the granted installation in the runner (and in `doctor`, services and probes):

   ```python
   from oarbank_sdk import tools
   java = tools.path("jdk") + "/bin/java"     # the JDK's home directory, as the node granted it
   tools.version("jdk")                       # "17.0.12"
   ```

   The agent writes `OARBANK_TOOLS_FILE` for every module process:
   `{"jdk": [{"path": "<canonical path>", "version": "17.0.12", "arch": "aarch64"}]}`. The path is a JDK's home, an
   executable's file, or an interpreter's file (its prefix is granted with it); the sandbox grants read and execute on
   exactly that. `tools.path` raises `ToolMissing` when the module was not granted the tool on this node. Never probe
   conventional locations: links such as `/opt/homebrew/opt/...` are not readable inside the sandbox, only the
   canonical paths in the file. There are no tool-specific variables such as `JAVA_HOME`.

3. Give the conformance kit an installation on your computer, in `conformance.json` next to the manifest (a path, or
   `{path, version, arch}`):

   ```json
   {"tools": {"jdk": {"path": "/opt/homebrew/opt/openjdk@17", "version": "17.0.12", "arch": "aarch64"}}}
   ```

   ```sh
   oarbank-sdk check .        # refuses a bad constraint or an id listed twice
   oarbank-sdk conform .      # grants each tool in the sandbox and lists it in OARBANK_TOOLS_FILE, as an agent does
   ```

   A `version` your constraint does not accept fails the kit's check `host tool versions`
   ([conformance.md](../spec/conformance.md#fixtures)).

## Version constraints

Clauses are separated by commas and all must hold. The operators are those of HashiCorp Nomad:

| Operator | Means | Example |
|---|---|---|
| `=` (or `==`) | exactly | `=17.0.2` |
| `!=` | anything but | `!=21.0.0` |
| `>`, `>=`, `<`, `<=` | ordered comparison | `>=17, <22` |
| `~>` | the last given segment may grow | `~> 17.0.2` is `>=17.0.2, <17.1`; `~> 17` is `>=17, <18` |

Versions are dot-separated numbers (`_` separates too), with an optional pre-release (`22-ea`, which sorts below `22`)
and build (`+7`, ignored); missing segments are zero. A node reports a JDK 8 (`1.8.0_392` in its `release` file) as
`8.0.392`. The SDK stores the constraint in its canonical spelling. The reference implementation is
`oarbank_sdk.toolversion`, and [vectors/tool-versions.json](../spec/vectors/tool-versions.json) holds the cases the SDK,
the coordinator and the agent all replay.

## What the owner sees

The owner approves the whole set of requests by the bundle's digest, so a new version range is a new approval. Then
each node reports what it found, and the coordinator places the module's jobs only where every tool resolves. Where one
does not, the jobs wait with `TOOL_NOT_FOUND` (nothing installed, or no definition for the id), `TOOL_VERSION_UNMET`
(installed, but no version the module accepts) or `TOOL_REFUSED` (a path the owner set was refused), and the module's
readiness checklist shows, per tool, how many nodes have it and the install command for the others. Stages that need no
certification get no tools and are not held back ([manifest.md](../spec/manifest.md#stages-that-run-before-certification)).
Bootstrap jobs get no tools either.

The owner can choose among a node's installations, or add a path, with the `tool.<id>.path` setting, optionally for one
module only; the agent still verifies that it is an installation your request accepts. The owner's side is on
[Host tools](https://docs.codonic.dev/oarbank/operate/host-tools).

## Move from tool registry ids

Before core 2.9 a module named an operator-defined registry id (`java17`) that the coordinator mapped to paths per OS.
That registry is gone. To move a module:

1. Replace the id with the built-in tool and a constraint: `{ id = "java17" }` becomes
   `{ id = "jdk", version = ">=17", trust = "code-exec" }`.
2. Read the tools file as above: each entry is now an object with `path`, `version` and `arch`, not a path string.
3. Set `requires.core = ">=2.9,<3"`, and publish a new module version. Its grants changed, so the owner approves it
   once.

A module version still asking for an id the fleet does not define never runs, and its readiness checklist says it
needs a new version.

## Name doctor checks after what they prove

If the module's doctor checks the tool, name the check after a capability one of its probes or services provides
(`java17` beside a `java17` probe). A failing check named after a capability then keeps off only the stages that
require that capability; a check named after nothing makes the whole doctor unhealthy, so nothing that needs
certification runs. See the [runner protocol](../spec/runner-protocol.md#doctor).
