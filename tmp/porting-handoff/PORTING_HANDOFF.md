# Contexture Porting Handoff

Date: 2026-09-07

This temporary handoff is committed for continuity and may be deleted later.
It is not a product-parity claim.

## Current state

- Python reference: local `release/0.12.0`, 34 commits ahead of its remote
  branch. Its evidence ledger is intentionally local and has not been pushed
  to Python `master`.
- Go binding: `master` is clean and synchronized with `origin/master`.
- TypeScript binding: `master` is synchronized with `origin/master` at
  `8c82275`, but two post-audit evidence edits are currently uncommitted:
  `scripts/verify-package-consumer.mjs` and `test/system-api.test.ts`. Preserve
  them, run the full gate, then commit/push only after audit.
- Product ledger: 33 `implemented`, 1 `verified`, 26 `missing` out of 60
  source modules. Credit requires both bindings, focused execution evidence,
  native gates, consumer evidence, and an independent audit.

## Completion acceptance definition

The full objective is complete only when all 60 manifest modules are no longer
`missing`, and every public product surface has equivalent executable evidence
in both bindings. For every module or Host adapter, the evidence must include:

1. native implementation and focused tests that execute the behavior;
2. all relevant language gates, including conformance, race/type checks,
   lint/format, build and package/scaffold consumers;
3. independent read-only audit against the normative specification and Python
   reference, with any native mapping explicitly documented;
4. English-first documentation plus the Simplified Chinese translation;
5. no weakened/deleted tests, no copied-fixture-only claims, and no golden
   changes unless the Python producer regenerated a deliberate protocol change;
6. clean binding worktrees and remote `master` synchronized with accepted
   commits.

Line counts, compilation alone, copied fixtures, prose, or a green narrow test
do not satisfy completion. The final audit must run the complete Python gate,
verify the product manifest and porting contract, and confirm there are no
unreviewed `missing` modules or unresolved specification conflicts.

## Latest pushed binding work

Go includes the audited foundation, identity, errors, Role, Tool, Node,
Disclosure API, Execution API, GraphContext, and server options work. The
latest options fix is `fd9df4a`; it has full Go gates and completed audit.

TypeScript includes the audited foundation, identity, errors, Role, and Tool
work. Tool commit `5cec7be` passed the complete serial `npm run check` and its
independent audit. Disclosure source commit `8c82275` passed its full gate,
but its independent audit required additional public-facade and packed-consumer
evidence; those two evidence edits are the uncommitted files listed above.

## Immediate continuation order

1. Finish and verify the existing TypeScript Disclosure evidence edits; run
   exclusive `npm run check`, commit/push them, and independently audit the
   complete Disclosure API slice.
2. Implement and audit TypeScript server options. Known gaps are auth ownership
   and stdio conflict handling, body-size/413 enforcement, `?`/`#` path
   rejection, listener safety, packed-consumer evidence, and bilingual docs.
3. Implement and audit TypeScript counterparts for Go's Execution API,
   GraphContext, and Node before recording those Python modules.
4. Continue the manifest's remaining CLI, Server/Surface, Web, Demo, and
   initializer modules. Never change golden files merely to make a port pass.

## Required gates

```bash
# Python reference
uv run --extra dev pytest -q
uv run --extra dev pyright
uv run --extra dev ruff check contexture tests scripts
uv run --extra dev validate-pyproject pyproject.toml
uv run python scripts/verify_porting_contract.py
uv run python scripts/product_manifest.py --check

# Go
go run ./internal/conformancecheck
go test -race ./...
go vet ./...

# TypeScript (never concurrent with another TS build/gate)
npm run check
```
