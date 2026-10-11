# Share a GPU with a protected program

Owners protect their own GPU programs (a trainer, a renderer, a game) from fleet work. By default a node runs no fleet
GPU work while a protected program uses the GPU. A node whose owner sets `gpu_jobs = "certified"` lets GPU work run
beside such a program, but only work that **holds a GPU duty**: the share of each second it keeps the GPU busy, which
the agent sets and checks. This page shows how to write a runner that holds it, how to prove it with the conformance
kit, and how [`examples/llmserve`](../examples/llmserve), the reference module, does it for language-model inference.
It needs Oarbank core 2.10.

## What the agent does

- **The duty.** Beside a protected GPU program the agent runs a GPU budget for the node: it starts at the level a
  co-tenancy measurement certified as harmless for that program, shrinks when the program slows (halved at its owner's
  target, to 0 at twice the target), and grows back by 0.05 a minute while it does not. Each cooperative GPU job gets
  an equal share, as `gpu_duty` in its control document ([runner-protocol.md](../spec/runner-protocol.md#control)).
- **The check.** Every two seconds the agent reads how much GPU time each job's processes used. A job over its duty
  plus 0.1 for three samples in a row is paused for a minute; the third time within an hour it is stopped, and the
  module's jobs get no duty for an hour, so they wait beside protected programs like work that cannot throttle. A job
  whose GPU time the node cannot read is paused the same way.
- **Who gets it.** Only runners that declare `cooperative_throttle` and use the GPU (`[runner] gpu = { use = "shared"
  }`). Everything else waits while a protected program uses the GPU, as before.

No operating system lets the agent throttle a GPU from outside on Apple Silicon: Metal has no queue priorities and a
submitted command buffer always runs to the end. The duty is held inside your runner, and verified from outside.

## Hold the duty in your runner

`oarbank_sdk.duty.Duty` is a small governor (standard library only; vendor it with `control.py` if your runner does not
import the SDK). Wrap each unit of GPU work in `duty.unit()`, ending the block only when the GPU has finished it:

```python
from oarbank_sdk.control import Control, Stopped
from oarbank_sdk.duty import Duty

ctl = Control()                                 # first: it installs the nudge handler
duty = Duty(ctl, events=args.events)            # reads ctl.gpu_duty after every nudge
ctl.phase("work")
try:
    for batch in batches:
        ctl.safe_point()                        # pause and stop
        with duty.unit():
            out = model(batch)
            mx.eval(out)
            mx.synchronize()                    # the unit ends when the GPU is done
except Stopped:
    sys.exit(ctl.acknowledge_stop())
duty.close()
```

After a unit of `b` seconds at duty `d` the governor idles `b·(1−d)/d` before the next one, so over any second the GPU
is busy at most `d` of it. While it idles it applies a newer control document at once: a stop raises `Stopped`, a new
duty changes the idle. At duty 0 it waits for a duty above 0.

**Keep units short.** A unit of two seconds is a two-second burst whatever the duty, and the protected program feels
it. `duty.scale()` says how much smaller your next units should be for one to fit the duty's share of a second; apply
it to whatever sets your burst length:

| Framework | One unit | Make it shorter with |
|---|---|---|
| MLX, `mlx_lm` | one generation step (`next()` on `generate_step`, then `mx.synchronize()`) | `prefill_step_size` (default 2048), the batch size |
| llama.cpp (`llama-cpp-python`) | one `Llama.eval` call | the prompt chunk you pass (at most `n_batch`), `n_ubatch` |
| Metal | one command buffer, `waitUntilCompleted` | fewer dispatches per buffer |

**Memory.** Beside a protected program on unified memory, set a memory limit (MLX: `mx.set_memory_limit`) and never a
wired limit: wired memory is memory the protected program cannot have.

**Report what you held.** With `events`, the governor writes a `gpu_duty` metric event every two seconds: the share of
that time the GPU was busy with your units, the duty asked, and `device` (`gpu`; `cpu` for a stand-in that does its
units on the CPU). The conformance kit reads them.

## Prove it with the conformance kit

List one or more duty specs in `conformance.json`: tasks that run long enough to measure (the kit stops a `cancellable`
runner when it has measured):

```json
{"duty_specs": [{"name": "batch-at-0.3", "stage": "batch", "duty": 0.3, "window_s": 4,
                 "payload": {"prompts": ["a long enough task"], "max_tokens": 4000, "backend": "stub"}}]}
```

Once your runner has written its first `phase`, the kit writes `gpu_duty` into its control document and nudges it, as
the agent does, then measures for `window_s`. It checks that your runner reports the duty it held and that both its
reports and what the kit measures stay within the duty plus 0.1. The kit's own measure is your runner's GPU time where
the host shows it (macOS, from the same driver counters the agent reads), else its CPU time per thread when your
reports say the units run on the CPU. A runner with only a real GPU backend is therefore fully checked on a Mac;
elsewhere give it a CPU stand-in backend, as llmserve does, so CI without a GPU checks the same code path.

```bash
uv run oarbank-sdk conform examples/llmserve
```

## The reference module: llmserve

[`examples/llmserve`](../examples/llmserve) runs batches of prompts through a language model inside its runner:

- **`batch`** (determinism `none`): the prompts of a `queue_prompts` operation, `per_job` at a time, on MLX (macOS on
  Apple Silicon) or llama.cpp (Metal, CUDA, Vulkan or the CPU), chosen with `backend` (`auto` picks MLX where it is
  installed, else llama.cpp). Each generation step is one unit; prefill runs in steps `duty.scale()` sizes.
- **`selftest`** (the default stage, determinism `exact`): a deterministic stand-in model in pure Python. Its golden
  certifies the module on every node, and its CPU units let the duty spec run anywhere.
- It declares `cancellable`, `cooperative_pause`, `cooperative_throttle` and `progress_events`, and `[sandbox] devices =
  { gpu = "compute" }`. It needs no network: models are read from the module's data directory.

### Install a real backend and model (owners)

The module ships no model and depends on neither MLX nor llama.cpp, so CI never downloads one. On a node where you want
real inference:

1. **Install a backend into the module's Python.** For a module you build yourself, switch the runner to a locked
   environment: `runtime = { kind = "uv", lock = "uv.lock", python = "3.12.x" }`, with `mlx-lm` (macOS on Apple
   Silicon) or `llama-cpp-python` (built for Metal, CUDA or Vulkan) in its `pyproject.toml`, then `uv lock` and rebuild
   the bundle. `oarbank-agent` installs it offline from the bundle's wheels on every node.
2. **Put a model in the module's data directory**, under `models/`: an MLX model directory (for example a 4-bit
   quantised one) or a single GGUF file. Pick one that leaves the protected program its memory: the node's memory guard
   and the stage's `mem_gb` both count. A model is the owner's choice and the owner's download; check its licence.
3. **Queue work** with the model's name: `oarbank mod llmserve queue_prompts` with `{"prompts": [...], "model":
   "my-model.gguf", "backend": "llamacpp"}`. The runner's doctor lists the backends it can load (`attrs.backends`).
4. **Measure before you share.** Run a co-tenancy measurement of llmserve's work against your protected program
   (`oarbank protection ab sweep <node> --rule <id> --work dev.codonic.oarbank.llmserve`), and set the node's `gpu_jobs
   = "certified"` only once a level passes ([Prove co-tenancy is safe](https://docs.codonic.dev/oarbank/operate/prove-co-tenancy/)).

## Checklist

- `cooperative_throttle` in `[runner].capabilities`, and `gpu = { use = "shared" }` (or `exclusive`).
- Every unit of GPU work inside `duty.unit()`, ended only when the GPU is done; units shortened with `duty.scale()`.
- `ctl.safe_point()` between units (pause and stop), `Duty(..., events=...)` so it reports, `duty.close()` at the end.
- A duty spec in `conformance.json`, and a CPU stand-in backend if your CI has no GPU.
- Throughput you report leaves out `duty.idle_s`.
