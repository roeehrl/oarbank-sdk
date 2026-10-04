"""Module protocol 1: coordinator <-> module, newline-delimited JSON-RPC 2.0 over the module's stdio.
See spec/module-protocol.md.

Lifecycle: host spawns the module (argv from the manifest, the module's own venv, clean environment),
sends `initialize`, the module answers with the protocol version it chose and its capabilities, the host
sends the `initialized` notification, then verbs flow. Shutdown: host sends `shutdown`, closes stdin,
then SIGTERM, then SIGKILL. stderr is captured into the module's log. The module is **stateless between
calls**: the host owns leases, attempts, retries, certification and the database, and every call carries
what it needs. Verbs must be pure functions of their input (the conformance kit checks this).
"""
from typing import Annotated, Any, Literal

from pydantic import Field, field_validator

from . import platform as pf
from . import portable
from ._base import Contract, ModuleId, Name, SemVer

JSONRPC = "2.0"
# A platform token (`linux-amd64`) or an OS name (`linux`).
PlatformKey = Annotated[str, Field(pattern=portable.PLATFORM_KEY.pattern, max_length=40)]

# ---------------------------------------------------------------------------- errors

ERR_PARSE, ERR_INVALID_REQUEST, ERR_METHOD_NOT_FOUND, ERR_INVALID_PARAMS, ERR_INTERNAL = -32700, -32600, -32601, -32602, -32603
ERR_UNSUPPORTED_PROTOCOL = -32001    # data: {supported: [...], requested: [...]}
ERR_PERMISSION_DENIED = -32002       # a host callback not granted by the manifest's permissions
ERR_CAPABILITY_MISSING = -32003      # a verb called that the module did not advertise
ERR_CANCELLED = -32004               # the host cancelled the request ($/cancel)


class Error(Contract):
    code: int
    message: str
    data: dict[str, Any] | None = None


class Request(Contract):
    jsonrpc: Literal["2.0"] = JSONRPC
    id: int | str
    method: str
    params: dict[str, Any] = Field(default_factory=dict)


class Notification(Contract):
    jsonrpc: Literal["2.0"] = JSONRPC
    method: str
    params: dict[str, Any] = Field(default_factory=dict)


class Response(Contract):
    jsonrpc: Literal["2.0"] = JSONRPC
    id: int | str | None
    result: Any | None = None
    error: Error | None = None


# ---------------------------------------------------------------------------- handshake

# Host capability names for the per-platform declarations (spec/module-protocol.md, "Host capabilities"). A module that
# uses the matching fields only at run time checks for them with `ctx.host_has(...)`; a manifest that relies on them
# needs requires.core >= 2.2 instead.
HOST_PLACEMENT = "placement.v1"                  # campaigns.create placement, jobs.enqueue group/platforms, datasets.create platform
HOST_NODES_PLATFORM = "nodes.platform"           # host.nodes.query rows carry platform/os/arch/os_version; `platforms` filter
HOST_GOLDENS_BY_PLATFORM = "goldens.by_platform"  # Golden.platforms and expected_by_platform are honoured
HOST_COORDINATOR_VARIANTS = "coordinator.variants"  # coordinator.variants applied, OARBANK_PLATFORM and host.platform set
HOST_JOBS_STAGE = "jobs.stage"                   # jobs.enqueue items' `stage` is honoured (core 2.3)
HOST_DATASETS_ORIGINS = "datasets.origins"       # datasets.create files may name origins for blobs it does not hold (core 2.5)
HOST_CAPABILITIES = (HOST_PLACEMENT, HOST_NODES_PLATFORM, HOST_GOLDENS_BY_PLATFORM, HOST_COORDINATOR_VARIANTS, HOST_JOBS_STAGE,
                     HOST_DATASETS_ORIGINS)


class HostInfo(Contract):
    name: Literal["oarbank"] = "oarbank"
    version: str
    capabilities: list[str] = Field(default_factory=list, description=(
        "[stable] Host features the module may rely on, e.g. host.datasets.query, placement.v1, nodes.platform, "
        "goldens.by_platform, coordinator.variants, jobs.stage, datasets.origins."))
    platform: str | None = Field(None, description=(
        "[beta] The coordinator host's platform token (also OARBANK_PLATFORM in the module's environment). Verbs keep "
        "job keys platform-independent: never put it in key_inputs."))


