"""Shared model conventions for every contract.

- Wire models FORBID unknown fields when validated by the conformance kit (strict=True), but readers
  in production tolerate unknown fields (lenient); `Contract` defaults to lenient, `strict()` builds
  the strict variant used by `oarbank-sdk check` and the conformance suites.
- Every field carries a stability label in its description: [stable], [beta] or [experimental].
"""
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

Stability = Literal["experimental", "beta", "stable"]

# Reverse-DNS-style module id: at least two dot-separated labels, lowercase.
ModuleId = Annotated[str, StringConstraints(pattern=r"^[a-z0-9]+(\.[a-z0-9][a-z0-9-]*)+$", max_length=128)]
SemVer = Annotated[str, StringConstraints(
    pattern=r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(-[0-9A-Za-z.-]+)?(\+[0-9A-Za-z.-]+)?$")]
# A version range like ">=2.0,<3" (comma-separated comparisons).
VersionRange = Annotated[str, StringConstraints(pattern=r"^\s*(==|>=|<=|>|<|~=)\s*[0-9][0-9A-Za-z.]*(\s*,\s*(==|>=|<=|>|<|~=)\s*[0-9][0-9A-Za-z.]*)*\s*$")]
Name = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*$", max_length=64)]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
# argv: a non-empty list of strings, never a shell string.
Argv = Annotated[list[str], Field(min_length=1)]


class Contract(BaseModel):
    """Lenient reader: tolerates (and preserves) unknown fields, per the versioning policy."""
    model_config = ConfigDict(extra="allow", frozen=True, populate_by_name=True)


class StrictContract(BaseModel):
    """Strict variant for authoring-time checks: unknown fields are errors (catches typos)."""
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)
