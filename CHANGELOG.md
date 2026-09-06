# Changelog

All notable user-facing changes are recorded here. Contexture follows
[Semantic Versioning](https://semver.org/) while it is pre-1.0: minor releases
may contain documented breaking changes.

## [Unreleased]

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

[Unreleased]: https://github.com/CarterShi01/contexture-mcp/compare/v0.12.0rc1...HEAD
[0.12.0rc1]: https://github.com/CarterShi01/contexture-mcp/compare/v0.11.0...v0.12.0rc1
[0.11.0]: https://github.com/CarterShi01/contexture-mcp/compare/v0.10.0...v0.11.0
[0.10.0]: https://github.com/CarterShi01/contexture-mcp/compare/v0.9.0...v0.10.0
[0.9.0]: https://github.com/CarterShi01/contexture-mcp/compare/v0.8.0...v0.9.0
[0.8.0]: https://github.com/CarterShi01/contexture-mcp/compare/v0.6.0...v0.8.0
[0.6.0]: https://github.com/CarterShi01/contexture-mcp/releases/tag/v0.6.0