class InitializeParams(Contract):
    protocol_versions: list[int] = Field(min_length=1, description="[stable] Majors the host supports, highest first.")
    host: HostInfo
    module_digest: str | None = Field(None, description="[stable] Content digest of the installed bundle, for the module's logs.")
    settings: dict[str, Any] = Field(default_factory=dict, description="[stable] The module's own settings (validated against its schema).")


class ModuleIdentity(Contract):
    id: ModuleId
    version: SemVer


class InitializeResult(Contract):
    protocol_version: int = Field(description="[stable] The one major the module chose from protocol_versions.")
    module: ModuleIdentity
    capabilities: list[str] = Field(default_factory=list, description="[stable] Optional verbs implemented (absent = unsupported).")


# ---------------------------------------------------------------------------- DTOs the host passes in

class DatasetRef(Contract):
    id: str
    kind: str
    attrs: dict[str, Any] = Field(default_factory=dict)
    files: list[dict[str, Any]] = Field(default_factory=list, description="[stable] {path, digest, size} per file, with [beta] `origins` when it has any.")


class GPUAPIs(Contract):
    """The GPU APIs a node provides, from its agent's doctor report (spec/runner-protocol.md, "GPU use"). [beta]"""
    host: list[str] = Field(default_factory=list, description="[beta] On the node itself (metal, cuda, rocm, vulkan, opencl, directml).")
    containers: list[str] = Field(default_factory=list, description="[beta] Inside containers the agent's broker runs with `gpus = \"all\"`.")


class NodeClass(Contract):
    """What golden.list may know about a node: its platform class, capabilities and pools; never identity. [stable]"""
    platform: str | None = Field(None, description="[stable] Platform token, e.g. darwin-arm64.")
    os_version: str | None = Field(None, description="[stable] OS version (macOS 26.1, Windows build 10.0.26100, Linux distro release).")
    cpu: dict[str, Any] = Field(default_factory=dict, description="[beta] {vendor, model, logical, core_classes, features}.")
    gpus: list[dict[str, Any]] = Field(default_factory=list, description="[beta] [{vendor, model, vram_gb, unified}].")
    gpu_apis: GPUAPIs = Field(default_factory=GPUAPIs, description="[beta] The GPU APIs the node provides, so goldens can "
                              "differ per API. Needs core 2.5 (older hosts send none).")
    capabilities: list[str] = Field(default_factory=list)
    pools: dict[str, int] = Field(default_factory=dict)


class Issue(Contract):
    path: str = Field("", description="[stable] JSON pointer into the input, '' = whole document.")
    message: str
    code: str | None = Field(None, description="[stable] Module-namespaced reason code, e.g. render/tile_size_order.")


# ---------------------------------------------------------------------------- verbs

class ParamsCheckParams(Contract):
    params: dict[str, Any]


class ParamsCheckResult(Contract):
    ok: bool
    normalized_params: dict[str, Any] | None = None
    errors: list[Issue] = Field(default_factory=list)


class PlanItem(Contract):
    """One evaluation the module wants run (e.g. one (params, dataset) pair)."""
    key_inputs: dict[str, Any] = Field(description="[stable] Canonical inputs of the job key; the host hashes (module_id, compat, key_inputs).")
    datasets: list[str] = Field(default_factory=list)
    stages: list[Name] = Field(default_factory=list, description="[stable] Stages to run (subset of the manifest's, in dependency order); empty = all.")
    resources: dict[Name, dict[str, Any]] = Field(default_factory=dict, description="[stable] Per-stage resource overrides within the manifest's bounds.")
    label: str | None = None
    group: str | None = Field(None, max_length=64, description=(
        "[beta] A job group within the campaign: with [placement] unit = \"group\", the jobs of one group stay on one "
        "platform class."))
    platforms: list[PlatformKey] = Field(default_factory=list, description=(
        "[beta] Only on these platforms (tokens or OSes; empty: every platform the stages allow)."))


class JobPlanParams(Contract):
    params: dict[str, Any]
    datasets: list[DatasetRef] = Field(default_factory=list)
    study: dict[str, Any] = Field(default_factory=dict, description="[stable] Study id, name and module-specific options.")


