"""UI contract 1: how a module defines its GUI (spec/ui-contract.md).

A module describes its pages and panels as *data*: static JSON layouts shipped in the bundle
(`ui/pages/*.json`, `ui/panels/*.json`), made of about 16 host components, with dynamic rows bound to
host-evaluated queries over the module's own data or to module-computed views that the host caches per
data version. The console renders everything with its own templates; no module HTML, CSS or JavaScript
ever runs in the console origin. Buttons and forms name registry operations (`mod.<module>.<verb>`,
declared in the manifest); the host draws the label, tier, preview and confirmation.

Rich UIs that cannot be expressed this way use an `iframe` component: a bundle-shipped HTML entry served
from a separate origin inside `sandbox="allow-scripts allow-forms"`, talking to the host only through a
MessageChannel bridge that can read the module's own data and *request* operations (the host confirms).

Rules that make this safe, enforced by `oarbank-sdk check`, the installer and the renderer:
- strings are plain text (<= 3000 chars) or a restricted markdown subset; never HTML;
- values are raw and typed; the host formats them from a whitelist (CELL_TYPES, FORMAT_RE);
- colours/styles are semantic `tone`s from a fixed list; links are typed internal references, or https
  URLs from the manifest's allowlist;
- sources are a closed catalogue, always filtered to the module's own rows; no query language;
- unknown components or props degrade to a placeholder (per-component `fallback`), never a page error.
"""
import re
from typing import Annotated, Any, Literal, Union

from pydantic import Field, field_validator, model_validator

from ._base import Contract, Name, StrictContract

UI_CONTRACT = "1.2"
UI_CONTRACT_MAJOR = 1

Text = Annotated[str, Field(max_length=3000)]
Label = Annotated[str, Field(min_length=1, max_length=80)]
Ident = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_.-]{0,63}$")]
Tone = Literal["ok", "warn", "error", "info", "neutral", "running"]

CELL_TYPES = ("text", "number", "integer", "percent", "bytes", "duration", "relative_time", "timestamp", "bool",
              "digest", "code", "status", "job_ref", "node_ref", "dataset_ref", "campaign_ref", "artifact_ref", "link")
CellType = Literal["text", "number", "integer", "percent", "bytes", "duration", "relative_time", "timestamp", "bool",
                   "digest", "code", "status", "job_ref", "node_ref", "dataset_ref", "campaign_ref", "artifact_ref", "link"]
FORMAT_RE = re.compile(r"^([+]?\.\d{1,2}[fe%]|d|,d|s|\.\d{1,2}s|d/d)?$")

# Closed, host-published catalogue of core queries (always filtered to the module's own rows).
SOURCES = ("results", "jobs", "attempts", "campaigns", "datasets", "module_settings", "module_events", "nodes",
           "node_metrics", "store", "secrets", "checkpoints", "services", "pins", "images", "platforms")
QueryName = Literal["results", "jobs", "attempts", "campaigns", "datasets", "module_settings", "module_events", "nodes",
                    "node_metrics", "store", "secrets", "checkpoints", "services", "pins", "images", "platforms"]
# The params each host query filters on (anything else is ignored); `store` needs `collection`, `node_metrics` `node_id`.
QUERY_PARAMS = {"results": ("job_id", "node_id"), "jobs": ("job_id", "state", "kind", "dataset_id", "campaign"),
                "attempts": ("attempt_id", "job_id", "node_id", "state"), "campaigns": ("campaign", "state"),
                "datasets": ("dataset_id", "kind"), "module_settings": (), "module_events": ("kind", "job_id", "campaign"),
                "nodes": ("node_id",), "node_metrics": ("node_id",), "store": ("collection", "campaign"),
                "secrets": ("name",), "checkpoints": ("job_id", "node_id"), "services": ("node_id", "service"),
                "pins": ("dataset_id",), "images": ("set_name",), "platforms": ("platform",)}
