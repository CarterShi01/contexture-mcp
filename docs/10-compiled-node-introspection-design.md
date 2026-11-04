# Design 10 — Compiled Node Introspection and the Node Catalog

**Status:** accepted; Python v1 and One Creator integration implemented

**Date:** 2026-11-04

**Audience:** framework maintainers, binding authors, application integrators,
developer-tool authors

## 0. Decision

Contexture will provide a versioned, read-only introspection contract over a
compiled application. The contract is called the **Node Catalog**. It gives a
programmatic consumer one stable way to enumerate and describe Roles, Skills,
and Tools without replaying progressive disclosure or reaching into Python
implementation objects.

The Node Catalog is a graph-level projection, not a `Role.to_dict()` method and
not a new disclosure level:

```text
ContextNode        facts intrinsic to one declaration
Index              ref, parentage, containment, dependencies, binding
Disclosure         surface visibility, presentation, active payload
CompiledApplication       application kind and publications
        │
        └── NodeCatalog   stable read-only integration contract
                ├── in-process query API
                ├── deterministic JSON snapshot
                ├── optional HTTP/debug service
                └── human developer inspector
```

The framework does not prescribe where declaration data is stored and does not
make compiled nodes editable. Python constructors, YAML, Markdown, generated
classes, and databases may all remain valid application-owned declaration
sources. Editing and publication remain application concerns.

## 1. Problem

Contexture already has all the facts required to explain a compiled
application, but no stable integration contract that assembles them:

- a `ContextNode` knows its kind, name, routing description, authored fields,
  and declared `uses`;
- `Index` knows canonical refs, parents, children, reverse dependencies, and
  Tool bindings;
- `Disclosure` knows model visibility, localized presentation, and the exact
  active payload;
- a compiled application knows whether execution is bound, which roots belong
  to the model or Prompt plane, and which publications exist;
- `contexture.inspection` can replay what an agent receives, but is deliberately
  a disclosure trace rather than a complete graph description.

Without a public catalog, every application that needs a human architecture
view, a capability search index, a CI graph diff, or a developer inspector must
walk these objects itself. Those project-local exporters then make subtly
different decisions about process members, visibility, localized copy,
instructions, Tool schemas, ordering, and digest generation.

This duplication is not merely inconvenient. It creates parallel contracts
that can drift from the graph the framework actually serves.

## 2. Goals

The design must:

1. expose every compiled Role, Skill, and Tool through one typed API;
2. preserve canonical refs and declaration order;
3. distinguish intrinsic declaration text from framework-composed active text;
4. expose Tool execution metadata only when a binding exists;
5. represent containment and `uses` as separate graph relations;
6. support complete application, model-visible, and selected-surface views;
7. resolve presentation through the same locale rules as the MCP server;
8. serialize deterministically with a portable content digest;
9. work for runtime and disclosure-only applications;
10. let applications attach namespaced, JSON-safe extension metadata without
    changing Contexture's closed node-kind model;
11. power CLI, CI, HTTP, and a developer UI from the same projection;
12. add no business Tool, Channel lifecycle, invocation, or model context cost.

## 3. Non-goals

The Node Catalog does not:

- define an authoring file format;
- rewrite Python source, YAML, Markdown, or database rows;
- mutate a compiled `Index`;
- replace `contexture_discover`, `contexture_inspect`, or `contexture_open`;
- add a sixth model-controlled gateway Tool;
- expose Tool handler source code, Channel objects, credentials, or invocation
  results;
- become an authorization policy or a capability grant;
- merge independent Runtime and Architecture applications into one graph;
- define application vocabulary such as owner, provider, deployment unit, or
  source authority;
- promise that a valid graph produces correct model behavior.

## 4. Why the complete contract does not belong on `ContextNode`

Introspection is a fundamental framework capability, but complete node metadata
is not a node-local capability.

A node can truthfully report only intrinsic facts. Before registration it does
not know:

- its canonical ref;
- its parent or sibling order;
- reverse dependents;
- whether it belongs to the model or Prompt plane;
- whether a SurfaceSelection includes it;
- which locale presentation applies;
- whether a Tool has an executable binding;
- the schema derived by that binding.

