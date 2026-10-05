"""SDK 1.5: datasets by origin, folder grants, media components and portable checkpoints (the models, rules and helpers;
the conformance kit is in test_conformance.py, the sandbox in test_sandbox.py)."""
import hashlib
import json
import tomllib
from pathlib import Path

import pytest
from pydantic import ValidationError

from oarbank_sdk import control, effects as fx, folders, manifest as mf, media, origins, ui as U
from oarbank_sdk.envelopes import ResultEnvelope, SpecEnvelope
from oarbank_sdk.render import Host, render_component
from oarbank_sdk.runner_protocol import Control, Event, checkpoint_digest

REEL = Path(__file__).parents[1] / "examples" / "reel"
D = "ab" * 32


def reel_doc() -> dict:
    return tomllib.loads((REEL / "oarbank-module.toml").read_text(encoding="utf-8"))


def manifest(**edit) -> mf.Manifest:
    doc = reel_doc()
    for path, value in edit.items():
        cur = doc
        keys = path.split("__")
        for k in keys[:-1]:
            cur = cur[int(k)] if isinstance(cur, list) else cur.setdefault(k, {})
        if value is None:
            cur.pop(keys[-1], None)
        else:
            cur[keys[-1]] = value
    return mf.Manifest.model_validate(doc)


# ---------------------------------------------------------------------------- origins

def test_an_origin_is_https_to_a_public_name():
    assert origins.url_problem("https://huggingface.co/m/resolve/main/model.safetensors") is None
    assert origins.url_problem("https://cdn.example.org:8443/x") is None
    for bad, why in [("http://example.org/x", "https"), ("https://10.0.0.1/x", "IP"), ("https://[::1]/x", "IP"),
                     ("https://localhost/x", "public"), ("https://printer.local/x", "public"), ("https://intranet/x", "public"),
                     ("https://u:p@example.org/x", "user info"), ("ftp://example.org", "https"), ("https://" + "a" * 3000, "2048")]:
        assert why in (origins.url_problem(bad) or ""), bad


def test_the_origin_host_policy_matches_hosts_subdomains_and_ports():
    url = "https://cdn-lfs.huggingface.co/repos/x"
    assert origins.allowed(url, []) and origins.allowed(url, ["*.huggingface.co"])
    assert not origins.allowed(url, ["huggingface.co"]) and not origins.allowed("https://huggingface.co/x", ["*.huggingface.co"])
    assert origins.allowed("https://files.example.org:8443/a", ["files.example.org:8443"])
    assert not origins.allowed("https://files.example.org:8443/a", ["files.example.org"])
    assert origins.host_port("https://EXAMPLE.org:443/a") == "example.org"


def test_a_dataset_file_always_names_its_digest_and_size():
    ok = {"path": "model/weights.bin", "digest": D, "size": 5_000_000_000, "origins": ["https://example.org/w.bin"]}
    assert origins.file_problem(ok) is None and origins.file_problem({**ok, "origins": None}) is None
    assert "size" in origins.file_problem({**ok, "size": None})
    assert "digest" in origins.file_problem({**ok, "digest": "AB" * 32})
    assert "1 to 8" in origins.file_problem({**ok, "origins": []})
    assert "https" in origins.file_problem({**ok, "origins": ["http://example.org/w.bin"]})
    assert "path" in origins.file_problem({**ok, "path": "../x"})
    e = fx.datasets_create("model:w", "model", [ok])
    assert e.args["files"][0]["origins"] == ["https://example.org/w.bin"]
    with pytest.raises(ValueError, match="size"):
        fx.datasets_create("model:w", "model", [{"path": "a", "digest": D}])


# ---------------------------------------------------------------------------- manifest: folders and checkpoints

def test_the_reference_module_uses_the_new_keys_and_needs_core_2_5():
    m = manifest()
    assert m.checkpoint_of("render").max_mb == 64 and m.runner.checkpoint_grace_s == 30
    with pytest.raises(ValidationError, match=r"need requires.core >= 2.5"):
        manifest(requires__core=">=2.4,<3")


