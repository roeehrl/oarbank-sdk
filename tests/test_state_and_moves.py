"""Module files, integrity checks and the coordinator-move verbs (spec/module-protocol.md)."""
import base64
import hashlib
import sys
import tomllib
from pathlib import Path

import pytest
from pydantic import ValidationError

from oarbank_sdk import effects as fx
from oarbank_sdk import manifest as mf
from oarbank_sdk import module_protocol as mp
from oarbank_sdk.client import ModuleClient
from oarbank_sdk.conformance import FakeHost, conform

TOY = Path(__file__).parents[1] / "examples" / "toy"
PERMS = {"files:read:self", "store:read:self"}


def toy(fixtures: dict):
    return ModuleClient.spawn([sys.executable, "-I", "toy_module.py"], cwd=TOY, callbacks=FakeHost(fixtures).callbacks(),
                              permissions=PERMS)


def test_file_paths_are_relative_and_plain():
    for ok in ("a", "notes/x.txt", "a/.hidden", "x..y"):
        mp.FilesReadParams(path=ok)
    for bad in ("", "/etc/passwd", "../x", "a/../b", "a//b", "a/", ".", "a/./b", "a b"):
        with pytest.raises(ValidationError):
            mp.FilesReadParams(path=bad)


def test_effect_builders():
    e = fx.files_write("notes/a.txt", b"hi")
    assert e.kind == "files.write" and base64.b64decode(e.args["content_b64"]) == b"hi"
    with pytest.raises(ValueError, match="files_put"):
        fx.files_write("big", b"x" * (fx.FILE_WRITE_MAX + 1))
    with pytest.raises(ValueError):
        fx.files_delete()
    assert fx.files_delete(prefix="cache/").args == {"prefix": "cache/"}
    assert fx.integrity([fx.check("a", False, severity="warn")]).ok
    assert not fx.integrity([fx.check("a", False)]).ok
    assert fx.fingerprint([("a", "1")]) == fx.fingerprint([("a", "1")]) != fx.fingerprint([("a", "2")])


def _toy_doc():
    return tomllib.loads((TOY / "oarbank-module.toml").read_text(encoding="utf-8"))


def test_move_rules_are_validated():
    doc = _toy_doc()
    assert [r.class_ for r in mf.Manifest.model_validate(doc).coordinator.move.rules] == ["rebuild", "drop"]
    doc["coordinator"]["move"]["rules"] = [{"files": "cache/", "store": "x", "class": "drop"}]
    with pytest.raises(ValidationError, match="exactly one"):
        mf.Manifest.model_validate(doc)
    doc = _toy_doc()
    doc["coordinator"]["capabilities"].remove("move.postflight")
    with pytest.raises(ValidationError, match="move.postflight"):
        mf.Manifest.model_validate(doc)
    doc = _toy_doc()
    doc["coordinator"]["capabilities"] = [c for c in doc["coordinator"]["capabilities"] if not c.startswith("move.")]
    doc["coordinator"]["move"]["rules"] = []
    with pytest.raises(ValidationError, match="coordinator.move.effects"):
        mf.Manifest.model_validate(doc)


def test_toy_integrity_reads_its_files_through_the_host():
    notes = {"notes/a.txt": "alpha", "notes/b.txt": "beta", "cache/tmp.txt": "scratch"}
    with toy({"files": notes}) as m:
        m.initialize()
        r = mp.IntegrityCheckResult.model_validate(m.call("integrity.check", {"scope": "on_demand", "deep": True, "now": 1.0}))
        assert r.ok and {c.name for c in r.checks} == {"notes_are_text", "notes_match_digests"}
        want = fx.fingerprint(sorted((k, hashlib.sha256(v.encode()).hexdigest()) for k, v in notes.items() if k.startswith("notes/")))
        assert r.fingerprint == want                       # the cache does not count: it is rebuilt after a move
    with toy({"files": {**notes, "notes/b.txt": "changed"}}) as m:
        m.initialize()
        assert m.call("integrity.check", {"scope": "move_target", "now": 1.0})["fingerprint"] != want


def test_toy_move_verbs():
    with toy({"store": {"toy_block/now": {"reason": "a sync is mid-round"}}}) as m:
        m.initialize()
        r = m.call("move.preflight", {"move_id": "mv1", "to_url": "http://b", "not_before": 0, "phase": "planned", "now": 1.0})
        assert [b["code"] for b in r["blockers"]] == ["toy/blocked"]
    with toy({}) as m:
        m.initialize()
        assert m.call("move.preflight", {"move_id": "mv1", "to_url": "http://b", "not_before": 0, "phase": "draining", "now": 1.0})["blockers"] == []
        post = m.call("move.postflight", {"move_id": "mv1", "from_url": "http://a", "epoch": 2, "now": 1.0,
                                          "skipped": [{"kind": "files", "selector": "cache/", "class": "rebuild", "count": 3, "bytes": 9}]})
        kinds = [e["kind"] for e in post["effects"]]
        assert kinds == ["store.write", "files.write"]
        assert m.call("move.cancelled", {"move_id": "mv1", "reason": "operator", "now": 1.0})["effects"][0]["args"]["doc"] == {"cancelled": "operator"}


def test_conformance_runs_the_lifecycle_verbs():
    rep = conform(TOY, fixtures={"files": {"notes/a.txt": "alpha"}}, runner=False)
    names = {c.name: c.status for c in rep.checks}
    for v in ("integrity.check", "move.preflight", "move.postflight", "move.cancelled"):
        assert names[f"{v} pure"] == "pass", rep.text()
    assert names["integrity.check passes on the fixtures"] == "pass"
    assert names["move.postflight effects declared"] == "pass"
