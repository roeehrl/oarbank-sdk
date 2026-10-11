"""Workload protocol 1: how the agent runs a workload, a supervised long-running process that is the work itself. See
spec/workload-protocol.md.

    <workload exec...> run       the workload: one long-running process, the leader of its process container
    <workload exec...> status    the health check (with `health` declared): one Status JSON object on stdout, exit 0

The environment is the protocol's (spec/workload-protocol.md, "Environment"). The control document is the runner's
(`runner_protocol.Control`), at OARBANK_CONTROL_FILE; a workload re-reads it when nudged and at least every
CONTROL_REREAD_S. It appends Event lines to OARBANK_EVENTS_FILE. `oarbank_sdk.workload` is a stdlib-only reference
implementation (the supervisor shim and the event helpers).
"""
import json
from typing import Annotated, Any, Literal, Union

from pydantic import Field, RootModel, field_validator

from ._base import Contract

OPS = ("run", "status")
EXIT_USAGE = 64                                # an op the workload does not know (EX_USAGE)

ENV_WORKLOAD, ENV_MODULE, ENV_NODE_ID = "OARBANK_WORKLOAD", "OARBANK_MODULE", "OARBANK_NODE_ID"
ENV_RUN, ENV_MODULE_DATA = "OARBANK_WORKLOAD_RUN", "OARBANK_MODULE_DATA"
ENV_CONTROL_FILE, ENV_EVENTS_FILE, ENV_SECRETS_FILE = "OARBANK_CONTROL_FILE", "OARBANK_EVENTS_FILE", "OARBANK_SECRETS_FILE"
ENV_SETTINGS_FILE, ENV_TOOLS_FILE, ENV_LIMITS_FILE = "OARBANK_SETTINGS_FILE", "OARBANK_TOOLS_FILE", "OARBANK_LIMITS_FILE"
ENV_SEAT_ID, ENV_SEAT_FENCE = "OARBANK_SEAT_ID", "OARBANK_SEAT_FENCE"
ENV_CONTROL_EVENT = "OARBANK_CONTROL_EVENT"    # Windows: the inherited auto-reset event's handle, in decimal
ENV_ENDPOINT_CHANNEL = "OARBANK_ENDPOINT_CHANNEL"
EVENTS_FILE = "events.ndjson"                  # <run>/events.ndjson

CONTROL_REREAD_S = 2.0                         # a workload re-reads the control document at least this often
REPORT_DATA_MAX = 4096                         # bytes of a report event's `data` (compact UTF-8 JSON)
REPORTS_PER_MINUTE = 60                        # what the agent ships per workload; the rest waits (200 queued)
MESSAGE_MAX = 4096                             # characters of an error or log message the agent keeps


def compact(data) -> bytes:
    """Compact UTF-8 JSON, as the 4 KiB limit on `data` is measured."""
    return json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


class Status(Contract):
    """What `<exec> status` prints: one JSON object on stdout, then exit 0. The agent runs it every `health.period_s`
    (with `health.timeout_s`); `health.failures` consecutive unhealthy answers (a timeout, a non-zero exit or `healthy:
    false`) end the run as a failure. [beta]"""
    healthy: bool = Field(description="[beta] Whether the workload is doing its work.")
    detail: str = Field("", max_length=MESSAGE_MAX, description="[beta] Why, in a line: shown with the workload's state.")
    paused: bool | None = Field(None, description="[beta] It holds a cooperative pause (control `pause`).")
    threads: int | None = Field(None, ge=1, description="[beta] The compute-thread limit it applies now (None: none).")
    gpu_duty_supported: bool | None = Field(None, description="[beta] It honours the control document's `gpu_duty`.")


class _EventBase(Contract):
    t: float = Field(description="[beta] Unix seconds when the workload wrote it.")


class ProgressEvent(_EventBase):
    """A count of something the workload did (`n` of `event`); the agent sums them into the node's heartbeat. [beta]"""
    type: Literal["progress"]
    event: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_.-]*$", description="[beta] What happened.")
    n: int = Field(1, ge=0, description="[beta] How many times.")


class ErrorEvent(_EventBase):
    """Something went wrong: the agent keeps the latest as the run's last error. [beta]"""
    type: Literal["error"]
    message: str = Field(max_length=MESSAGE_MAX)


class ReportEvent(_EventBase):
    """An observation for the module's own records: shipped to the coordinator (at most 60 a minute per workload), which
    calls the module's `workload.report` verb with it (manifest `reports = true`). [beta]"""
    type: Literal["report"]
    data: dict[str, Any] = Field(description="[beta] At most 4 KiB as compact JSON.")

    @field_validator("data")
    @classmethod
    def _small(cls, v):
        n = len(compact(v))
        if n > REPORT_DATA_MAX:
            raise ValueError(f"report data is {n} bytes as JSON; at most {REPORT_DATA_MAX}")
        return v


class LogEvent(_EventBase):
    """A line for the workload's log, with a level. [beta]"""
    type: Literal["log"]
    level: Literal["debug", "info", "warn", "error"] = "info"
    message: str = Field(max_length=MESSAGE_MAX)


class Event(RootModel[Annotated[Union[ProgressEvent, ErrorEvent, ReportEvent, LogEvent], Field(discriminator="type")]]):
    """One line of OARBANK_EVENTS_FILE (`<run>/events.ndjson`), UTF-8 JSON, LF-terminated, appended by the workload; the
    agent reads complete lines only. [beta]"""