CONTEXT_MINOR = "1.2"              # owner-scoped sources and fields for what cores 2.2 to 2.5 added, frame context, links
# Host queries new in UI contract 1.2, and fields 1.2 added to older ones: a component that reads them sets
# requires = "1.2" (check_page), so a 1.1 host draws its fallback.
SOURCE_MINOR = {q: CONTEXT_MINOR for q in ("secrets", "checkpoints", "services", "pins", "images", "platforms")}
SOURCE_FIELDS_1_2 = {
    "attempts": {"resumed_from_attempt", "resumed_from_node", "resume_digest", "module_version", "rss_gb"},
    "nodes": {"platform", "os", "arch", "os_version", "gpu_apis_host", "gpu_apis_containers", "container_gpu",
              "container_runtime", "container_state", "container_platforms", "container_detail", "container_missing",
              "container_fixes", "services", "service_health",
              "folders", "folders_ok", "enforcement", "sandbox_gaps"},
    "campaigns": {"placement_mix", "placement_unit", "placement_pin", "bound_class", "binding_state", "binding_source",
                  "stranded_since"},
    "datasets": {"owner", "module", "platform", "files", "size", "origins", "pinned"},
}
PLACEMENT_SLOTS = ("module.overview", "module.page", "job.detail.panel", "node.detail.panel", "campaign.panel")
SLOT_LIMITS = {"module.overview": 1, "module.page": 6, "job.detail.panel": 2, "node.detail.panel": 1, "campaign.panel": 1}
EFFECTS = ("jobs.enqueue", "jobs.cancel", "campaigns.create", "campaigns.update", "campaigns.cancel",
           "datasets.create", "datasets.update", "datasets.delete", "module_settings.update", "store.write",
           "store.delete", "files.write", "files.put", "files.delete", "external")
# Effects that force a minimum tier regardless of what the module declares.
EFFECT_TIER_FLOOR = {"jobs.cancel": "T1", "campaigns.cancel": "T2", "datasets.delete": "T2", "store.delete": "T1", "files.delete": "T1",
                     "external": "T1"}


# ------------------------------------------------------------------------------------------ bindings

class Source(StrictContract):
    """Where a component's rows come from. Exactly one of `query` or `view`."""
    query: QueryName | None = None
    view: Ident | None = Field(None, description="A module view declared in the manifest ([ui.views.<id>]).")
    params: dict[str, Any] = Field(default_factory=dict,
                                   description="Literals, or $route / $var interpolations only (e.g. '$node', '$var.region').")
    fields: list[Name] = Field(default_factory=list)
    group_by: Name | None = None
    agg: dict[Name, Literal["count", "mean", "median", "min", "max", "p95", "sum"]] = Field(default_factory=dict)
    order_by: Name | None = None
    descending: bool = True
    limit: Annotated[int, Field(ge=1, le=200)] = 50

    @model_validator(mode="after")
    def _one(self):
        if (self.query is None) == (self.view is None):
            raise ValueError("a source names exactly one of `query` or `view`")
        for v in self.params.values():
            if isinstance(v, str) and v.startswith("$") and not re.match(r"^\$(route\.[a-z_]+|var\.[a-z_]+|node|job|campaign|self)$", v):
                raise ValueError(f"interpolation {v!r} is not allowed (only $route.*, $var.*, $node, $job, $campaign, $self)")
        return self


class Column(StrictContract):
    key: Name
    label: Label | None = None
    type: CellType = "text"
    format: str | None = None
    unit: Annotated[str, Field(max_length=16)] | None = None
    direction: Literal["min", "max"] | None = Field(None, description="Which way is better; enables best/colouring generically.")
    tone_by_sign: bool = False
    sortable: bool = True
    download: bool = Field(False, description="A dataset_ref or campaign_ref cell links to its download. [UI contract 1.2]")

    @field_validator("format")
    @classmethod
    def _fmt(cls, v):
        if v is not None and not FORMAT_RE.match(v):
            raise ValueError(f"format {v!r} not in the whitelist")
        return v

    @model_validator(mode="after")
    def _download(self):
        if self.download and self.type not in ("dataset_ref", "campaign_ref"):
            raise ValueError("download goes with a dataset_ref or campaign_ref column")
        return self


