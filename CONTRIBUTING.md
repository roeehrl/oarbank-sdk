# Contributing

Contributions are welcome under the Apache License 2.0.

## Developer Certificate of Origin

Every commit must be signed off, which certifies the [Developer Certificate of Origin 1.1](https://developercertificate.org/): you wrote the change, or otherwise have the right to submit it under this project's license.

```
git commit -s -m "your message"
```

This adds a `Signed-off-by: Name <email>` trailer. CI rejects commits without one.

## Contract changes

Anything under `spec/` or `schemas/`, and any Pydantic model they are generated from, is a **public contract**. Changes must follow [spec/versioning.md](spec/versioning.md):
- additive only within a major;
- no reused or retyped fields;
- stability labels on everything new;
- a schema-compatibility check against the previous release.
