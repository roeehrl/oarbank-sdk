# Use secrets and signed container images

Two features for modules that call paid services and run many container images: write-only **secrets** delivered to
one stage, and container **image sets** approved by who signed them instead of by a list of digests. Both need
`requires.core >= 2.5`. The contracts are [manifest.md](../spec/manifest.md#secrets) and
[sandbox.md](../spec/sandbox.md#image-sets); this page shows the steps.

## Give one stage an API key

1. Declare the secret and list it on the stage that needs it. Nothing else receives it: not the other stages, not
   services, probes or `doctor`, not the coordinator side.

   ```toml
   [requires]
   core = ">=2.5,<3"

   [[secrets]]
   name = "llm_api_key"
   description = "API key for the model provider"

   [[stages]]
   name = "attempt"
   determinism = "none"
   secrets = ["llm_api_key"]

   [sandbox]
   net = { mode = "egress-allowlist", allow = ["api.provider.example"] }   # where the key may go, and nowhere else
   ```

2. Read it in the runner:

   ```python
   from oarbank_sdk import secrets
   key = secrets.get("llm_api_key")      # SecretMissing if this job's stage does not list it
   ```

   The value comes from `OARBANK_SECRETS_FILE`, an owner-only file in the job's work directory that the agent deletes
   with it. Never log it, write it into the result or artifacts, or put it on a tool's command line. The agent replaces
   exact copies of the value in the logs it sends with `[secret:llm_api_key]`, but that is a safety net: an encoded or
   split copy passes through.

3. The owner sets the value, for the module or for one node (a node's own value wins):

   ```
   oarbank secret set my-module llm_api_key            # reads the value from stdin or a no-echo prompt
   oarbank secret set my-module llm_api_key --node gpu-box-2
   oarbank secret list my-module                       # set or not, fingerprints, never the value
   ```

   Until a node has a value, the stage's jobs wait there (`SECRETS_NOT_SET` in `oarbank explain`).

4. Run `oarbank-sdk conform`. The kit hands each declared secret a random value (or `conformance.json` `secrets`),
   delivers it only to runner specs of the stages that list it, and fails any run whose output, result, artifacts or
   files contain it, and any coordinator verb whose answer does (a spec reaches every stage).

A coordinator verb that truly needs the value declares `coordinator.permissions = ["secrets:read:self"]` and calls
`ctx.host.secret("llm_api_key")`; it gets the module's value, never a node's.

## Approve a set of task images by signature

1. Sign each task image with a cosign key pair (`cosign generate-key-pair`; `cosign sign --key cosign.key
   <image>@<digest>`), and put the public key in the bundle:

   ```toml
   [[sandbox.container_sets]]
   name = "tasks"
   registry = "ghcr.io"
   repository = "example/swe-tasks/"      # every repository below this prefix
   platform = "linux/amd64"
   key = "keys/tasks.pub"

   [[stages]]
   name = "attempt"
   requires = { pools = { containers = 1 } }
   ```

   The operator approves the prefix and the key's fingerprint once. Pushing and signing more images needs no new module
   version.

2. List each job's images when you enqueue it, by digest:

   ```python
   from oarbank_sdk import effects as fx
   fx.job(key, spec, stage="attempt", images=["ghcr.io/example/swe-tasks/django-1234@sha256:..."])
   ```

3. Run them from the runner through the broker:

   ```python
   from oarbank_sdk import broker
   r = broker.run("ghcr.io/example/swe-tasks/django-1234@sha256:...", ["pytest", "-x"], platform="linux/amd64",
                  mounts=[broker.Mount("repo", "/repo")])
   ```

   The agent verifies the signature before it pulls. An image outside the set, unsigned, or not listed by the job is
   refused with `image_not_approved`.

4. To approve images by list rather than one signature each, publish a signed index instead and name it in the set
   (`index = "ghcr.io/example/swe-tasks-index:current"`): an OCI artifact whose layer
   `oarbank_sdk.images.index_document(registry, repository, seq, digests)` writes, pushed with `oras push` and signed
   with `cosign sign --key`. Raise `seq` with every new index; nodes never accept an older one.

5. Test it with `oarbank-sdk conform`: name a few members and an OCI image layout in `conformance.json`
   (`oarbank_sdk.imagetest` writes signed test images without cosign or a registry). The kit verifies the members and
   checks that an image outside the set and an unsigned image inside it are refused.

## GPUs in containers

A stage whose containers need the GPU reserves the agent's `gpu` pool and declares it:

```toml
[runner]
gpu = { use = "exclusive", in_container = true }

[[stages]]
name = "attempt"
requires = { pools = { containers = 1, gpu = 1 } }
```

and asks for it per run with `broker.run(..., gpus="all")`. Linux nodes with a CDI spec for their GPU (`nvidia-ctk cdi
generate`) and Windows nodes whose WSL containers session sees a GPU offer the pool; macOS nodes cannot pass a GPU into a
container, so such jobs never go there. A node's facts list the GPU APIs its containers get (`containers.gpu_apis`:
`cuda`, `directml`, ...). On Windows, use a glibc-based image for GPU containers.
