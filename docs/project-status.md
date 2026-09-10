# Project status

Updated 2026-09-10 after completion of the TypeScript and Go 0.12 parity
ledger.

Contexture has one shipped implementation: the Python package
`contexture-mcp`. Its current architecture is register → compile → disclose,
with an optional shared execution runtime behind MCP and explicit REST Host
surfaces.

The TypeScript and Go repositories now have complete evidence for every
applicable Python 0.12 product-manifest row. Their conformance metadata is
`conformant`, all 16 rules have execution evidence, external package/module
consumers pass, and Claude Code 2.1.133 completed the maintained MCP-only
diagnosis against both candidates on 2026-09-10. This is a parity statement,
not publication authorization: the npm package remains private and the Go
module remains untagged until maintainers approve their separate release
gates.

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

For the TypeScript and Go candidates, Claude Code verification is recorded and
green. Codex CLI 0.153.0 was available but the local account was not logged in,
so that row remains an external account blocker to repeat after maintainer
authentication; no inference was attempted and no product failure is claimed.

## Deliberately deferred

- Publishing TypeScript, Go, and PHP packages. TypeScript and Go 0.12 parity
  evidence is complete, but their public package/module releases remain
  separately guarded and unauthorized. PHP remains a documented compatibility
  target only.
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