def test_folder_grants_are_unique_ids_with_an_access_and_need_core_2_5():
    m = manifest(sandbox={"folders": [{"id": "inputs", "access": "read"}, {"id": "outbox", "access": "write"}]})
    assert [f.access for f in m.sandbox.folders] == ["read", "write"] and m.sandbox.requests()
    assert ("sandbox.folders", (2, 5)) in m.core_keys_used()
    with pytest.raises(ValidationError, match="more than once"):
        manifest(sandbox={"folders": [{"id": "inputs", "access": "read"}, {"id": "inputs", "access": "write"}]})
    with pytest.raises(ValidationError):
        manifest(sandbox={"folders": [{"id": "Inputs", "access": "read"}]})
    with pytest.raises(ValidationError):
        manifest(sandbox={"folders": [{"id": "inputs", "access": "execute"}]})
    assert not m.sandbox.for_bootstrap().folders            # bootstrap jobs never get folders


def test_checkpoint_rules():
    with pytest.raises(ValidationError, match="runner.capabilities lists `checkpoint`"):
        manifest(runner__capabilities=["cancellable"])
    with pytest.raises(ValidationError, match="needs a stage with `checkpoint`"):
        manifest(stages__0__checkpoint=None)
    with pytest.raises(ValidationError):
        manifest(stages__0__checkpoint={"max_mb": 0})
    with pytest.raises(ValidationError):
        manifest(stages__0__checkpoint={"max_mb": 10, "min_interval_s": 5})
    doc = reel_doc()
    doc["stages"].append({"name": "fetch", "bootstrap": True, "determinism": "none", "checkpoint": {"max_mb": 1}})
    doc["datasets"]["pinned"] = [{"dataset_id": "asset:x", "kind": "asset", "files": [{"path": "x", "sha256": D, "size": 1}]}]
    doc["stages"][0]["default"] = True
    with pytest.raises(ValidationError, match="bootstrap stage keeps nothing"):
        mf.Manifest.model_validate(doc)
    assert "checkpoint" in mf.RUNNER_CAPABILITIES


# ---------------------------------------------------------------------------- runner protocol

def test_checkpoint_events_control_and_resume_are_additive():
    ev = Event.model_validate({"t": 1.0, "kind": "checkpoint", "files": [{"path": "ckpt/000001/state.json", "name": "state.json"}]})
    assert ev.files[0].checkpoint_name() == "state.json"
    assert Event.model_validate({"t": 1.0, "kind": "checkpoint", "files": [{"path": "a/b"}]}).files[0].checkpoint_name() == "a/b"
    with pytest.raises(ValidationError):
        Event.model_validate({"t": 1.0, "kind": "checkpoint", "files": [{"path": "../escape"}]})
    assert Control.model_validate({"seq": 3, "stop": True, "checkpoint": True}).checkpoint
    assert not Control.model_validate({"seq": 1}).checkpoint
    env = {"envelope": 1, "schema": "reel/spec@1", "module_id": "dev.codonic.oarbank.reel", "module_version": "0.1.0",
           "job_key": "k", "payload": {}}
    assert SpecEnvelope.model_validate(env).resume is None
    r = SpecEnvelope.model_validate({**env, "resume": {"from_attempt": 7, "digest": D, "data": {"next": 4}}}).resume
    assert (r.from_attempt, r.data) == (7, {"next": 4})
    res = ResultEnvelope.model_validate({"schema": "reel/result@1", "module_version": "0.1.0", "payload": {}, "artifacts": [
        {"name": "frames", "files": [{"path": "f.png", "local": "out/f.png", "thumbnail": {"local": "out/t.png"}}]}]})
    assert res.artifacts[0].files[0].thumbnail.local == "out/t.png"


def test_the_checkpoint_digest_is_over_sorted_names_digests_and_sizes():
    a = [{"name": "b", "digest": D, "size": 2}, {"name": "a", "digest": D, "size": 1}]
    assert checkpoint_digest(a) == checkpoint_digest(list(reversed(a)))
    assert checkpoint_digest(a) != checkpoint_digest([{**a[0], "size": 3}, a[1]])