Putting `meta()` or `to_dict()` on `Role` would therefore either return an
incomplete object or make the node reach upward into `Index`, `Disclosure`, and
server layers. Both outcomes violate the existing declaration/compilation/
disclosure boundaries.

Concrete node classes may expose a small internal contribution method for
kind-specific intrinsic facts if that removes a type switch. The public,
canonical descriptor is nevertheless assembled by `NodeCatalog`, against one
compiled graph.

## 5. Vocabulary

### 5.1 Node descriptor

A **NodeDescriptor** is the stable, complete description of one node in one
catalog view. It is not the same object as a routing card or active payload.

### 5.2 Catalog view

A **CatalogView** defines which compiled nodes are included:

- `application`: every node in the compiled Index, including Prompt roots;
- `model`: nodes visible to the model plane before request selection;
- `surface`: nodes visible after applying an explicit SurfaceSelection.

The caller must choose the view explicitly. Developer tooling normally asks
for `application`; product UI that claims to show what an agent can navigate
normally asks for `model` or `surface`.

### 5.3 Detail level

Two detail levels prevent list and search operations from carrying every
procedure and JSON Schema:

- `summary`: identity, routing copy, containment, dependency, and presentation;
- `definition`: summary plus Role/Skill instructions, process relations, and
  Tool execution contract.

A snapshot may contain either level and records the chosen level in its
manifest. A consumer must not infer that an omitted definition field is empty.

### 5.4 Catalog snapshot

A **NodeCatalogSnapshot** is a deterministic, serializable value containing an
application manifest, roots, counts, and a flat ordered list of descriptors.
It is safe to store as a build artifact and compare in CI.

## 6. Public in-process API

The Python API is:

```python
from contexture.introspection import CatalogDetail, CatalogView, NodeCatalog

catalog = NodeCatalog.of(
    compiled,
    view=CatalogView.MODEL,
    detail=CatalogDetail.DEFINITION,
    locale="zh-CN",
)

roots = catalog.roots
architect = catalog.get("team/architect")
children = catalog.children("team")
matches = catalog.list(kind="skill", query="architecture")
snapshot = catalog.snapshot()
wire_json = snapshot.to_json()
```

Both `CompiledApplication` and `CompiledDisclosureApplication` also provide the
convenience method:

```python
catalog = compiled.catalog(
    view="model", detail="definition", locale="zh-CN"
)
```

`NodeCatalog.of` is the primary constructor so other bindings can implement the
same concept without copying a Python convenience method.

The query API is synchronous and side-effect free. Building it must not open
Channels, enter application lifecycle, run a Tool, read a Resource, or update
Telemetry.

## 7. Canonical JSON contract

The first wire form is `contexture.node-catalog/v1`:

```json
{
  "schema": "contexture.node-catalog/v1",
  "content_digest": "sha256:...",
  "application": {
    "name": "one-creator",
    "mode": "runtime",
    "view": "model",
    "detail": "definition",
    "locale": "zh-CN",
    "execution_bound": true
  },
  "roots": ["project", "work", "team"],
  "counts": {"role": 80, "skill": 63, "tool": 368},
  "nodes": []
}
```

`nodes` is flat rather than recursively nested. Canonical refs already encode
containment, and a flat representation:

- addresses each node exactly once;
- represents `uses` without embedding or duplication;
- supports lookup, pagination, search, and graph rendering;
- produces localized diffs when one deep node changes;
- avoids recursive consumer limits;
- allows a tree to be projected from `parent_ref` and ordered `members`.

The array order is `Index.walk()` declaration order. Object-key order is not
semantic.

## 8. Node descriptor contract

Every descriptor contains:

```json
{
  "ref": "team/architect",
  "kind": "role",
  "name": "architect",
  "description": "Choose system boundaries and review architecture.",
  "presentation": {
    "title": "系统架构师",
    "description": "选择系统边界并审查架构。"
  },
  "plane": "model",
  "parent_ref": "team",
  "ordinal": 3,
  "members": [],
  "uses": [],
  "dependents": [],
  "role": null,
  "skill": null,
  "tool": null,
  "extensions": {}
}
```

