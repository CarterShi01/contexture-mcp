# Contexture Porting Handoff

Date: 2026-09-07

This temporary handoff is committed for continuity and may be deleted later.
It is not a product-parity claim.

## Current state

- Python reference: local `release/0.12.0`, 32 commits ahead of its remote
  branch. Its evidence ledger is intentionally local and has not been pushed
  to Python `master`.
- Go binding: `master` is clean and synchronized with `origin/master`.
- TypeScript binding: `master` is clean and synchronized with `origin/master`.
- Product ledger: 33 `implemented`, 1 `verified`, 26 `missing` out of 60
  source modules. Credit requires both bindings, focused execution evidence,
  native gates, consumer evidence, and an independent audit.

## Latest pushed binding work

Go includes the audited foundation, identity, errors, Role, Tool, Node,
Disclosure API, Execution API, GraphContext, and server options work. The
latest options fix is `cde084f`; it has full Go gates and completed audit.

TypeScript includes the audited foundation, identity, errors, Role, and Tool
work. Tool commit `5cec7be` passed the complete serial `npm run check` and its
independent audit. An uncommitted Disclosure API slice was present when this
handoff was prepared; finish and push it only after its complete gate and audit.

## Immediate continuation order

1. Finish the existing TypeScript Disclosure API slice; run exclusive
   `npm run check`, push `master`, and independently audit it.
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
