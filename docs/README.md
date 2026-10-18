# Documentation

[简体中文](README.zh-CN.md) · [Project README](../README.md)

## Start here

| Your question | Start here |
| --- | --- |
| New to Contexture? | [Quickstart](../README.md#quickstart), then the [application tutorial](handbook.md) |
| Want to see it first? | [Two-minute video](overview.md) |
| Can I try it without infrastructure? | [Built-in deterministic incident demo](../contexture/demo/README.md) |

## Tutorials — learn by doing

The [application handbook](handbook.md) is the canonical end-to-end tutorial:
install → generate → inspect → call → extend → connect a host.

- [Understand the generated application](handbook.md#3-understand-the-generated-application).
- [Make your first change](handbook.md#5-make-your-first-change).
- [Add capabilities](handbook.md#6-add-capabilities-in-the-right-place).

## How-to guides — complete a task

- [Authoring](guides/authoring.md): complete Python declaration, vocabulary,
  development commands, and public API boundaries.
- [Hosting](guides/hosting.md): Claude Code / Codex, MCP gateways, HTTP, and REST.
- [External dependencies](handbook.md#7-connect-external-systems-only-when-needed): Channels and lifecycle.
- [Prompts and resources](handbook.md#8-publish-optional-entrances): additional entrances to existing capabilities.
- [Safety and troubleshooting](handbook.md#10-local-safety-and-troubleshooting).

## Explanation — understand the choices

- [Design and trade-offs](design.md): responsibility-based composition,
  progressive disclosure, alternatives, and when to choose Contexture.
- [Video and transcript](overview.md): the implemented interaction model.
- [Roadmap](roadmap.md): planned visualization and capability-level evaluation.

## Reference — look up a contract

- [Language-neutral specification](../spec/README.md): normative model,
  conformance rules, fixtures, and golden outputs.
- [Changelog](../CHANGELOG.md): versioned changes.

## Maintainers and contributors

- [Contribution guide](../CONTRIBUTING.md): boundaries and required checks.
- [Security policy](../SECURITY.md): private vulnerability reporting.
- [Release runbook](../RELEASING.md): packaging and publishing procedures.
- [Project status](project-status.md): dated release preparation record, not a
  substitute for checking the installed package or current release evidence.

### Design history and verification

[ADRs](adr/) and numbered design/execution-plan documents preserve why past
decisions were made. Read their dates and versions; proposals and old diagrams
are not necessarily the current API. Use the handbook and specification for
current behavior rather than silently rewriting historical documents.

- [Controller framework and MVC](05-controller-framework-and-mvc.md): architecture rationale.
- [Historical Atlas](atlas/index.html): v0.5 visual architecture, not a current 1.0 reference.
- [Host verification](verification/hosts.md): version-specific historical runs
  and their limits; not evidence that every current host/version was tested.
