"""Where the folders a runner was granted live on this node (spec/runner-protocol.md, OARBANK_FOLDERS_FILE). Stdlib
only, so a runner can vendor it.

    from oarbank_sdk import folders
    inputs = Path(folders.path("inputs"))            # read-only
    out = Path(folders.path("outbox", "write"))      # an outbox: create and write files, never read or list it

The agent writes `{"<folder id>": {"path": "<canonical path>", "access": "read" | "write"}}` for every approved
`[sandbox].folders` id the operator mapped on this node and the node accepted: exactly the paths the sandbox grants.
"""
import json
import os


class FolderMissing(LookupError):
    pass


def all_folders() -> dict:
    p = os.environ.get("OARBANK_FOLDERS_FILE")
    if not p:
        return {}
    with open(p, encoding="utf-8") as f:
        return {k: dict(v) for k, v in json.load(f).items()}


def path(folder_id: str, access: str = "read") -> str:
    """The granted folder's path; FolderMissing when the runner was not granted it here with that access."""
    f = all_folders().get(folder_id)
    if not f or f.get("access") != access:
        raise FolderMissing(f"folder {folder_id!r} ({access}) is not granted on this node (declare it in [sandbox].folders; "
                            "the operator maps it in the folder registry)")
    return f["path"]
