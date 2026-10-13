"""Where the host tools a module was granted live on this node (spec/runner-protocol.md, OARBANK_TOOLS_FILE). Stdlib
only, so a runner can vendor it.

    from oarbank_sdk import tools
    java = tools.path("jdk") + "/bin/java"          # the JDK home the node granted
    tools.version("jdk")                            # "17.0.12"

The agent writes `{"<tool id>": [{"path": "<canonical path>", "version": "17.0.12", "arch": "aarch64"}]}` for every
approved `[sandbox].tools` id it could resolve on this node: the one detected installation that satisfies the request's
`version` and `arch` (a JDK's home directory; an executable's file). The sandbox grants exactly these paths. A tool the
node has no matching installation for is left out (its jobs do not reach this node). Never probe conventional
locations: symlinks such as /opt/homebrew/opt/... are not readable inside the sandbox, only the canonical paths here.
"""
import json
import os


class ToolMissing(LookupError):
    pass


def all_tools() -> dict:
    """{tool id: [{path, version, arch}]} as the agent wrote it ({} outside a node process)."""
    p = os.environ.get("OARBANK_TOOLS_FILE")
    if not p:
        return {}
    with open(p, encoding="utf-8") as f:
        return {k: [dict(i) for i in v] for k, v in json.load(f).items()}


def installations(tool_id: str) -> list:
    """The installations granted for a tool ([] when none is granted here)."""
    return all_tools().get(tool_id, [])


def paths(tool_id: str) -> list:
    return [i["path"] for i in installations(tool_id)]


def _first(tool_id: str) -> dict:
    found = installations(tool_id)
    if not found:
        raise ToolMissing(f"host tool {tool_id!r} is not granted on this node (declare it in [sandbox].tools; the node "
                          "detects it and grants an installation that satisfies the version asked for)")
    return found[0]


def path(tool_id: str) -> str:
    """The granted installation's canonical path; ToolMissing when the module was not granted the tool here."""
    return _first(tool_id)["path"]


def version(tool_id: str) -> str | None:
    """The granted installation's version as the node detected it (e.g. "17.0.12")."""
    return _first(tool_id).get("version")


def arch(tool_id: str) -> str | None:
    """The granted installation's architecture as the node detected it (e.g. "aarch64")."""
    return _first(tool_id).get("arch")
