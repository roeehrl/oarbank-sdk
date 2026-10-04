# Oarbank SDK

The open SDK for building **Oarbank modules**: the job types an Oarbank fleet runs on macOS, Linux and Windows,
on x64 and arm64.

Oarbank spreads batch jobs across the computers you already own: one coordinator, nodes on macOS, Linux and
Windows, host protection while people work, and every job in an OS sandbox. The core (coordinator, node agent,
console) knows nothing about any particular workload. Everything a job type needs (what to run, how to check a
result, which nodes may run it, what the console shows) lives in a module, and a module talks to the core **only**
through the versioned contracts in this repository.

## What is in the SDK

| Part | What it covers | Spec |
|---|---|---|
| Manifest (schema 1) | Everything the core must know before it runs any module code | [spec/manifest.md](spec/manifest.md) |
| Module protocol 1 | Coordinator ↔ module: JSON-RPC 2.0 over stdio | [spec/module-protocol.md](spec/module-protocol.md) |
| Runner protocol 1 | Agent ↔ job runner: argv, files, exit codes, events, Control | [spec/runner-protocol.md](spec/runner-protocol.md) |
| Service protocol 1 | Agent ↔ node services and probes | [spec/service-protocol.md](spec/service-protocol.md) |
| Envelopes | Spec and result documents | [spec/envelopes.md](spec/envelopes.md) |
| Platforms | Platform tokens, per-platform declarations, placement | [spec/platforms.md](spec/platforms.md) |
| Sandbox | What a module process may touch, and the grants an operator approves | [spec/sandbox.md](spec/sandbox.md) |
| UI contract 1 | Pages, panels and views the console renders for a module | [spec/ui-contract.md](spec/ui-contract.md) |
| Bundles and lifecycle | The `.mfb` file, its digest, install, canary, promote, rollback | [spec/bundles.md](spec/bundles.md) |
| Conformance kit | What a module must pass (`oarbank-sdk conform`) | [spec/conformance.md](spec/conformance.md) |

The Python package `oarbank_sdk` implements these contracts:

- **Typed models** (Pydantic) for the manifest, the protocols and the envelopes. The JSON Schemas in
  [`schemas/`](schemas/) are generated from them.
- **`oarbank_sdk.server.Module`**, the coordinator side of a module: declare verbs, return effects, call the host.
- **`oarbank_sdk.control.Control`**, the runner side of Control: stop, pause and thread limits at safe points, nudged
  by the agent (a signal on POSIX, an event on Windows), never polled. It is stdlib only, so a runner can vendor it.
- **The conformance kit** (`oarbank-sdk conform`): it checks the manifest, builds and verifies the bundle, drives the
  coordinator side over the module protocol and runs the runner on its goldens, and on any other runner specs its
  fixtures list, as this host's platform would (sandboxed, through the egress proxy).
- **Bundles** (`oarbank-sdk bundle build|verify|wheels`): digest-addressed `.mfb` files, with per-platform wheels and
  files. `oarbank-sdk deps compile` resolves a `requirements.in` into one hash-pinned, marker-free requirements file
  for every platform that installs it.
- **A preview server** (`oarbank-sdk preview`) that renders a module's pages as the console will, from fixtures.
- **Shared test vectors** in [`spec/vectors/`](spec/vectors/) (canonical JSON, job keys, portable paths, platform
  tokens, variant resolution, placement classes) that every implementation reproduces exactly.

The rules on versions, deprecation and what is public are in [spec/versioning.md](spec/versioning.md) and
[spec/public-surface.md](spec/public-surface.md).

## Quick start

You need Python 3.12 or newer, on macOS, Linux or Windows.

```bash
git clone https://github.com/roeehrl/oarbank-sdk
cd oarbank-sdk
uv sync --group dev                                    # or: python -m venv .venv && pip install -e .
uv run oarbank-sdk check examples/toy/oarbank-module.toml
uv run oarbank-sdk conform examples/toy                 # must end with "0 failed"
uv run oarbank-sdk bundle build examples/toy            # examples/toy/dist/toy-<version>.mfb
```

Then follow the tutorial, **[Build an Oarbank module in a day](docs/tutorial.md)**. It builds a complete module step
by step: the manifest, the coordinator side, the runner, goldens, the conformance kit, a bundle and more than one
platform. The reference module in [`examples/toy`](examples/toy) is the finished shape: a coordinator side
(`toy_module.py`, on `oarbank_sdk.server`) and a runner (`toy_runner.py`, stdlib only).

## Developing the SDK

```bash
uv sync --group dev
uv run pytest -q
uv run oarbank-sdk export-schemas        # regenerate schemas/ and spec/manifest-reference.md
uv run python scripts/gen_vectors.py     # regenerate spec/vectors/
```

CI runs the tests on macOS, Linux (x64 and arm64) and Windows, and fails if the generated schemas, the manifest
reference or the vectors are stale.

## Licence

The core is source-available: free for personal and noncommercial use, commercial licence from Codonic; the module
SDK is open source (Apache-2.0).

Contributions to the SDK use the Developer Certificate of Origin; see [CONTRIBUTING.md](CONTRIBUTING.md).
