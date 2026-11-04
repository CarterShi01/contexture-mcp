# ADR 024 — Stable machine identity and human presentation are separate

**Status:** accepted

**Date:** 2026-11-04

**Extends:** [ADR 001](001-native-mcp-server.md) and
[ADR 017](017-application-is-a-lazy-declaration.md).

## Context

The fixed gateway is intentionally named for machines:
`contexture_discover`, `contexture_inspect`, `contexture_open`, and the two
invoke doors. Those identifiers are compact, stable, globally recognisable,
and part of the protocol contract. They are poor interface copy. A person
watching an agent work needs to understand which product is being used, what
the next call intends to do, and whether that call is observational or may
change the world.

MCP already separates these concerns. A server and a Tool have programmatic
`name` fields and optional human-readable `title` fields. Tool annotations also
retain a title for older protocol consumers. A Host decides which field to
render. The Host may instead show the local connection key, the machine tool
name, or model-authored narration; a server cannot force that user interface.

There are three tempting but incorrect ways to bridge that client gap:

- rename the gateway per product, breaking the one stable protocol and every
  instruction, fixture, and integration that calls it;
- add a model-supplied `title` argument so one current client happens to render
  it, polluting schemas with presentation state and letting the model spoof UI;
- emit vendor-specific invocation status metadata from the portable core,
  making one Host's extension part of every binding's contract.

The product also has a granularity mismatch to respect. MCP Tool metadata is a
static list. A title can differ between application surfaces, but it cannot
truthfully change for each `ref` passed to the same gateway call. Dynamic
object-level narration belongs to a Host that understands Contexture payloads,
not to the generic MCP Tool declaration.

## Decision

`Contexture` accepts an immutable, SDK-independent `Presentation`:

```python
Presentation(
    title="Service Operations",
    description="Operate the production service safely.",
    gateway=GatewayPresentation(
        discover="Browse operations",
        inspect="Compare operations",
        open="Open an operation",
        invoke_read_only="Read service data",
        invoke="Run an operational action",
    ),
)
```

`title` and `description` describe the server to a person. When a
`GatewayPresentation` is supplied, its five values are non-empty human titles.
It carries English intent-based defaults for concise explicit adoption, while
omitting it preserves the stable 1.0 Tool metadata byte for byte.

Compilation carries the declaration unchanged to the Host adapter. The MCP
adapter publishes each gateway title in both the Tool `title` and
`annotations.title` fields and publishes the application title and description
in server information. It does not change Tool descriptions: those are the
framework-owned routing contract read by the model, while titles are short UI
copy read by a person.

The following remain framework-owned and cannot be configured through
presentation:

- the application `name` and five gateway Tool names;
- gateway descriptions and input schemas;
- read-only classification and refusal behaviour;
- the number, order, and availability of the runtime or disclosure gateways.

Presentation is selected once per `Contexture` application. Separate runtime,
architecture, administration, or other MCP surfaces may therefore use distinct
product copy. Root selection within one surface does not vary it: current MCP
Tool lists are static and all selected roots share the same gateway.

The abstraction has no MCP SDK import. Authoring and declaration remain lazy,
and another Host adapter may reuse the human application identity without
depending on MCP types.

## Product consequences

A supporting Host can now show calls such as “One Creator · Read data” instead
of exposing only `oc-app.contexture_invoke_read_only`. The writing door can use
explicit action language and remains distinguishable through the standard
read-only annotation. An architecture-only endpoint can say “Open One Creator
architecture” while exposing no execution door at all.

This is progressive enhancement, not a rendering guarantee. A Host that ignores
MCP titles will continue to show the client-configured server key and machine
tool name. Operators should still choose a clear connection key. Changing the
server-side title cannot rename an entry such as `context-mcp` in somebody
else's local configuration.

Object-specific labels and progress are separate concerns. A Host may render
the `ref` argument or returned routing card, and MCP progress can report long
work when the client requests it, but neither mechanism changes the static Tool
title. Icons and vendor lifecycle strings may be added later through explicit
adapter extensions; they are not smuggled into this portable contract.

## Alternatives considered

- **Make every business capability a top-level Tool.** Rejected: it abandons
  progressive disclosure and makes every session pay for the whole graph.
- **Allow arbitrary per-tool metadata dictionaries.** Rejected: it weakens the
  fixed gateway, leaks vendor concerns into declarations, and makes portability
  impossible to validate.
- **Put titles on `SystemTool`.** Rejected: `SystemTool` is the machine and model
  contract in the kernel. Product language belongs to the application and Host
  projection, not to navigation behaviour.
- **Add per-node display titles in the same change.** Rejected: node cards and
  top-level Tool-call UI solve different problems. Node names and descriptions
  already route payloads; expanding that contract should have its own evidence.

## Compatibility and evidence

Existing declarations receive no implicit presentation and retain the exact
1.0 golden Tool list, machine names, schemas, descriptions, ordering, calls,
and results. Applications opt in explicitly; their Tool list changes only by
populating the two already-standard title locations.

Application, runtime-surface, and disclosure-surface tests cover propagation,
validation, opt-in default titles, custom titles, server identity, and the unchanged
gateway inventory. The public model and conformance fixture make presentation
part of the portable contract while explicitly leaving rendering to the Host.

