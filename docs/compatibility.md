# Hardware compatibility

Where Oarbank's GPU detection, GPU containers and Windows container runtime have run, and with what result. Each
row is one configuration someone ran: its operating system, GPU and driver, where the work ran, the GPU APIs the
agent reported, the gated tests and their results, the core commit, the date and a link to the report.

Oarbank's CI has no GPU runners, and the maintainers' machines have no NVIDIA, AMD or Intel GPU, so rows for those
come from contributors. **Untested** rows are configurations nobody has reported yet: if you have one, its issue says
what to run, and [Add your machine](#add-your-machine) says how to report it.

**5 configurations reported:** 5 verified, 0 partial, 0 not working. **6 untested.**

Generated from [`compatibility/reports.json`](compatibility/reports.json) by `scripts/gen_compatibility.py`. Do not
edit by hand.

## Legend

| Status | Meaning |
|---|---|
| Verified | Works as documented: every gated test in the run passed, or skipped itself where its hardware is absent, and the APIs reported are the ones the machine has. |
| Partial | Some of it works: a test failed, an API is reported that does not work, or one that works is missed. The notes say which. |
| Not working | The path fails on this configuration. The notes say where it stopped. |
| Untested | Nobody has reported this configuration yet. Help wanted: the linked issue says what to run. |

**GPU APIs** are the `gpu_apis` the agent reported: `host` for runners on the machine itself, `containers` for
container jobs (see [Place work by GPU API](gpu-placement.md)). *none* means the list was reported and empty; *not
reported* means the run did not record it. **Ran in** is the host, a Linux container (Podman, Docker or Colima), or
WSL containers on Windows, with the versions of the tools involved. **Tests** are the gated tests below.

## macOS

| Status | GPU | OS | Ran in | GPU APIs | Tests | Notes | Report |
|---|---|---|---|---|---|---|---|
| Verified | Apple M4 Max | macOS 27, darwin-arm64 | host, container (Colima 0.10.3; krunkit 1.3.2; Lima 2.2.0; Mesa 25.3.6-102.fc44, libkrun build) | host: `metal`, `opencl`; containers: `vulkan` | `test_gpu_apis` passed; `live_colima_gpu` passed | The GPU container sees `Virtio-GPU Venus (Apple M4 Max)` and the compute shader's output is right. The image needs the libkrun build of Mesa: stock Mesa fails `vkCreateInstance` under krunkit. | [maintainers, `9a8e235`, 2026-10-04](https://github.com/roeehrl/oarbank/commit/9a8e2357490f4b9294b61d0ae2414c7d14195482) |
| Verified | Apple paravirtual GPU (CI runner) | macOS 26.6.2, darwin-arm64 | host (runner image macos-26-arm64 20260907.0351) | host: not reported; containers: not reported | `test_gpu_apis` passed | GitHub's macos-26 runner is a macOS VM with a paravirtual GPU. The gpuinfo module is certified with Metal reached from its sandbox. The test asserts that `metal` is among the host APIs and that the agent and the SDK agree; the job does not print the whole list. | [CI, `1dc15f7`, 2026-10-04](https://github.com/roeehrl/oarbank/actions/runs/37240935327/job/111549350958) |
| Untested | Intel Mac with Intel or AMD Radeon graphics | macOS (darwin-amd64) | – | – | to run: `live_colima_gpu` | Does the host report `metal`, and does Vulkan reach containers through krunkit, or does the agent report container GPU unavailable? | [Help wanted: #16](https://github.com/roeehrl/oarbank/issues/16) |

## Linux

| Status | GPU | OS | Ran in | GPU APIs | Tests | Notes | Report |
|---|---|---|---|---|---|---|---|
| Verified | No GPU (virtual machine) | Ubuntu 26.04 LTS, kernel 7.0, linux-arm64 | host | host: none; containers: none | `test_gpu_apis` passed | A virtual machine with no GPU device, only Mesa's lavapipe, a CPU Vulkan device. Detection correctly reports no API, the gpuinfo module is `undetected` and explain says `GPU_API_MISSING`. | [maintainers, `df64852`, 2026-10-04](https://github.com/roeehrl/oarbank/commit/df6485221bbdad04cb59d68976c4728327c7971b) |
| Untested | NVIDIA GPU with the NVIDIA Container Toolkit | Linux (amd64 or arm64) | – | – | to run: `doctor_probe_gpu`, `test_gpu_apis` | Does a container get the GPU through CDI, with `cuda` in `gpu_apis.containers`? | [Help wanted: #12](https://github.com/roeehrl/oarbank/issues/12) |
| Untested | AMD GPU (ROCm or Mesa) or Intel GPU (Arc, Iris Xe or integrated) | Linux (amd64 or arm64) | – | – | to run: `doctor_probe_gpu`, `test_gpu_apis` | Are `rocm`, `vulkan` and `opencl` detected correctly, and does a container reach the GPU through CDI (`/dev/dri`, and `/dev/kfd` for ROCm)? | [Help wanted: #13](https://github.com/roeehrl/oarbank/issues/13) |

## Windows

| Status | GPU | OS | Ran in | GPU APIs | Tests | Notes | Report |
|---|---|---|---|---|---|---|---|
| Verified | Microsoft Hyper-V Video, driver 10.0.26100.1150 (CI runner) | Windows Server 2025 Datacenter (build 26100), WSL kernel 6.18.40.1-1, windows-amd64 | host, WSL containers (runner image windows-2025-vs2026 20260925.250.1; WSL 3.0.1) | host: not reported; containers: none | `verify_windows_containers` passed | The agent's WSL containers session is ready (`linux/amd64`). A signed image runs with the Linux rules: no network, then a granted one; the reservation's memory and CPU limits; the work directory mounted; unsigned and unapproved images refused before any pull. The doctor's probe passes as an administrator. No GPU: the Hyper-V video adapter is not one, and `gpu` is `undetected`. | [CI, `1dc15f7`, 2026-10-04](https://github.com/roeehrl/oarbank/actions/runs/37240935327/job/111549350751) |
| Verified | No GPU (virtual machine) | Windows 11 Pro (build 26300), windows-arm64 | host | host: none; containers: none | none | A QEMU virtual machine whose only adapter is the Microsoft Basic Render Driver, which is excluded for DirectML, Vulkan (Dozen) and OpenCL (OpenCLOn12): the agent and the SDK both report no API. WSL containers cannot start in this VM (no nested virtualisation). | [maintainers, `df64852`, 2026-10-04](https://github.com/roeehrl/oarbank/commit/df6485221bbdad04cb59d68976c4728327c7971b) |
| Untested | NVIDIA GPU | Windows 10 or 11 (amd64 or arm64) | – | – | to run: `verify_windows_containers_gpu` | Does a WSL container get the GPU (`cdi:microsoft.com/wslc`), with `cuda` and `directml` in `gpu_apis.containers`? | [Help wanted: #10](https://github.com/roeehrl/oarbank/issues/10) |
| Untested | AMD Radeon, Intel Arc or Iris Xe GPU | Windows 10 or 11 (amd64 or arm64) | – | – | to run: `verify_windows_containers_gpu` | Which APIs reach WSL containers: `directml`, and ROCm or Level Zero from the image's own runtime? | [Help wanted: #11](https://github.com/roeehrl/oarbank/issues/11) |
| Untested | Any PC, no GPU needed (amd64 especially) | Windows 10 or 11 | – | – | to run: `doctor_probe_service`, `verify_windows_containers` | Does the container runtime work on a real machine, and can the installed service's account drive WSL containers? | [Help wanted: #14](https://github.com/roeehrl/oarbank/issues/14) |

## Tests

Run from the repository root of [roeehrl/oarbank](https://github.com/roeehrl/oarbank) (the agent's commands from
`rust/` with `cargo run -p oarbank-agent --` in place of `oarbank-agent` when it is not installed).

| Test | Command | What it shows | Issues |
|---|---|---|---|
| `doctor_probe_gpu` | `oarbank-agent containers doctor --probe --gpu` | The container runtime pulls, runs and removes a probe container with the GPU passed through; the report names the mechanism (`gpu`, e.g. `cdi:nvidia.com/gpu`) and the APIs in `gpu_apis.containers`. | [#12](https://github.com/roeehrl/oarbank/issues/12), [#13](https://github.com/roeehrl/oarbank/issues/13) |
| `doctor_probe_service` | `"C:\Program Files\Oarbank\oarbank-agent.exe" --home C:\ProgramData\Oarbank\agent containers doctor --probe` | The doctor's container probe as the installed agent runs it, under the service's own home. | [#14](https://github.com/roeehrl/oarbank/issues/14) |
| `live_colima_gpu` | `OARBANK_LIVE_COLIMA=1 cargo test -p oarbank-agent --bins -- --ignored --nocapture live_colima_gpu_runs_vulkan_compute_on_the_apple_gpu` | A container on the agent's krunkit VM runs a Vulkan compute shader on the Mac's GPU and prints `compute: ok 65536`. | [#16](https://github.com/roeehrl/oarbank/issues/16) |
| `test_gpu_apis` | `uv run pytest -q tests/rust/test_gpu_apis.py` | The agent and the SDK detect the same host APIs, and a real agent's module is placed by them: certified where one of its APIs exists, excluded with `GPU_API_MISSING` where none does. Passes with `2 passed`. | [#12](https://github.com/roeehrl/oarbank/issues/12), [#13](https://github.com/roeehrl/oarbank/issues/13), [#15](https://github.com/roeehrl/oarbank/issues/15) |
| `verify_windows_containers` | `scripts\verify-windows-containers.ps1 -InstallWsl` | WSL 3.0.1 or newer, the agent's own WSL containers session, a signed image run through the broker with the Linux rules (egress, limits, mounts, cleanup) and the doctor's probe. Passes with `OK: the Windows container runtime works here`. | [#14](https://github.com/roeehrl/oarbank/issues/14) |
| `verify_windows_containers_gpu` | `scripts\verify-windows-containers.ps1 -InstallWsl -Gpu` | The same, and a container given the GPU: the doctor reports `gpu` as `cdi:microsoft.com/wslc` and the APIs in `gpu_apis.containers`. | [#10](https://github.com/roeehrl/oarbank/issues/10), [#11](https://github.com/roeehrl/oarbank/issues/11) |

## Add your machine

1. Find your hardware in [#9](https://github.com/roeehrl/oarbank/issues/9), which links the issue with the exact commands for each kind of
   machine. Any machine, with or without a GPU, can run [#15](https://github.com/roeehrl/oarbank/issues/15): one command, `oarbank-agent gpu-apis`.
2. Run them on the current `main` of the core, and note the commit (`git rev-parse --short HEAD`).
3. Open a [hardware report](https://github.com/roeehrl/oarbank/issues/new?template=hardware-report.yml). It asks for exactly what a row holds,
   so a maintainer can turn it into one. A failed run is as useful as a passing one: don't fix anything to make it
   pass, say where it stopped.

Before you post, remove anything that identifies you or your network from the output: host names, user names,
paths, serial numbers and addresses. A row keeps only what is in the tables above, and names a reporter only by the
GitHub handle the report was posted under. How maintainers add a row is in
[`compatibility/README.md`](compatibility/README.md).
