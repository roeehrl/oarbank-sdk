# Manifest field reference (generated)

Generated from `oarbank_sdk.manifest` by `oarbank-sdk export-schemas`. Do not edit by hand.
Cross-field rules are in [manifest.md](manifest.md).

| Key | Type | Default | Description |
|---|---|---|---|
| `manifest` | `1` | `1` | [stable] Manifest schema major. |
| `module` | ModuleInfo | required |  |
| `module.id` | str | required | [stable] Namespaced, globally unique module id (reverse DNS). Removed ids are tombstoned, never reused. |
| `module.version` | str | required | [stable] SemVer of the module package. |
| `module.compat` | str | required | [stable] Result-semantics tag. Part of every job_key: bump it only when results for the same inputs change meaning. Patch releases keep it, so caches survive. |
| `module.publisher` | str | required | [stable] Publisher identity (today a free-form owner name). |
| `module.license` | str | required | [stable] SPDX license expression of the module. |
| `module.codeowners` | list of str | `[]` | [stable] Maintainers; modules without codeowners cannot be verified. |
| `module.stability` | `"experimental"` \| `"beta"` \| `"stable"` | `"beta"` | [stable] Maturity of the module itself. |
| `module.description` | str | `""` | [stable] One-line description for the console and docs. |
| `requires` | Requires | required |  |
| `requires.core` | str | required | [stable] Supported oarbank core versions, e.g. '>=2.0,<3'. |
| `requires.agent` | str (optional) |  | [stable] Supported agent versions. |
| `requires.os` | OSRequirements (optional) |  | [stable] Supported OS versions per OS (only for the declared platforms). |
| `requires.os.darwin` | str (optional) |  |  |
| `requires.os.linux` | LinuxRequirements (optional) |  |  |
| `requires.os.linux.kernel` | str (optional) |  | [stable] Kernel versions, e.g. '>=6.1'. |
| `requires.os.linux.glibc` | str (optional) |  | [stable] glibc versions, e.g. '>=2.35'. |
| `requires.os.windows` | str (optional) |  | [stable] Windows versions by build, e.g. '>=10.0.19045'. |
| `requires.platforms` | list of str | required | [stable] Node platforms the module runs on, `<os>-<arch>` (spec/platforms.md). Required; an unknown token means no node of that platform, never an error. |
| `requires.module_protocol` | list of int | required | [stable] Module protocol majors the coordinator side speaks. |
| `requires.runner_protocol` | list of int | `[1]` | [stable] Runner protocol majors the runner speaks. |
| `requires.service_protocol` | list of int | `[]` | [stable] Service protocol majors (only when services/probes are declared). |
| `requires.experimental` | list of str | `[]` | [stable] Opt-ins to experimental contract items; any opt-in blocks the verified badge. |
| `requires.ui_contract` | str (optional) |  | [beta] UI contract versions the module's pages use, e.g. '>=1.0,<2' (spec/ui-contract.md). |
| `requires.coordinator_platforms` | list of str (optional) |  | [beta] Platforms the coordinator side runs on (the coordinator host's platform); `platforms` lists node platforms only. Absent: any platform. A core refuses to install or enable the module on a coordinator host not listed, and blocks a coordinator move to one. Needs requires.core >= 2.2. |
| `requires.unsupported` | Unsupported | `"runner={} coordinator={}"` | [beta] Why the module does not run somewhere, shown by explain and the console. Needs requires.core >= 2.2. |
| `requires.unsupported.runner` | table str → str | `{}` | [beta] Node platforms, beside requires.platforms. |
| `requires.unsupported.coordinator` | table str → str | `{}` | [beta] Coordinator platforms, beside requires.coordinator_platforms (which must then be set: absent means any). |
| `requires.features` | list of str | `[]` | [beta] Must-understand list: a reader that does not know a listed feature refuses the manifest. Known: `placement`. Needs requires.core >= 2.2. |
| `coordinator` | Coordinator | required |  |
| `coordinator.exec` | list of str | required | [stable] argv (spec/manifest.md, "Exec"); cwd is the bundle root; the process speaks module protocol JSON-RPC on stdin/stdout. |
| `coordinator.runtime` | Runtime | required |  |
| `coordinator.runtime.kind` | str | required | [stable] Open set. `python`: the host's managed CPython with the SDK (plus the bundle's requirements, installed from wheels); `uv`: a locked uv project (wheels only, resolvable for every declared platform) built offline into a per-module environment; `native`: argv[0] is a native executable in the bundle (Mach-O, ELF or PE, or a POSIX shebang script in darwin/linux variants). An unknown kind means the entry point cannot run on this node. |
| `coordinator.runtime.lock` | str (optional) |  | [stable] Path of uv.lock inside the bundle (kind=uv). |
| `coordinator.runtime.python` | str (optional) |  | [stable] Exact Python version for the venv (kind=uv), e.g. 3.12.14. |
| `coordinator.capabilities` | list of str | `[]` | [stable] Optional verbs/features implemented, e.g. result.merge, golden.compare, study.metrics, params.distance, result.upgrade, and campaign.tick.results [beta] (campaign.tick sees each done job's payload and artifacts; the host validates payloads against results.schema at acceptance; needs requires.core >= 2.3). |
| `coordinator.concurrency` | int | `1` | [stable] Max in-flight requests the module accepts; 1 = serial. |
| `coordinator.timeouts_s` | table str → float | `{"default": 10.0}` | [stable] Per-verb timeouts; key `default` applies to unlisted verbs. |
| `coordinator.permissions` | list of `"datasets:read"` \| `"blobs:stat"` \| `"settings:read:self"` \| `"store:read:self"` \| `"nodes:read"` \| `"jobs:read:self"` \| `"files:read:self"` \| `"secrets:read:self"` | `[]` | [stable] Host callbacks the module may call; everything else is denied. |
| `coordinator.campaign_effects` | list of `"jobs.enqueue"` \| `"jobs.cancel"` \| `"campaigns.create"` \| `"campaigns.update"` \| `"campaigns.cancel"` \| `"datasets.create"` \| `"datasets.update"` \| `"datasets.delete"` \| `"module_settings.update"` \| `"store.write"` \| `"store.delete"` \| `"files.write"` \| `"files.put"` \| `"files.delete"` \| `"external"` | `[]` | [beta] Effects campaign.tick may request (requires the campaign.tick capability). |
| `coordinator.move` | MoveSection | `"rules=[] effects=[]"` |  |
| `coordinator.move.rules` | list of MoveRule | `[]` | [beta] The first matching rule wins; unmatched state is carried. |
| `coordinator.move.rules[].files` | str (optional) |  | [beta] A path prefix in the module's files ('' = all of them). |
| `coordinator.move.rules[].store` | str (optional) |  | [beta] A store collection. |
| `coordinator.move.rules[].class` | `"carry"` \| `"rebuild"` \| `"drop"` | required | [beta] `carry` (the default for everything): moved and verified by digest; `rebuild`: not transferred, the module recreates it in move.postflight; `drop`: scratch, deleted on the new coordinator. |
| `coordinator.move.effects` | list of `"jobs.enqueue"` \| `"jobs.cancel"` \| `"campaigns.create"` \| `"campaigns.update"` \| `"campaigns.cancel"` \| `"datasets.create"` \| `"datasets.update"` \| `"datasets.delete"` \| `"module_settings.update"` \| `"store.write"` \| `"store.delete"` \| `"files.write"` \| `"files.put"` \| `"files.delete"` \| `"external"` | `[]` | [beta] Effects move.preflight, move.postflight and move.cancelled may request. |
| `coordinator.env` | table str → str | `{}` | [beta] Extra environment for the coordinator process (names `^[A-Z][A-Z0-9_]*$`, never a reserved name). The host also sets OARBANK_PLATFORM. Needs requires.core >= 2.2. |
| `coordinator.variants` | table str → CoordinatorVariant | `{}` | [beta] Per-platform overrides for the coordinator host, keyed by platform token or OS (checked against requires.coordinator_platforms when set). Needs requires.core >= 2.2. |
| `coordinator.variants.exec` | list of str (optional) |  | [beta] Replaces coordinator.exec. |
| `coordinator.variants.runtime` | Runtime (optional) |  | [beta] Replaces coordinator.runtime. |
| `coordinator.variants.runtime.kind` | str | required | [stable] Open set. `python`: the host's managed CPython with the SDK (plus the bundle's requirements, installed from wheels); `uv`: a locked uv project (wheels only, resolvable for every declared platform) built offline into a per-module environment; `native`: argv[0] is a native executable in the bundle (Mach-O, ELF or PE, or a POSIX shebang script in darwin/linux variants). An unknown kind means the entry point cannot run on this node. |
| `coordinator.variants.runtime.lock` | str (optional) |  | [stable] Path of uv.lock inside the bundle (kind=uv). |
| `coordinator.variants.runtime.python` | str (optional) |  | [stable] Exact Python version for the venv (kind=uv), e.g. 3.12.14. |
| `coordinator.variants.timeouts_s` | table str → float (optional) |  | [beta] Merged over coordinator.timeouts_s verb by verb. |
| `coordinator.variants.concurrency` | int (optional) |  | [beta] Replaces coordinator.concurrency. |
| `coordinator.variants.env` | table str → str (optional) |  | [beta] Merged over coordinator.env name by name. |
| `runner` | Runner | required |  |
| `runner.exec` | list of str | required | [stable] argv (spec/manifest.md, "Exec"); the agent appends `run --spec S --workdir W --out R [--events E]` or `doctor --json`. |
| `runner.runtime` | Runtime | required |  |
| `runner.runtime.kind` | str | required | [stable] Open set. `python`: the host's managed CPython with the SDK (plus the bundle's requirements, installed from wheels); `uv`: a locked uv project (wheels only, resolvable for every declared platform) built offline into a per-module environment; `native`: argv[0] is a native executable in the bundle (Mach-O, ELF or PE, or a POSIX shebang script in darwin/linux variants). An unknown kind means the entry point cannot run on this node. |
| `runner.runtime.lock` | str (optional) |  | [stable] Path of uv.lock inside the bundle (kind=uv). |
| `runner.runtime.python` | str (optional) |  | [stable] Exact Python version for the venv (kind=uv), e.g. 3.12.14. |
| `runner.capabilities` | list of str | `[]` | [stable] By intent: cancellable (honours a stop request within stop_grace_s), freeze_ok (safe to freeze at any instruction), cooperative_pause (pauses on control.json), resumable (resumes from its own checkpoint after a restart), progress_events, deterministic_output, cooperative_throttle, checkpoint (writes portable checkpoints, honours a checkpoint-then-stop request and resumes from <W>/checkpoint/; needs requires.core >= 2.5). |
| `runner.stop_grace_s` | float | `20.0` | [stable] Seconds between the stop request and forced termination of the process container. |
| `runner.checkpoint_grace_s` | float | `120.0` | [beta] Seconds between a checkpoint-then-stop request and forced termination of the process container. Needs requires.core >= 2.5. |
| `runner.gpu` | GPUNeed | `"use='none' apis_any=[] min_vram_gb=None in_container=False"` |  |
| `runner.gpu.use` | `"none"` \| `"shared"` \| `"exclusive"` | `"none"` | [beta] `shared`/`exclusive` jobs are not admitted while a protected process group uses that GPU. |
| `runner.gpu.apis_any` | list of str | `[]` | [beta] Any of these GPU APIs; jobs run only on nodes providing one (open set; detected: cuda, directml, metal, opencl, rocm, vulkan). Needs `use` shared or exclusive and requires.core >= 2.5. |
| `runner.gpu.min_vram_gb` | float (optional) |  | [beta] Minimum device memory where memory is not unified. |
| `runner.gpu.in_container` | bool | `false` | [beta] The GPU is used from inside a broker-run container. |
| `runner.bandwidth_class` | `"low"` \| `"medium"` \| `"high"` (optional) |  | [experimental] Measured memory-bandwidth appetite relative to the node's memory system (measured on Apple unified memory so far). |
| `runner.env` | table str → str | `{}` | [beta] Extra environment for the runner and doctor, applied after the agent's own variables (names `^[A-Z][A-Z0-9_]*$`, never OARBANK_* or another reserved name), e.g. MKL_CBWR or OMP_NUM_THREADS for determinism. Needs requires.core >= 2.2. |
| `runner.variants` | table str → RunnerVariant | `{}` | [beta] Per-platform overrides, keyed by platform token (`linux-amd64`) or OS (`linux`). |
| `runner.variants.exec` | list of str (optional) |  |  |
| `runner.variants.runtime` | Runtime (optional) |  |  |
| `runner.variants.runtime.kind` | str | required | [stable] Open set. `python`: the host's managed CPython with the SDK (plus the bundle's requirements, installed from wheels); `uv`: a locked uv project (wheels only, resolvable for every declared platform) built offline into a per-module environment; `native`: argv[0] is a native executable in the bundle (Mach-O, ELF or PE, or a POSIX shebang script in darwin/linux variants). An unknown kind means the entry point cannot run on this node. |
| `runner.variants.runtime.lock` | str (optional) |  | [stable] Path of uv.lock inside the bundle (kind=uv). |
| `runner.variants.runtime.python` | str (optional) |  | [stable] Exact Python version for the venv (kind=uv), e.g. 3.12.14. |
| `runner.variants.capabilities` | list of str (optional) |  |  |
| `runner.variants.stop_grace_s` | float (optional) |  |  |
| `runner.variants.gpu` | GPUNeed (optional) |  |  |
| `runner.variants.gpu.use` | `"none"` \| `"shared"` \| `"exclusive"` | `"none"` | [beta] `shared`/`exclusive` jobs are not admitted while a protected process group uses that GPU. |
| `runner.variants.gpu.apis_any` | list of str | `[]` | [beta] Any of these GPU APIs; jobs run only on nodes providing one (open set; detected: cuda, directml, metal, opencl, rocm, vulkan). Needs `use` shared or exclusive and requires.core >= 2.5. |
| `runner.variants.gpu.min_vram_gb` | float (optional) |  | [beta] Minimum device memory where memory is not unified. |
| `runner.variants.gpu.in_container` | bool | `false` | [beta] The GPU is used from inside a broker-run container. |
| `runner.variants.env` | table str → str (optional) |  | [beta] Merged over [runner].env name by name. Needs requires.core >= 2.2. |
| `stages` | list of Stage | required |  |
| `stages[].name` | str | required | [stable] Stage name; unique within the manifest. |
| `stages[].after` | str (optional) |  | [stable] Stage whose output this stage consumes (its artifacts become inputs). |
| `stages[].determinism` | `"exact"` \| `"within_tolerance"` \| `"none"` (optional) |  | [beta] This stage's determinism (absent: results.determinism). `none`: its results depend on when it ran (an ingestion job pulling a moving feed), so the host never replicates, compares, caches or golden-tests them; when the stage also needs no capability and no pool, its jobs run before the module is certified on a node, wherever its runner starts. Only a standalone stage sets it (a chain compares as results.determinism). Needs requires.core >= 2.3. |
| `stages[].default` | bool | `false` | [beta] The default stage: what a job runs when it names no stage (the single-stage form). Only a standalone stage sets it; exactly one does when several stages are standalone. Needs requires.core >= 2.3. |
| `stages[].bootstrap` | bool | `false` | [beta] A bootstrap stage: its jobs run on nodes where the module's runner starts, before the goldens pass, with only the module's egress allowlist (no tools, GPU, containers, module data or settings), and the host registers their output only when it is exactly datasets of [[datasets.pinned]]. A standalone stage, not the default one, with determinism none and no pools. Needs requires.core >= 2.4. |
| `stages[].secrets` | list of str | `[]` | [beta] Declared [[secrets]] this stage's runner receives in OARBANK_SECRETS_FILE (no other stage, service, probe or doctor does). A job waits until each has a value for its node. Never on a bootstrap stage. Needs requires.core >= 2.5. |
| `stages[].checkpoint` | StageCheckpoint (optional) |  | [beta] The stage keeps portable checkpoints: the latest one a job's runner wrote is uploaded, and the job's next attempt, on any node, resumes from it. Needs the runner capability `checkpoint` and requires.core >= 2.5. |
| `stages[].checkpoint.max_mb` | int | required | [beta] The largest checkpoint (all its files) the agent uploads, MB. |
| `stages[].checkpoint.min_interval_s` | float | `600.0` | [beta] The agent uploads at most one checkpoint per interval; a checkpoint answering a checkpoint-then-stop request is always uploaded. |
| `stages[].requires` | StageRequires | `"capabilities=[] pools={} needs_pools=[] platforms=[] resources=Resources(cpu=1.0, mem_gb=1.0)"` |  |
| `stages[].requires.capabilities` | list of str | `[]` | [stable] Node capabilities that must be healthy (from probes/services). |
| `stages[].requires.pools` | table str → int | `{}` | [stable] Countable pool tokens reserved for the job's lifetime. |
| `stages[].requires.needs_pools` | list of str | `[]` | [beta] Pools that must exist on the node but are not reserved (a short phase uses them). |
| `stages[].requires.platforms` | list of str | `[]` | [beta] Only on these platforms (empty: every declared platform). |
| `stages[].requires.resources` | Resources | `"cpu=1.0 mem_gb=1.0"` |  |
| `stages[].requires.resources.cpu` | float | `1.0` | [stable] CPU cores the job uses. |
| `stages[].requires.resources.mem_gb` | float | `1.0` | [stable] Peak memory (GB) the job may use; the agent budgets it before admission. |
| `stages[].timeout_s` | float | `1800.0` | [stable] Hard wall-clock limit per attempt. |
| `stages[].retry` | Retry | `"max_attempts=3"` |  |
| `stages[].retry.max_attempts` | int | `3` | [stable] Execution attempts before the job is quarantined. |
| `stages[].variants` | table str → StageVariant | `{}` | [beta] Per-platform `timeout_s`, `requires.resources` and `retry`, keyed by platform token or OS. Needs requires.core >= 2.2. |
| `stages[].variants.timeout_s` | float (optional) |  | [beta] Replaces the stage's timeout_s. |
| `stages[].variants.requires` | StageVariantRequires (optional) |  | [beta] Only `resources` may vary per platform. |
| `stages[].variants.requires.resources` | ResourcesPatch (optional) |  | [beta] Merged over the stage's resources field by field. |
| `stages[].variants.requires.resources.cpu` | float (optional) |  | [beta] Replaces the stage's cpu on this platform. |
| `stages[].variants.requires.resources.mem_gb` | float (optional) |  | [beta] Replaces the stage's mem_gb on this platform. |
| `stages[].variants.retry` | Retry (optional) |  | [beta] Replaces the stage's retry. |
| `stages[].variants.retry.max_attempts` | int | `3` | [stable] Execution attempts before the job is quarantined. |
| `stages[].placement` | StagePlacement (optional) |  | [beta] Needs requires.core >= 2.2. |
| `stages[].placement.mix` | str | required | [beta] Between this stage and its `after` stage: `any`, `same-os`, `same-arch` or `same-platform` (open set; an unknown value applies as `same-platform`). Combined with [placement].mix, the stricter one wins. |
| `services` | list of Service | `[]` |  |
| `services[].name` | str | required |  |
| `services[].exec` | list of str | required | [beta] argv (spec/manifest.md, "Exec"); cwd is the bundle root; the agent appends the operation (fingerprint\|start\|stop\|status\|ready\|list_owned\|destroy). |
| `services[].platforms` | list of str | `[]` | [beta] Only on these platforms (empty: every declared platform). |
| `services[].lifecycle` | `"on_demand"` \| `"always"` \| `"manual"` | `"on_demand"` | [beta] on_demand: refcounted by admitted jobs and stopped after idle_timeout_s. |
| `services[].idle_timeout_s` | float | `900.0` |  |
| `services[].start_timeout_s` | float | `120.0` |  |
| `services[].stop_timeout_s` | float | `120.0` |  |
| `services[].restart` | RestartPolicy | `"backoff_initial_s=10.0 backoff_max_s=600.0 max_failures=5"` |  |
| `services[].restart.backoff_initial_s` | float | `10.0` |  |
| `services[].restart.backoff_max_s` | float | `600.0` |  |
| `services[].restart.max_failures` | int | `5` |  |
| `services[].provides` | ServiceProvides | `"capabilities=[] pools=[]"` |  |
| `services[].provides.capabilities` | list of str | `[]` |  |
| `services[].provides.pools` | list of str | `[]` | [stable] Pools whose token counts the service's `fingerprint` reports. |
| `services[].reserves_host_memory` | bool | `false` | [beta] The fingerprint's reserve.mem_gb is charged to the host while running. |
| `services[].yieldable` | bool | `true` | [beta] The agent may stop it when idle under memory pressure, and host protection may stop it, releasing the jobs using it, when it evicts or while GPU work may not run (a GPU service). |
| `services[].freeze_ok` | bool | `false` | [beta] The agent may freeze the service's process container (freezing returns no memory). |
| `services[].endpoint` | bool | `false` | [beta] Jobs reach the service: each attempt whose stage reserves one of its pools gets OARBANK_SERVICE_<NAME>, and the agent hands the service every connection over its endpoint channel; the service never listens (spec/service-protocol.md, "Endpoints"). Provides at least one pool; lifecycle on_demand or always. Needs requires.core >= 2.5. |
| `services[].gpu` | ServiceGPU | `"use='none' apis_any=[]"` | [beta] Needs requires.core >= 2.5 when `use` is not none. |
| `services[].gpu.use` | `"none"` \| `"shared"` \| `"exclusive"` | `"none"` | [beta] A running service that is not `none` is GPU-resident fleet work: host protection stops it, when yieldable, while GPU work may not run, and a job reserving one of its pools is a GPU job. Needs sandbox.devices.gpu = 'compute' and requires.core >= 2.5. |
| `services[].gpu.apis_any` | list of str | `[]` | [beta] Any of these GPU APIs; jobs run only on nodes providing one (open set; detected: cuda, directml, metal, opencl, rocm, vulkan). Needs `use` shared or exclusive and requires.core >= 2.5. |
| `probes` | list of Probe | `[]` |  |
| `probes[].name` | str | required |  |
| `probes[].exec` | list of str | required |  |
| `probes[].period_s` | float | `3600.0` |  |
| `probes[].platforms` | list of str | `[]` | [beta] Only on these platforms (empty: every declared platform). |
| `settings` | Settings | `"schema_=None"` |  |
| `settings.schema` | str (optional) |  | [stable] JSON Schema (bundle path) for the module's settings; the core stores but never interprets them. |
| `secrets` | list of Secret | `[]` | [beta] Write-only credentials. Needs requires.core >= 2.5. |
| `secrets[].name` | str | required | [beta] The secret's name; unique. Stages list it in `secrets`. |
| `secrets[].description` | str | `""` | [beta] What it is for, shown where the owner sets it. |
| `results` | Results | required |  |
| `results.schema` | str | required | [stable] JSON Schema (bundle path) for the result payload. |
| `results.schema_version` | int | required |  |
| `results.determinism` | `"exact"` \| `"within_tolerance"` \| `"none"` | required | [stable] exact: replicas must produce the same digest; `none`: results are not compared (see stages[].determinism). |
| `results.determinism_scope` | str | `"global"` | [stable] Open set. `global`: replicas on any platform must agree; `platform`: replicas and tie-breaks compare only within one platform (libm, BLAS and GPU differ across operating systems); `os` and `arch` [beta]: within one OS or one arch (they need requires.core >= 2.2). An unknown scope compares within one platform. |
| `results.digest` | Digest | required |  |
| `results.digest.version` | int | required | [stable] Replica/golden comparisons happen only between equal digest versions. |
| `results.digest.over` | list of str | required | [stable] Result payload fields the digest covers (documentation; the module computes it in result.evaluate). |
| `results.value` | Value (optional) |  |  |
| `results.value.field` | str | required | [stable] Result field studies optimise. |
| `results.value.direction` | `"maximize"` \| `"minimize"` | `"maximize"` |  |
| `results.max_inline_kb` | int | `64` |  |
| `results.fields` | list of ResultField | `[]` |  |
| `results.fields[].name` | str | required |  |
| `results.fields[].type` | `"number"` \| `"integer"` \| `"string"` \| `"boolean"` | required |  |
| `results.fields[].indexed` | bool | `false` | [stable] Promote to a generated column with an index (sortable/filterable). |
| `results.fields[].unit` | str (optional) |  |  |
| `results.fields[].ui` | FieldUI | `"column=None format=None unit=None"` |  |
| `results.fields[].ui.column` | str (optional) |  | [stable] Column header in result tables; absent = not shown. |
| `results.fields[].ui.format` | str (optional) |  | [stable] Restricted format spec: .Nf, .Ne, .N%, d, ,d or s. |
| `results.fields[].ui.unit` | str (optional) |  |  |
| `datasets` | Datasets | `"kinds=[] attrs=[] platform_bound=[] pinned=[]"` |  |
| `datasets.kinds` | list of str | `[]` | [stable] Dataset kinds this module registers (datasets.create refuses others). Kinds are short names scoped by the dataset's owning module, so two modules' kinds never collide; host.datasets.query takes the same short kind. |
| `datasets.attrs` | list of DatasetAttr | `[]` |  |
| `datasets.attrs[].name` | str | required |  |
| `datasets.attrs[].type` | `"number"` \| `"integer"` \| `"string"` \| `"boolean"` | required |  |
| `datasets.attrs[].indexed` | bool | `false` |  |
| `datasets.platform_bound` | list of str | `[]` | [beta] Kinds whose datasets only make sense on one platform (an index built by a native tool): datasets.create must give their `platform`, and jobs using them run only there. Needs requires.core >= 2.2. |
| `datasets.pinned` | list of PinnedDataset | `[]` | [beta] The datasets the module's bootstrap stages may provide, each file with its sha256 and size. The host registers a bootstrap job's artifact only when its files are exactly one entry's, and datasets.create of a pinned id only with the pinned contents. Needs a bootstrap stage and requires.core >= 2.4. |
| `datasets.pinned[].dataset_id` | str | required | [beta] The dataset id the host registers. |
| `datasets.pinned[].kind` | str | required | [beta] One of [datasets].kinds. |
| `datasets.pinned[].meta` | dict | `{}` | [beta] The registered dataset's meta (its attrs). |
| `datasets.pinned[].platform` | str (optional) |  | [beta] Set exactly when `kind` is platform-bound: one of requires.platforms. |
| `datasets.pinned[].files` | list of PinnedFile | required | [beta] Every file, with unique paths. |
| `datasets.pinned[].files[].path` | str | required | [beta] PortablePath inside the dataset (what a job sees under the dataset's mount). |
| `datasets.pinned[].files[].sha256` | str | required | [beta] The file's sha256, 64 lowercase hex digits. |
| `datasets.pinned[].files[].size` | int | required | [beta] The file's size in bytes. |
| `goldens` | Goldens (optional) |  |  |
| `goldens.fixtures` | str | required | [stable] Glob (bundle path) of golden fixtures: inputs plus the expected digest, optionally per platform (`Golden.platforms`, `Golden.expected_by_platform`; oarbank_sdk.goldens.load reads them). |
| `goldens.compare` | `"digest"` \| `"verb"` | `"digest"` | [stable] `verb` calls golden.compare instead of digest equality. |
| `ui` | UISection | `"icon=None digest_line=None pages=[] panels=[] views={} iframes=[] external_urls=[]"` |  |
| `ui.icon` | `"cpu"` \| `"gpu"` \| `"dna"` \| `"chart"` \| `"flask"` \| `"cube"` \| `"bolt"` (optional) |  |  |
| `ui.digest_line` | str (optional) |  | Restricted template: {field} or {field:FMT} only. |
| `ui.pages` | list of PageDecl | `[]` |  |
| `ui.pages[].id` | str | required |  |
| `ui.pages[].title` | str | required |  |
| `ui.pages[].slot` | `"module.overview"` \| `"module.page"` \| `"job.detail.panel"` \| `"node.detail.panel"` \| `"campaign.panel"` | required |  |
| `ui.pages[].file` | str | required |  |
| `ui.pages[].when` | str (optional) |  |  |
| `ui.panels` | list of PageDecl | `[]` |  |
| `ui.panels[].id` | str | required |  |
| `ui.panels[].title` | str | required |  |
| `ui.panels[].slot` | `"module.overview"` \| `"module.page"` \| `"job.detail.panel"` \| `"node.detail.panel"` \| `"campaign.panel"` | required |  |
| `ui.panels[].file` | str | required |  |
| `ui.panels[].when` | str (optional) |  |  |
| `ui.views` | table str → ViewDecl | `{}` |  |
| `ui.views.shape` | `"rows"` \| `"kv"` \| `"series"` \| `"stat"` | required |  |
| `ui.views.inputs` | list of str | `[]` | What invalidates it: results, jobs, datasets:<kind>, store:<collection>. |
| `ui.views.params` | table str → Any | `{}` | Restricted JSON Schema properties for view params. |
| `ui.views.columns` | list of Column | `[]` |  |
| `ui.views.columns[].key` | str | required |  |
| `ui.views.columns[].label` | str (optional) |  |  |
| `ui.views.columns[].type` | `"text"` \| `"number"` \| `"integer"` \| `"percent"` \| `"bytes"` \| `"duration"` \| `"relative_time"` \| `"timestamp"` \| `"bool"` \| `"digest"` \| `"code"` \| `"status"` \| `"job_ref"` \| `"node_ref"` \| `"dataset_ref"` \| `"campaign_ref"` \| `"artifact_ref"` \| `"link"` | `"text"` |  |
| `ui.views.columns[].format` | str (optional) |  |  |
| `ui.views.columns[].unit` | str (optional) |  |  |
| `ui.views.columns[].direction` | `"min"` \| `"max"` (optional) |  | Which way is better; enables best/colouring generically. |
| `ui.views.columns[].tone_by_sign` | bool | `false` |  |
| `ui.views.columns[].sortable` | bool | `true` |  |
| `ui.views.columns[].download` | bool | `false` | A dataset_ref or campaign_ref cell links to its download. [UI contract 1.2] |
| `ui.views.refresh_s` | int (optional) |  | Clock-driven refresh; floor 5 minutes. |
| `ui.views.max_rows` | int | `1000` |  |
| `ui.iframes` | list of IframeDecl | `[]` |  |
| `ui.iframes[].id` | str | required |  |
| `ui.iframes[].entry` | str | required | Bundle path of the HTML entry (served from the module origin with its own CSP). |
| `ui.iframes[].title` | str | required |  |
| `ui.iframes[].bridge` | list of `"read.query"` \| `"read.view"` \| `"read.media"` \| `"request.operation"` \| `"resize"` \| `"navigate"` | `["read.view", "resize"]` | Bridge capabilities the frame may use. |
| `ui.external_urls` | list of str | `[]` |  |
| `operations` | list of OperationDecl | `[]` | [beta] Module operations, registered as mod.<module>.<verb>. |
| `operations[].verb` | str | required |  |
| `operations[].title` | str | required |  |
| `operations[].tier` | `"T0"` \| `"T1"` \| `"T2"` \| `"T3"` | `"T1"` |  |
| `operations[].min_role` | `"viewer"` \| `"operator"` \| `"admin"` | `"operator"` |  |
| `operations[].target` | `"none"` \| `"dataset"` \| `"job"` \| `"node"` \| `"campaign"` \| `"store"` \| `"module"` | `"none"` |  |
| `operations[].params_schema` | str (optional) |  | Bundle path of the parameter schema. |
| `operations[].effects` | list of `"jobs.enqueue"` \| `"jobs.cancel"` \| `"campaigns.create"` \| `"campaigns.update"` \| `"campaigns.cancel"` \| `"datasets.create"` \| `"datasets.update"` \| `"datasets.delete"` \| `"module_settings.update"` \| `"store.write"` \| `"store.delete"` \| `"files.write"` \| `"files.put"` \| `"files.delete"` \| `"external"` | `[]` |  |
| `operations[].preview` | bool | `false` | Implements op.plan (required when the effective tier is T2/T3). |
| `cli` | CLI (optional) |  |  |
| `cli.exec` | list of str | required | [beta] Module CLI; `oarbank cli <module> ...` runs it on the coordinator, sandboxed, with a token scoped to the module (OARBANKD_URL, OARBANK_TOKEN). |
| `sandbox` | SandboxSection | `"contract=1 net=SandboxNet(mode='none', allow=[]) tools=[] devices=SandboxDevices(gpu='none') containers=[] container_sets=[] exec_writable=False folders=[]"` |  |
| `sandbox.contract` | `1` | `1` | [beta] Sandbox contract version. |
| `sandbox.net` | SandboxNet | `"mode='none' allow=[]"` |  |
| `sandbox.net.mode` | str | `"none"` | [beta] Open set. `none`; `egress-allowlist`: only the `allow` hosts, through the agent's local proxy (the SDK sets HTTPS_PROXY/ALL_PROXY); `egress-any`: any public address, a separately approved full-trust grant. Never loopback or link-local, never listening. |
| `sandbox.net.allow` | list of str | `[]` | [beta] `host[:port]` entries (a leading `*.` matches subdomains); never IP addresses or CIDRs. Required with egress-allowlist; port defaults to 443. |
| `sandbox.tools` | list of ToolGrant | `[]` | [beta] Host tools (registry ids) runners may read and execute. |
| `sandbox.tools[].id` | str | required | [beta] Logical tool id, e.g. `renderer4`. |
| `sandbox.tools[].trust` | `"read"` \| `"code-exec"` | `"read"` | [beta] `code-exec`: the tool runs arbitrary code (a JVM, an interpreter, a shell); the approval UI flags it. On Windows any readable binary is executable. |
| `sandbox.devices` | SandboxDevices | `"gpu='none'"` |  |
| `sandbox.devices.gpu` | str | `"none"` | [beta] Open set: `none` or `compute` (GPU compute through the platform's APIs, no display server; weakens isolation, so it is flagged at approval). |
| `sandbox.containers` | list of ContainerImage | `[]` | [beta] Images the agent's container broker may run for the module's jobs (a stage that uses them reserves the `containers` pool). |
| `sandbox.containers[].image` | str | required | [beta] A digest-pinned reference, e.g. `docker.io/org/tool:1.2@sha256:<64 hex>`. |
| `sandbox.containers[].platform` | str | `"linux/arm64"` | [beta] OCI platform (open set), e.g. linux/arm64 or linux/amd64 (emulated where the node's arch differs). |
| `sandbox.container_sets` | list of ContainerSet | `[]` | [beta] Image sets approved by signature (registry and repository prefix, a pinned cosign key, optionally a signed index). A job runs a set's images only if it lists them (jobs.enqueue `images`). Needs requires.core >= 2.5. |
| `sandbox.container_sets[].name` | str | required | [beta] The set's name; unique; shown at approval and in the audit. |
| `sandbox.container_sets[].registry` | str | required | [beta] The registry host[:port], lowercase (`docker.io` for Docker Hub). |
| `sandbox.container_sets[].repository` | str | required | [beta] A repository path; ending with `/` it is a prefix (every repository below it), else exactly that repository. |
| `sandbox.container_sets[].platform` | str | `"linux/arm64"` | [beta] OCI platform of the set's images. |
| `sandbox.container_sets[].key` | str | required | [beta] Bundle path of the cosign public key: one ECDSA P-256 key, PEM `PUBLIC KEY` (SPKI), as `cosign generate-key-pair` writes `cosign.pub`. |
| `sandbox.container_sets[].index` | str (optional) |  | [beta] A tagged reference of a signed image index (artifact type application/vnd.oarbank.image-set.v1+json): only the digests it lists are members. Absent: every image signed by the key is. |
| `sandbox.exec_writable` | bool | `false` | [beta] Runners may execute files they wrote into the data or work directory (downloaded tools). Not enforceable as `false` on Windows without application control; nodes report it. |
| `sandbox.folders` | list of FolderGrant | `[]` | [beta] Folders on the node (registry ids the operator maps to a path per node) the runner may read, or write into as an outbox. Needs requires.core >= 2.5. |
| `sandbox.folders[].id` | str | required | [beta] Logical folder id, e.g. `inputs`. |
| `sandbox.folders[].access` | `"read"` \| `"write"` | required | [beta] `read`: read the folder's files and listings, never write. `write`: an outbox: create files and directories and write them, never read, list, rename or delete anything there (files it creates may replace files there). |
| `bundle` | BundleSection | `"executables=[] platform_files={}"` |  |
| `bundle.executables` | list of str | `[]` | [stable] Globs (bundle paths) of files that get mode 755; every other file is 644. The argv[0] of every native exec is executable too. Modes never come from the build host's filesystem. |
| `bundle.platform_files` | table str → list of str | `{}` | [beta] Glob (bundle path) -> the platforms or OSes whose nodes receive the matching files; a file matched by several globs goes to each one's platforms, and unmatched files go everywhere. The coordinator and the CLI always have the whole bundle. Needs requires.core >= 2.2. |
| `placement` | Placement (optional) |  | [beta] Needs requires.core >= 2.2. |
| `placement.mix` | str | `"any"` | [beta] Open set: `any`, `same-os`, `same-arch` or `same-platform`. An unknown value applies as `same-platform` (fail safe) and `oarbank-sdk check` warns. |
| `placement.unit` | `"campaign"` \| `"group"` \| `"dataset"` \| `"pipeline"` | `"campaign"` | [beta] What stays together: a campaign, a job group within a campaign (jobs.enqueue `group`), the jobs of one dataset within a campaign, or one pipeline (head and tail stages, replicas and tie-breaks). |
| `placement.bind` | `"capacity"` \| `"first-claim"` \| `"explicit"` (optional) |  | [beta] How a unit gets its class: `capacity` (the feasible class with the most free certified CPU, at creation), `first-claim` (the class of the first node that claims one of its jobs) or `explicit` (only a pin). Absent: `capacity` for campaigns, `first-claim` otherwise. |
| `placement.rebind` | `"never"` \| `"if-stranded"` | `"never"` | [beta] When no node of the bound class has been eligible for `stranded_after_s`: `never` raises an alert naming the remedy; `if-stranded` rebinds to the best feasible class and re-queues the unit's finished jobs. |
| `placement.stranded_after_s` | float | `1800.0` | [beta] Seconds before a bound unit counts as stranded. |
