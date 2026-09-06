# Contexture 0.12 porting ledger

Status at initialization: Python is the reference implementation. TypeScript
and Go are guarded scaffolds and truthfully implement rule 1 only. `R#` refers
to the numbered rule in `../conformance.md`.

| Rule | Deliverable and observable evidence | Depends on | Risk / model | TS | Go |
| --- | --- | --- | --- | --- | --- |
| R1 | Lazy application declaration; construction calls no root factory and opens no external resource | none | medium / Terra high | implemented | implemented |
| R2 | Compiler registers ordinary and Prompt roots once, freezes one canonical Index, and derives fresh bindings per runtime build | R1 | high / Terra high, use brief defaults | not-started | not-started |
| R3 | Negative tests for empty apps/names, duplicate cross-kind names and identities, cycles, separator ambiguity, and unresolved `uses` | R2 | medium / Terra | not-started | not-started |
| R4 | One Binding owns exported schema, validation, and handler call; schema corpus proves parity and invalid input never enters handler | R2-R3 | high / Terra high, use brief defaults | not-started | not-started |
| R5 | SDK integration lists exactly the four gateway tools and no business Tool | R4 | high / Terra high, use brief defaults | not-started | not-started |
| R6 | Implementation-produced discover/open/read-only invoke/write invoke outputs and all recovery strings match golden contract | R4-R5 | high / Terra high | not-started | not-started |
| R7 | Prompt-only roots are absent from model discovery and model open/invoke, but person-controlled Prompt navigation reaches them | R2, R6 | high / Terra high | not-started | not-started |
| R8 | Prompt, Resource, completion, instructions, and signposts match golden; Resource rejects non-tool, argument-taking, or writing targets | R4, R6-R7 | high / Terra high | not-started | not-started |
| R9 | RootSelection validates exact non-empty roots; every door, invocation graph, and concurrent request observes its own effective selection | R2, R6 | high / Terra high, use brief defaults | not-started | not-started |
| R10 | Selected roots retain complete subtrees and sibling groups; cross-root `uses` never widens the view | R9 | medium / Terra | not-started | not-started |
| R11 | Caller selection only attenuates an identity ceiling; tests prove selection is not permission policy and excluded names do not leak | R9 | high / Terra high | not-started | not-started |
| R12 | Channels open before service, close after it, and unwind successful acquisitions in reverse after partial failure; validation opens nothing | R2 | high / Terra high, use brief defaults | not-started | not-started |
| R13 | Disclosure-only build has fresh unbound nodes/Index, no bindings/execution/resources/channels, and exposes navigation plus selected Prompts only | R2, R7 | high / Terra high | not-started | not-started |
| R14 | REST uses an explicit route allowlist over the same Binding; method/read-only mismatch and arbitrary refs are rejected | R4, R9 | high / Terra high, use brief defaults | not-started | not-started |
| R15 | Principal, telemetry, graph, and selection are request-local; concurrency tests prove isolation; exporter failure never changes outcomes | R4, R9 | high / Terra high, use brief defaults | not-started | not-started |
| R16 | Dependency/layering tests prove core has no MCP or HTTP SDK imports | R1 and continuously | low / Terra | not-started | not-started |

“Implemented” requires focused repository tests for every evidence phrase in the
row, passing full CI, and a manifest entry naming those tests. `in-progress`
means code may exist but no conformance credit is claimed. Rule 16 is currently
`not-started` in the manifests because existing scaffold layering tests are not
yet evidence for the completed port's full core surface.

Risk/model labels select reasoning effort; they do not introduce approval
pauses. Terra high follows the approved defaults in `PORTING_BRIEF.md` and asks
for no intermediate handoff. Exceptional items are recorded for the final Sol
audit under `TERRA_GOAL.md`.

## Recommended phases

1. Declarations and immutable ownership: R1, then the declaration prerequisites
   of R2-R3.
2. Compiler and Index: R2-R3, while keeping R16 as a continuous invariant.
3. Disclosure and root projection: structural parts of R6-R7 and R9-R10.
4. One execution vertical slice: R4, runtime parts of R6, and R15. Establish it
   in TypeScript first, then translate the settled contract into Go.
5. MCP publications and gateway: R5, R7-R8, R13.
6. Lifecycle, identity, selection ceiling, and REST: R11-R12, R14-R15.
7. Final cross-language differential audit and R16 sign-off.

Do not advance a phase merely because files exist. Advance it when its rule
evidence passes.
