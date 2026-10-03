"""Spec and result envelopes (envelope 1). See spec/envelopes.md.

A **spec envelope** is what the coordinator stores per job stage and the agent writes to spec.json.
A **result envelope** is what the runner writes to result.json; the core stores it as written and
upgrades old payload versions on read (result.upgrade). Payloads are module-owned and validated
against the module's declared schemas; everything outside `payload` is the core's contract.
"""
from typing import Any, Literal

from pydantic import Field, field_validator

from . import portable

from ._base import Contract, ModuleId, Name, SemVer, Sha256

SCHEMA_REF = r"^[a-z0-9][a-z0-9.-]*/(spec|result)@[1-9][0-9]*$"


def _portable(v):
    if v is not None:
        portable.check_portable_path(v)
    return v


class InputRef(Contract):
    dataset: str = Field(description="[stable] Dataset id (e.g. an artifact dataset `art:<digest>`).")
    mount: str = Field(description="[stable] Workdir-relative directory (a PortablePath) where the agent materialises it.")
    _m = field_validator("mount")(classmethod(lambda cls, v: _portable(v)))


class SpecEnvelope(Contract):
    envelope: Literal[1] = 1
    schema_: str = Field(alias="schema", pattern=SCHEMA_REF, description="[stable] `<module>/spec@N` of the payload.")
    module_id: ModuleId
    module_version: SemVer
    job_key: str = Field(description="[stable] Content key: sha256 of the RFC 8785 canonical JSON of {module, compat, inputs: key_inputs} (spec/platforms.md).")
    stage: Name | None = Field(None, description="[stable] Stage this spec runs (absent for single-stage modules).")
    protocol: int = Field(1, description="[stable] Runner protocol major chosen for this node (also OARBANK_PROTOCOL).")
    datasets: list[str] = Field(default_factory=list, description="[stable] Dataset ids the agent must stage before spawn.")
    mounts: dict[str, str] = Field(default_factory=dict, description="[stable] dataset id -> workdir-relative mount directory (a PortablePath).")
    platform: str | None = Field(None, description="[beta] The platform token of the node the spec was written for (also OARBANK_PLATFORM).")
    inputs: dict[Name, InputRef] = Field(default_factory=dict, description="[stable] Outputs of an upstream stage consumed by this stage.")
    resources: dict[str, Any] = Field(default_factory=dict, description="[stable] Resources the job reserves (cpu, mem_gb, pools).")
    timeout_s: float | None = None
    payload: dict[str, Any] = Field(description="[stable] Module-owned spec payload.")

    @field_validator("mounts")
    @classmethod
    def _mounts(cls, v):
        for m in v.values():
            portable.check_portable_path(m)
        return v


class ArtifactFile(Contract):
    path: str = Field(description="[stable] PortablePath inside the artifact (what the consumer sees under its mount). Artifacts carry no file modes.")
    local: str | None = Field(None, description="[stable] Runner-written only: workdir-relative source (a PortablePath, `/`-separated). The agent uploads it and replaces it with digest/size.")
    _p = field_validator("path", "local")(classmethod(lambda cls, v: _portable(v)))
    digest: Sha256 | None = None
    size: int | None = None


class Artifact(Contract):
    name: Name = Field(description="[stable] Output name; a downstream stage receives it as inputs.<name>.")
    files: list[ArtifactFile] = Field(min_length=1)


class Provenance(Contract):
    argv: dict[str, list[str]] = Field(default_factory=dict, description="[stable] Tool invocations that produced the result.")
    tool_versions: dict[str, str] = Field(default_factory=dict)
    host: str | None = None


class ResultEnvelope(Contract):
    envelope: Literal[1] = 1
    schema_: str = Field(alias="schema", pattern=SCHEMA_REF, description="[stable] `<module>/result@N` of the payload.")
    module_version: SemVer
    protocol: int = 1
    effective: dict[str, Any] = Field(default_factory=dict, description="[stable] The subset of spec parameters/modes actually honoured; the core compares it with what was requested.")
    provenance: Provenance = Field(default_factory=Provenance)
    artifacts: list[Artifact] = Field(default_factory=list)
    payload: dict[str, Any] = Field(description="[stable] Module-owned result payload (<= max_inline_kb; larger data goes in artifacts).")
