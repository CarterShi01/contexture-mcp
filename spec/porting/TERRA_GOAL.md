# Historical Terra goal: Contexture 0.12 kernel ports

> **Superseded for new work.** Follow
> [FULL_PRODUCT_PARITY_PLAN.md](FULL_PRODUCT_PARITY_PLAN.md) for the approved
> objective: complete TypeScript and Go products equivalent to Python, not only
> the 0.12 kernel contract. This file is retained to explain the scope of the
> existing prototype work.

## Operating objective

Complete the TypeScript port and then the Go port of the Contexture 0.12
contract. Work autonomously across the reference, TypeScript, and Go
repositories until both ports have passing evidence for all 16 conformance
rules, or a genuinely irreducible blocker remains. Both ports start as
scaffolds implementing rule 1 only; preserve that truthful status until new
evidence actually passes.

This is one uninterrupted Goal. Phase reports, green commits, local failures,
and locally blocked rows are progress markers, never reasons to yield or wait
for the user. Continue until both bindings are complete, or every remaining
task is blocked by the same irreducible condition after safe alternatives have
been exhausted.

Read, in order:

1. the repository `AGENTS.md`;
2. this file and `PORTING_BRIEF.md`;
3. `CONFORMANCE_MATRIX.md`;
4. the exact normative rule, fixtures, golden files, and Python modules named by
   the selected task.

## Long-running execution loop

Start with TypeScript. Repeatedly choose the next matrix row whose prerequisites
are implemented, reduce it to a reviewable slice, state its observable behavior
and focused tests in the working record, implement it, and run focused plus
repository-wide checks. When the slice is green, make a small commit and
continue automatically. After TypeScript satisfies all 16 rules and its final
gate, apply the settled contract and lessons to Go using the same loop. Do not
wait for the user between green slices.

You may edit, test, and make small green commits in all three repositories. The
reference repository may receive non-normative porting infrastructure or test
harness improvements needed by both bindings. Do not push, publish, create a
tag or release, remove package release guards prematurely, modify golden files,
or change the normative specification.

Update a rule to `implemented` only when all evidence listed in the row exists
and passes. Commit the implementation and its evidence together. Keep unrelated
cleanup out of the slice.

Never update golden files, lower an assertion, skip a failing test, add a broad
`any`, depend on map iteration, or import an MCP SDK into the core to obtain a
green result. Do not infer Python-only mechanisms as contract requirements.

## Deferred Sol review

`PORTING_BRIEF.md` already records the default public, schema, context,
lifecycle, SDK, and sequencing decisions. Implement those decisions directly;
their risk classification is not, by itself, a reason to pause.

Do not wait for a live model handoff. Record an item for the final consolidated
Sol audit only when:

- the normative contract and observable Python reference behavior conflict;
- progress requires changing a normative asset or golden output;
- a public decision not covered by the brief would change product semantics;
- an acceptance failure remains after two focused attempts and expanded
  diagnosis still cannot determine a contract-preserving fix.

After two failed attempts, expand diagnosis, reduce or reorder the slice, and
try a safe alternative. If the issue remains local, mark it for final Sol audit
and keep working on every independent row or prerequisite. Do not stop for a
stage report, failing test, green commit, local blocker, or need for review.
Stop only when both bindings are conformant or when the same proven blocker
prevents every remaining task and all safe alternatives are exhausted.

## Completion report

For each green commit, record the matrix row, tests, and manifest movement in
the commit message or working log. At the end, report commits, files changed,
focused and full test results, conformance status, deferred items, blockers, and
any public behavior introduced. A green build without the row's named evidence
is incomplete.