def test_checkpoints_are_written_into_fresh_directories_and_announced(tmp_path):
    events = tmp_path / "events.ndjson"
    ck = control.Checkpoints(tmp_path, events, {})
    assert ck.resume() is None
    with ck.write({"step": 4}) as d:
        (d / "state.json").write_text("{}")
        (d / "frames").mkdir()
        (d / "frames" / "f0.png").write_bytes(b"x")
    with ck.write({"step": 8}) as d2:
        (d2 / "state.json").write_text("{}")
    assert d != d2
    lines = [json.loads(x) for x in events.read_text().splitlines()]
    assert [e["data"]["step"] for e in lines] == [4, 8]
    assert lines[0]["files"] == [{"path": "ckpt/000001/frames/f0.png", "name": "frames/f0.png"},
                                 {"path": "ckpt/000001/state.json", "name": "state.json"}]
    for e in lines:
        Event.model_validate(e)
    with pytest.raises(ValueError, match="at most 4096"):
        with ck.write({"x": "y" * 5000}):
            pass
    (tmp_path / "checkpoint").mkdir()
    resumed = control.Checkpoints(tmp_path, events, {"resume": {"from_attempt": 3, "digest": D, "data": {"step": 8}}})
    assert resumed.resume_data() == {"step": 8} and resumed.resume_dir() == tmp_path / "checkpoint"


def test_a_checkpoint_then_stop_request_reads_as_such(tmp_path):
    (tmp_path / "control.json").write_text(json.dumps({"seq": 1, "stop": True, "checkpoint": True}))
    ctl = control.Control(tmp_path)
    assert ctl.checkpoint_requested
    with pytest.raises(control.Stopped):
        ctl.check()
    (tmp_path / "control.json").write_text(json.dumps({"seq": 2, "stop": True}))
    ctl.refresh()
    assert ctl.stop and not ctl.checkpoint_requested


def test_folders_file(tmp_path, monkeypatch):
    f = tmp_path / "folders.json"
    f.write_text(json.dumps({"inputs": {"path": "/data/in", "access": "read"}, "outbox": {"path": "/data/out", "access": "write"}}))
    monkeypatch.setenv("OARBANK_FOLDERS_FILE", str(f))
    assert folders.path("inputs") == "/data/in" and folders.path("outbox", "write") == "/data/out"
    with pytest.raises(folders.FolderMissing):
        folders.path("outbox")                              # an outbox is never readable
    with pytest.raises(folders.FolderMissing):
        folders.path("missing")


# ---------------------------------------------------------------------------- media

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 20
WEBP = b"RIFF\x10\x00\x00\x00WEBPVP8 " + b"\x00" * 8
AVIF = b"\x00\x00\x00\x1cftypavif" + b"\x00" * 8
MP4 = b"\x00\x00\x00\x18ftypisom" + b"\x00" * 8
WEBM = (REEL / "assets" / "clip.webm").read_bytes()[:64]
SVG = b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
HTML_AS_PNG = b"<!doctype html><html><script>fetch('/api')</script></html>"


@pytest.mark.parametrize("kind,head,want", [
    ("image", PNG, "image/png"), ("image", JPEG, "image/jpeg"), ("image", WEBP, "image/webp"), ("image", AVIF, "image/avif"),
    ("thumbnail", PNG, "image/png"), ("video", MP4, "video/mp4"), ("video", WEBM, "video/webm"),
    ("audio", b"ID3\x04" + b"\x00" * 10, "audio/mpeg"), ("audio", b"\xff\xfb\x90\x00", "audio/mpeg"),
    ("audio", b"\x00\x00\x00\x20ftypM4A " + b"\x00" * 8, "audio/mp4"), ("audio", b"OggS\x00", "audio/ogg"),
    ("audio", b"RIFF\x00\x00\x00\x00WAVEfmt ", "audio/wav"), ("text", "subtitles: é\n".encode(), media.TEXT),
    ("text", b"WEBVTT\n\n00:00.000 --> 00:01.000\n<b>hi</b>", media.TEXT)])
def test_allowed_types_are_recognised_by_their_bytes(kind, head, want):
    assert media.sniff(kind, head) == want


