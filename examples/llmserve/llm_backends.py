"""llmserve's model backends, one interface: `load(...)` then `generate(prompt, max_tokens, duty, control)`, which yields
the completion's text piece by piece and runs every unit of GPU work inside `duty.unit()` (oarbank_sdk.duty), so the
runner holds the agent's `gpu_duty`.

- `stub`: a small deterministic stand-in written for tests. It generates words of a fixed text from its bigram
  table (so it can only say what that text says), and each token costs an integer matrix-vector product on the CPU (`work` sets its size) that stands for a GPU
  unit. Integer arithmetic only, so every platform gives the same text: the `selftest` golden and the coordinator's own
  check of a selftest result rely on it. It is not a language model.
- `mlx`: mlx_lm as a library in this process (its HTTP server wires memory and cannot be paused or throttled). A unit is
  one step of generation, which ends with the token on the CPU (the GPU's work done); prefill runs in steps no larger
  than `duty.scale()` allows. Beside protected work it sets an MLX memory limit and no wired limit.
- `llamacpp`: llama-cpp-python in this process (llama-server would listen on a port, which the sandbox refuses). A unit
  is one evaluation (a prompt chunk of at most `n_batch` tokens, or one generated token).

Neither real backend is a dependency of the module: an owner installs one (docs: "Run GPU work beside a protected
program"). `available()` says which can be imported here.
"""
import importlib.util
import os
import sys

STUB_TEXT = ("the fleet runs work beside the programs its owner protects. a duty is the share of each second the gpu "
             "may be busy with fleet work; the agent measures it and the runner holds it. when the owner needs the "
             "machine, fleet work waits, and when the owner is away it runs again. ")
MASK = (1 << 31) - 1


def available() -> list[str]:
    """The backends this Python can load: stub always; mlx on macOS on Apple Silicon with mlx_lm installed; llamacpp
    with llama_cpp installed."""
    out = ["stub"]
    if sys.platform == "darwin" and os.uname().machine == "arm64" and importlib.util.find_spec("mlx_lm"):
        out.append("mlx")
    if importlib.util.find_spec("llama_cpp"):
        out.append("llamacpp")
    return out


def choose(asked: str) -> str:
    """`auto`: MLX where it can run, else llama.cpp; otherwise the backend asked for (ValueError when it cannot load)."""
    have = available()
    if asked == "auto":
        for b in ("mlx", "llamacpp"):
            if b in have:
                return b
        raise ValueError("no model backend installed (install mlx-lm on macOS on Apple Silicon, or llama-cpp-python)")
    if asked not in have:
        raise ValueError(f"backend {asked!r} is not installed here (installed: {', '.join(have)})")
    return asked


# ---------------------------------------------------------------------------- the stand-in

class Stub:
    name = "stub"

    def __init__(self, work: int = 64, seed: int = 7):
        self.work = max(4, min(int(work), 1024))
        words = STUB_TEXT.split()
        self.vocab = sorted(set(words))
        self.index = {w: i for i, w in enumerate(self.vocab)}
        nxt: dict[int, list[int]] = {}
        for a, b in zip(words, words[1:] + words[:1]):
            nxt.setdefault(self.index[a], []).append(self.index[b])
        self.next = nxt
        x = seed & MASK
        self.matrix = []
        for _ in range(self.work):
            row = []
            for _ in range(self.work):
                x = (1103515245 * x + 12345) & MASK
                row.append(x >> 16)
            self.matrix.append(row)

    def _mix(self, state: list[int]) -> list[int]:
        """The unit of work: an integer matrix-vector product (what a GPU unit stands for)."""
        return [sum(m * s for m, s in zip(row, state)) & MASK for row in self.matrix]

    def generate(self, prompt: str, max_tokens: int, duty, control):
        """Words of the fixed text only, each one token, in an order the prompt seeds."""
        last = self.index.get((prompt.split() or [""])[-1].lower(), 0)
        state = [(ord(c) * 31 + i) & MASK for i, c in enumerate((prompt or " ")[: self.work].ljust(self.work))]
        for _ in range(max_tokens):
            control.safe_point()
            with duty.unit():
                state = self._mix(state)
            options = self.next.get(last) or [0]
            last = options[state[0] % len(options)]
            yield " " + self.vocab[last]


# ---------------------------------------------------------------------------- MLX

class Mlx:
    name = "mlx"

    def __init__(self, path: str, mem_gb: float | None = None):
        import mlx.core as mx
        from mlx_lm import load
        self.mx = mx
        if mem_gb:                       # beside a protected process: a memory limit, and nothing wired
            for f in ("set_memory_limit", "set_wired_limit"):
                fn = getattr(mx, f, None) or getattr(getattr(mx, "metal", None), f, None)
                if fn:
                    fn(int(mem_gb * 2**30) if f == "set_memory_limit" else 0)
        self.model, self.tokenizer = load(path)

    def generate(self, prompt: str, max_tokens: int, duty, control):
        from mlx_lm.generate import generate_step
        ids = self.mx.array(self.tokenizer.encode(prompt))
        step = max(32, int(2048 * duty.scale()))
        gen = generate_step(ids, self.model, max_tokens=max_tokens, prefill_step_size=step)
        detok = self.tokenizer.detokenizer
        detok.reset()
        while True:
            control.safe_point()
            with duty.unit():
                try:
                    tok, _ = next(gen)       # the step ends with the token on the CPU: the GPU's work is done
                    self.mx.synchronize()
                except StopIteration:
                    break
            if tok in self.tokenizer.eos_token_ids:
                break
            detok.add_token(int(tok))
            yield detok.last_segment
        detok.finalize()
        yield detok.last_segment


# ---------------------------------------------------------------------------- llama.cpp

class LlamaCpp:
    name = "llamacpp"

    def __init__(self, path: str, n_ctx: int = 4096, n_batch: int = 256, seed: int = 7):
        from llama_cpp import Llama
        self.n_batch = n_batch
        self.llm = Llama(model_path=path, n_ctx=n_ctx, n_batch=n_batch, n_ubatch=min(n_batch, 256), n_gpu_layers=-1,
                         seed=seed, verbose=False)

    def generate(self, prompt: str, max_tokens: int, duty, control):
        llm = self.llm
        llm.reset()
        toks = llm.tokenize(prompt.encode("utf-8"), add_bos=True)
        chunk = max(8, int(self.n_batch * duty.scale()))
        for i in range(0, len(toks), chunk):           # prefill in units no longer than the duty allows
            control.safe_point()
            with duty.unit():
                llm.eval(toks[i:i + chunk])
        out = b""
        for _ in range(max_tokens):
            control.safe_point()
            tok = llm.sample(temp=0.0)
            if tok == llm.token_eos():
                break
            with duty.unit():
                llm.eval([tok])
            piece = llm.detokenize([tok])
            out += piece
            try:
                text = out.decode("utf-8")
            except UnicodeDecodeError:
                continue                               # a character split across tokens
            out = b""
            yield text


def load(backend: str, model_path: str | None, work: int = 64, mem_gb: float | None = None):
    if backend == "stub":
        return Stub(work)
    if not model_path or not os.path.exists(model_path):
        raise FileNotFoundError(f"no model at {model_path!r}: put an MLX model directory or a GGUF file under the module's "
                                "data directory, models/<name>, and name it in the job")
    return Mlx(model_path, mem_gb) if backend == "mlx" else LlamaCpp(model_path)
