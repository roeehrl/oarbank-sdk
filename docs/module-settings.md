# Declare module settings

A module's settings are the choices its owner makes for it: a VM size, a threshold, a word its runners read. From core
2.9 a module declares each one as a key with a type, a scope and constraints; the core validates every value, shows
each key in the console with where its value comes from, and hands each process exactly the keys meant for it. Node
settings, required settings and campaign overrides need `requires.core >= 2.9`. The contract is
[manifest.md](../spec/manifest.md#settings); this page shows the steps.

## Declare the keys

1. Point the manifest at a JSON Schema file:

   ```toml
   [requires]
   core = ">=2.9,<3"

   [settings]
   schema = "schemas/settings.schema.json"
   ```

2. Write one property per setting:

   ```json
   {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {
     "vm_mem_gb": {"type": "number", "minimum": 2, "maximum": 64, "default": 8,
                   "title": "VM memory", "description": "Memory the module's VM gets on a node.",
                   "x-oarbank": {"scope": "node", "unit": "GB"}},
     "batch_size": {"type": "integer", "minimum": 1, "maximum": 4096, "default": 64,
                    "title": "Batch size", "description": "Items per job.",
                    "x-oarbank": {"campaign": true}},
     "upstream_url": {"type": "string", "format": "uri",
                      "title": "Upstream feed", "description": "Where the ingestion stage reads from.",
                      "x-oarbank": {"required": true}}
    }
   }
   ```

   The property is the contract. Its `type` (`boolean`, `integer`, `number`, `string`, `array` or `object`,
   optionally with `"null"`) and every constraint on it (`minimum`, `maximum`, `enum`, `pattern`, `maxLength`, `items`,
   `format`, …) are what the core checks each value against, on every write. `default` must itself be valid. `title`
   is the label the owner reads (at most 80 characters) and `description` its one line of help (at most 500). A name is
   lower-case letters, digits and `_`, starting with a letter, at most 64 characters; a schema declares at most 128
   settings.

3. Say how the core treats each key in `x-oarbank`:

   | Field | Default | Meaning | Needs |
   |---|---|---|---|
   | `scope` | `"fleet"` | `fleet`: one value for the fleet, read by the module's coordinator side. `node`: a value the owner sets for the fleet, a node group or one node (the most specific wins), delivered to the module's runners, doctor and services on each node | `node`: core 2.9 |
   | `required` | `false` | The owner must set it. Until a value exists, the module's work waits with `SETTINGS_NOT_SET` and its readiness checklist shows **Required settings**. A required key has no `default`. The schema's own `required` list counts too | core 2.9 |
   | `unit` | none | A short word shown beside the value (`GB`, `s`, `items`) | |
   | `advanced` | `false` | Shown under the module's Advanced disclosure | |
   | `campaign` | `false` | A campaign of the module may override it while it runs ([Campaign overrides](#campaign-overrides)) | core 2.9 |

4. Check it:

   ```sh
   oarbank-sdk check .        # refuses a schema the core would not read
   ```

   `oarbank-sdk check`, `bundle build` and `bundle verify` refuse an invalid schema (an unknown `x-oarbank` field, an
   invalid default, a required key with a default, a bad name), so an installed bundle always registers. `check` warns
   about a property named like a core key (`enabled`, `pipeline`, `replica_rate`, `services`) or like a credential.

**Credentials are never settings.** Settings are stored and shown in clear. A property with `writeOnly`, `x-secret` or
`x-oarbank.secret` is refused, and a name that looks like a credential (`token`, `password`, `api_key`, …) gets a
warning. Declare credentials in `[[secrets]]` ([Use secrets and signed container images](secrets-and-images.md)).

## Read the values

**On a node.** A runner, `doctor` and services get the module's `node` keys resolved for their node: the owner's value
for the fleet, a group or the node, else the key's default (a key with neither is absent), and no `fleet` keys, in
`OARBANK_SETTINGS_FILE`:

```python
from oarbank_sdk import settings
mem = settings.read().get("vm_mem_gb", 8)      # {} outside a node process, or when the file is missing
```

Bootstrap jobs get `{}`. A change to another module's settings never touches your file, and a change to yours reaches
the node with its next heartbeat.

**On the coordinator.** The module's coordinator side gets every key at fleet scope the same way: `ctx.settings` in a
verb (from `initialize`), or `host.settings.get`:

```python
threshold = ctx.settings.get("batch_size", 64)
```

**From the module's own operations.** An operation changes the module's settings at fleet scope with the
`module_settings.update` effect, `{key: value}`, where `null` resets a key to inherit. Each key must be declared and
each value valid, or the whole effect is refused (`bad_settings`):

```python
from oarbank_sdk import module_protocol as mp
return mp.OpApplyResult(effects=[mp.Effect(kind="module_settings.update", args={"batch_size": 128})])
```

The toy module's **Set the favorite n** operation does exactly this ([examples/toy](../examples/toy)).

The owner sets values in the module's **Settings** tab, or with
`oarbank settings set vm_mem_gb 12 --module <module> [--node N | --group G]`
([Settings](https://docs.codonic.dev/oarbank/operate/settings#module-settings)).

## Campaign overrides

A key marked `"x-oarbank": {"campaign": true}` may be overridden by a campaign of the module while that campaign runs:
by the module itself, or by the owner on the campaign's page. The campaign's value reaches that campaign's jobs only.

1. Check that the host supports it (core 2.9), if the module also runs on older cores:

   ```python
   if ctx.host_has(mp.HOST_CAMPAIGN_SETTINGS):
       ...
   ```

2. Set overrides when you create the campaign, or change them later (`None` resets a key):

   ```python
   from oarbank_sdk import effects as fx
   fx.campaign_create("c_big", "Big batches", settings={"batch_size": 1024})
   fx.campaign_settings("c_big", {"batch_size": None})
   ```

   Besides its own campaign keys, a campaign may override the core keys `replica_rate`, `jobs`, `run_on_battery` and
   `user_present_slots`, which may only tighten (a value looser than the fleet's is refused). A key the owner locked,
   an undeclared key or an invalid value refuses the whole effect (`bad_settings`).

3. Read them: `campaign.tick` carries `campaign.settings`, every key at fleet scope with the campaign's overrides on
   top. A campaign's override of a `node` key reaches its jobs' runners in `OARBANK_SETTINGS_FILE`.

The host capability is `campaign_settings.v1` ([module-protocol.md](../spec/module-protocol.md)).

## New versions and old values

The core reads the schema of the module's current version (before it is enabled, the newest installed one):

- a key a new version no longer declares keeps its values, unused, until the owner deletes them or a later version
  declares it again;
- a value the new version's schema no longer accepts (its type or range changed, or a `node` key became `fleet` while a
  value was set below the fleet) is dropped, and the module's event names it.

So change a key's type or meaning under a new name.

## Test them

In `conformance.json`, `settings` holds the owner's values. The kit checks them against your keys, gives runs, `doctor`
and services the `node` keys (a fixture value, else the default) in `OARBANK_SETTINGS_FILE`, and gives the coordinator
side every key ([conformance.md](../spec/conformance.md#fixtures)):

```json
{"settings": {"vm_mem_gb": 4, "upstream_url": "https://feed.example.org/v1"}}
```

## Move a module from SDK 1.5

Before core 2.9 the core stored a module's settings as one opaque object. To move a module:

1. Declare every key the module reads or writes in its settings schema.
2. Mark the keys its runners or services need on a node `"x-oarbank": {"scope": "node"}`: only those reach
   `OARBANK_SETTINGS_FILE`.
3. Move any credential to `[[secrets]]`.
4. Drop a `pipeline` property: the core's own `pipeline` setting replaces it.
5. Set `requires.core = ">=2.9,<3"` if you use `scope = "node"`, `required` or `campaign`.

An undeclared key in `module_settings.update` is refused. When a fleet upgrades to core 2.9, the values its owner had
set are kept only for keys the module declares and whose schema accepts them.
