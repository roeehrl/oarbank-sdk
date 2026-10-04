// Repository files that the site publishes as pages. scripts/sync-content.mjs copies
// each one into src/content/docs/<dest> (gitignored) before every build, so the
// repo file stays the single source and GitHub readers see the same text.
//
// `src` and `dest` are repo-relative and site-content-relative, always with `/`.
// `title` overrides the file's H1; `description` and `type` are required by the
// content schema (src/content.config.ts).

export const PAGES = [
  {
    src: 'docs/tutorial.md',
    dest: 'build/tutorial.md',
    type: 'tutorial',
    title: 'Build a module in a day',
    description:
      'Build primes, a module that counts primes on the fleet’s nodes: manifest, coordinator side, runner, goldens, the conformance kit, a bundle and per-platform declarations.',
    order: 1,
  },
  {
    src: 'docs/secrets-and-images.md',
    dest: 'build/secrets-and-images.md',
    type: 'how-to',
    description:
      'Give one stage an API key that nothing else sees, approve hundreds of container task images by their signing key, and give containers the GPU.',
    order: 2,
  },
  {
    src: 'docs/service-endpoints.md',
    dest: 'build/service-endpoints.md',
    type: 'how-to',
    title: 'Serve jobs from a warm service',
    description:
      'Load a model once per node in an endpoint service and let every job on the node send it work, without anything listening: the manifest, the service, the job and the conformance kit.',
    order: 3,
  },
  {
    src: 'spec/public-surface.md',
    dest: 'spec/public-surface.md',
    type: 'spec',
    description:
      'Exactly what an Oarbank module may depend on, what stays internal to the core, and the guarantees the core gives every module.',
    order: 1,
  },
  {
    src: 'spec/versioning.md',
    dest: 'spec/versioning.md',
    type: 'spec',
    title: 'Versioning and deprecation',
    description:
      'How the public contracts are versioned and negotiated, what changes are additive, what the stability labels promise, and how deprecation and removal work.',
    order: 2,
  },
  {
    src: 'spec/platforms.md',
    dest: 'spec/platforms.md',
    type: 'spec',
    description:
      'Rules every contract shares across macOS, Linux and Windows: platform tokens, portable paths, encodings, canonical JSON, process containers, per-platform declarations and placement.',
    platforms: ['macos', 'linux', 'windows'],
    order: 3,
  },
  {
    src: 'spec/manifest.md',
    dest: 'spec/manifest.md',
    type: 'spec',
    description:
      'The oarbank-module.toml every bundle carries: identity, compatibility, entry points, stages, services, results, goldens, UI and the cross-field rules.',
    order: 4,
  },
  {
    src: 'spec/module-protocol.md',
    dest: 'spec/module-protocol.md',
    type: 'spec',
    description:
      'How the coordinator talks to a module’s coordinator side: newline-delimited JSON-RPC 2.0 over stdio, its lifecycle, verbs, host callbacks, effects and errors.',
    order: 5,
  },
  {
    src: 'spec/runner-protocol.md',
    dest: 'spec/runner-protocol.md',
    type: 'spec',
    description:
      'How the agent runs a module’s runner once per job attempt: argv, the fixed environment, the work directory, the control document, exit codes and doctor.',
    order: 6,
  },
  {
    src: 'spec/service-protocol.md',
    dest: 'spec/service-protocol.md',
    type: 'spec',
    description:
      'Long-lived node services and probes a module ships: the seven service operations, pools, ownership and probe fingerprints.',
    order: 7,
  },
  {
    src: 'spec/envelopes.md',
    dest: 'spec/envelopes.md',
    type: 'spec',
    description: 'The spec and result envelopes every job travels in: what the core reads and what belongs to the module.',
    order: 8,
  },
  {
    src: 'spec/bundles.md',
    dest: 'spec/bundles.md',
    type: 'spec',
    title: 'Bundles and the module lifecycle',
    description:
      'The .mfb bundle and its digest, dependencies, and the lifecycle from install and approval through canary, promote and rollback.',
    order: 9,
  },
  {
    src: 'spec/sandbox.md',
    dest: 'spec/sandbox.md',
    type: 'spec',
    title: 'Module sandbox',
    description:
      'What every module process may and may not reach on every platform, the [sandbox] grants an operator approves, and the container broker.',
    platforms: ['macos', 'linux', 'windows'],
    order: 10,
  },
  {
    src: 'spec/sandbox/backends/macos.md',
    dest: 'spec/sandbox-macos.md',
    type: 'spec',
    title: 'Sandbox on macOS',
    description: 'How macOS nodes enforce the module sandbox contract with Seatbelt: profiles, launch, verification and the mapping.',
    platforms: ['macos'],
    order: 11,
  },
  {
    src: 'spec/sandbox/backends/linux.md',
    dest: 'spec/sandbox-linux.md',
    type: 'spec',
    title: 'Sandbox on Linux',
    description:
      'How Linux nodes enforce the module sandbox contract with Landlock, seccomp and cgroup v2, and what each kernel version can enforce.',
    platforms: ['linux'],
    order: 12,
  },
  {
    src: 'spec/sandbox/backends/windows.md',
    dest: 'spec/sandbox-windows.md',
    type: 'spec',
    title: 'Sandbox on Windows',
    description:
      'How Windows nodes enforce the module sandbox contract with AppContainers in Job Objects and the elevated helper for the egress allowlist.',
    platforms: ['windows'],
    order: 13,
  },
  {
    src: 'spec/ui-contract.md',
    dest: 'spec/ui-contract.md',
    type: 'spec',
    description:
      'How a module defines console pages as data: placements, pages, data bindings, actions, forms, the sandboxed iframe and the tooling.',
    order: 14,
  },
  {
    src: 'spec/conformance.md',
    dest: 'spec/conformance.md',
    type: 'spec',
    description: 'The checks oarbank-sdk conform runs on a module before a host relies on it, and what it does not cover.',
    order: 15,
  },
  {
    src: 'spec/manifest-reference.md',
    dest: 'reference/manifest.md',
    type: 'reference',
    title: 'Manifest fields',
    description: 'Every oarbank-module.toml key with its type, default and description, generated from the SDK’s manifest models.',
    order: 1,
  },
];

/** Raw artefacts copied into public/ and served as-is: repo dir → published dir. */
export const RAW_DIRS = [
  { src: 'schemas', dest: 'schemas', match: /\.schema\.json$/ },
  { src: 'spec/vectors', dest: 'vectors', match: /\.json$/ },
];