### 8.1 Common fields

| Field | Meaning |
| --- | --- |
| `ref` | Canonical openable ref from the compiled Index. |
| `kind` | Closed value: `role`, `skill`, or `tool`. |
| `name` | Machine node name, unchanged by presentation. |
| `description` | Authored routing description. |
| `presentation` | Resolved human title and optional localized routing description. |
| `plane` | `model` or `prompt`; never inferred from visibility alone. |
| `parent_ref` | Containment parent or `null` for a root. |
| `ordinal` | Zero-based position in the parent's complete ordered member list, or root list. |
| `members` | Direct contained members with ref, kind, and semantic slot. |
| `uses` | Declared dependency refs in declaration order. |
| `dependents` | Reverse dependency refs in compiled order. |
| `extensions` | Namespaced application contributions; empty by default. |

Each entry in `members` is:

```json
{"ref": "team/architect/review", "kind": "skill", "slot": "skill"}
```

The Role slots are `pre_process`, `child`, `post_process`, `skill`, and `tool`.
This preserves process meaning that a bare child-ref list would lose.

### 8.2 Role definition

At `definition` detail, a Role descriptor adds:

```json
{
  "role": {
    "authored_instructions": "Review boundaries against declared evidence.",
    "effective_instructions": "<framework process contract plus authored text>",
    "pre_process_ref": null,
    "post_process_ref": null
  }
}
```

`authored_instructions` is the Role field owned by the application.
`effective_instructions` is the exact active instruction text produced by the
same compiler path used by `contexture_open`. They are separate because process
composition may make them differ.

### 8.3 Skill definition

At `definition` detail, a Skill descriptor adds:

```json
{
  "skill": {
    "authored_instructions": "Compare the contract and implementation.",
    "effective_instructions": "Compare the contract and implementation."
  }
}
```

The two fields remain distinct even when equal so a future framework-composed
Skill contract does not silently change the meaning of an existing field.

### 8.4 Tool definition

At `definition` detail, a Tool descriptor adds:

```json
{
  "tool": {
    "execution_available": true,
    "read_only": true,
    "input_schema": {"type": "object", "properties": {}}
  }
}
```

For a disclosure-only application:

```json
{
  "tool": {
    "execution_available": false
  }
}
```

`read_only` and `input_schema` are omitted rather than invented when no binding
exists. The catalog never imports or serializes the callable, handler source,
Channels, or credentials.

## 9. Declaration, presentation, and effective disclosure

The catalog keeps three questions separate:

1. **What did the application author?** `name`, `description`, authored
   instructions, containment, and `uses`.
2. **What did compilation derive?** refs, parentage, dependents, process slots,
   binding availability, and input schema.
3. **What does this surface present?** localized title/description, visibility,
   and effective active instructions.

The descriptor carries authored and effective instruction text separately and
does not overwrite machine identity with presentation.

For `surface` view, the same selection resolution and monotonic attenuation
used by the server must construct the catalog. A catalog implementation may not
reimplement wildcard matching or ancestor promotion.

## 10. Extension metadata

Contexture's standard fields are intentionally closed. Applications often need
additional read-model facts such as ownership, source authority, deployment
classification, or a Team runtime label. Those facts do not belong in the
framework vocabulary.

The catalog accepts explicit contributors:

```python
class NodeMetadataContributor(Protocol):
    namespace: str

    def describe(
        self, ref: str, node: ContextNode, graph: CompiledGraph
    ) -> JsonObject | None: ...
```

Usage:

```python
catalog = NodeCatalog.of(
    compiled,
    contributors=(ArchitectureSources(), TeamLabels()),
)
```

Output:

```json
{
  "extensions": {
    "one-creator/source": {
      "owner": "account",
      "classification": "authority",
      "source": "oc_app/modules/account"
    }
  }
}
```

Rules:

- namespaces must contain an owner prefix and `/`;
- duplicate namespaces are rejected;
- values must be JSON objects and pass recursive JSON-value validation;
- contributors are pure and receive no Channels or invocation API;
- contributor output participates in the content digest;
- framework disclosure never sends extensions to a model automatically;
- an HTTP host decides which contributor namespaces it is authorized to expose.

