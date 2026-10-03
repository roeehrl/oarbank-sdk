# Bundles and the module lifecycle

A module ships as a **bundle**: one immutable file per version, addressed by its content digest. The host
installs it, verifies it, and decides where each version runs. Nothing about a module is enabled by
installing it.

## The bundle

`oarbank-sdk bundle build <module-dir>` writes `dist/<name>-<version>.mfb`, a gzip tar of the module
directory plus `bundle.json`:

```json
{"bundle": 2, "module_id": "dev.example.primes", "name": "primes", "version": "1.2.0", "compat": "primes1",
 "files": [{"path": "oarbank-module.toml", "sha256": "…", "mode": "644"}, …],
 "content_digest": "h2:…", "sdk_version": "…"}
```

- **`content_digest`** is `h2:` plus the SHA-256 of the lines `<sha256> <mode> <path>\n`, sorted by the UTF-8 bytes of
  the path. It covers contents **and modes**, not the archive, so the same identity verifies a tarball, a mirror or an
  unpacked directory, and an operator's approval covers which files are executable.
- **Modes** are `644` or `755`, from the manifest: `[bundle].executables` globs plus the argv[0] of every native exec.
  They never come from the build host's filesystem, so a Windows build and a macOS build are identical. On Windows,
  modes are recorded but not applied.
- **Paths** are PortablePaths (leading dots allowed), case-fold unique ([platforms.md](platforms.md#portable-paths)).
  The host refuses any other member name, which keeps a bundle from escaping its directory on any OS.
- **Reproducible.** The same sources give the same bytes: sorted entries, zeroed times and owners, no name
  in the gzip header.
- **Excluded:** `__pycache__`, `.git`, `.venv`, `dist`, `build`, `tests`, `__MACOSX`, `*.pyc`, `*.mfb`, `*.sig`, `.DS_Store`, `._*`, `Thumbs.db`, `desktop.ini`, `.gitignore`. Everything else ships, `conformance.json` included (harmless).
- **Refused:** symlinks, and module code that imports the host (`import oarbank` or `from oarbank…`).
  Modules are built only on `oarbank_sdk`. Vendored third-party code under `vendor/` is not scanned.

**Files per platform.** `[bundle.platform_files]` maps globs to the platforms or OSes whose nodes receive the matching
files (`"native/windows-amd64/**" = ["windows-amd64"]`); unmatched files go everywhere. The bundle, its file list and
its digest stay whole. The host builds each platform's release from that platform's subset
(`oarbank_sdk.bundle.subset`), so a node stages only its platform's files; the coordinator and the CLI have every file.
No glob may match the manifest, every bundle path an exec names must reach each platform that runs it, and
`bundle build` warns about a glob that matches no file.

`oarbank-sdk bundle verify <file>` checks that every path is safe and every file is regular, every hash,
the exact file list, the digest, and that `bundle.json` agrees with the manifest.

## Dependencies

Installing a module downloads nothing and runs none of its code. A module's Python dependencies ship **inside the
bundle** as wheels, pinned by hash:

- **Requirements files.** The coordinator's is `requirements.txt` at the bundle root; the runner's is a
  `requirements.txt` beside the runner script (for example `node/requirements.txt`). Each lists **every**
  distribution, transitive ones included, as `name==version --hash=sha256:<64 hex>`: the output of
  `uv pip compile --generate-hashes`. Options (`--index-url`, `-e`, `-r`, …) and environment markers are refused.
  `oarbank_sdk`, `pydantic` and their own dependencies come from the host and are never pinned.
- **Wheels.** `wheels/` holds a wheel for every pinned distribution and every platform in `requires.platforms`
  (a `py3-none-any` wheel covers all), for CPython 3.12. Source distributions are refused: building one runs
  arbitrary code. `oarbank-sdk bundle wheels <dir>` downloads them; `bundle build` refuses a bundle whose wheels or
  hashes are missing or wrong.
- **Install on a host.** Into an overlay virtual environment that also sees the host's site packages, with
  `uv pip install --offline --no-index --find-links <bundle>/wheels --require-hashes --only-binary :all: --no-deps
  --no-cache -r <requirements>`, inside the module sandbox where the host has one: the bundle and the interpreter
  read-only, only the new environment writable, no network.
- **Coordinator entry point.** `coordinator.exec` runs with the bundle root as its working directory, under
  `python -I` (no current directory or `PYTHONPATH` on `sys.path`). A package inside the bundle needs a small entry
  script:

  ```python
  # bin/coordinator.py  (coordinator.exec = ["python", "-I", "{bundle}/bin/coordinator.py"])
  import sys
  from pathlib import Path
  sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
  from mymodule.module import module
  module.run()
  ```

## Install

`oarbank module install dist/primes-1.2.0.mfb` (or the console's Modules page) uploads the bytes and runs
the `modules.install` operation (tier T2: you review the plan first). Install:

1. verifies the bundle as above;
2. checks `requires.core`, `module_protocol`, `runner_protocol`, `requires.features` and, when set,
   `requires.coordinator_platforms` (the coordinator host's platform) against the host;
3. refuses a result field whose type changed within the same major version, so stored history stays
   comparable;
4. refuses a version that is already installed with another digest (versions are immutable);
5. builds the coordinator environment offline from the bundle's wheels (above) and starts the coordinator once
   to complete `initialize` (the self-test);
6. records the version.

## Which version runs where

| Operation | Tier | Effect |
|---|---|---|
| `modules.enable <name>@<version>` | T1 | The first version becomes current on every node, or the module is re-enabled after `disable`. |
| `modules.enable_canary <name>@<version> --node …` | T2 | Only those nodes get the new version. Their release is recomposed, they re-doctor, and they re-certify on its goldens. Their jobs run it, and that version's own coordinator process judges them. |
| `modules.promote <name>` | T2 | The canary becomes current everywhere and the old current is kept as previous. Refused until every canary node is certified on the canary's digest. |
| `modules.rollback <name>` | T1 | Abandons a canary, or flips current back to previous. In-flight jobs finish or are cancelled, never silently re-bound. |
| `modules.disable <name>` | T1 | The kill switch. No dispatch fleet-wide from the next claim; live attempts are revoked in the next heartbeat and their jobs requeued. |
| `modules.pin <name>@<version> --node N` | T1 | One node runs one version, until the pin is cleared. |
| `modules.uninstall <name>@<version>` | T2 | Refused while the version is current, previous, canary or pinned. |

Certification is keyed by each module's digest on each node. A new version re-certifies that module on
the nodes that get it and nothing else. When release signing is on, a node's release is the signed lock of
module digests, and agents refuse unsigned or rolled-back releases.
