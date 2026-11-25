# ADR 026: Subject usage is recorded beside telemetry

- Status: Accepted
- Date: 2026-11-25
- Extends: the node-level `Telemetry` side channel (`core/model/telemetry.py`)

## Context

`Telemetry` counts how often a Tool or Role was used and when it last failed.
Applications built on Contexture keep asking the same question about their own
*objects* instead of the graph: which projects were read this week, which
document changed yesterday, which member nobody has opened in a month. Every
such application would otherwise re-implement the same bookkeeping, usually
inside business code and usually counting HTTP polling as use.

The framework cannot know what a business object is, but a Tool can say which
objects one call touched. Applications also commonly serve people through
their own HTTP handlers over the same module APIs — a peer inbound adapter the
framework does not run — so recording only at the framework's execution point
would see agents and miss people.

## Decision

1. A Tool may declare `subjects = Subjects...` as a **class attribute**. It is
   not a node field: declaring one changes no disclosure payload, no canonical
   graph and no digest. `Subjects.template("project://{project}")` fills from
   the arguments, `Subjects.result("ref")` reads the result, `Subjects.all(...)`
   combines, and any callable `(arguments, result) -> subjects` is accepted.
2. `ApplicationRuntime` reports one use after every invocation, beside the
   existing node telemetry, through `report_use`. The managed runtime does the
   same: an `ExecutionAdapterRegistration` may carry the Tool's `subjects`
   (outside its equality and contract) and `ManagedRuntime(..., usage=...)`
   takes the recorder; the request's bound `Principal` names the actor. The telemetry collector keeps
   a ledger when it implements `record_use`; `LedgerTelemetry(ledger)` is the
   shipped one. Reporting never raises into the call.
3. An inbound adapter the framework does not run records through the same
   `LedgerTelemetry.record_use(subjects=..., action=..., node=..., principal=...)`,
   optionally inside `bound_door("rest")`, so both doors land in one ledger
   with one vocabulary.
4. A principal's repeated touch of one subject within a window (default ten
   minutes) counts once; failed calls are kept apart and are not uses.
5. The ledger keeps per-day totals and the last touch per subject, actor and
   action — never arguments or results — for a retention period (default 90
   days). `InMemoryUsageLedger` and `SqliteUsageLedger` (standard library only,
   batched writes) ship; any object implementing `UsageLedger` can replace them.
6. `query(prefix, windows=..., actor=..., half_life_days=..., write_weight=...)`
   returns per-subject windows, last read and write, and a decaying score that
   is meant for ordering, not for display.
7. The actor of a use is `agent` when a principal carries a `client_id`,
   `human` when it carries only a `subject`, otherwise `system`; applications
   with their own notion pass `actor_of`.

## Consequences

- Usage is evidence about a call, never a dependency of it: a full disk or a
  broken extractor costs a count, not a result.
- Nothing leaves the process the application chose; there is no exporter.
- Per-person day totals are not kept in this version (only the last touch per
  person is implied by the coalescing state); a per-person view is a later,
  separate decision because it multiplies storage.
- Subject strings are opaque to the framework; prefixes are matched literally.
- `ManagedGraphHost.open` builds registrations from registry entries rather
  than from Tool objects, so it passes no subjects yet; a host that registers
  Tools itself (as an application with native bindings does) passes them.
- The durable ledger is `contexture.server.SqliteUsageLedger`: it owns a file,
  so it lives in the server layer beside the managed journal.