class Upload(StrictContract):
    """The console's folder upload with this module and `kind` filled in; with `then`, the module's importer operation
    (target "dataset") is offered on the new dataset once it is registered. [beta, UI contract 1.2]"""
    kind: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]{0,40}$")] = Field(description="One of the module's [datasets].kinds.")
    then: Annotated[str, Field(pattern=r"^self\.[a-z][a-z0-9_]{0,40}$")] | None = Field(
        None, description="self.<verb>: an operation of this module with target = \"dataset\".")


class Link(StrictContract):
    """A typed reference the host turns into a URL; never a raw href (except allowlisted https)."""
    job: int | None = None
    node: str | None = None
    dataset: str | None = None
    campaign: str | None = None
    page: Ident | None = None
    url: Annotated[str, Field(pattern=r"^https://[^\s\"'<>]+$")] | None = Field(None, description="Must match manifest ui.external_urls.")
    tab: Literal["secrets", "health"] | None = Field(None, description="The module's own core tab. [UI contract 1.2]")
    upload: Upload | None = Field(None, description="Upload a folder as this module's dataset. [UI contract 1.2]")
    download: bool = Field(False, description="With `dataset` or `campaign`: its download instead of its page. [UI contract 1.2]")

    @model_validator(mode="after")
    def _one(self):
        if sum(v is not None for v in (self.job, self.node, self.dataset, self.campaign, self.page, self.url, self.tab,
                                       self.upload)) != 1:
            raise ValueError("a link names exactly one target")
        if self.download and self.dataset is None and self.campaign is None:
            raise ValueError("download goes with a dataset or a campaign")
        return self

    def minor(self) -> str | None:
        """The UI contract minor this link needs beyond 1.0 (None: any)."""
        return CONTEXT_MINOR if (self.tab or self.upload or self.download) else None


class ActionRef(StrictContract):
    """A button bound to a registry operation. The host draws the label (registry title) and friction."""
    op: Annotated[str, Field(pattern=r"^([a-z]+(\.[a-z_]+)+|self\.[a-z_]+)$")] = Field(
        description="A core operation id, or self.<verb> for this module's own operation (mod.<module>.<verb>).")
    target: str | None = Field(None, description="Literal or interpolation ($row.<field>, $route.*, $node, ...).")
    params: dict[str, Any] = Field(default_factory=dict)
    hint: Annotated[str, Field(max_length=120)] | None = Field(None, description="Secondary line under the host label.")
    when: Annotated[str, Field(max_length=200)] | None = None


# ------------------------------------------------------------------------------------------ components

class _C(StrictContract):
    id: Ident | None = None
    when: Annotated[str, Field(max_length=200)] | None = Field(None, description="Visibility, e.g. \"node.online == true\".")
    requires: str | None = Field(None, description="Minimum ui_contract minor for this component, e.g. '1.1'.")
    fallback: Literal["drop", "placeholder"] = "placeholder"


class Section(_C):
    type: Literal["section"]
    title: Label | None = None
    span: Literal[1, 2, "full"] = "full"
    children: list["Component"] = Field(default_factory=list, max_length=40)


class Tab(StrictContract):
    title: Label
    children: list["Component"] = Field(default_factory=list, max_length=40)


class Tabs(_C):
    type: Literal["tabs"]
    tabs: list[Tab] = Field(min_length=1, max_length=8)


class Columns(_C):
    type: Literal["columns"]
    children: list["Component"] = Field(min_length=2, max_length=3)


class TextC(_C):
    type: Literal["text"]
    text: Text
    tone: Tone | None = None


class Markdown(_C):
    type: Literal["markdown"]
    text: Text = Field(description="Paragraphs, emphasis, code, lists and typed internal links only.")


class KVItem(StrictContract):
    label: Label
    field: Name | None = None
    value: str | int | float | bool | None = None
    type: CellType = "text"
    format: str | None = None


class KV(_C):
    type: Literal["kv"]
    source: Source | None = None
    items: list[KVItem] = Field(min_length=1, max_length=30)


class Stat(_C):
    type: Literal["stat"]
    label: Label
    source: Source
    field: Name
    format: str | None = None
    unit: Annotated[str, Field(max_length=16)] | None = None
    direction: Literal["min", "max"] | None = None
    sparkline: Name | None = None


class Status(_C):
    type: Literal["status"]
    label: Label
    source: Source | None = None
    field: Name | None = None
    tones: dict[str, Tone] = Field(default_factory=dict, description="value -> tone")


