# Project status

Updated 2026-09-06 for the 0.12 release candidate.

Contexture has one shipped implementation: the Python package
`contexture-mcp`. Its current architecture is register → compile → disclose,
with an optional shared execution runtime behind MCP and explicit REST Host
surfaces.

## Release candidate scope

- Python 3.11–3.14 support and bounded compatible dependencies.
- A snapshotted top-level authoring API and advanced `contexture.server` API.
- Runtime MCP, independent disclosure-only MCP, Prompt-only roots,
  request-selected complete root surfaces, and explicit REST routes.
- English and Simplified Chinese README and onboarding handbook.
- Language-neutral model, conformance rules, fixtures, and golden MCP outputs.
- CI, distribution consumption tests, and OIDC Trusted Publishing workflows.
- Changelog, security policy, contribution guide, and release runbook.

## Required before stable 0.12.0

1. Publish `0.12.0rc1` to TestPyPI through the `testpypi` environment and run
   the clean-install/scaffold checks in `RELEASING.md`.
2. Publish the same candidate to PyPI through the protected `pypi` environment;
   this first successful upload is what reserves `contexture-mcp`.
3. Re-run and record Claude Code and Codex against the current four-tool
   gateway, including Prompt-only and selected-root behavior where supported.
4. Collect candidate feedback, fix with a new immutable candidate version when
   necessary, then repeat the release gate for `0.12.0`.

These steps require maintainer accounts or external Host sessions. They are not
replaced by local unit tests.

## Deliberately deferred

- TypeScript, Go, and PHP packages. Their compatibility shape is documented,
  but no unmaintained placeholder package will be published.
- A hosted documentation site; repository documentation is the current source.
- Translation of historical ADRs. ADRs preserve decision history; current user
  paths are bilingual.
- Full style-only Ruff normalization. Release-blocking correctness rules are
  enabled; broader formatting can move incrementally.
- A 1.0 stability promise or complex governance process before public candidate
  feedback exists.

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
