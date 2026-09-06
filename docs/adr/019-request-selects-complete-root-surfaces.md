# ADR 019 — Requests select complete root surfaces

## Decision

One compiled Contexture application may expose a different exact allowlist of
top-level roots to each request. `RootSelection` is an immutable Disclosure
constraint over the canonical Index. Selecting a root selects its complete
subtree; descendants are never matched independently.

The HTTP adapter is opt-in:

```python
from contexture.server import HeaderRootSelector, compile_application

server = compile_application(app).server(
    root_selector=HeaderRootSelector(),
)
```

A client sends `Contexture-Roots: team,work`. A missing header preserves the
all-roots compatibility surface. Empty, descendant, and unknown selections are
rejected. Exact names are used instead of regular expressions so a future root
cannot silently broaden an existing caller.

The same effective selection governs dynamic server instructions, discovery,
open, both invoke doors, Prompts, Resources, completion, lookup errors, and the
graph returned by `current_graph()`. The four fixed Contexture gateway Tools do
not vary. Request state is task-local and stateless; concurrent projections
share the Index without affecting one another.

## Authority

The header is caller input and only attenuates. Applications that need an
authorization boundary supply a `ceiling(Principal) -> RootSelection` to
`HeaderRootSelector`; Contexture intersects the requested selection with that
authenticated ceiling. Business Tools continue to authorize their own data and
effects.

## Compatibility

Servers that do not configure a root selector retain their prior instructions
and behavior byte-for-byte. This opt-in hosting capability is released in
0.11.0.