class Progress(_C):
    type: Literal["progress"]
    label: Label
    source: Source
    value: Name
    total: Name


class Callout(_C):
    type: Literal["callout"]
    tone: Tone = "info"
    text: Text


class Empty(_C):
    type: Literal["empty"]
    text: Text
    action: ActionRef | None = None


class Table(_C):
    type: Literal["table"]
    source: Source
    columns: list[Column] = Field(min_length=1, max_length=20)
    row_link: Link | None = None
    row_actions: list[ActionRef] = Field(default_factory=list, max_length=4)
    page_size: Annotated[int, Field(ge=5, le=200)] = 25


class Chart(_C):
    type: Literal["chart"]
    kind: Literal["line", "bar", "scatter", "histogram", "parallel_coords"]
    source: Source
    x: Name
    y: list[Name] = Field(min_length=1, max_length=6)
    summary: Text = Field(description="Required text alternative (accessibility); also shown as a caption.")


class Logs(_C):
    type: Literal["logs"]
    stream: Literal["module", "job", "attempt"] = "module"


class JSONView(_C):
    type: Literal["json"]
    source: Source


class Form(_C):
    type: Literal["form"]
    schema_: str = Field(alias="schema", description="Bundle path of a restricted JSON Schema (spec/ui-contract.md).")
    hints: dict[str, Any] = Field(default_factory=dict, description="Order, grouping, help, widget from the host registry.")
    submit: ActionRef


class FilterVar(StrictContract):
    name: Name
    label: Label | None = None
    type: Literal["string", "number", "integer", "boolean", "date"] = "string"
    enum: list[str | int | float] | None = None
    default: str | int | float | bool | None = None


class FilterBar(_C):
    type: Literal["filter_bar"]
    vars: list[FilterVar] = Field(min_length=1, max_length=8)


class Action(_C):
    type: Literal["action"]
    action: ActionRef


class LinkC(_C):
    type: Literal["link"]
    text: Label
    to: Link


MEDIA_MINOR = "1.1"                # the media components are new in UI contract 1.1


class ArtifactRef(StrictContract):
    """What a media component's field holds (a row value from a host query or a module view): a file of one of a job's
    canonical result's artifacts, or a blob the module can see by digest. The host checks it belongs to the module before
    it serves a byte. [beta]"""
    job: int | None = Field(None, description="A job of this module.")
    artifact: Name | None = Field(None, description="The artifact's name in the job's canonical result.")
    path: str | None = Field(None, description="The file's path in the artifact.")
    digest: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")] | None = Field(None, description="A blob the module can see.")
    thumbnail: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")] | None = Field(None, description="With `digest`: its preview image.")

    @model_validator(mode="after")
    def _form(self):
        job_fields = [self.job, self.artifact, self.path]
        by_job = all(v is not None for v in job_fields) and self.digest is None and self.thumbnail is None
        by_digest = self.digest is not None and all(v is None for v in job_fields)
        if by_job == by_digest:
            raise ValueError("an artifact reference is {job, artifact, path} or {digest, thumbnail?}")
        return self


class Media(_C):
    """One artifact: an image, a video or audio player, or a text file shown as plain text. [beta, UI contract 1.1]"""
    type: Literal["media"]
    kind: Literal["image", "video", "audio", "text"]
    source: Source
    field: Name = Field(description="The field of the source's first row that holds the artifact reference.")
    caption: Label | None = None
    caption_field: Name | None = None
    height: Annotated[int, Field(ge=60, le=2000)] = 360


class Gallery(_C):
    """A grid of image or video artifacts, one per source row, each shown by its thumbnail when it has one and linked to
    its job. [beta, UI contract 1.1]"""
    type: Literal["gallery"]
    kind: Literal["image", "video"] = "image"
    source: Source
    field: Name
    caption_field: Name | None = None
    columns: Annotated[int, Field(ge=2, le=8)] = 4


class Compare(_C):
    """Two images from the source's first row, side by side or overlaid with a slider. [beta, UI contract 1.1]"""
    type: Literal["compare"]
    source: Source
    left: Name
    right: Name
    mode: Literal["side_by_side", "slider"] = "side_by_side"
    labels: Annotated[list[Label], Field(min_length=2, max_length=2)] | None = None


