"""Service protocol 1: node helpers and probes the agent runs generically. See spec/service-protocol.md.

    <service exec...> <op> [args]      ops: fingerprint | start | stop | status | ready | list_owned | destroy <id> [--force]

Inputs arrive as environment variables (OARBANK_HOME, OARBANK_SERVICE, OARBANK_MODULE, OARBANK_SETTINGS_JSON,
OARBANK_NODE_ID); output is one JSON document on stdout; exit 0 = success. Every op is idempotent.
Probes implement only `fingerprint`.
"""
from typing import Any, Literal

from pydantic import Field

from ._base import Contract

OPS = ("fingerprint", "start", "stop", "status", "ready", "list_owned", "destroy")
DEFAULT_TIMEOUT_S = {"fingerprint": 5, "status": 5, "ready": 5, "list_owned": 5, "destroy": 60, "start": 120, "stop": 120}
OWNER_LABELS = ("oarbank.attempt_id", "oarbank.node", "oarbank.service")


class Versions(Contract):
    supported: list[int] = Field(min_length=1)


class Fingerprint(Contract):
    service_protocol: Versions
    health: Literal["healthy", "unhealthy", "undetected"] = Field(description="[beta] undetected: not offered, no alert; unhealthy: offered at zero capacity, the agent tries to heal.")
    attrs: dict[str, Any] = Field(default_factory=dict)
    pools: dict[str, int] = Field(default_factory=dict, description="[beta] Tokens this service can provide right now, computed by the module from its own settings.")
    reserve: dict[str, float] = Field(default_factory=dict, description="[beta] Host resources charged while running, e.g. {mem_gb: 8}.")
    running: bool | None = Field(None, description="[beta] Services only: whether it is currently up (used for adoption after an agent restart).")


class Status(Contract):
    running: bool
    ready: bool | None = None
    detail: str = ""


class OwnedObject(Contract):
    id: str
    labels: dict[str, str] = Field(description="[beta] Must include the oarbank.* ownership labels; unlabelled objects are never reaped.")
    created_at: float | None = None
    kind: str | None = None


class OwnedList(Contract):
    objects: list[OwnedObject] = Field(default_factory=list)


class OpResult(Contract):
    ok: bool = True
    detail: str = ""
    escalated: bool = Field(False, description="[beta] stop/destroy needed the forceful path.")
