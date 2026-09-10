# Historical Contexture 0.12 kernel conformance ledger

> **Superseded as a completion ledger.** The 16 rules remain mandatory kernel
> evidence, but they cover only a subset of full product parity. New work is
> governed by [FULL_PRODUCT_PARITY_PLAN.md](FULL_PRODUCT_PARITY_PLAN.md) and
> its required Python product manifest.

At initialization, Python was the reference implementation and both new ports
had evidence only for rule 1. The table now records the completed result: both
bindings report `conformant` with execution evidence for R1–R16, and the full
product manifest records every applicable row as verified. Public
package/module release remains a separate guarded decision. `R#` refers to the
numbered rule in `../conformance.md`.

| Rule | Deliverable and observable evidence | Depends on | Risk / model | TS | Go |
| --- | --- | --- | --- | --- | --- |
| R1 | Lazy application declaration; construction calls no root factory and opens no external resource | none | medium / Terra high | implemented | implemented |
| R2 | Compiler registers ordinary and Prompt roots once, freezes one canonical Index, and derives fresh bindings per runtime build | R1 | high / Terra high, use brief defaults | implemented | implemented |
| R3 | Negative tests for empty apps/names, duplicate cross-kind names and identities, cycles, separator ambiguity, and unresolved `uses` | R2 | medium / Terra | implemented | implemented |
| R4 | One Binding owns exported schema, validation, and handler call; schema corpus proves parity and invalid input never enters handler | R2-R3 | high / Terra high, use brief defaults | implemented | implemented |
| R5 | SDK integration lists exactly the four gateway tools and no business Tool | R4 | high / Terra high, use brief defaults | implemented | implemented |
| R6 | Implementation-produced discover/open/read-only invoke/write invoke outputs and all recovery strings match golden contract | R4-R5 | high / Terra high | implemented | implemented |
| R7 | Prompt-only roots are absent from model discovery and model open/invoke, but person-controlled Prompt navigation reaches them | R2, R6 | high / Terra high | implemented | implemented |
| R8 | Prompt, Resource, completion, instructions, and signposts match golden; Resource rejects non-tool, argument-taking, or writing targets | R4, R6-R7 | high / Terra high | implemented | implemented |
| R9 | RootSelection validates exact non-empty roots; every door, invocation graph, and concurrent request observes its own effective selection | R2, R6 | high / Terra high, use brief defaults | implemented | implemented |
| R10 | Selected roots retain complete subtrees and sibling groups; cross-root `uses` never widens the view | R9 | medium / Terra | implemented | implemented |
| R11 | Caller selection only attenuates an identity ceiling; tests prove selection is not permission policy and excluded names do not leak | R9 | high / Terra high | implemented | implemented |
| R12 | Channels open before service, close after it, and unwind successful acquisitions in reverse after partial failure; validation opens nothing | R2 | high / Terra high, use brief defaults | implemented | implemented |
| R13 | Disclosure-only build has fresh unbound nodes/Index, no bindings/execution/resources/channels, and exposes navigation plus selected Prompts only | R2, R7 | high / Terra high | implemented | implemented |
| R14 | REST uses an explicit route allowlist over the same Binding; method/read-only mismatch and arbitrary refs are rejected | R4, R9 | high / Terra high, use brief defaults | implemented | implemented |
| R15 | Principal, telemetry, graph, and selection are request-local; concurrency tests prove isolation; exporter failure never changes outcomes | R4, R9 | high / Terra high, use brief defaults | implemented | implemented |
| R16 | Dependency/layering tests prove core has no MCP or HTTP SDK imports | R1 and continuously | low / Terra | implemented | implemented |

Current per-rule evidence is maintained in each binding's
`conformance/specification.json`; current full-product completion is maintained
in the generated Python product manifest.

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
