# Place work by GPU API

A runner built on CUDA cannot run on a Mac, and a Metal build cannot run on Linux. Name the GPU APIs your runner (or
your service) can use, and the core grants its jobs only to nodes that provide one of them.
[`examples/gpuinfo`](../examples/gpuinfo) is the complete reference. The contract is
[runner-protocol.md, "GPU use"](../spec/runner-protocol.md#gpu-use).

## 1. Name the APIs, per platform

```toml
[requires]
core = ">=2.5,<3"                     # GPU placement needs core 2.5

[runner]
exec = ["python", "-I", "{bundle}/runner.py"]
runtime = { kind = "python" }
gpu = { use = "shared", apis_any = ["cuda", "rocm", "vulkan"] }

[runner.variants.darwin]              # replaces [runner].gpu whole on macOS
gpu = { use = "shared", apis_any = ["metal"] }

[sandbox]
devices = { gpu = "compute" }         # the grant that lets the runner reach the GPU at all
```

`apis_any` needs `use = "shared"` or `"exclusive"`. A core detects `cuda`, `rocm`, `vulkan`, `opencl`, `directml` and
`metal`; another name is valid but no node provides it yet (`oarbank-sdk check` warns).

A service declares the same: `gpu = { use = "shared", apis_any = ["metal", "cuda"] }`. It is offered and started only on
nodes that provide one of them, and a job that reserves its pool is placed only there.

## 2. See what a node provides

On a node with the agent installed:

```bash
oarbank-agent gpu-apis
```

```json
{"host": ["metal", "opencl"], "containers": ["vulkan"],
 "evidence": {"metal": "Apple M4 Max", "cuda": "CUDA does not run on macOS", "...": "..."},
 "platform": "darwin-arm64", "agent_version": "2.5.0"}
```

On your own machine, `oarbank-sdk gpu-apis` runs the same probes (without the containers, which only an agent knows).
An API counts only when its runtime finds a real GPU: a CPU Vulkan or OpenCL device does not. The agent probes again
whenever its doctors run; after installing a driver, run the doctor from the console or `oarbank op nodes.run_doctor`.

On Linux the agent's account must be able to open the GPU's device files: add the `oarbank` account to the `render`
and `video` groups (and restart the agent) when a GPU is missing from the list.

## 3. Let the doctor and the goldens agree

Where none of your APIs is reachable, say so in `doctor --json` with `health = "undetected"`: the node then never offers
the module, and never alerts. `examples/gpuinfo` checks with `oarbank_sdk.gpu.detect()`:

```python
from oarbank_sdk import gpu
found = gpu.detect()["host"]
health = "healthy" if set(found) & {"cuda", "rocm", "vulkan"} else "undetected"
```

`golden.list` sees the node's APIs in `node_class.gpu_apis` (`{host, containers}`), so a module whose results differ per
API can give each its own goldens.

## 4. GPUs in containers

A stage that runs GPU containers reserves the agent's `gpu` pool, and the runner says the GPU is used in containers:

```toml
[runner]
gpu = { use = "exclusive", in_container = true, apis_any = ["vulkan"] }

[[stages]]
name = "infer"
requires.pools = { containers = 1, gpu = 1 }
```

`apis_any` is then checked against the APIs the node reports in its containers. On Linux they come from the node's CDI
spec (an NVIDIA spec gives `cuda`, and `vulkan` and `opencl` when it mounts their drivers). On a Mac with Apple
silicon, installing krunkit (`brew tap slp/krun && brew install krunkit`) gives containers `vulkan` on the Mac's GPU:
the agent runs GPU containers in a second VM whose virtio-gpu device carries Vulkan to the host. The image needs Mesa's
Venus driver and the Vulkan loader (Fedora's `mesa-vulkan-drivers` and `vulkan-loader`). Metal itself never reaches a
Linux container.

## 5. Check it with the kit

```bash
uv run oarbank-sdk conform examples/gpuinfo
```

The kit compares the runner's APIs for this host's platform with what this host provides. Where the host provides one,
the goldens run as usual; where it provides none, they are skipped with the reason (a node like your machine would get
none of these jobs), never failed. Run the kit on one machine of each kind you support.
