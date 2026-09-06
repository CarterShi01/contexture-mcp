# Language binding notes

This page is non-normative. The normative contract is
[model.md](model.md), [conformance.md](conformance.md), fixtures, and golden
outputs.

| Binding | Public authoring surface | Tool-schema strategy |
| --- | --- | --- |
| Python (shipped) | top-level facade, base classes, `.pyi`, `py.typed` | type hints on `invoke()` |
| TypeScript (design target) | package export map, classes/interfaces, `.d.ts` | explicit schema object coupled to a typed handler |
| Go (design target) | exported structs and interfaces; implementation hidden under `internal/` | tagged input struct |
| PHP (design target) | Composer namespace, abstract classes/interfaces, PHPDoc/PHPStan | DTO or explicit schema object |

All bindings must preserve lazy Application construction, explicit identity,
Role/Skill/Tool semantics, ref grammar, Channels lifecycle, fixed gateway, and
the produced Tool schema. No binding is required to reproduce Python class
inheritance or reflection.

Bindings must also preserve ordinary versus Prompt-only roots, bound runtime
versus unbound disclosure-only compilation, monotonic path-selected surfaces,
and explicit REST publication when they offer that Host adapter. A binding may
omit REST and still conform to the MCP contract; it may not expose a
caller-supplied arbitrary Tool ref and call that equivalent to `RestSurface`.
