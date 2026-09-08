# ADR 022 — INSPECT is a non-activating structural view

**Status:** accepted

**Date:** 2026-09-08

## Context

Contexture originally had two disclosure levels: ROUTE for choosing a node and
ACTIVE for using it. That assumed there was no meaningful state between “not
chosen” and “chosen”. Team-aware planning disproves the assumption. A planner
first shortlists Roles from their descriptions, then needs to compare what each
candidate directly contains before adopting any Role's instructions.

Opening every candidate is incorrect. It places several competing procedures
in one model context and can introduce framework-owned Publication obligations
for Roles that were merely evaluated. The problem is semantic contamination,
not Tool execution: opening has always been disclosure-only.

Adding an authored `details` field was rejected. It would duplicate
descriptions or instructions and create another source that can drift.

## Decision

Contexture has three disclosure levels:

```text
ROUTE    choose a shortlist from each node's description
INSPECT  compare a shortlist through one structural level
ACTIVE   adopt one node's instructions and actionable facets
```

Execution remains separate and happens only through an invocation gateway.

`contexture_inspect(refs)` accepts from 1 through 32 unique non-empty refs. It
atomically validates the complete batch against the same model visibility and
SurfaceSelection boundary as `contexture_open`, then returns:

- one fixed framework notice;
- each requested node's pure routing card;
- pure routing cards for that node's direct containment members;
- pure routing cards for its declared `uses` targets.

A pure routing card is exactly `kind`, `name`, `description`, and canonical
`ref`. INSPECT never includes instructions, Tool schemas, read-only execution
classification, Publication designation or finishing contract, content,
invocation results, or recursive expansion. Breadth is requested as multiple
refs; depth is explicit repetition over refs returned by the previous call.

INSPECT reuses existing declarations and adds no authored field. It is also
reported separately from ACTIVE open telemetry, so evaluation is not counted
as adoption.

## Consequences

- The fixed runtime gateway grows from four to five Tools. This is a documented
  pre-1.0 wire change.
- Disclosure-only applications expose discover, inspect, and open, but still
  have no execution surface.
- Existing ROUTE and ACTIVE payloads remain unchanged.
- `uses` remains non-recursive and never widens a selected surface.
- Publications appear only as ordinary Role member cards during inspection.
