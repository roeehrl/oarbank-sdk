"""Runner protocol 1: how the agent runs a job. See spec/runner-protocol.md and spec/platforms.md.

    <runner exec...> run --spec <W>/spec.json --workdir <W> --out <W>/result.json [--events <W>/events.ndjson]
    <runner exec...> doctor --json

The environment is exactly the protocol's (spec/runner-protocol.md, "Environment"), with the conventional variables
derived per OS (spec/platforms.md). The control plane is <W>/control.json, always present: `stop` and `pause` are
requests a runner honours at its safe points. The agent nudges the runner after every change (SIGUSR1 on POSIX, the
inherited event named by OARBANK_CONTROL_EVENT on Windows), and the runner re-reads the document then, never on a timer.
"""
from typing import Annotated, Any, Literal

from pydantic import Field, field_validator

from . import portable
from ._base import Contract

# Exit codes ------------------------------------------------------------------
EXIT_OK = 0             # result.json written atomically
EXIT_INVALID_SPEC = 2   # the spec can never succeed (job fault)
EXIT_MISSING_DEP = 3    # a node dependency is missing (host fault: re-doctor)
EXIT_RETRYABLE = 75     # EX_TEMPFAIL: transient; retried without counting as a job fault
# 2, 3 and 75 count only when failure.json was written (Windows abort() exits 3, argparse exits 2); anything else is a
# failure attributed by the core's breaker rules

ENV_WORKDIR, ENV_TMP, ENV_PLATFORM = "OARBANK_WORKDIR", "OARBANK_TMP", "OARBANK_PLATFORM"
ENV_MODULE, ENV_MODULE_DATA, ENV_ATTEMPT, ENV_PROTOCOL = "OARBANK_MODULE", "OARBANK_MODULE_DATA", "OARBANK_ATTEMPT_ID", "OARBANK_PROTOCOL"
ENV_SETTINGS_FILE, ENV_LIMITS_FILE = "OARBANK_SETTINGS_FILE", "OARBANK_LIMITS_FILE"
ENV_BROKER = "OARBANK_BROKER"
ENV_TOOLS_FILE = "OARBANK_TOOLS_FILE"          # {"<tool id>": ["<canonical path>", ...]} for the approved [sandbox].tools
ENV_FOLDERS_FILE = "OARBANK_FOLDERS_FILE"      # {"<folder id>": {"path": "<canonical path>", "access": "read|write"}}
ENV_CONTROL_EVENT = "OARBANK_CONTROL_EVENT"    # Windows: the inherited control event's handle, in decimal
CONTROL_FILE, FAILURE_FILE, RESULT_FILE = "control.json", "failure.json", "result.json"
CHECKPOINT_DIR = "checkpoint"                  # <W>/checkpoint/: the checkpoint a resumed attempt starts from (read-only)
CHECKPOINT_DATA_MAX = 4096                     # bytes of a checkpoint event's `data` (compact JSON)


class Versions(Contract):
    supported: list[int] = Field(min_length=1)


class DoctorCheck(Contract):
    name: str
    ok: bool
    detail: str = ""


class DoctorOutput(Contract):
    """`doctor --json` stdout."""
    runner_protocol: Versions = Field(description="[stable] Majors this runner speaks; the agent picks the highest shared one.")
    capabilities: list[str] = Field(default_factory=list)
    health: Literal["healthy", "unhealthy", "undetected"] = Field(description="[stable] undetected = cannot run here, don't offer, don't alert.")
    attrs: dict[str, Any] = Field(default_factory=dict)
    checks: list[DoctorCheck] = Field(default_factory=list)


class CheckpointFile(Contract):
    """One file of a checkpoint: a regular file in the workdir, and where it appears in the checkpoint. [beta]"""
    path: str = Field(description="[beta] The workdir file (a PortablePath). Once the event is written it belongs to the agent, which moves it away.")
    name: str | None = Field(None, description="[beta] Where it appears under <W>/checkpoint/ on resume (a PortablePath; default: `path`).")

    @field_validator("path", "name")
    @classmethod
    def _portable(cls, v):
        if v is not None:
            portable.check_portable_path(v)
        return v

    def checkpoint_name(self) -> str:
        return self.name or self.path


