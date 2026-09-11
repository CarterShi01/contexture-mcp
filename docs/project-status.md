# Project status

Updated 2026-09-11 for the coordinated Python, TypeScript, and Go 1.0 release.

All three implementations have complete evidence for every applicable Python
0.12 product-manifest row and the verified 0.13–0.16 incremental capabilities.
Their native conformance, package-consumer, CLI, MCP, REST, identity, selection,
lifecycle, inspection, and documentation gates are the release baseline.

The source trees are prepared as stable 1.0 candidates. This is not a claim
that registry publication has already happened: PyPI, npm, and the Go module
tag remain external release operations protected by maintainer accounts and
repository environments.

## 1.0 release scope

- Python 3.11–3.14 support and bounded compatible dependencies.
- A snapshotted top-level authoring API and advanced `contexture.server` API.
- Runtime MCP, independent disclosure-only MCP, Prompt-only roots,
  request-selected complete root surfaces, and explicit REST routes.
- English and Simplified Chinese README and onboarding handbook.
- Language-neutral model, conformance rules, fixtures, and golden MCP outputs.
- CI, external distribution consumers, and guarded registry workflows.
- Changelog, security policy, contribution guide, and release runbook.

## Required before publishing 1.0.0

1. Run the complete clean-tree gate in each repository and inspect the exact
   wheel, npm tarball, and Go module consumer outputs.
2. Record current real-Host verification from the exact 1.0.0 release artifacts;
   historical 0.12 Host runs do not satisfy this gate.
3. Publish `contexture-mcp==1.0.0` to TestPyPI and repeat the clean-install,
   scaffold, and CLI checks from `RELEASING.md`.
4. Confirm ownership or creation of the npm `contexture` scope, configure npm
   Trusted Publishing, and protect all three release environments.
5. Publish Python and npm from immutable `v1.0.0` tags, then create the Go
   `v1.0.0` module tag through its guarded workflow.
6. Resolve every public artifact from its registry or proxy and record the
   exact version, provenance, package contents, and smoke-test output.

These steps require maintainer accounts or external Host sessions. They are not
replaced by local unit tests.

Claude Code verification is recorded only for historical 0.12 binding
candidates. Current 1.0 Host verification, including the Codex row, remains an
external account-dependent check and must not be inferred from local unit tests.

## Deliberately deferred

- A PHP package; PHP remains a documented compatibility target only.
- A hosted documentation site; repository documentation is the current source.
- Translation of historical ADRs. ADRs preserve decision history; current user
  paths are bilingual.
- Full style-only Ruff normalization. Release-blocking correctness rules are
  enabled; broader formatting can move incrementally.
- A complex governance process beyond the compatibility and security policies
  required for the 1.x line.

## Sources of truth

- User path: `README.md`, `README.zh-CN.md`, and `docs/handbook*.md`
- Current changes: `CHANGELOG.md`
- Normative cross-language behavior: `spec/`
- Architectural history: `docs/adr/`
- Release operations: `RELEASING.md`
- Real Host evidence: `docs/verification/`

The removed root `HANDOFF.md` described a sequence beginning at 0.2 and mixed
completed migrations with open ideas. Its PyPI procedure now lives in
`RELEASING.md`; current unfinished work belongs in this file or an issue.