This extension seam is preferred over an open `metadata` map on
`ContextNode`. It keeps the node model language-portable, avoids making every
business fact part of declaration construction, and lets an independent
architecture application attach authority data by ref.

## 11. Determinism, digest, and versioning

The catalog is suitable for generated artifacts and cross-language consumers.
The v1 canonicalization contract is:

1. UTF-8 JSON;
2. object keys recursively sorted by Unicode code point;
3. array order preserved;
4. no timestamps, object ids, memory addresses, paths discovered from source
   inspection, or other ambient process values;
5. integral floating-point values canonicalized as integers;
6. compact separators for digest input;
7. SHA-256 over the complete document excluding `content_digest`;
8. lowercase `sha256:<hex>` output.

Any field addition, removal, or meaning change to the standard envelope
requires a schema revision. Additive application extension fields do not.

The language-neutral specification owns the JSON Schema, fixtures, canonical
digest vectors, and semantic rules. Python dataclasses are an implementation,
not the normative contract.

## 12. CLI

The framework adds a structural command distinct from the existing disclosure
trace:

```bash
contexture catalog --target package.module:app --view model --detail summary
contexture catalog --target package.module:app --view application \
  --detail definition --json
contexture catalog team/architect --detail definition --json
```

`contexture inspect` remains the answer to “what would an agent receive, step
by step, and what would it cost?”

`contexture catalog` answers “what nodes and relations did this application
compile?”

Neither command invokes business Tools. `catalog` never supports `--read`.

## 13. Developer service

The Python binding exposes the same catalog through a separate developer
service:

```bash
contexture debug --target package.module:app \
  --view application --host 127.0.0.1 --port 8787
```

The implemented read-only surface is deliberately small:

```text
GET /                         bundled inspector
GET /app.css
GET /app.js
GET /healthz
GET /api/catalog
GET /api/nodes/{url-encoded-ref}
HEAD /api/catalog
```

Catalog responses carry the catalog digest as an `ETag` and honor
`If-None-Match`. The bundled UI renders the complete containment forest,
searches the full descriptor, and displays node definitions, process members,
dependencies, extensions, and Tool schemas. The server has no mutation or
invocation route.

The developer service is not mounted into the application MCP endpoint and
does not alter the fixed gateway. This built-in adapter accepts only loopback
bindings, validates Host and Origin, and emits a restrictive CSP and browser
security headers. A remotely published catalog requires a separate,
application-owned authenticated adapter; the debug server cannot be switched
into a public mode.

## 14. Security and disclosure policy

Instructions and Tool schemas can contain internal operational detail even
when they contain no credentials. Therefore:

- the in-process API is trusted application code, not a remote authorization
  boundary;
- the JSON exporter writes only to an explicit destination;
- the debug server defaults to loopback and read-only;
- full `application` view is never exposed remotely by accident;
- a production Host must authenticate and authorize catalog access separately
  from MCP capability access;
- extension contributors must omit secrets and may be namespace-filtered;
- Tool defaults and schemas follow their existing declaration contract, but
  Tool callables, Channels, environment, and invocation results are excluded;
- catalog reads do not increment Role, Skill, open, inspect, or Tool telemetry.

## 15. Deferred diagnostics

Diagnostics are derived observations and do not change catalog validity. They
are intentionally outside Node Catalog v1 and the first debug adapter. A future
separate diagnostic report should reuse existing inspection checks where
applicable:

- routing description exceeds the advisory budget;
- routing text repeats held member names;
- localized presentation is missing when a requested locale falls back;
- a process member changes effective instructions;
- an extension contributor failed or returned non-JSON data;
- a catalog view excluded a declared `uses` target;
- application and model views differ because of Prompt roots or selection.

Diagnostics have stable codes, node refs, severity, and a human message. They
are excluded from the catalog digest unless explicitly embedded in a separate
diagnostic report, so adding a new advisory does not make the graph appear to
change.

## 16. Relationship to existing framework surfaces