class JobPlanResult(Contract):
    jobs: list[PlanItem]


class SpecBuildParams(Contract):
    jobs: list[PlanItem]
    datasets: list[DatasetRef] = Field(default_factory=list, description="[stable] Resolved dataset manifests referenced by the jobs.")
    target_spec_version: int = Field(description="[stable] Highest spec payload version every eligible runner understands.")


class StageSpec(Contract):
    stage: Name | None = None
    payload: dict[str, Any]
    datasets: list[str] = Field(default_factory=list)
    mounts: dict[str, str] = Field(default_factory=dict)


class BuiltSpec(Contract):
    key_inputs: dict[str, Any]
    spec_version: int
    stages: list[StageSpec]


class SpecBuildResult(Contract):
    specs: list[BuiltSpec]


Verdict = Literal["accept", "reject", "retry", "fail_permanent"]


class ResultEvaluateParams(Contract):
    spec: dict[str, Any] = Field(description="[stable] The spec envelope the attempt ran.")
    result: dict[str, Any] = Field(description="[stable] The result envelope as written by the runner (after artifact upload).")
    stage: Name | None = None


class ResultEvaluateResult(Contract):
    verdict: Verdict = Field(description="[stable] accept; reject (bad result, counts against the node); retry (transient, re-run); fail_permanent (the job can never succeed).")
    reason: str = Field("ok", description="[stable] Reason code (module-namespaced for module-specific reasons).")
    value: float | None = Field(None, description="[stable] Objective value (the manifest's results.value field).")
    digest: str | None = Field(None, description="[stable] Content digest for replica/golden comparison.")
    digest_version: int | None = None
    summary: dict[str, Any] = Field(default_factory=dict, description="[stable] Small dict of declared fields for tables.")
    fields: dict[str, Any] = Field(default_factory=dict, description="[stable] Declared result fields (indexed ones become generated columns).")


class ResultMergeParams(Contract):
    stages: dict[Name, dict[str, Any]] = Field(description="[stable] Stage name -> accepted result envelope.")


class ResultMergeResult(Contract):
    result: dict[str, Any]


class ResultUpgradeParams(Contract):
    payload: dict[str, Any]
    from_version: int
    to_version: int


class ResultUpgradeResult(Contract):
    payload: dict[str, Any]


class GoldenListParams(Contract):
    node_class: NodeClass


class Golden(Contract):
    name: str
    key_inputs: dict[str, Any]
    datasets: list[str] = Field(default_factory=list)
    stages: list[Name] = Field(default_factory=list, description="[stable] Stages this golden exercises (e.g. only 'call' on nodes without the scoring pool).")
    expected: dict[str, Any] = Field(description="[stable] At least {digest, digest_version}; more for golden.compare.")
    platforms: list[PlatformKey] = Field(default_factory=list, description=(
        "[beta] Only for nodes of these platforms (tokens or OSes; empty: every platform). The host drops the golden for "
        "other nodes."))
    expected_by_platform: dict[PlatformKey, dict[str, Any]] = Field(default_factory=dict, description=(
        "[beta] `expected` per platform token or OS, for results that legitimately differ across platforms; the most "
        "specific key wins, then `expected`. Keys are declared platforms or their OSes."))

    def for_platform(self, platform: str | None) -> "Golden":
        """This golden as a node of `platform` runs it: `expected` resolved (token, then OS, then `expected`)."""
        if not self.expected_by_platform:
            return self
        exp = pf.resolve(self.expected_by_platform, platform, self.expected) if platform else self.expected
        return self.model_copy(update={"expected": exp, "expected_by_platform": {}})

    def runs_on(self, platform: str | None) -> bool:
        """Whether a node of `platform` gets this golden (a node of unknown platform gets only unrestricted ones)."""
        if not self.platforms:
            return True
        return bool(platform) and pf.matches(platform, self.platforms)


class GoldenListResult(Contract):
    goldens: list[Golden]


class GoldenCompareParams(Contract):
    expected: dict[str, Any]
    result: dict[str, Any]


class GoldenCompareResult(Contract):
    ok: bool
    reason: str = "ok"


