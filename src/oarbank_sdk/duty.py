"""Hold the GPU duty the agent asks for (runner protocol "Control", `gpu_duty`; spec/runner-protocol.md "Holding a GPU
duty"). Standard library only, so a runner can vendor this file with control.py.

    ctl = Control(workdir)
    duty = Duty(ctl, events=events_path)
    for batch in batches:
        ctl.safe_point()
        with duty.unit():                  # one unit of GPU work, finished on the GPU when the block ends
            out = model(batch)
            mx.eval(out)                   # MLX: evaluate and wait (mx.eval, then mx.synchronize() for async work)
        # here the governor idled for as long as the duty needs before the next unit
    duty.close()

How it holds the duty. A unit of GPU work is timed from its start to the end of the GPU's work (the caller ends the
block only once the GPU is done: `mx.eval` plus `mx.synchronize`, a llama.cpp `eval` call, a Metal command buffer's
`waitUntilCompleted`). After a unit of `b` seconds at duty `d` the governor idles `b * (1 - d) / d`, so over any
stretch longer than one unit the GPU is busy at most `d` of the time. A unit longer than the window (1 s) still makes
one burst that long: keep units short with `duty.scale()`, which says how much smaller the next units should be (a
batch, a prefill step, a ubatch) for a unit to fit `d` of the window. Duty 0 holds before the next unit until the
agent sends a duty above 0 (or a stop); no duty (`gpu_duty` absent) runs flat out.

The agent measures every runner's GPU time and holds, then stops, one that keeps the GPU busier than its duty plus
0.1; time a runner spends idling here is time its throughput should leave out (`duty.idle_s`).

Reporting. With `events`, the governor writes a `metric` event named `gpu_duty` every `report_s` seconds: `value` is
the share of that stretch the GPU was busy with this runner's units, `data` says the duty asked, the busy and elapsed
seconds, the units, and the device the units run on (`gpu`, or `cpu` for a CPU stand-in such as a test backend). The
conformance kit compares these with what it measures itself.

Knobs for common frameworks (what to make smaller when `scale()` is under 1):

| Framework | Unit | Knobs |
|---|---|---|
| MLX, mlx_lm | one forward pass (a prefill step or a decode step), ended by `mx.eval` + `mx.synchronize` | `prefill_step_size` (default 2048), batch size, `max_kv_size`; beside a protected process set `mx.metal.set_memory_limit` and no wired limit |
| llama.cpp (llama-cpp-python) | one `Llama.eval` call (a ubatch) | `n_ubatch` (the burst length), `n_batch`, threads |
| Metal directly | one command buffer, `waitUntilCompleted` | ops per command buffer, threadgroups per dispatch |
"""
import contextlib
import json
import time

DEFAULT_WINDOW_S = 1.0
DEFAULT_REPORT_S = 2.0
MIN_UNIT_S = 0.002              # the shortest unit worth splitting work into (scheduling overhead dominates below)
IDLE_SLICE_S = 0.25             # idle in slices this long, so a stop or a new duty is seen within a slice