| Existing surface | Purpose | Relationship to Node Catalog |
| --- | --- | --- |
| `Index` | Frozen compiled graph facts | Source of structural facts; remains lower-level. |
| `Disclosure` | Progressive model context | Source of visibility, presentation, and active payload. |
| `contexture_inspect` | Compare candidates without activation | Unchanged; never becomes a bulk catalog API. |
| `contexture.inspection` | Replay agent disclosure and estimate cost | Reused by debug trace; not replaced. |
| disclosure-only application | Independent business/architecture graph | Has its own catalog; not a debug view of Runtime. |
| Node Catalog | Stable human/system integration read model | New graph-level projection. |

## 17. Failure behavior

Catalog construction fails atomically when:

- a requested ref does not exist in the chosen view;
- a contributor namespace is invalid or duplicated;
- contributor output is not JSON-safe;
- presentation or selection cannot be resolved through the existing framework
  resolver;
- a bound Tool has no valid binding schema;
- serialization encounters a value outside the normative JSON contract.

The API does not silently drop a node, schema, extension, or relation. Querying
an unknown ref uses the same structured lookup facts as the compiled Index but
raises an introspection-facing error rather than rendering an agent recovery
sentence.

## 18. Conformance and tests

The normative specification must include:

- JSON Schema for `contexture.node-catalog/v1`;
- one runtime fixture and one disclosure-only fixture;
- one Prompt-root fixture;
- one localized presentation fixture;
- one selected-surface fixture;
- one process-member fixture showing authored/effective instructions;
- one extension contributor fixture;
- canonical JSON and digest golden files.

Python tests must prove:

- every `Index.walk()` node appears exactly once in application view;
- refs, parents, ordinals, slots, uses, and dependents match the Index;
- model and surface filtering reuse `Disclosure`/selection behavior;
- bound Tool schemas are byte-equivalent as JSON values to active cards;
- unbound Tools never invent execution metadata;
- catalog construction opens no Channels and invokes no Tool;
- repeated builds are byte-identical;
- `summary` omission cannot be confused with empty definition text;
- contributor failures are atomic;
- `contexture catalog` JSON contains no terminal commentary;
- existing discover/inspect/open golden payloads do not change.

Other language bindings implement the same fixtures and digest vectors before
claiming product parity.

## 19. Rejected alternatives

### 19.1 Put `to_dict()` on every Role, Skill, and Tool

Rejected because nodes do not know graph position, reverse dependencies,
selection, presentation, or binding schema.

### 19.2 Recursively serialize roots

Rejected as the canonical contract because it duplicates graph relations,
makes deep diffs noisy, and complicates lookup and dependency overlays. A tree
endpoint may remain a convenience projection.

### 19.3 Add a full-tree MCP gateway Tool

Rejected because it bypasses progressive disclosure, adds model context cost,
and mixes developer/system integration with the agent navigation surface.

### 19.4 Treat `contexture inspect --all --json` as the catalog

Rejected because an inspection trace describes a sequence of agent-visible
payloads and token costs. It deliberately omits facts the catalog must carry
and repeats context by step.

### 19.5 Require declaration text to live in separate data files

Rejected because storage format is orthogonal to compiled introspection.
Contexture should describe the compiled truth regardless of authoring source.

### 19.6 Add an arbitrary `metadata` mapping to `ContextNode`

Rejected for v1 because it turns application vocabulary into part of every
declaration object and is difficult to keep typed across bindings. Explicit,
namespaced contributors provide the needed extension point without weakening
the node model.

## 20. Delivery record

Completed in the Python/One Creator slice:

1. neutral schema, fixture selector, canonical golden, and digest contract;
2. immutable Python descriptors, snapshot, graph-semantic validation, and
   runtime/disclosure-only assemblers;
3. `CompiledApplication.catalog()` and
   `CompiledDisclosureApplication.catalog()`;
4. `contexture catalog`, conformance tests, and package typing;
5. One Creator exporter migration with a namespaced source contributor;
6. loopback-only HTTP service and bundled developer inspector.

The contract is language-neutral, but TypeScript and Go binding
implementations remain a separate opt-in extension. Their pinned MCP release
bindings still pass and no Node Catalog parity claim is made for them yet.

The read contract is the foundation. The developer UI is a consumer of it,
not the contract itself.

