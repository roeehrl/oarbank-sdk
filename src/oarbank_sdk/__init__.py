"""oarbank-sdk: contracts and tools for oarbank job modules.

The public API is the wire contracts (manifest, module/runner/service protocols, envelopes) and
their JSON Schemas. This package is one convenient implementation of them; it has no dependency
on the oarbank core, and nothing here may import it.
"""

__version__ = "1.2.2"

MANIFEST_SCHEMA = 1        # oarbank-module.toml `manifest = 1`
MODULE_PROTOCOL = 1        # coordinator <-> module JSON-RPC major
RUNNER_PROTOCOL = 1        # agent <-> job runner major
SERVICE_PROTOCOL = 1       # agent <-> node service / probe major
ENVELOPE = 1               # spec and result envelope major
SDK_MAJOR = MODULE_PROTOCOL
