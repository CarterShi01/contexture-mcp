# Contexture TypeScript and Go porting brief

## Outcome

Port the language-neutral Contexture 0.12 contract, not the Python syntax. No
normative behavior is inexpressible in TypeScript or Go. The work is still more
than a line-by-line translation because reflection, task-local state, lifecycle,
immutability, errors, and Host SDKs have language-native implementations.

The source of truth is, in descending order:

1. `spec/model.md` and `spec/conformance.md`;
2. `spec/fixtures/` and `spec/golden/`;
3. current Python behavior where the normative material is silent;
4. `spec/bindings.md` and ADRs as non-normative design guidance.

The immutable target is Specification 0.12 at revision
`e107a81a933c5eb5b4530e762311619be3a7a80f`. A port must update this pin through
an explicit specification upgrade, never by silently following the reference
repository's moving branch.

## Required language-native mappings

| Python mechanism | TypeScript mapping | Go mapping | Risk |
| --- | --- | --- | --- |
| zero-argument class factory | closure returning a discriminated declaration | typed factory returning a fresh pointer-backed node | medium |
| `Protocol`, ABC, `isinstance` | interfaces plus runtime validators and discriminants | small interfaces, concrete structs, explicit type switches | medium |
| `inspect.signature` and type hints | explicit runtime schema coupled to an inferred handler | tagged input struct or explicit decoder/schema coupled to a handler | high |
| `ContextVar` | `AsyncLocalStorage` with scoped restoration | explicit `context.Context`; never goroutine-local global state | high |
| `AsyncExitStack` | private LIFO cleanup stack with `try/finally` | registered cleanup functions unwound with `defer` | high |
| frozen dataclasses and immutable collections | defensive copies, private storage, `readonly`; freeze where useful | unexported storage and defensive slice/map copies | medium |
| object identity and mutable path stamping | object-keyed metadata maps | pointer identity or compiler-owned metadata; avoid node value copies | high |
| ordered dictionaries/generators | arrays and insertion-ordered `Map` | ordered slices; never depend on map iteration | medium |
| exception hierarchy and causes | typed `Error` subclasses with structured facts | typed errors with `errors.Is`/`errors.As` | medium |
| final singleton Tool plane | fixed unexported gateway registration | fixed server constructor behavior | low |
| ASGI and Python MCP SDK adapters | native Node transport and official TypeScript SDK | `net/http` and official Go SDK | high |

In both bindings, `Role`, `Skill`, and `Tool` remain the closed node set. Roots
must accept all three kinds. Business tools never become top-level MCP tools.
JSON object maps should be confined to the wire boundary; internal logic should
prefer typed values.

## Work classification

Mechanical work includes constants, recursive JSON types, node cards, stable
grouping, reference parsing, pure validation, root-set intersection, fixture
loading, and deterministic comparisons. It is suitable for GPT-5.6 Terra, or
Luna only when an exact test oracle already exists.

Reasoning-heavy work includes the declaration API, compiler-owned identity,
registration, Index diagnostics, disclosure projections, System API refusal
semantics, publication validation, and public API ergonomics. Use Terra with a
final consolidated Sol audit.

High-risk work includes Binding/schema parity, request-local state, lifecycle
rollback, concurrency, identity, MCP/HTTP adapters, transport security, and any
conformance or release claim. The defaults below are the Sol design decision;
Terra should implement them directly and use the tests as the review boundary.

## Approved implementation defaults

### Sequence and ownership

Complete TypeScript first, including all 16 conformance rules and the final
package gate. Then port the settled behavior to Go. Do not develop competing
public contracts in parallel. Shared non-normative harness improvements may be
made in the reference repository, but normative files and golden outputs remain
unchanged.

Within each language, build in this order: declarations; compiler and Index;
disclosure and root selection; Binding and runtime; MCP publications and fixed
gateway; lifecycle, identity, and REST; CLI and packaging. Each green slice is a
small commit and the next eligible slice starts automatically.

### TypeScript

- Use factory closures for every root and nested node so each compilation owns
  fresh objects. Expose the closed node set as a discriminated union. A Role
  has distinct `children`, `skills`, and `tools` collections; all three node
  kinds may be application roots. Runtime validators enforce facts that erased
  TypeScript interfaces cannot.
- Use Zod 4 as a direct dependency and schema-first authoring surface. A Tool
  couples one Zod input object, its inferred handler input, exported JSON
  Schema, validation, and invocation in one Binding. Normalize only the
  incidental fields that the contract excludes, such as generated `title`;
  cover optional versus nullable, strict unknown properties, arrays, records,
  nested objects, enums, integer/number, and unions with focused fixtures.