@pytest.mark.parametrize("kind,head", [
    ("image", SVG), ("image", HTML_AS_PNG), ("thumbnail", MP4), ("image", AVIF.replace(b"avif", b"heic")),
    ("video", PNG), ("video", AVIF), ("audio", HTML_AS_PNG), ("text", b"\x00binary"), ("text", b"\xff\xfe\xfd bad utf8"),
    ("image", b""), ("model", PNG)])
def test_svg_html_and_mismatched_bytes_are_refused(kind, head):
    assert media.sniff(kind, head) is None


def test_caps_and_text_cut_mid_character():
    assert media.problem("thumbnail", PNG, 2 * media.MiB) and "cap" in media.problem("thumbnail", PNG, 2 * media.MiB)
    assert media.problem("image", PNG, 1000) is None and "allowed" in media.problem("image", SVG, 10)
    assert media.sniff("text", "aé".encode()[:-1]) == media.TEXT          # a sample that ends inside a character


def test_byte_ranges():
    assert media.byte_range(None, 100) is None and media.byte_range("bytes=0-9", 100) == (0, 9)
    assert media.byte_range("bytes=90-", 100) == (90, 99) and media.byte_range("bytes=-10", 100) == (90, 99)
    assert media.byte_range("bytes=50-500", 100) == (50, 99)
    assert media.byte_range("bytes=100-", 100) == "unsatisfiable" and media.byte_range("bytes=-0", 100) == "unsatisfiable"
    assert media.byte_range("bytes=0-1,5-6", 100) is None and media.byte_range("items=0-1", 100) is None


# ---------------------------------------------------------------------------- UI contract 1.1

def test_artifact_references_have_two_forms():
    assert U.ArtifactRef.model_validate({"job": 3, "artifact": "frames", "path": "f.png"}).job == 3
    assert U.ArtifactRef.model_validate({"digest": D, "thumbnail": D}).thumbnail == D
    for bad in [{"job": 3, "artifact": "frames"}, {"digest": D, "job": 3, "artifact": "a", "path": "p"},
                {"job": 1, "artifact": "a", "path": "p", "thumbnail": D}, {}, {"digest": "nothex"}]:
        with pytest.raises(ValidationError):
            U.ArtifactRef.model_validate(bad)


def test_media_components_need_requires_1_1():
    page = U.Page.model_validate(json.loads((REEL / "ui" / "pages" / "overview.json").read_text()))
    man = manifest()
    assert U.check_page(page, man) == []
    bare = U.Page.model_validate({"body": [{"type": "media", "kind": "image", "source": {"view": "frames"}, "field": "frame"}]})
    assert any("requires" in e for e in U.check_page(bare, man))
    assert U.minor_of(U.UI_CONTRACT) >= 1 and {"media", "gallery", "compare"} <= set(U.COMPONENT_TYPES)


def test_artifact_ref_columns_reach_the_page_and_need_1_1_and_core_2_5():
    man = manifest()
    assert [c.type for c in man.ui.views["frames"].columns] == ["artifact_ref", "integer"]   # declared, so not projected away
    assert ("ui.views.columns[].type artifact_ref", (2, 5)) in man.core_keys_used()
    table = {"type": "table", "source": {"view": "renders"}, "columns": [{"key": "clip", "type": "artifact_ref"}]}
    assert any("requires" in e for e in U.check_page(U.Page.model_validate({"body": [table]}), man))
    ok = U.Page.model_validate({"body": [{**table, "requires": "1.1", "fallback": "drop"}]})
    assert U.check_page(ok, man) == []
    from oarbank_sdk.render import fmt_value
    assert fmt_value({"job": 7, "artifact": "clip", "path": "clip.webm"}, "artifact_ref") == "clip/clip.webm"
    assert fmt_value({"digest": D}, "artifact_ref") == D[:12] and fmt_value("<b>", "artifact_ref") == "—"


def _host(rows, media_fn, ui_minor=1):
    return Host(resolve=lambda src, ctx: {"rows": rows}, operation=lambda op: None, op_url=lambda op: "/do/" + op,
                link_url=lambda l: f"/jobs/{l.job}" if l.job else None, frame=lambda v: None, media=media_fn,
                module="reel", ui_minor=ui_minor)