class StudyMetricsParams(Contract):
    study: dict[str, Any]
    results: list[dict[str, Any]] = Field(description="[stable] Per trial: accepted result summaries and values.")


class StudyMetricsResult(Contract):
    columns: dict[str, dict[str, Any]] = Field(description="[stable] trial label -> {study_column_name: value}.")


class ParamsDistanceParams(Contract):
    a: dict[str, Any]
    b: dict[str, Any]


class ParamsDistanceResult(Contract):
    distance: float = Field(ge=0)


# ---------------------------------------------------------------------------- UI contract 1 verbs (spec/ui-contract.md)

class ViewComputeParams(Contract):
    view_id: str
    params: dict[str, Any] = Field(default_factory=dict)
    inputs: dict[str, Any] = Field(default_factory=dict, description="[beta] Data the host resolved for the view's declared inputs.")
    data_version: int = Field(description="[beta] The input data version this computation is for (echoed back).")


class ViewComputeResult(Contract):
    rows: list[dict[str, Any]] | None = None
    kv: dict[str, Any] | None = None
    series: dict[str, list[Any]] | None = None
    stat: dict[str, Any] | None = None
    data_version: int


class Effect(Contract):
    """A core change the module asks the host to make; the host applies it on the single writer."""
    kind: Literal["jobs.enqueue", "jobs.cancel", "campaigns.create", "campaigns.update", "campaigns.cancel",
                  "datasets.create", "datasets.update", "datasets.delete", "module_settings.update",
                  "store.write", "store.delete", "files.write", "files.put", "files.delete", "external"]
    args: dict[str, Any] = Field(default_factory=dict)


class OpPlanParams(Contract):
    verb: str
    target: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    actor: str


class OpPlanResult(Contract):
    summary: str = Field(max_length=500, description="[beta] Plain text; shown under the host's own operation title.")
    targets: list[str] = Field(default_factory=list)
    effects: list[Effect] = Field(default_factory=list)
    diff: list[dict[str, Any]] = Field(default_factory=list, description="[beta] {path, before, after} rows.")


class OpApplyParams(Contract):
    verb: str
    target: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    actor: str
    plan: OpPlanResult | None = Field(None, description="[beta] The reviewed plan for T2/T3 operations.")


class OpApplyResult(Contract):
    effects: list[Effect] = Field(default_factory=list)
    response: Literal["toast", "refresh", "redirect", "job", "errors"] = "toast"
    message: str = Field("", max_length=500)
    errors: list[Issue] = Field(default_factory=list)
    result: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------- campaigns

# Coordinator capabilities that are features rather than verbs (a module advertises them with Module(features=...)).
CAP_TICK_RESULTS = "campaign.tick.results"       # campaign.tick gets each done job's canonical payload and artifacts
FEATURES = (CAP_TICK_RESULTS,)
TICK_RESULTS_BUDGET = 8 << 20                    # bytes of results one campaign.tick carries at most (newest first)


class ResultFile(Contract):
    path: str = Field(description="[beta] The file's path inside the artifact.")
    digest: str = Field(description="[beta] sha256 of the file: a blob the coordinator holds (datasets.create, files.put).")
    size: int | None = None


class ResultArtifact(Contract):
    name: str
    files: list[ResultFile] = Field(default_factory=list)


class CampaignResult(Contract):
    """A done job's canonical result as campaign.tick sees it (capability campaign.tick.results). [beta]"""
    payload: dict[str, Any] = Field(description=(
        "[beta] The result payload, valid against results.schema and within results.max_inline_kb (the host checked both "
        "when it accepted the result)."))
    artifacts: list[ResultArtifact] = Field(default_factory=list, description="[beta] The uploaded artifacts' files.")


class CampaignJob(Contract):
    job_id: int
    job_key: str
    state: str
    kind: str = "eval"
    stage: str | None = None
    dataset_id: str | None = None
    labels: dict[str, Any] = Field(default_factory=dict)
    value: float | None = None
    digest: str | None = None
    fields: dict[str, Any] = Field(default_factory=dict)
    done_at: float | None = None
    platform: str | None = Field(None, description="[beta] Platform of the node that produced the canonical result.")
    group: str | None = Field(None, description="[beta] The job's group (jobs.enqueue `group`).")
    result: CampaignResult | None = Field(None, description=(
        "[beta] With the campaign.tick.results capability: the canonical result of a done job, when a module version "
        "declaring the capability accepted it. One tick carries at most TICK_RESULTS_BUDGET bytes of results, newest "
        "done first."))
    result_omitted: bool = Field(False, description="[beta] A done job's result left out to keep the tick within its budget.")


