# ADR 020 — Path selectors promote complete subtrees

## Decision

Contexture 0.13 generalizes request-selected roots into request-selected
surfaces. A selector is segment-aware and anchored to a canonical Contexture
ref:

```text
team/notebook-editor   # this node and its complete subtree
team/*                 # each direct member of team, as a complete subtree
```

The only pattern operator is `*` as the complete final segment. It matches one
segment and never `/`. Contexture does not accept partial-segment wildcards,
recursive `**`, regular expressions, or negation. This deliberately small
grammar is readable in configuration, portable across language bindings, and
cannot change meaning merely because a regex engine differs.

Every resolved selector becomes a root of the projected surface while keeping
its canonical ref. If `team/notebook-editor` is selected, discovery returns
that node directly. `team`, its instructions, and its other members are not
disclosed. Opening and invoking the selected subtree continue to use the
canonical `team/notebook-editor/...` addresses.

Resolution occurs against the immutable Index. A wildcard is expanded in Index
order, a selector that matches nothing is rejected, and overlapping roots are
reduced to an antichain: selecting `team` makes a simultaneous
`team/notebook-editor` redundant. Intersections are containment-aware and keep
the narrower subtree.

The HTTP adapter uses `Contexture-Select`. `Contexture-Roots` remains a legacy
spelling accepted by the same adapter. A request carrying both is invalid.
`SurfaceSelection`, `HeaderSurfaceSelector`, `FixedSurfaceSelector`, and the
`surface_selector=` server argument are the new public names; the 0.11/0.12
Root-prefixed names and `root_selector=` remain compatible.

## Scope and authority

The effective selection governs dynamic instructions, discover, open, both
invoke doors, Prompts, Resources, completion, lookup refusals, `uses` cards,
and `current_graph()`. A promoted surface root has no visible parent in the
selected graph.

A caller-provided selector remains attenuation, not authorization. An
application-owned ceiling derived from a verified Principal is resolved through
the same model and intersected with the request. Business Tools still authorize
their data and effects.

## Rejected alternatives

- Regular expressions expose engine-specific syntax, make review difficult,
  and allow a future ref to broaden a pattern in ways that are hard to see.
- Globstar `**` is unnecessary for the first deep-selection use case because
  selecting a node already includes its complete subtree.
- Ancestor navigation shells either leak parent instructions or introduce a
  second partial Role representation. Promoting selected refs is smaller and
  preserves the existing two-level compile lifecycle.

## Compatibility

Selecting top-level refs produces the same surface and declaration order as
0.11/0.12. Missing headers still select the full application. Existing
Root-prefixed Python APIs and `Contexture-Roots` clients continue to work.

This decision supersedes ADR 019's prohibition on descendant selection; its
authority and whole-subtree guarantees remain in force.
