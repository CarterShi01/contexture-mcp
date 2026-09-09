# 09 — Process members and instruction emphasis: execution plan

Companion to [ADR 023](adr/023-process-members-and-instruction-emphasis.md).
The decision says what is true after this change; this plan says what has to be
touched to get there, in what order, and what would make it wrong.

Release: **0.16.0**. This is a breaking change to a snapshotted public surface,
which pre-1.0 is permitted and which the minor version is what announces.

## 1. Scope

| Change | Where |
| --- | --- |
| `framework_instruction` / `binding_instruction` | `contexture/core/emphasis.py` (new) |
| `PreProcess`, `PostProcess`; `pre_process`, `post_process` fields; composition | `contexture/core/model/role.py` |
| `Publication` removed from every export path | `contexture/core/model/__init__.py`, `contexture/core/__init__.py`, `contexture/__init__.py` |
| `binding_instruction` added to the public surface | `contexture/core/__init__.py`, `contexture/__init__.py` |
| Agent-visible wording | `contexture/core/model/system_api.py` (INSPECT tool description) |
| Internal comments naming the old concept | `contexture/core/model/index.py`, `contexture/inspection.py` |
| Tests | `tests/test_process.py`, `tests/test_process_integration.py` (replacing the two `test_publication*` modules) |
| Docs | `README.md`, `README.zh-CN.md`, `docs/handbook.md`, `docs/handbook.zh-CN.md`, `spec/model.md`, `spec/conformance.md` |
| Release metadata | `pyproject.toml`, `contexture/core/constants.py`, `CHANGELOG.md` |

## 2. Sequence

1. Add `core/emphasis.py`. It depends only on `core.errors`, so it can land and
   be tested before anything imports it.
2. Change `role.py`: fields, validation helper, `members()` order, the two
   compositions, `_disclosed_ref`, and the two classes.
3. Update the three export paths, then smoke-test that `contexture.Publication`
   is gone and the three new names resolve.
4. Rewrite the tests. The old modules import `Publication` at module scope, so
   until they are replaced the suite cannot even collect.
5. Update docs and spec.
6. Bump the version in `pyproject.toml` and `core/constants.py`, write the
   changelog entry.
7. Run the full gate: `pytest`, `pyright`, `ruff`, and a build.

## 3. Traps

**Do not global-replace "publication".** Three unrelated uses of the lowercase
word survive this change and must be left exactly as they are:
`contexture/web/route.py` and `contexture/web/__init__.py` (REST publication of
a route), and `contexture/server/application.py` (the compile-and-publish path
in a docstring). ADR 021 already recorded that the capitalized node concept and
the lowercase exposure sense are different words that happen to be spelled the
same; a `sed` over the tree cannot tell them apart. Grep for the capitalized
`Publication` to find what must change, and read each lowercase hit before
touching it.

**The INSPECT tool description is wire text, not a comment.**
`system_api.py` names "Publication contracts" in the description an agent reads
when deciding what `contexture_inspect` returns. It has to change with the
concept; the two comments in `index.py` and `inspection.py` matter only to
readers of the source.

**`payload["roles"]` is always present.** `group_cards` emits all three group
keys, empty ones included, so the disclosure check in `_disclosed_ref` cannot
raise `KeyError` on a Role that holds nothing else. This was verified rather
than assumed; if that invariant ever changes, this check breaks first.

**Composition order is load-bearing.** The PostProcess branch appends to the
value the PreProcess branch has already written, which is what puts the
business text between the two contracts rather than before both of them. A
refactor that reads `self.instructions` in the second branch silently drops the
first contract.

## 4. Verification

- `uv run --extra dev pytest -q` — whole suite, not only the new modules.
- `uv run --extra dev pyright` — the two new fields are typed as their specific
  classes, so a mis-slotted member is a type error before it is a runtime one.
- `uv run --extra dev ruff check contexture tests`.
- `uv build` and `twine check --strict dist/*`.
- Manual read of one composed payload end to end: an owner with both members
  declared should show the PreProcess block, then untouched business text, then
  the PostProcess block, with each banner appearing exactly once and each ref
  matching the card in `roles`.

## 5. Downstream

One Creator is the only known consumer. It vendors this package by path and
pins `contexture-mcp>=0.15,<0.16`, which excludes this release, so its
constraint must move in the same migration rather than afterwards. Its own
adaptation is planned in that repository under
`docs/design/contexture-process-members-adaptation.md`.
