"""Where the host tools a module was granted live on this node (spec/runner-protocol.md, OARBANK_TOOLS_FILE). Stdlib
only, so a runner can vendor it.

    from oarbank_sdk import tools
    renderer = tools.path("renderer4") + "/bin/renderer"

The agent writes `{"<tool id>": ["<canonical path>", ...]}` for every approved `[sandbox].tools` id, with the paths
the operator's tool registry maps it to on this OS, resolved: those are exactly the paths the sandbox grants. Never
probe conventional locations (symlinks such as /opt/homebrew/opt/... are not readable inside the sandbox).
"""
import json
import os


class ToolMissing(LookupError):
    pass


def all_tools() -> dict:
    p = os.environ.get("OARBANK_TOOLS_FILE")
    if not p:
        return {}
    with open(p, encoding="utf-8") as f:
        return {k: list(v) for k, v in json.load(f).items()}


def paths(tool_id: str) -> list:
    return all_tools().get(tool_id, [])


def path(tool_id: str) -> str:
    """The first granted path of a tool; ToolMissing when the module was not granted it here."""
    p = paths(tool_id)
    if not p:
        raise ToolMissing(f"host tool {tool_id!r} is not granted on this node (declare it in [sandbox].tools; the "
                          "operator maps it in the tool registry)")
    return p[0]