class Event(Contract):
    """One NDJSON line in --events (capability progress_events)."""
    t: float = Field(description="[stable] Unix seconds.")
    kind: Literal["log", "progress", "metric", "checkpoint", "phase"]
    level: Literal["debug", "info", "warn", "error"] | None = None
    msg: str | None = None
    fraction: float | None = Field(None, ge=0, le=1, description="[stable] progress: 0..1.")
    name: str | None = Field(None, description="[stable] metric/phase name.")
    value: float | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    files: list[CheckpointFile] = Field(default_factory=list, description=(
        "[beta] checkpoint: the checkpoint's files (runner capability `checkpoint`, a stage with `checkpoint`); `data` (at "
        "most 4 KiB) comes back in the resumed attempt's spec envelope."))


class Control(Contract):
    """<workdir>/control.json: written atomically by the agent for every job, at start (`{"seq": 0}`) and on every
    change, each change followed by a nudge: SIGUSR1 to the runner process on POSIX, the inherited event named by
    OARBANK_CONTROL_EVENT on Windows. The runner re-reads the document when nudged and acts at its next safe point; it
    never re-reads on a timer. Absent fields mean "no constraint". [stable]"""
    seq: int = Field(description="[stable] Monotonic; apply only newer documents.")
    stop: bool = Field(False, description="[stable] Finish now: exit non-zero (or write a result if done) within stop_grace_s; the agent then terminates the process container.")
    threads: int | None = Field(None, ge=1, description="[beta] Max active compute threads.")
    gpu_duty: float | None = Field(None, ge=0, le=1, description="[beta] Max GPU duty fraction (reduce batch size/concurrency).")
    pause: bool = Field(False, description="[stable] Hold at the next safe point until a newer document clears it (cooperative_pause).")
    reason: str | None = Field(None, description="[beta] Human-readable reason code for logs.")
    checkpoint: bool = Field(False, description=(
        "[beta] Sent only with `stop`: write a checkpoint at the next safe point, then acknowledge the stop, within "
        "runner.checkpoint_grace_s (runner capability `checkpoint`)."))


# failure.json reasons the agent reports as the attempt's end reason (the coordinator maps each to its registry code)
FAILURE_REASONS = ("bad_input", "mode_mismatch", "oom", "doctor", "no_metrics")
FailureReason = Literal["bad_input", "mode_mismatch", "oom", "doctor", "no_metrics"]
ModuleReason = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_-]*/[a-z0-9][a-z0-9_.-]*$", max_length=80)]


class Failure(Contract):
    """<workdir>/failure.json, written (atomically) by a runner that exits non-zero. Exit codes 2, 3 and 75 are
    attributed only when this file exists."""
    reason: FailureReason | ModuleReason = Field(description=(
        "[stable] One of the agent's end reasons: `bad_input` (the spec can never succeed), `mode_mismatch`, `oom`, "
        "`doctor` (a node dependency is missing) or `no_metrics`, which the coordinator maps to its reason codes; or "
        "the module's own `<module-short>/<code>`, which ends the attempt as `exit_nonzero` with the code kept in its "
        "detail."))
    detail: str = Field("", description="[stable] Human-readable detail (truncated to 4 KiB by the agent).")
    retryable: bool | None = Field(None, description="[beta] Overrides the exit-code attribution when set (true = transient).")
    fault: Literal["job", "host", "transient"] | None = Field(None, description="[stable] Who is at fault; overrides the exit code (job: never succeeds; host: the node lacks something; transient: retry).")


# <workdir>/phase: a single line naming the current phase (e.g. `call`, `scoring`, `finishing`), replaced
# atomically (write phase.tmp, rename). Shown in the console and used by host protection to learn
# per-phase resource profiles. Optional; the `phase` event kind carries the same information.
PHASE_FILE = "phase"


def checkpoint_digest(files: list[dict]) -> str:
    """A checkpoint's digest (spec envelope `resume.digest`): sha256 of the canonical JSON list of its files'
    {name, digest, size}, sorted by name."""
    import hashlib
    from .keys import canonical_json
    entries = sorted(({"name": f["name"], "digest": f["digest"], "size": int(f["size"])} for f in files), key=lambda f: f["name"])
    return hashlib.sha256(canonical_json(entries).encode()).hexdigest()