class Frame(_C):
    type: Literal["iframe"]
    view: Ident = Field(description="An iframe view declared in the manifest ([[ui.iframes]]).")
    height: Annotated[int, Field(ge=120, le=2000)] = 480
    title: Label = Field(description="Accessible name of the frame.")


Component = Annotated[Union[Section, Tabs, Columns, TextC, Markdown, KV, Stat, Status, Progress, Callout, Empty, Table,
                            Chart, Logs, JSONView, Form, FilterBar, Action, LinkC, Frame, Media, Gallery, Compare],
                      Field(discriminator="type")]
for _m in (Section, Tab, Tabs, Columns):
    _m.model_rebuild()

COMPONENT_TYPES = ("section", "tabs", "columns", "text", "markdown", "kv", "stat", "status", "progress", "callout",
                   "empty", "table", "chart", "logs", "json", "form", "filter_bar", "action", "link", "iframe", "media",
                   "gallery", "compare")
# components newer than UI contract 1.0, with the minor that introduced them: a page names it in `requires`
COMPONENT_MINOR = {"media": MEDIA_MINOR, "gallery": MEDIA_MINOR, "compare": MEDIA_MINOR}


def minor_of(version: str | None) -> int:
    """The minor of a UI contract version `1.<minor>` (0 when absent or malformed)."""
    try:
        return int(str(version).split(".")[1])
    except (IndexError, ValueError):
        return 0


class Page(StrictContract):
    """ui/pages/<id>.json and ui/panels/<id>.json (a panel is a page without tabs of its own)."""
    ui_contract: Annotated[str, Field(pattern=r"^1\.\d+$")] = UI_CONTRACT
    title: Label | None = None
    vars: list[FilterVar] = Field(default_factory=list, max_length=8)
    body: list[Component] = Field(min_length=1, max_length=40)


# ------------------------------------------------------------------------------------------ manifest parts

class PageDecl(Contract):
    id: Ident
    title: Label
    slot: Literal["module.overview", "module.page", "job.detail.panel", "node.detail.panel", "campaign.panel"]
    file: str
    when: Annotated[str, Field(max_length=200)] | None = None


class ViewDecl(Contract):
    """A module-computed view (verb ui.view.compute), materialized by oarbankd per data version."""
    shape: Literal["rows", "kv", "series", "stat"]
    inputs: list[str] = Field(default_factory=list, description="What invalidates it: results, jobs, datasets:<kind>, store:<collection>.")
    params: dict[str, Any] = Field(default_factory=dict, description="Restricted JSON Schema properties for view params.")
    columns: list[Column] = Field(default_factory=list, max_length=30)
    refresh_s: Annotated[int, Field(ge=300)] | None = Field(None, description="Clock-driven refresh; floor 5 minutes.")
    max_rows: Annotated[int, Field(ge=1, le=5000)] = 1000


class IframeDecl(Contract):
    id: Ident
    entry: str = Field(description="Bundle path of the HTML entry (served from the module origin with its own CSP).")
    title: Label
    bridge: list[Literal["read.query", "read.view", "read.media", "request.operation", "resize", "navigate"]] = Field(
        default_factory=lambda: ["read.view", "resize"], description="Bridge capabilities the frame may use.")


class OperationDecl(Contract):
    """Registered at install as mod.<module>.<verb> in the operation registry."""
    verb: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,40}$")]
    title: Label
    tier: Literal["T0", "T1", "T2", "T3"] = "T1"
    min_role: Literal["viewer", "operator", "admin"] = "operator"
    target: Literal["none", "dataset", "job", "node", "campaign", "store", "module"] = "none"
    params_schema: str | None = Field(None, description="Bundle path of the parameter schema.")
    effects: list[Literal["jobs.enqueue", "jobs.cancel", "campaigns.create", "campaigns.update", "campaigns.cancel",
                          "datasets.create", "datasets.update", "datasets.delete", "module_settings.update",
                          "store.write", "store.delete", "files.write", "files.put", "files.delete", "external"]] = Field(default_factory=list)
    preview: bool = Field(False, description="Implements op.plan (required when the effective tier is T2/T3).")

    def effective_tier(self) -> str:
        order = ("T0", "T1", "T2", "T3")
        t = max([order.index(self.tier)] + [order.index(EFFECT_TIER_FLOOR[e]) for e in self.effects if e in EFFECT_TIER_FLOOR])
        return order[t]