- A business handler takes `(input, context)` and may return a value or Promise.
  The explicit call context carries cancellation and Host/SDK handles. The
  runtime enters one `AsyncLocalStorage` scope around the handler to expose
  `currentPrincipal`, `currentTelemetry`, `currentGraph`, and the effective
  selection without adding those framework facts to model input. Never rely on
  process-global mutable request state.
- Implement a private LIFO cleanup stack compatible with the minimum Node
  version. Register a disposer immediately after each successful acquisition.
  On partial open failure, unwind registered disposers in reverse. After a
  successful open, run `Channels.close` while acquired dependencies remain
  live, then unwind them in reverse in `finally`. Preserve the primary failure
  and attach cleanup failures without replacing it.
- Keep the core free of MCP and HTTP imports. The server package registers the
  fixed Contexture gateway, then Prompt and Resource publications through the
  official SDK. Business Tools are payload cards, never SDK-registered top-level
  tools. Implement stdio before streamable HTTP, then auth/root selection, then
  an explicit REST allowlist over the same Binding.
- CLI is not a prerequisite for kernel conformance. After the runtime and Host
  surfaces pass, add a small package `bin` entry for serve/inspect/scaffold as
  supported by the binding; do not reproduce Python argparse internals or let
  CLI work delay the conformance gate.

### Go

- Start only after the TypeScript contract is green. Use factory functions that
  return fresh pointer-backed nodes. Represent the closed node set with an
  interface sealed by an unexported marker and concrete Role, Skill, and Tool
  declarations. Keep children, skills, and tools distinct, permit all three at
  the application root, use slices for observable order, and return defensive
  copies from public accessors.
- Provide a generic constructor such as `NewTool[I, O]` over a declaration whose
  handler is `func(context.Context, I) (O, error)`. It returns an erased internal
  Tool/Binding interface stored by the compiler. Reflect the tagged input struct
  once during compilation; the same erased Binding owns exported JSON Schema,
  decoding/validation, and handler invocation. Require explicit JSON tags and
  test pointer/zero-value optionality, unknown fields, nested structs, slices,
  maps, enums, integer/number, and nullable pointers. Keep `map[string]any` at
  the wire edge.
- Pass `context.Context` explicitly from every Host door through runtime and
  Binding to the handler. Store principal, telemetry, selected graph, and
  effective root selection with private typed context keys. Current-value
  helpers require a context argument. Do not simulate ambient goroutine-local
  state.
- Channels opens with a framework cleanup registrar. Register each cleanup
  immediately after acquisition and unwind in reverse on partial failure. Call
  `Close` only after a successful `Open`, while dependencies are live, then run
  registered cleanup functions. Preserve the primary error and combine cleanup
  errors with wrapping compatible with `errors.Is`/`errors.As`.
- Keep the root package SDK-neutral. The `server` package owns the official MCP
  SDK and the same fixed-gateway/publication order used by TypeScript. Implement
  stdio, then streamable HTTP and trusted identity/root attenuation, then
  explicit `net/http` REST routes over the same Binding. All concurrent tests
  and the final suite run with `-race`.
- CLI follows conformance: add `cmd/contexture` only for supported
  serve/inspect/scaffold workflows. Match observable outputs where specified;
  do not translate Python CLI implementation details.

### Errors, ordering, and blocked work

Both ports use structured error facts internally and format the exact recovery
sentences at the System API boundary. Arrays/slices carry all observable order;
maps never decide output order. A locally blocked row is recorded and skipped
while independent work continues. Items needing judgment are collected for the
final Sol audit; no live handoff interrupts the Goal. Work ends only after both
ports complete or the same irreducible blocker prevents every remaining task
after all safe alternatives have been exhausted.

## Test and review policy

- A port reads neutral fixtures and exercises its own declarations and runtime.
  Copying expected bytes is not execution evidence.
- Golden files are immutable inputs during port implementation. Only the
  Python producer may deliberately regenerate them after a separately reviewed
  contract change.
- Preserve declaration order. Compare JSON semantically where incidental object
  key order is not expressible; compare every string exactly.
- Go must pass `go test -race ./...`. TypeScript must include concurrent async
  isolation and attempted-mutation tests.
- Keep one kernel concept or one Host adapter in a review unit. After two failed
  attempts at the same acceptance test, expand diagnosis, reduce or reorder the
  slice, record the issue if still unresolved, and continue independent work.

## Known infrastructure limit

The current neutral fixtures describe declarations and expected boundaries but
do not define executable Tool bodies or a transport-neutral observation
protocol. Therefore this phase validates manifests, pins, and asset inventories
only. It intentionally does not introduce a shared executable runner. Such a
runner requires a separately reviewed neutral execution format.
