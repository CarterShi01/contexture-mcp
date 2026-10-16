# Documentation

[简体中文](README.zh-CN.md) · [Project README](../README.md)

## Understand and try

| Your question | Start here |
| --- | --- |
| What is Contexture? | [Two-minute illustrated overview and transcript](overview.md) |
| Why one application model? When should I use it? | [Design, alternatives, and trade-offs](design.md) |
| Can I try it without infrastructure? | [Built-in deterministic incident demo](../contexture/demo/README.md) |
| How do I build and extend my own app? | [Application handbook](handbook.md) |
| What is planned next? | [Roadmap](roadmap.md) |

The handbook remains the step-by-step user path; the overview and design pages
explain the model without creating another competing tutorial. Python is the
reference implementation in this repository. Links to other implementations
do not by themselves establish identical versions or validation results.

## Reference and contribution

- [Language-neutral specification](../spec/README.md): normative model,
  conformance rules, fixtures, and golden outputs.
- [Changelog](../CHANGELOG.md): versioned changes.
- [Contribution guide](../CONTRIBUTING.md): boundaries and required checks.
- [Security policy](../SECURITY.md): private vulnerability reporting.
- [Release runbook](../RELEASING.md): packaging and publishing procedures.
- [Project status](project-status.md): dated release preparation record, not a
  substitute for checking the installed package or current release evidence.

## Design history and verification

[ADRs](adr/) and numbered design/execution-plan documents preserve why past
decisions were made. Read their dates and versions; proposals and old diagrams
are not necessarily the current API. Use the handbook and specification for
current behavior rather than silently rewriting historical documents.

- [Controller framework and MVC](05-controller-framework-and-mvc.md): architecture rationale.
- [Historical Atlas](atlas/index.html): v0.5 visual architecture, not a current 1.0 reference.
- [Host verification](verification/hosts.md): version-specific historical runs
  and their limits; not evidence that every current host/version was tested.
