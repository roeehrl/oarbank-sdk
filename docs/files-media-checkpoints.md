# Files, media and checkpoints

SDK 1.5 and core 2.5 add four ways for a module to move bytes: datasets that come from a URL or an operator's upload,
folders on the node a runner reads from or writes into, media artifacts a module page shows without an iframe, and
portable checkpoints that let a job paused or moved on one node finish on another. [`examples/reel`](../examples/reel)
uses all of them; this page walks through each with reel's code. Every key below needs `requires.core >= 2.5`.

## Datasets from a URL

A module can register a large file nobody uploaded: `datasets.create` with `origins` names a file by its sha256, its
size and one to eight https URLs. Nodes fetch it straight from an origin and check the digest; the coordinator holds no
copy unless every origin fails for some node, and then fetches it once.

```python
from oarbank_sdk import effects as fx

effect = fx.datasets_create("asset:clip-1", "asset", [{"path": "clip.webm", "digest": sha256, "size": size,
                                                        "origins": ["https://assets.example.org/reel/clip.webm"]}])
```

- An origin is `https` to a public host name, never an IP address, `localhost` or a `.local` name; redirects are
  followed (at most five, each held to the same rule), so a model hub's CDN redirect works.
- The operator's origin host policy decides which hosts nodes may fetch from at all.
- `fx.datasets_create` refuses a file without a digest and size, or with an origin that breaks the rule, before the
  host sees it, and so does the conformance kit for every fixture operation (`ops` in `conformance.json`).

reel's `import_asset` operation does exactly this ([`reel_module.py`](../examples/reel/reel_module.py)).

## Importing an uploaded dataset

Operators upload folders with `oarbank dataset upload <dir> --kind <kind>` or the console's dataset upload page. To turn
an upload into one of your module's datasets, write an operation with `target = "dataset"` that reads the upload's file
list and creates a dataset naming the same blobs; nothing is copied. Declare the `datasets:read` permission.

```python
found = ctx.host.datasets_query(ids=[p.target], with_files=True).datasets
files = [{k: f[k] for k in ("path", "digest", "size", "origins") if f.get(k) is not None} for f in found[0].files]
effect = fx.datasets_create("asset:" + found[0].id.split(":", 1)[-1], "asset", files, meta={"from": found[0].id})
```

The coordinator side never reads file contents: anything that needs the bytes is a job. reel's `adopt_upload` is the
complete operation.

## Folders on the node

A runner can read files from a folder the operator chose on each node, or drop its outputs into one:

```toml
[sandbox]
folders = [{ id = "inbox", access = "read" }, { id = "outbox", access = "write" }]
```

- The operator approves the request with the version, then maps each id to a path per node. Jobs run only on nodes
  where every folder is mapped and accepted.
- A `read` folder is read-only. A `write` folder is an outbox: the runner can create and write files there, but never
  read, list, rename or delete anything in it, so write each output under a new name.
- The runner finds the paths with `oarbank_sdk.folders`:

```python
from oarbank_sdk import folders
inbox = Path(folders.path("inbox"))
outbox = Path(folders.path("outbox", "write"))
```

In `conformance.json`, `folders` maps each read folder to a directory of the module; every outbox starts empty. See
[spec/sandbox.md](../spec/sandbox.md#folders) for what each OS enforces.

## Media on module pages

UI contract 1.1 adds three components that show a job's artifacts with no iframe: `media` (one image, video, audio or
text file), `gallery` (a grid of images or videos) and `compare` (two images side by side or under a slider). Each
names a field whose value is an artifact reference:

```json
{"job": 812, "artifact": "frames", "path": "frame-0003.png"}
```

- Give each component `"requires": "1.1"` and a `fallback` (`placeholder` or `drop`) for older consoles.
- A view that carries references declares those columns with the cell type `artifact_ref`; undeclared columns never
  reach the console.
- The console checks every reference belongs to your module and serves its bytes from the sandboxed module origin, only
  as an allowed type for the kind (PNG, JPEG, WebP or AVIF images; MP4 or WebM video; MP3, M4A, Ogg or WAV audio; UTF-8
  text). An SVG, or HTML named `.png`, is refused.