class CampaignTickParams(Contract):
    """The host's periodic call for a running campaign of this module (and after its jobs change)."""
    campaign: dict[str, Any] = Field(description=(
        "[beta] {campaign_id, name, state, priority, weight, labels, placement}; placement [beta] is {mix, unit, class, "
        "state} (class: the bound class key or null; state: unbound, soft, hard or pinned)."))
    jobs: list[CampaignJob] = Field(default_factory=list, description=(
        "[beta] Every evaluation of the campaign, with its canonical result's value, digest and fields (and, with "
        "campaign.tick.results, its payload and artifacts)."))
    now: float


class CampaignTickResult(Contract):
    effects: list[Effect] = Field(default_factory=list, description="[beta] Only kinds declared in coordinator.campaign_effects.")
    message: str = Field("", max_length=500)


# ---------------------------------------------------------------------------- integrity checks and coordinator moves

class CheckItem(Contract):
    """One named check a module ran over its own state. [beta]"""
    name: str = Field(max_length=120, description="[beta] Short, stable check name, e.g. `rounds_index_matches_files`.")
    ok: bool
    severity: Literal["error", "warn", "info"] = Field("error", description="[beta] Only failed `error` checks fail the whole result.")
    detail: str = Field("", max_length=1000)


class IntegrityCheckParams(Contract):
    """The host asks a module to verify its own state (store documents, files, datasets) through host callbacks.
    Read-only: the verb returns no effects. [beta]"""
    scope: Literal["routine", "on_demand", "move_source", "move_target"] = Field(
        description="[beta] `routine`: the host's daily run; `on_demand`: an operator asked; `move_source` / `move_target`: "
                    "the frozen old coordinator and the target's verified copy during a coordinator move.")
    deep: bool = Field(False, description="[beta] The operator asked for an expensive check (for example re-reading every file).")
    move_id: str | None = None
    now: float


class IntegrityCheckResult(Contract):
    ok: bool = Field(description="[beta] False when any `error` check failed.")
    checks: list[CheckItem] = Field(default_factory=list)
    fingerprint: str | None = Field(None, max_length=128, description=(
        "[beta] A digest of the state the module considers its own. During a move the host compares the source's and the "
        "target's fingerprints, and any difference aborts the move. Derive it from the state only (never from time)."))


class MoveBlocker(Contract):
    code: str = Field(max_length=80, description="[beta] `<module-short>/<code>`.")
    message: str = Field(max_length=500)


class MovePreflightParams(Contract):
    """A coordinator move is planned or starting. [beta]"""
    move_id: str
    to_url: str
    not_before: float
    phase: Literal["planned", "draining"] = Field(description=(
        "[beta] `planned`: the operator is reviewing or requesting the move (report blockers; effects are ignored); "
        "`draining`: the time lock passed and dispatch stopped. The host calls again every few seconds until no blocker "
        "remains or the wait expires."))
    now: float


class MovePreflightResult(Contract):
    blockers: list[MoveBlocker] = Field(default_factory=list, description="[beta] While any remains, the cutover waits.")
    checks: list[CheckItem] = Field(default_factory=list)
    effects: list[Effect] = Field(default_factory=list, description="[beta] Only kinds in coordinator.move.effects; applied while draining.")
    message: str = Field("", max_length=500)


class MoveSkipped(Contract):
    kind: Literal["files", "store"]
    selector: str = Field(description="[beta] The rule's files prefix or store collection.")
    class_: Literal["rebuild", "drop"] = Field(alias="class")
    count: int
    bytes: int


class MovePostflightParams(Contract):
    """Called once on the new coordinator after it took over. [beta]"""
    move_id: str
    from_url: str
    epoch: int
    skipped: list[MoveSkipped] = Field(default_factory=list, description="[beta] What did not move, by the module's own rules.")
    now: float


