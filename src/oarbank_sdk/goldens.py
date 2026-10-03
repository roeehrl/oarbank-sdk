"""Golden fixtures kept in the bundle, filtered and resolved per node class (spec/module-protocol.md, "golden.list").

    from oarbank_sdk import goldens

    @module.verb("golden.list")
    def golden_list(p: mp.GoldenListParams, ctx) -> mp.GoldenListResult:
        return mp.GoldenListResult(goldens=goldens.load(HERE, "goldens/*.json", p.node_class))

Each file holds one `Golden` (or a list of them). A golden whose `platforms` exclude the node's platform is left out
(a node of unknown platform gets only unrestricted goldens), and `expected_by_platform` is resolved for the node's
platform (token, then OS, then `expected`), so the host compares against one `expected`. Reading the bundle's own files
keeps the verb pure: they are immutable.
"""
import json
from pathlib import Path

from . import module_protocol as mp


def _platform(node_class) -> str | None:
    if node_class is None:
        return None
    if isinstance(node_class, dict):
        return node_class.get("platform")
    return node_class.platform


def load(root, glob: str = "goldens/*.json", node_class: "mp.NodeClass | dict | None" = None) -> list[mp.Golden]:
    """The goldens under `root` matching `glob` (a bundle-relative pattern), sorted by file path, for `node_class`."""
    plat = _platform(node_class)
    out = []
    for f in sorted(Path(root).glob(glob), key=lambda p: p.relative_to(root).as_posix()):
        doc = json.loads(f.read_text(encoding="utf-8"))
        for g in (doc if isinstance(doc, list) else [doc]):
            g = mp.Golden.model_validate(g)
            if g.runs_on(plat):
                out.append(g.for_platform(plat))
    return out
