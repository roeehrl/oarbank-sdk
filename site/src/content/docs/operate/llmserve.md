---
title: Run language-model batches with llmserve
description: Build and install llmserve, the reference module for GPU work beside a protected program, give a node a backend and a model you choose, queue prompts, and let it share the GPU only up to a certified duty.
type: how-to
platforms: [macos, linux, windows]
core: '2.10.2'
sidebar:
  order: 14
---

**llmserve** runs batches of prompts through a language model on your nodes: MLX on Macs with Apple silicon, or
llama.cpp (Metal, CUDA, Vulkan or the CPU) elsewhere. It is the reference module for GPU work that runs beside a
program you protect: it holds the GPU duty the node's agent gives it, so under `gpu_jobs = "certified"` it uses only the
share of the GPU a co-tenancy measurement showed harmless. It ships with the module SDK, bundles no model and needs no
network. This page covers Oarbank 2.10.2 and module SDK 1.7.0.

## 1. Build and install the bundle

llmserve is an example in the SDK repository, not a download. Build its bundle with the SDK (Python 3.12 or later and `uv`), copy it to the
coordinator, and install it like any module ([Running modules](/oarbank/operate/modules)):

```sh
git clone --branch v1.7.0 https://github.com/roeehrl/oarbank-sdk && cd oarbank-sdk
uv run oarbank-sdk conform examples/llmserve      # must end with "0 failed"
uv run oarbank-sdk bundle build examples/llmserve # examples/llmserve/dist/llmserve-0.1.0.mfb
```

```sh
oarbank module install llmserve-0.1.0.mfb --dry-run
oarbank module install llmserve-0.1.0.mfb
oarbank module approve llmserve@0.1.0             # it asks for GPU compute access
oarbank module enable llmserve@0.1.0
```

Sign each platform's release as usual. Its default stage, `selftest`, runs a small stand-in model on the CPU: that is
how each node certifies the module, with or without a GPU. It runs on macOS (Apple silicon), Linux (x86_64, arm64) and Windows (x64), and
on any core from 2.5. Cores before 2.10 never give it a duty, so there it runs flat out.

## 2. Give a node a backend and a model

The bundle as built runs only the stand-in. For real inference:

1. **A backend in the module's Python.** Change the runner's `runtime` to a locked environment (`kind = "uv"`) with
   `mlx-lm` (Macs with Apple silicon) or `llama-cpp-python` (built for Metal, CUDA or Vulkan) in its dependencies, lock
   it, bump the version and build the bundle again. The node installs it offline from the bundle's wheels. The runner's
   doctor lists the backends it can load on each node (`attrs.backends` on the node page).
2. **A model you install.** Put an MLX model directory or one GGUF file under `models/` in the module's data directory
   on the node: `modules-data/llmserve/models/` in the node's home (`/Library/Application Support/Oarbank` on a Mac
   system install, `/var/lib/oarbank` on Linux, `C:\ProgramData\Oarbank` on Windows), readable by the node's service
   account. The model is your choice and your download: check its licence. Pick one that leaves your protected programs
   their memory.

## 3. Queue prompts

```sh
oarbank mod llmserve queue_prompts --json prompts.json
```

```json
{"prompts": ["Summarise the attached note in one line.", "List three uses of a spreadsheet."],
 "model": "example-model-q4.gguf", "backend": "llamacpp", "per_job": 16, "max_tokens": 128, "mem_gb": 6}
```

| Parameter | Default | Meaning |
|---|---|---|
| `prompts` | required | The prompts; each job takes `per_job` of them |
| `model` | none | The model's path under `models/` |
| `backend` | `auto` | `mlx`, `llamacpp`, or `auto` (MLX where it is installed, else llama.cpp) |
| `per_job`, `max_tokens` | 16, 128 | Prompts per job, and tokens per answer |
| `mem_gb` | 8 | The memory each job reserves on its node: the model's size and its working set |

The jobs form a campaign; their results show the tokens and the backend used.

## 4. Share the GPU only as far as you measured

On a node where you protect a GPU program, fleet GPU work waits beside it by default. To let llmserve run beside it:

1. Measure what llmserve's work costs the program with a duty sweep:
   `oarbank protection ab sweep <node> --rule <rule> --work llmserve`.
2. When a level passes, set the node's `gpu_jobs` to `certified`.
3. Watch the budget with `oarbank protection duty <node>` or the **GPU duty** card on the node's **Protection** page.

Each generation step is one unit of GPU work, and llmserve idles after each one to hold its duty. If it ever kept the
GPU busier than its duty, the agent would pause it, then stop it and give its module no duty for an hour
(`GPU_DUTY_EXCEEDED`). [Prove co-tenancy is safe](/oarbank/operate/prove-co-tenancy) explains the measurement, the
certificate and the governor.

Module authors: [Share a GPU with a protected program](/oarbank/build/gpu-duty) shows how llmserve holds its duty.