DIGEST_LINE_RE = re.compile(r"^(?:[^{}]|\{\{|\}\}|\{[a-z][a-z0-9_]*(?::(?:\.\d{1,2}[fe%]|d|,d|s))?\})*$")


class UISection(Contract):
    icon: Literal["cpu", "gpu", "dna", "chart", "flask", "cube", "bolt"] | None = None
    digest_line: str | None = Field(None, max_length=120, description="Restricted template: {field} or {field:FMT} only.")
    pages: list[PageDecl] = Field(default_factory=list, max_length=12)
    panels: list[PageDecl] = Field(default_factory=list, max_length=6)
    views: dict[Ident, ViewDecl] = Field(default_factory=dict)
    iframes: list[IframeDecl] = Field(default_factory=list, max_length=4)
    external_urls: list[Annotated[str, Field(pattern=r"^https://")]] = Field(default_factory=list, max_length=10)

    @field_validator("digest_line")
    @classmethod
    def _line(cls, v):
        if v is not None and not DIGEST_LINE_RE.match(v):
            raise ValueError("digest_line may contain text and {field} / {field:FMT} placeholders only")
        return v

    @model_validator(mode="after")
    def _slots(self):
        counts = {}
        for d in [*self.pages, *self.panels]:
            counts[d.slot] = counts.get(d.slot, 0) + 1
        for slot, n in counts.items():
            if n > SLOT_LIMITS[slot]:
                raise ValueError(f"slot {slot} allows at most {SLOT_LIMITS[slot]} contributions, got {n}")
        for d in self.pages:
            if d.slot not in ("module.overview", "module.page"):
                raise ValueError(f"page {d.id}: pages go in module.overview or module.page (panels go in [[ui.panels]])")
        for d in self.panels:
            if d.slot in ("module.overview", "module.page"):
                raise ValueError(f"panel {d.id}: panels go in a detail slot")
        ids = [d.id for d in [*self.pages, *self.panels]] + [f.id for f in self.iframes]
        if len(ids) != len(set(ids)):
            raise ValueError("page, panel and iframe ids must be unique")
        return self


def walk(components):
    """Every component in a tree (depth first)."""
    for c in components:
        yield c
        for child in getattr(c, "children", []) or []:
            yield from walk([child])
        for tab in getattr(c, "tabs", []) or []:
            yield from walk(tab.children)


def _names_read(c) -> set[str]:
    """The row fields a component reads from its source (what decides whether it reads a 1.2 field)."""
    out = set(getattr(getattr(c, "source", None), "fields", None) or [])
    for col in getattr(c, "columns", None) or []:
        if isinstance(col, Column):
            out.add(col.key)
    for it in getattr(c, "items", None) or []:
        if isinstance(it, KVItem) and it.field:
            out.add(it.field)
    for attr in ("field", "value", "total", "x", "caption_field", "left", "right"):
        v = getattr(c, attr, None)
        if isinstance(v, str):
            out.add(v)
    out.update(getattr(c, "y", None) or [])
    return out


def needed_minor(c) -> str | None:
    """The UI contract minor a component needs beyond 1.0, from its type, cell types, source, fields and links."""
    need = [COMPONENT_MINOR.get(c.type)]
    cols = getattr(c, "columns", None)
    if isinstance(cols, list) and any(col.type == "artifact_ref" for col in cols if isinstance(col, Column)):
        need.append(MEDIA_MINOR)                             # a cell type new in 1.1
    if isinstance(cols, list) and any(col.download for col in cols if isinstance(col, Column)):
        need.append(CONTEXT_MINOR)
    src = getattr(c, "source", None)
    if src is not None and src.query:
        need.append(SOURCE_MINOR.get(src.query))
        if _names_read(c) & SOURCE_FIELDS_1_2.get(src.query, set()):
            need.append(CONTEXT_MINOR)
    for link in (getattr(c, "to", None), getattr(c, "row_link", None)):
        if isinstance(link, Link):
            need.append(link.minor())
    need = [n for n in need if n]
    return max(need, key=minor_of) if need else None