class Duty:
    """The duty governor. `control`: an oarbank_sdk.control.Control (its `gpu_duty`, and its nudges while idle and held);
    None for a fixed `duty`. `events`: the runner's --events path (None: no reports)."""

    def __init__(self, control=None, events=None, duty: float | None = None, window_s: float = DEFAULT_WINDOW_S,
                 report_s: float = DEFAULT_REPORT_S, device: str = "gpu", threads: int = 1,
                 clock=time.monotonic, sleep=time.sleep):
        self.control = control
        self.events = events
        self._fixed = duty
        self.window_s = float(window_s)
        self.report_s = float(report_s)
        self.device = device
        self.threads = int(threads)
        self.clock, self.sleep = clock, sleep
        self.busy_s = 0.0                       # every unit's time, total
        self.idle_s = 0.0                       # time idled or held for the duty, total (leave it out of throughput)
        self.units = 0
        self._recent: list[float] = []          # the last units' lengths (scale())
        self._rep_start = clock()
        self._rep_busy = 0.0
        self._rep_units = 0
        self._rep_duty = self.duty

    # ------------------------------------------------------------------ the duty

    @property
    def duty(self) -> float | None:
        """The duty in force: the control document's `gpu_duty` (0..1), or the fixed one; None: unlimited."""
        d = self.control.gpu_duty if self.control is not None else self._fixed
        if d is None:
            return None
        return min(1.0, max(0.0, float(d)))

    def set(self, duty: float | None):
        """Set the duty of a governor made without a control document (`control=None`): a workload's supervisor passes
        the `gpu_duty` of its control document here from its `gpu_duty` callback (oarbank_sdk.workload)."""
        self._fixed = duty

    def _check(self):
        if self.control is not None:
            self.control.check()                # applies a nudged document; raises Stopped on a stop request

    def _hold(self):
        """Duty 0: wait for a newer document (a duty above 0, or a stop) before the next unit."""
        t = self.clock()
        while self.duty == 0.0:
            if self.control is None:
                raise RuntimeError("gpu_duty 0 with no control document: nothing can raise it")
            self.control.wait()
        self.idle_s += self.clock() - t

    def scale(self) -> float:
        """How much smaller (a factor up to 1) the next units should be for one unit to fit `duty` of the window: 1 while
        units already fit (or no duty is set). Multiply the batch, prefill step or ubatch size by it."""
        d = self.duty
        if d is None or d >= 1.0 or not self._recent:
            return 1.0
        mean = sum(self._recent) / len(self._recent)
        budget = max(MIN_UNIT_S, d * self.window_s)
        return 1.0 if mean <= budget else max(0.01, budget / mean)

    # ------------------------------------------------------------------ units

    @contextlib.contextmanager
    def unit(self):
        """One unit of GPU work: the block submits it and waits for the GPU to finish it. Before it, the governor applies a
        nudged control document (a stop raises Stopped) and holds while the duty is 0; after it, it idles as long as the
        duty needs."""
        self._check()
        if self.duty == 0.0:
            self._hold()
        t0 = self.clock()
        yield self
        b = max(0.0, self.clock() - t0)
        self.busy_s += b
        self.units += 1
        self._rep_busy += b
        self._rep_units += 1
        self._recent.append(b)
        del self._recent[:-8]
        self.after(b)

    def after(self, busy_s: float):
        """Idle for a unit of `busy_s` seconds that just ended (unit() calls it; call it yourself for work you time
        another way, such as a server's reported per-batch GPU time)."""
        d = self.duty
        if d is not None and d < 1.0:
            want = busy_s * (1.0 - d) / d if d > 0 else 0.0
            t = self.clock()
            while True:
                left = want - (self.clock() - t)
                if left <= 1e-6:
                    break
                self.sleep(min(IDLE_SLICE_S, left))
                if self.control is not None and self.control.poll():
                    if self.control.stop:
                        self.idle_s += self.clock() - t
                        self._report(force=True)
                        self.control.check()            # raises Stopped
                    if self.duty != d:          # a new duty: idle for what it asks of this unit instead
                        d = self.duty
                        if d is None or d >= 1.0:
                            break
                        want = busy_s * (1.0 - d) / d if d > 0 else 0.0
            self.idle_s += self.clock() - t
            if d == 0.0:
                self._hold()
        self._report()

    # ------------------------------------------------------------------ reports

    def _report(self, force: bool = False):
        """Close the reporting stretch when it is `report_s` long, when the duty changed (a stretch holds one duty) or on
        `force`, and write its event."""
        now = self.clock()
        span = now - self._rep_start
        d = self.duty
        if span < self.report_s and not force and d == self._rep_duty:
            return
        if self.events is not None and span > 0 and self._rep_units:
            line = {"t": time.time(), "kind": "metric", "name": "gpu_duty", "value": round(min(1.0, self._rep_busy / span), 4),
                    "data": {"asked": self._rep_duty, "busy_s": round(self._rep_busy, 4), "elapsed_s": round(span, 4),
                             "units": self._rep_units, "device": self.device, "threads": self.threads}}
            with open(self.events, "a", encoding="utf-8") as f:
                f.write(json.dumps(line, separators=(",", ":")) + "\n")
        self._rep_start, self._rep_busy, self._rep_units, self._rep_duty = now, 0.0, 0, d

    def close(self):
        """Write the last report."""
        self._report(force=True)
