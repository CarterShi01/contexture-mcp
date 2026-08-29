# Contexture public model

This is the language-neutral contract for a Contexture application. Python,
TypeScript, Go, and PHP bindings may use different declaration syntax; they
must preserve these meanings.

## Application

An Application has a non-empty name, at least one root, optionally one shared
Channels handle, and optional Prompt and Resource declarations. It is a lazy
specification: importing or constructing it creates no node, connection,
Index, Disclosure, or server.

Each build creates a fresh forest, registers roots, derives bindings, validates
the complete forest, and produces an immutable Index.

## Nodes

| Node | Required facts | Meaning |
| --- | --- | --- |
| Role | name, description, instructions, optional uses refs | A stable responsibility boundary an agent can recognize, enter, and choose. It may hold child Roles, Skills, and Tools. |
| Skill | name, description, instructions, optional uses refs | Stable procedural knowledge the model follows; the framework does not execute it. |
| Tool | name, description, read_only, optional uses refs, typed input and invoke body | A deterministic capability the framework executes. |

Containment is a forest. Every node has one address. Any node may reference an
existing address through `uses`; references do not create containment or depth.
Roles describe responsibility, not data instances: a scheduler can be a Role,
but each scheduled job remains data returned by a Tool.

`Application` is a technical composition root. It is not a fourth semantic node
and does not appear in the object graph.

## One disclosure and one telemetry side channel

The compiled forest has one progressive Disclosure. Discover returns root
cards; opening one node returns its kind-specific definition, one level of
contained members, and any explicitly declared `uses` cards. Business and
system Roles use this identical path. Parent, members, uses, and reverse
dependents are compiled Index facts available to an explicit architecture Tool;
reverse edges are not injected into ordinary disclosure because doing so could
reveal sibling branches the caller has not entered.

Live health, connection, queue, version, instance, and business state are not
universal Node fields. The responsibility that owns such state exposes it
through an ordinary read-only Tool with its own domain semantics.

The runtime automatically records minimal usage telemetry beside disclosure:
`ref`, `call_count`, `error_count`, and `last_used_at`. Entering a Role or Skill
and invoking a Tool are calls; merely discovering cards is not. Telemetry never
changes disclosure output, and exporter failure must never change the observed
business call. An authorized Tool may query the current collector through
`current_telemetry()`.

During Tool invocation, `current_graph()` refers to the exact serving Index and
`current_telemetry()` refers to its runtime collector. This lets architecture
and telemetry Tools query their own application without maintaining a second
registry. Both bindings are local to the concurrent call and access outside a
Tool invocation is an error.

## Integration declarations

Channels owns application-wide external dependencies and an optional open/close
lifecycle. Prompt and Resource are not nodes: each names an existing node by
ref and creates a second entry point on its respective MCP primitive.

## Required behavior

- Node identity is explicit; names and descriptions are never inferred from
  class names or docstrings.
- Tool input schemas and Tool invocation validation come from one binding.
- A read-only Tool and a writing Tool use distinct gateway doors.
- The served surface cannot change after Index compilation.
- A Skill is opened; a Tool is invoked.
- Disclosure is progressive: opening a Role exposes one containment level.
- Every Host discloses the same immutable Index through the one Disclosure.