class MovePostflightResult(Contract):
    checks: list[CheckItem] = Field(default_factory=list, description="[beta] A failed `error` check raises an alert on the new coordinator.")
    effects: list[Effect] = Field(default_factory=list, description="[beta] Only kinds in coordinator.move.effects (resume work, rebuild what was skipped).")
    message: str = Field("", max_length=500)


class MoveCancelledParams(Contract):
    """The move was cancelled or aborted before the commit decision; this coordinator keeps serving. [beta]"""
    move_id: str
    reason: str = ""
    now: float


class MoveCancelledResult(Contract):
    effects: list[Effect] = Field(default_factory=list, description="[beta] Only kinds in coordinator.move.effects.")
    message: str = Field("", max_length=500)


# Verb registry: method -> (params model, result model, required?, capability name if optional)
VERBS: dict[str, tuple[type, type, bool, str | None]] = {
    "params.check": (ParamsCheckParams, ParamsCheckResult, True, None),
    "job.plan": (JobPlanParams, JobPlanResult, True, None),
    "spec.build": (SpecBuildParams, SpecBuildResult, True, None),
    "result.evaluate": (ResultEvaluateParams, ResultEvaluateResult, True, None),
    "result.merge": (ResultMergeParams, ResultMergeResult, False, "result.merge"),
    "result.upgrade": (ResultUpgradeParams, ResultUpgradeResult, False, "result.upgrade"),
    "golden.list": (GoldenListParams, GoldenListResult, True, None),
    "golden.compare": (GoldenCompareParams, GoldenCompareResult, False, "golden.compare"),
    "study.metrics": (StudyMetricsParams, StudyMetricsResult, False, "study.metrics"),
    "params.distance": (ParamsDistanceParams, ParamsDistanceResult, False, "params.distance"),
    "ui.view.compute": (ViewComputeParams, ViewComputeResult, False, "ui.view.compute"),
    "op.plan": (OpPlanParams, OpPlanResult, False, "op.plan"),
    "op.apply": (OpApplyParams, OpApplyResult, False, "op.apply"),
    "campaign.tick": (CampaignTickParams, CampaignTickResult, False, "campaign.tick"),
    "integrity.check": (IntegrityCheckParams, IntegrityCheckResult, False, "integrity.check"),
    "move.preflight": (MovePreflightParams, MovePreflightResult, False, "move.preflight"),
    "move.postflight": (MovePostflightParams, MovePostflightResult, False, "move.postflight"),
    "move.cancelled": (MoveCancelledParams, MoveCancelledResult, False, "move.cancelled"),
}

# ---------------------------------------------------------------------------- host callbacks (module -> host)

class DatasetsQueryParams(Contract):
    kind: str | None = Field(None, description=(
        "[stable] The short dataset kind, as in [datasets].kinds (optional with `ids`): the module's own datasets of that "
        "kind and the operator's unowned ones."))
    ids: list[str] = Field(default_factory=list, description="[beta] Exact dataset ids (at most 5000); `kind` and `limit` then do not apply.")
    attrs: dict[str, Any] = Field(default_factory=dict)
    limit: int = Field(100, ge=1, le=5000)
    with_files: bool = Field(False, description="[beta] Include each dataset's file list.")


class DatasetsQueryResult(Contract):
    datasets: list[DatasetRef]


class BlobStatParams(Contract):
    digest: str


class BlobStatResult(Contract):
    exists: bool
    size: int | None = None


class SettingsGetParams(Contract):
    key: str


class SettingsGetResult(Contract):
    value: Any | None = None


class SecretsGetParams(Contract):
    name: str = Field(description="[beta] A secret the manifest declares in [[secrets]].")


class SecretsGetResult(Contract):
    set: bool = Field(description="[beta] Whether the owner set a value for the module (node values never reach the coordinator side).")
    value: str | None = Field(None, description="[beta] The value; never log it, return it or put it in a spec.")


class StoreGetParams(Contract):
    collection: str
    key: str


class StoreGetResult(Contract):
    doc: dict[str, Any] | None = None


class StoreQueryParams(Contract):
    collection: str
    where: dict[str, Any] = Field(default_factory=dict, description="[beta] Equality on top-level document fields.")
    limit: int = Field(500, ge=1, le=5000)


