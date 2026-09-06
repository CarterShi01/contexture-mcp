# Repository instructions

These instructions apply to the entire reference repository.

## Language and authority

- English is the first language for source code, identifiers, comments, API
  documentation, errors, release notes, and authoritative documentation.
  Simplified Chinese documents are translations.
- `spec/model.md`, `spec/conformance.md`, `spec/fixtures/`, and `spec/golden/`
  are the normative cross-language contract. `spec/bindings.md` is
  non-normative guidance. Python implementation details are not normative when
  the specification says otherwise.
- Do not change a golden file merely to make a test pass. A golden change is a
  protocol-contract change: explain it, regenerate it from the Python producer,
  and review the byte diff separately.
- Never weaken, skip, delete, or broadly rewrite a test to make an
  implementation pass.

## Verification

Run the checks relevant to every change. Before handing off a broad change,
run the complete local gate:

```bash
uv run --extra dev pytest -q
uv run --extra dev pyright
uv run --extra dev ruff check contexture tests scripts
uv run --extra dev validate-pyproject pyproject.toml
uv run python scripts/verify_porting_contract.py
uv run python scripts/product_manifest.py --check
```

## Porting work

- Start with `spec/porting/FULL_PRODUCT_PARITY_PLAN.md`. It defines the
  required full-product equivalence target, architecture, phases, and gates.
  The older `TERRA_GOAL.md`, `PORTING_BRIEF.md`, and
  `CONFORMANCE_MATRIX.md` record the narrower 0.12 kernel-prototype work;
  use their language-native mappings and conformance evidence, but do not use
  them as a completion criterion.
- Keep changes reviewable: one kernel concept or one Host adapter per change.
  Do not mix public-API design, SDK integration, generated golden updates, and
  unrelated cleanup.
- GPT-5.6 Terra high owns bounded implementation slices from the product
  manifest. GPT-5.6 Sol owns baseline changes, architecture, differential-test
  design, and final acceptance. GPT-5.6 Luna is limited to mechanical work
  with an existing test oracle.
- Run the porting Goal without intermediate handoffs. Stage reports, green
  commits, local blockers, and test failures are not stopping conditions;
  diagnose, reduce or reorder the slice, and continue every independent task.
  Record specification/Python conflicts, required normative or golden changes,
  unrecorded product-semantic decisions, and failures unresolved after expanded
  diagnosis for one final GPT-5.6 Sol audit. End only when both bindings are
  complete or the same irreducible blocker prevents every remaining task after
  all safe alternatives are exhausted.

## Conformance claims

- A rule is implemented only when repository tests contain the evidence named
  in the porting ledger. Copied fixtures, copied golden bytes, or prose do not
  count as execution evidence.
- TypeScript and Go are incomplete kernel prototypes. Do not claim product
  equivalence until the product manifest is fully verified. Do not mark a
  conformance rule implemented until its focused tests and prerequisite rules
  pass.
- Do not design a supposedly shared executable runner until the neutral fixture
  format specifies handler behavior and observations. Each binding must produce
  outputs through its own implementation.
