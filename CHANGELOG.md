# Changelog

All notable user-facing changes are recorded here. Contexture follows
[Semantic Versioning](https://semver.org/). Beginning with 1.0, incompatible
public API changes require a new major version.

## [Unreleased]

### Fixed

- Strict managed journals now support local Windows NTFS with native file and
  directory-handle flushes, byte-range owner locking, and fail-closed owner
  and DACL validation. Reparse points, inherited journal-root permissions and
  allow entries for other principals remain refused.

## [2.1.1] - 2026-11-08

### Fixed

- `ManagedMCPServer` binds the verified bearer principal (`current_principal()`)
  for every managed Tool and publication call, as the static gateway does.
  Previously Host policies that resolve identity from the current principal
  refused every authenticated managed request over HTTP.

## [2.1.0] - 2026-11-08

### Added

- `ManagedMCPServer(selection_resolver=...)`: an optional trusted Host port
  returning request-scoped subtree anchors (for example from a
  `Contexture-Select` header) that are passed to `ManagedRuntime.request(
  selection=...)`. It only attenuates: runtime authorization still bounds every
  request, and a non-frozenset result is refused as `permission_denied`.

## [2.0.1] - 2026-11-08

### Fixed

- `ReleaseManager.wait()` returns `status="failed"` as soon as this instance's
  latest attempt for the awaited release ended without serving it (refused,
  failed, timed out or cancelled) and nothing is running or queued for it,
  instead of reporting `pending` only when the timeout expires. The cause is in
  `result.local.last_error` / `last_attempt`.
- Durable churn workload reads command receipts before closing the owner.

## [2.0.0] - 2026-11-08

The managed (dynamic, versioned) runtime becomes the 2.0 line. The static
`Contexture(...).compile()` API is unchanged; breaking changes affect the
managed graph APIs that were introduced as candidates after 1.1.

### Breaking

- Replay is registry-free: `apply_event`, `apply_facts`, `History.bootstrap`
  and `History.append` no longer accept `publication_contracts`. Registry
  evidence is indexed by exact binding descriptor (collection, mapping values
  or resolver) and is checked only for new candidates.
- Mutability: a locked node protects only its own surface; mutable members of
  a locked parent are fully editable; new members of a locked parent must be
  mutable. Locked nodes whose agent context changes are reported as
  `protected_dependents` (preview receipt field, part of admission impact).
- Granular `PresentationPut`/`PresentationRemoved` facts were removed.
- `diff.behavior_diff` and the `diff` alias were removed; `execution_diff` and
  `surface_diff` take ExecutionManifests / StructuralPlans.
- `GraphOwner` keeps no receipt copy and a bounded `history_window`; older
  revisions are rebuilt from the journal. `History(records)` verifies every link.
- The managed specification is accepted as the v1 baseline.

### Added

- `settle()` (public), `ManagedRuntime.graph_id`, `TypeHintBinding.call_contract` and
  `ManagedMCPServer.build(auth=...)`
  for hosts composing managed runtimes from static Tools.

- Added the candidate managed runtime for trees that change while agents use
  them: `ManagedGraphHost` (`contexture.server.managed_host`) composes a durable
  single-owner FileJournal, admitted idempotent graph transactions, replayable
  history with per-node history, stable-ID/execution/surface diffs and revert,
  explicit releases, and atomic hot swap where each request pins one immutable
  generation. `python_tool` registers an async Python handler as a binding; the
  managed schema ships with the package. See the
  [managed runtime guide](docs/guides/managed-runtime.md) and
  `examples/managed_dynamic_tree.py`.
- Added framework-level export/import and three-way merge for managed graphs:
  self-digested graph bundles (whole graph or subtrees with external refs),
  identity `preserve` (default) or `remap`, imports and merges committed as
  ordinary admitted transactions, conflict reports with explicit resolutions,
  and verified history bundles importable into a fresh store.
- Added the versioned, immutable `contexture.node-catalog/v1` read model over
  compiled Role, Skill, and Tool graphs, with deterministic JSON/digests,
  namespaced application contributors, `contexture catalog`, and common APIs
  on runtime and disclosure-only compiled applications.
- Added the loopback-only `contexture debug` inspector with a read-only HTTP
  API, ETag/304 support, Host/Origin validation, browser security headers, and
  no Tool invocation route.
- Added `ExplicitSchemaTool` for generated proxy adapters whose JSON Schema is
  owned by an external contract. The declared Draft 2020-12 schema is both
  disclosed and applied before invocation.

### Compatibility

- Node Catalog is an application/developer read model and does not change the
  fixed MCP gateway, progressive disclosure payloads, or the pinned 0.16
  cross-language release inventory. TypeScript and Go do not claim this
  post-1.1 extension until they implement its neutral corpus themselves.

## [1.1.0] - 2026-11-04

### Added

- Added SDK-independent `Presentation` and `GatewayPresentation` declarations
  for a human server title/description and five application-specific gateway
  titles.
- Added `NodePresentation` and `PresentationCatalog` for localized routing
  cards. Locale resolution uses exact tag, parent tag, then explicit default;
  all configured node refs are validated during compilation.
- When explicitly configured, MCP publishes gateway titles in both standard
  Tool `title` and compatibility `annotations.title` fields while preserving
  every machine name, schema, description, and read-only classification.
- Applications that omit Presentation retain the exact 1.0 MCP Tool metadata.

### Documentation

- Accepted [ADR 024](docs/adr/024-machine-identity-and-human-presentation.md),
  including the static-per-surface and Host-rendering boundaries.
- Accepted [ADR 025](docs/adr/025-localized-node-presentation.md), defining
  per-server locale selection and the boundary between presentation copy and
  semantic instructions.

## [1.0.0] - 2026-09-11

### Stable release

- Establish the first coordinated stable Contexture release for Python,
  TypeScript, and Go from the verified 0.16 cross-language baseline.
- Freeze the documented declaration, inspection, CLI, MCP, REST, identity,
  selection, lifecycle, and telemetry surfaces as the 1.x compatibility
  contract.
- Publish Python as `contexture-mcp`; the sibling release workflows publish
  `@contexture/mcp` and `github.com/CarterShi01/contexture-mcp-go` at the same
  semantic version.
- Remove the retired validation-only case study and its parity-ledger
  exceptions from the release repository.

## [0.16.0] - 2026-09-10

### Added

- Added optional `Role.pre_process` and `Role.post_process` members, each a
  specialized Role with ordinary containment, selection, Channels, and Tool
  Binding behavior.
- Added the public `binding_instruction(source, body, *, action=None)` helper
  for application-owned hard rules that cannot be enforced in code.
- Framework process obligations now use fixed head and tail markers and a
  separate `>>> REQUIRED:` action line.

### Changed

- Replaced the framework-level `Publication` concept and `publication=` slot
  with `PostProcess` / `post_process=`. No compatibility alias is provided.
- PreProcess contracts are composed before unchanged business instructions and
  explicitly return the Agent to owner work; PostProcess contracts remain after
  business instructions and preserve truthful failure and approval reporting.
- Accepted [ADR 023](docs/adr/023-process-members-and-instruction-emphasis.md),
  superseding ADR 021.

### Compatibility

- Applications without process members retain exact ROUTE and ACTIVE output.
  Process members do not add callbacks, workflow state, or execution guarantees;
  only explicit Tool calls have effects.
- Python 0.16.0 is a breaking pre-1.0 release for applications using the old
  `Publication` name or `publication` payload field. No TypeScript or Go parity
  is claimed by this release.

## [0.15.0] - 2026-09-08

### Added

- Added the non-activating `INSPECT` disclosure level and read-only
  `contexture_inspect(refs)` gateway for comparing one to 32 candidate nodes.
- INSPECT returns only existing descriptions as pure routing cards for each
  target, its direct members, and declared uses. It adds no authored details
  field and discloses no instructions or execution facets.
- Added separate inspection telemetry so candidate evaluation is not counted
  as ACTIVE Role or Skill use.

### Changed

- The fixed runtime gateway now contains five Tools; disclosure-only surfaces
  contain discover, inspect, and open. Existing ROUTE and ACTIVE payloads are
  unchanged.
- Accepted [ADR 022](docs/adr/022-inspect-is-a-non-activating-structural-view.md).

## [0.14.0] - 2026-09-08

### Added

- `Publication`, a specialized Role with the inherited constructor and ordinary
  Role cards, and optional `Role.publication` for a constructed finishing
  procedure with dedicated capabilities or shared Tool `uses`.
- ACTIVE owners designate the Publication with a ref string and append a
  framework contract to open it before finishing and follow its procedure with
  results and evidence. Business `Role.instructions` remains unchanged;
  Publication details stay hidden until opened.

### Compatibility

- No Publication means exact existing ROUTE/ACTIVE output and no extra
  obligations. Publications use ordinary validated containment and
  complete-subtree selection, not alternative work branches or automatic
  containment-tree inheritance.
- The four gateways, Tool-only execution, approval boundaries, and existing
  Prompt/Resource exposure pointers are unchanged. No automatic execution or
  guarantee of external Agent compliance is introduced.

### Documentation

- Accepted [ADR 021](docs/adr/021-role-publication-and-instruction-composition.md)
  and documented authoring, instruction composition, and the normative optional
  Role member contract.
- Publication extension evidence is in `tests/test_publication.py` and
  `tests/test_publication_integration.py`,
  separately from the unchanged 16 legacy conformance rules and 0.12
  fixture/golden inventories. No TypeScript or Go parity claim is added.

## [0.13.0] - 2026-09-07

### Added

- Added segment-aware path selection through `SurfaceSelection`,
  `HeaderSurfaceSelector`, the `surface_selector=` server argument, and
  `Contexture-Select`. Exact deep refs select a complete subtree, while a
  terminal `/*` expands only direct members.
- Selected descendants are promoted to surface roots without disclosing their
  ancestors or siblings. Path-aware ceilings and intersections keep the
  narrower overlapping subtree.

### Changed

- Instructions, discovery, navigation, invocation, MCP publications,
  completion, errors, dependency cards, and invocation graph introspection now
  share the generalized path-selected surface.
- `RootSelection`, the Root-prefixed selector classes, and
  `Contexture-Roots` remain compatibility aliases for 0.11/0.12 applications.

### Documentation

- Link the guarded TypeScript and Go binding scaffolds from the English,
  Simplified Chinese, and language-neutral entry points.

## [0.12.0rc1] - 2026-09-06

### Added

- English and Simplified Chinese README and onboarding handbook.
- CI on Python 3.11–3.14, strict distribution checks, an installed-wheel type
  consumer, and public API snapshots.
- TestPyPI and PyPI Trusted Publishing workflows with OIDC attestations.
- Contributor, security, and reproducible release guidance.

### Changed

- Python 3.11 is now the minimum supported version; Python 3.10 is no longer
  supported.
- Runtime dependencies are bounded to compatible major versions of AnyIO,
  `mcp`, and `mcp-types`; the Python 3.10-only `tomli` dependency was removed.
- The documented public authoring and server APIs are narrower. Low-level
  compiler, surface, binding, and gateway objects remain implementation detail.
- Package metadata now uses SPDX/PEP 639 licensing and points to the current
  repository.

### Fixed

- The shipped type contract now models each Tool's custom `invoke()` signature
  correctly and matches runtime exports.
- `LICENSE` is included in both the wheel and source distribution.

## [0.11.0] - 2026-09-06

- Added request-selected complete root surfaces for HTTP deployments.

## [0.10.0] - 2026-09-05

- Added prompt-only roots, separating user-controlled command trees from model
  discovery.

## [0.9.0] - 2026-08-29

- Published the MCP 2.x compatibility contract and dependency boundary.

## [0.8.0] - 2026-08-29

- Added the shared REST Controller surface, runtime graph introspection,
  telemetry, and MCP 2.0 permission refusal handling.

## [0.6.0] - 2026-08-21

- Established the application-first authoring path and current
  register–compile–disclose architecture.

[Unreleased]: https://github.com/CarterShi01/contexture-mcp/compare/v2.1.1...HEAD
[2.1.1]: https://github.com/CarterShi01/contexture-mcp/compare/v2.1.0...v2.1.1
[2.1.0]: https://github.com/CarterShi01/contexture-mcp/compare/v2.0.1...v2.1.0
[2.0.1]: https://github.com/CarterShi01/contexture-mcp/compare/v2.0.0...v2.0.1
[2.0.0]: https://github.com/CarterShi01/contexture-mcp/compare/v1.1.0...v2.0.0
[1.1.0]: https://github.com/CarterShi01/contexture-mcp/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/CarterShi01/contexture-mcp/compare/v0.11.0...v1.0.0
[0.16.0]: https://github.com/CarterShi01/contexture-mcp/compare/v0.15.0...v0.16.0
[0.15.0]: https://github.com/CarterShi01/contexture-mcp/compare/v0.14.0...v0.15.0
[0.14.0]: https://github.com/CarterShi01/contexture-mcp/compare/v0.13.0...v0.14.0
[0.13.0]: https://github.com/CarterShi01/contexture-mcp/compare/v0.12.0rc1...v0.13.0
[0.12.0rc1]: https://github.com/CarterShi01/contexture-mcp/compare/v0.11.0...v0.12.0rc1
[0.11.0]: https://github.com/CarterShi01/contexture-mcp/compare/v0.10.0...v0.11.0
[0.10.0]: https://github.com/CarterShi01/contexture-mcp/compare/v0.9.0...v0.10.0
[0.9.0]: https://github.com/CarterShi01/contexture-mcp/compare/v0.8.0...v0.9.0
[0.8.0]: https://github.com/CarterShi01/contexture-mcp/compare/v0.6.0...v0.8.0
[0.6.0]: https://github.com/CarterShi01/contexture-mcp/releases/tag/v0.6.0