def check_page(page: Page, man, bundle_files: set[str] | None = None) -> list[str]:
    """Cross-references a page of manifest `man` must satisfy (the installer runs this; so does oarbank-sdk check)."""
    ui, operations, kinds = man.ui, man.operations, set(man.datasets.kinds)
    errs = []
    ops = {o.verb: o for o in operations}
    frames = {f.id for f in ui.iframes}
    for c in walk(page.body):
        src = getattr(c, "source", None)
        if src is not None and src.view and src.view not in ui.views:
            errs.append(f"{c.type}: view {src.view!r} is not declared in [ui.views]")
        refs = [getattr(c, "action", None), getattr(c, "submit", None)] + list(getattr(c, "row_actions", []) or [])
        if isinstance(c, Empty) and c.action:
            refs.append(c.action)
        for a in [r for r in refs if r is not None]:
            if a.op.startswith("self.") and a.op[5:] not in ops:
                errs.append(f"{c.type}: operation {a.op!r} is not declared in [[operations]]")
        if isinstance(c, Frame) and c.view not in frames:
            errs.append(f"iframe: view {c.view!r} is not declared in [[ui.iframes]]")
        for link in (getattr(c, "to", None), getattr(c, "row_link", None)):
            if not isinstance(link, Link):
                continue
            if link.url and not any(link.url.startswith(u) for u in ui.external_urls):
                errs.append(f"{c.type}: {link.url!r} is not in ui.external_urls")
            if link.upload and link.upload.kind not in kinds:
                errs.append(f"{c.type}: upload kind {link.upload.kind!r} is not one of [datasets].kinds {sorted(kinds)}")
            if link.upload and link.upload.then:
                o = ops.get(link.upload.then[5:])
                if o is None or o.target != "dataset":
                    errs.append(f"{c.type}: upload then {link.upload.then!r} must be an operation with target = \"dataset\"")
        if isinstance(c, Form) and bundle_files is not None and c.schema_ not in bundle_files:
            errs.append(f"form: schema file {c.schema_!r} is not in the bundle")
        need = needed_minor(c)
        if need and minor_of(c.requires) < minor_of(need):
            errs.append(f"{c.type}: needs UI contract {need}, so it sets requires = \"{need}\" and a fallback for older hosts")
    return errs


def validate_view(decl: ViewDecl, doc: dict) -> dict:
    """A module's `ui.view.compute` answer checked against its declaration, reduced to what the console may read (the
    shape's value; for rows only the declared columns). Raises ValueError. oarbankd stores what this returns; the
    conformance kit runs the same check."""
    shape = decl.shape
    val = doc.get(shape)
    if val is None:
        raise ValueError(f"view returned no {shape!r}")
    if shape == "rows":
        if not isinstance(val, list) or not all(isinstance(r, dict) for r in val):
            raise ValueError("rows must be a list of objects")
        if len(val) > decl.max_rows:
            raise ValueError(f"{len(val)} rows > max_rows {decl.max_rows}")
        keys = {c.key for c in decl.columns}
        if keys:
            val = [{k: r.get(k) for k in keys} for r in val]      # only declared columns reach the console
    elif shape in ("kv", "stat") and not isinstance(val, dict):
        raise ValueError(f"{shape} must be an object")
    elif shape == "series" and not isinstance(val, dict):
        raise ValueError("series must be an object of lists")
    return {shape: val}


def view_ref_problems(decl: ViewDecl, doc: dict) -> list[str]:
    """`artifact_ref` cells of a validated rows view that are not artifact references (the console shows a placeholder
    for each; the conformance kit fails them)."""
    cols = [c.key for c in decl.columns if c.type == "artifact_ref"]
    out = []
    for i, row in enumerate(doc.get("rows") or [] if decl.shape == "rows" else []):
        for k in cols:
            if row.get(k) is None:
                continue
            try:
                ArtifactRef.model_validate(row[k])
            except ValueError as e:
                out.append(f"row {i} {k}: {str(e).splitlines()[-1]}")
    return out