def test_the_renderer_draws_only_what_the_host_resolved():
    ref = {"job": 9, "artifact": "frames", "path": "frame-0000.png"}
    seen = []

    def resolve_media(r, kind):
        seen.append((r, kind))
        return {"src": "https://m.example/b/T1", "thumb": "https://m.example/b/T2", "job": 9} if r == ref else None
    g = U.Gallery.model_validate({"type": "gallery", "requires": "1.1", "source": {"view": "frames"}, "field": "frame",
                                  "caption_field": "index"})
    html = render_component(g, _host([{"frame": ref, "index": 0}, {"frame": {"job": 1, "artifact": "x", "path": "y"}, "index": 1}],
                                     resolve_media))
    assert 'src="https://m.example/b/T2"' in html and 'href="https://m.example/b/T1"' in html and "/jobs/9" in html
    assert "no artifact of this module" in html                     # the second row's reference was refused
    v = U.Media.model_validate({"type": "media", "requires": "1.1", "kind": "video", "source": {"view": "r"}, "field": "clip"})
    html = render_component(v, _host([{"clip": ref}], resolve_media))
    assert '<video' in html and 'poster="https://m.example/b/T2"' in html and "controls" in html
    t = U.Media.model_validate({"type": "media", "requires": "1.1", "kind": "text", "source": {"view": "r"}, "field": "clip"})
    assert 'sandbox=""' in render_component(t, _host([{"clip": ref}], resolve_media))
    c = U.Compare.model_validate({"type": "compare", "requires": "1.1", "source": {"view": "p"}, "left": "a", "right": "b",
                                  "mode": "slider"})
    html = render_component(c, _host([{"a": ref, "b": ref}], resolve_media))
    assert "data-compare" in html and 'type="range"' in html
    old = render_component(v, _host([{"clip": ref}], resolve_media, ui_minor=0))
    assert "needs UI contract 1.1" in old and "<video" not in old
    dropped = U.Media.model_validate({**v.model_dump(), "fallback": "drop"})
    assert render_component(dropped, _host([{"clip": ref}], resolve_media, ui_minor=0)).count("mod-placeholder") == 0


def test_the_renderer_escapes_captions():
    m = U.Media.model_validate({"type": "media", "requires": "1.1", "kind": "image", "source": {"view": "r"}, "field": "f",
                                "caption_field": "c"})
    html = render_component(m, _host([{"f": {"digest": D}, "c": "<script>x</script>"}],
                                     lambda r, k: {"src": "https://m.example/b/T", "thumb": None, "job": None}))
    assert "<script>x" not in html and "&lt;script&gt;" in html


def test_preview_serves_fixture_media_through_the_sniffer(tmp_path):
    import shutil
    import urllib.request
    from oarbank_sdk.preview import Preview
    d = tmp_path / "reel"
    shutil.copytree(REEL, d)
    (d / "fixtures" / "ui" / "media" / "frames" / "evil.png").write_bytes(HTML_AS_PNG)
    pv = Preview(str(d / "oarbank-module.toml"), port=18731)
    try:
        pv.serve()
        import threading
        threading.Thread(target=pv.console.serve_forever, daemon=True).start()
        html = urllib.request.urlopen("http://127.0.0.1:18731/").read().decode()
        assert "http://127.0.0.1:18732/b/image/frames/frame-0000.png" in html and "<video" in html
        r = urllib.request.urlopen("http://127.0.0.1:18732/b/image/frames/frame-0000.png")
        assert r.headers["Content-Type"] == "image/png" and r.headers["X-Content-Type-Options"] == "nosniff"
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen("http://127.0.0.1:18732/b/image/frames/evil.png")
        assert e.value.code == 415
        digest = hashlib.sha256((d / "fixtures" / "ui" / "media" / "frames" / "frame-0001.png").read_bytes()).hexdigest()
        assert pv.media({"digest": digest}, "image")["src"].endswith("/b/image/frames/frame-0001.png")
    finally:
        pv.close()
