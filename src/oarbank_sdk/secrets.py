"""The secrets this job's stage receives (spec/runner-protocol.md, OARBANK_SECRETS_FILE). Stdlib only, so a runner can
vendor it.

    from oarbank_sdk import secrets
    key = secrets.get("llm_api_key")

The agent writes `{"<name>": "<value>"}` for the secrets the job's stage lists (`stages[].secrets`), each set by the
owner for the module or for this node, into an owner-only file inside the work directory, deleted with it. Other
stages, services, probes and doctor get no file. Never log a value, write it into the result or artifacts, or pass it
on the command line of a tool: the agent's redaction of exact values in logs is a safety net, not a guarantee.
"""
import json
import os

ENV = "OARBANK_SECRETS_FILE"


class SecretMissing(LookupError):
    pass


def names() -> list:
    """The names this job received (empty: its stage lists none)."""
    return sorted(_all())


def _all() -> dict:
    p = os.environ.get(ENV)
    if not p:
        return {}
    with open(p, encoding="utf-8") as f:
        return {str(k): str(v) for k, v in json.load(f).items()}


def get(name: str) -> str:
    """A secret's value; SecretMissing when this job's stage does not list it."""
    v = _all().get(name)
    if v is None:
        raise SecretMissing(f"secret {name!r} is not delivered to this job (list it in the stage's `secrets`; the owner "
                            "sets it with `oarbank secret set`)")
    return v
