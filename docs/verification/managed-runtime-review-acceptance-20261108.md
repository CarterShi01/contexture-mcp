# Managed runtime: review fixes and acceptance status (Python reference)

Date: 2026-11-08. Branch: `fix/managed-runtime-review` (base `b7778e7`).
Scope: Python framework only (phase 1). TypeScript/Go ports and One-Creator
adoption are phase 2. This report states what is verified on this branch and
what is not; it is not a release or a spec acceptance.

## Goal checked against

From the original research input: while agents use the system, a context tree
can be changed where nodes are mutable, through atomic, admitted, auditable
transactions; history, diffs and reverts are available by stable ID; the
runtime and Catalog switch together to an immutable new generation without
interrupting in-flight requests; startup does not require replay from genesis.

## What changed (commits)

| Commit | Milestone | Summary |
| --- | --- | --- |
| `c227a8e` | M1 | Mutability semantics; `protected_dependents`; rule diagnostics |
| `8e5abc7` | M2 | Registry evidence by binding; registry-free replay; History chain verification; real execution/surface diffs; legacy facts removed |
| `7b4a698` | M3 | No-replace publication; lock identity check; observable release refusals |
| `1e08f45` | M5 | `ManagedGraphHost`; binding-scoped execution adapters; packaged schema; logging; guide + example; 1.2.0.dev0 |
| `db138ca` | M4 (1) | Node sharing across revisions; incremental draft; cheaper recovery |
| (this commit) | M4 (2), M6 | Owner snapshots; streaming digest; in-place doctor; watch bisect; decisions ledger; this report |

Spec decisions: `spec/managed/decisions.md` § "Review-driven revisions" (R01-R11)
and port obligations for TS/Go.

## Review findings: disposition

| Finding (review 2026-11-08) | Status | Evidence |
| --- | --- | --- |
| B1 binding upgrade makes journal unreplayable | fixed | `tests/test_managed_binding_upgrade.py` (single-tx upgrade, reopen after v1 retired, reopen with no registry) |
| M3 mutable child of locked parent only partly editable | fixed | `tests/test_managed_mutability.py` (19 scenarios incl. bypass regressions) |
| Two owners after `writer.lock` deletion overwrite an acked record | fixed | `test_removed_or_replaced_lock_stops_the_original_owner`, `test_racing_writers_never_overwrite_an_acknowledged_record` |
| Release manager swallows refusal causes | fixed | `status().last_error`, release manager test |
| Runtime-created Tool cannot execute without rebuilding runtime | fixed | example + `tests/test_managed_host.py` (`new_tool`) |
| No composition root / docs / logging | fixed | `contexture/server/managed_host.py`, `docs/guides/managed-runtime*.md`, `examples/managed_dynamic_tree.py` |
| Schema not shipped (installed package cannot open a store) | fixed | wheel built and example run from a clean venv outside the checkout |
| History constructor did not verify the chain | fixed | `test_append_validates_only_new_link_but_public_construction_checks_all_links` |
| Diff placeholders | fixed | `test_execution_*diff*` |
| Cascade delete quadratic; per-op whole-graph copies | fixed | measurements below; `test_unchanged_nodes_are_shared_across_revisions_and_replay` |
| Startup replays from genesis | fixed (graph replay) | `tests/test_managed_snapshot.py` (only tail applied; old history on demand; mismatch falls back) |
| Doctor buffers whole store; 64 MiB ceiling | fixed (doctor), mitigated (backup) | in-place doctor tests; backup bound configurable |
| Watch poll scans whole history | fixed | bisect on journal_seq |
| CLI discards all diagnostics | fixed (framework-authored text and rule diagnostics only) | `contexture/cli/managed.py` |
| Durable workload guard accepted repo paths | fixed | runner refuses roots inside the checkout |
| TS/Go: fs-ext hard dependency, revert, auth retry, inline vectors | open (phase 2) | port obligations in decisions ledger |
| Catalog snapshot may retain an old generation until TTL | open (minor) | unchanged |

## Measurements (2-core arm64, ext4 /tmp, strict durability)

Same scripts as the review (`repro-core/perf.py`, `repro-durable/perf.py`).

| Operation | Before | After |
| --- | --- | --- |
| Rename, 2000-node graph | 2.36 s | 0.26 s |
| Cascade delete 1000 of 2000 nodes | 71.5 s | 1.84 s |
| Replay per event, 2000-node graph | 400 ms | 55 ms |
| Copies of an unchanged node over 21 revisions | 21 | 1 |
| Durable commit (4-node graph) | 37-63 ms | ~13 ms (7.7 ms fsync) |
| Reopen 2000 records: journal / owner replay | 14.8 s / 8.7 s | 1.7 s / 1.8 s |

WP14 durable workload (`scripts/managed_durable_workloads.py`, tracemalloc on):
WP14_RESULTS

## Not verified here

* TypeScript and Go: not changed in phase 1; they do not have R01-R11.
* Power-loss durability on physical hardware (process-crash only, SIGKILL repro).
* Five-repetition 10k/100k matrices with the full shape/text/schema mix.
* One-Creator adoption (phase 2): the Personal image dependency closure, the
  managed-mode entry point, and Team/Panel/worker wiring.
* Spec acceptance: `accepted_spec_revision` stays `null`; Q01-Q06 are undecided.