class StoreQueryResult(Contract):
    docs: list[dict[str, Any]] = Field(default_factory=list, description="[beta] Each with its `_key`.")


class NodesQueryParams(Contract):
    certified_for_self: bool = True
    platforms: list[PlatformKey] = Field(default_factory=list, description="[beta] Only nodes of these platforms (tokens or OSes; empty: all).")


class NodesQueryResult(Contract):
    nodes: list[dict[str, Any]] = Field(default_factory=list, description=(
        "[beta] {node_id, hostname, online, module_state, platform, os, arch, os_version}; the platform fields need the "
        "nodes.platform host capability."))


class JobsQueryParams(Contract):
    campaign_id: str
    states: list[str] = Field(default_factory=list, description="[beta] Job states to include (empty: all).")
    limit: int = Field(5000, ge=1, le=50000)


class JobsQueryResult(Contract):
    jobs: list[dict[str, Any]] = Field(default_factory=list, description="[beta] {job_id, job_key, state, kind, stage, dataset_id, "
                                       "labels, group, target_node, value, digest, fields, node_id, platform}; result fields "
                                       "(platform: the canonical result's node platform) only for done jobs.")


FILE_PATH = r"^[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+)*$"   # relative; `.` and `..` segments are refused by check_file_path


def check_file_path(v: str) -> str:
    if any(seg in (".", "..") for seg in v.split("/")):
        raise ValueError(f"{v!r}: `.` and `..` segments are not allowed")
    return v
FILE_READ_MAX = 1 << 20


class FileEntry(Contract):
    path: str
    digest: str
    size: int
    updated_at: float | None = None


class FilesListParams(Contract):
    prefix: str = Field("", max_length=512, description="[beta] Path prefix inside the module's files ('' = all).")
    limit: int = Field(1000, ge=1, le=10000)


class FilesListResult(Contract):
    files: list[FileEntry] = Field(default_factory=list, description="[beta] Sorted by path.")


class FilesStatParams(Contract):
    path: str = Field(pattern=FILE_PATH, max_length=512)
    _path = field_validator("path")(check_file_path)


class FilesStatResult(Contract):
    exists: bool
    file: FileEntry | None = None


class FilesReadParams(Contract):
    path: str = Field(pattern=FILE_PATH, max_length=512)
    _path = field_validator("path")(check_file_path)
    offset: int = Field(0, ge=0)
    length: int = Field(FILE_READ_MAX, ge=1, le=FILE_READ_MAX)


class FilesReadResult(Contract):
    content_b64: str
    size: int = Field(description="[beta] The whole file's size.")
    digest: str
    eof: bool


HOST_CALLBACKS: dict[str, tuple[type, type, str]] = {
    "host.datasets.query": (DatasetsQueryParams, DatasetsQueryResult, "datasets:read"),
    "host.blobs.stat": (BlobStatParams, BlobStatResult, "blobs:stat"),
    "host.settings.get": (SettingsGetParams, SettingsGetResult, "settings:read:self"),
    "host.secrets.get": (SecretsGetParams, SecretsGetResult, "secrets:read:self"),
    "host.store.get": (StoreGetParams, StoreGetResult, "store:read:self"),
    "host.store.query": (StoreQueryParams, StoreQueryResult, "store:read:self"),
    "host.nodes.query": (NodesQueryParams, NodesQueryResult, "nodes:read"),
    "host.jobs.query": (JobsQueryParams, JobsQueryResult, "jobs:read:self"),
    "host.files.list": (FilesListParams, FilesListResult, "files:read:self"),
    "host.files.stat": (FilesStatParams, FilesStatResult, "files:read:self"),
    "host.files.read": (FilesReadParams, FilesReadResult, "files:read:self"),
}


# ---------------------------------------------------------------------------- notifications

class LogParams(Contract):
    level: Literal["debug", "info", "warn", "error"] = "info"
    msg: str
    data: dict[str, Any] = Field(default_factory=dict)


class CancelParams(Contract):
    id: int | str


NOTIFICATIONS = {"initialized": None, "log": LogParams, "$/cancel": CancelParams, "shutdown": None}