- Give large images a thumbnail: `{"path": "frame-0003.png", "local": "out/frame-0003.png", "thumbnail": {"local":
  "out/thumbs/frame-0003.png"}}` in the result's artifacts. A gallery shows thumbnails and links each to the full file.

reel's overview page ([`ui/pages/overview.json`](../examples/reel/ui/pages/overview.json)) has all three. `oarbank-sdk
preview` serves `fixtures/ui/media/` through the same sniffer, so a page that previews well behaves the same on the
console. The rules are in [spec/ui-contract.md](../spec/ui-contract.md#media).

## Datasets, downloads and uploads on module pages

The `datasets` host query (UI contract 1.2) lists your datasets and the operator's of your kinds, never another
module's, with `owner`, `files`, `size`, the `origins` hosts and `pinned`. A `dataset_ref` or `campaign_ref` column links
to the console's page for it, or with `"download": true` to its download; an upload link brings the operator to the
folder upload with your module and kind filled in, then offers your importer on the new dataset. reel's Data page
([`ui/pages/data.json`](../examples/reel/ui/pages/data.json)) has all of it:

```json
{"type": "table", "requires": "1.2", "fallback": "placeholder", "source": {"query": "datasets"},
 "columns": [{"key": "dataset_id", "type": "dataset_ref"}, {"key": "owner"}, {"key": "size", "type": "bytes"},
             {"key": "dataset_id", "label": "download", "type": "dataset_ref", "download": true}]},
{"type": "link", "requires": "1.2", "fallback": "drop", "text": "Upload a folder", "to": {"upload": {"kind": "upload", "then": "self.adopt_upload"}}}
```

A module with bootstrap stages shows its pinned datasets with the `pins` query: each one `registered` exactly as pinned,
`missing` (no bootstrap job has brought it yet) or `conflict` (another dataset holds the id: `conflict` says whose, and
`alert` whether the conflict alert is open).

## Portable checkpoints

A long job need not start over when a node has to let it go. Declare the capability and the stage's limits:

```toml
[runner]
capabilities = ["cancellable", "cooperative_pause", "checkpoint"]
checkpoint_grace_s = 30

[[stages]]
name = "render"
checkpoint = { max_mb = 64, min_interval_s = 30 }
```

Then write checkpoints with `oarbank_sdk.control.Checkpoints`, from time to time and whenever a stop asks for one:

```python
ckpt = Checkpoints(workdir, events, spec)
start = ckpt.resume_data().get("next", 0) if ckpt.resume() else 0      # files under ckpt.resume_dir()
...
except Stopped:
    if ctl.checkpoint_requested:
        with ckpt.write({"next": i}) as d:
            save_state(d)
    return ctl.acknowledge_stop()
```

- When protection pauses the job past the node's limit, or must evict it, the agent asks for a checkpoint and stop
  instead of killing it, uploads the files and releases the job. Its next attempt, on any node, gets them read-only
  under `<workdir>/checkpoint/` and `resume` in its envelope.
- Keep in the checkpoint everything the result names: a resumed run that lacks a file it lists (a thumbnail, say)
  fails its upload.
- A resumed run must give the same result as an uninterrupted one. The conformance kit checks it: it stops a golden
  with checkpoint-then-stop, resumes it in a fresh workdir and compares the digests.
- Goldens and bootstrap jobs never resume: certification runs a golden whole.

The protocol is in [spec/runner-protocol.md](../spec/runner-protocol.md#checkpoints); reel's runner
([`reel_runner.py`](../examples/reel/reel_runner.py)) is a complete example.

A page shows them with the `checkpoints` host query (the latest checkpoint of each open job: who wrote it, its
sequence number, files and size) and the resume fields of `attempts` (`resumed_from_attempt`, `resumed_from_node`):
reel's Data page and its job panel ([`ui/panels/job.json`](../examples/reel/ui/panels/job.json)), which reads the
job's own with `"params": {"job_id": "$job"}`.
