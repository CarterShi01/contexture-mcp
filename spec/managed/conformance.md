# Managed conformance — target v1 draft

Status: specified acceptance plan with a limited candidate schema/vector test slice.
No fault experiment, benchmark, native parity, real Host or OC journey is proven.
The [manifest](manifest.json) maps every D00–D25, T01–T14 and C01–C30 requirement.
All entries are `planned` or `specified`, never execution evidence.

## Claim discipline

A prose rule, copied fixture, copied golden file, package version or schema-shaped
output is not proof of implementation. Every binding MUST execute native
declarations, handlers and adapters against independently specified observations.
The existing static contract and golden corpus remain unchanged. Candidate managed
schemas and raw/reference vectors now exist; behavioral fixtures/golden and all
accepted revisions remain pending. See [schema contracts](schema-contracts.md).

Required evidence includes work package, D/T/C IDs, repository and exact commit,
spec pin, fixture hash, artifact/manifest digest, actual task, OS/filesystem/runtime
and dependencies, timing, outcome, fault point/seed/load shape, raw log and
limitations/reviewer. No fictional language test path is assigned by this slice.
Absent platform/provider/deployment evidence remains an unmet gate, not a pass.

## Acceptance families (D23)

| Family | Required observations | Cases |
| --- | --- | --- |
| T01 | IDs, forest, roots/planes, slots/process, legal uses cycle, publications/locales/mode | C01 C02 C03 C04 C17 C22 |
| T02 | Every command, drafts/batches, precise preview, CAS/idempotency/noop/current admission | C02 C03 C04 C05 C26 |
| T03 | Raw-byte Unicode/numbers/duplicates/order and unchanged legacy profile | C06 |
| T04 | Given events→intent→resolved batch→pure replay; all checkpoint projections+tail=full | C07 C10 C20 |
| T05 | Ownership, short writes/sync unknown/crash/full disk/corruption/restore and profiles | C08 C09 C10 C13 C24 |
| T06 | Schema/validator/effect/artifact unity, resource unwind/nonportable boundary | C11 C12 C22 |
| T07 | Head/desired, epoch races, supersession, prepare failure, swap/boot/LKG | C13 C14 C26 |
| T08 | Concurrent requests/run pins, cancellation/shutdown/current revocation, stale refs | C12 C15 C16 C17 |
| T09 | Definition/effective/execution/release diff, stable moves/renameback/revert/rollback | C02 C21 |
| T10 | Catalog/view/digest/page/watch watermark, authority/cursor/slow reader/debug | C18 C19 C20 C23 |
| T11 | Wide/deep/high-indegree/text/churn/history/RSS and complexity growth | C18 C20 C25 C26 |
| T12 | Auth/redaction/secrets/backup/diagnostics/downgrade | C04 C05 C16 C23 C24 C30 |
| T13 | All native fixtures, races/ALS/context, types/consumers/CLI/MCP/REST | C06 C07 C11 C17 C22 C27 |
| T14 | Real Team/Project/reconciliation/worker/Panel/source-lock and reversible adoption | C28 C29 C30 |

Every C01–C30 is individually mapped in the manifest, not satisfied merely by a
family row. All negative cases measure forbidden handler/close/write counts and
hidden-data disclosure, as applicable. Equality of final JSON alone is insufficient.
Fixture handlers and their observable counters/errors must be specified before
any supposedly shared executable runner. Native implementations produce outputs;
they do not read golden output as their execution result.

## Required corpus partitions and gates

Planned managed corpus partitions (not existing asset paths): model/order/planes;
sequential commands and final facts; JCS raw-byte accept/refuse and legacy digest
preservation; complete record/checkpoint/upcast; references/typed instructions;
release/boot/race; leases/current authority; pages/large items/batch descriptors;
native binding defaults/coercion/handlers; management/errors and restore. Freeze
each schema/fixture hash and accepted spec revision before implementation claims.

G1 proves lossless real Runtime/Architecture/Team lowering across all locales,
process slots, bindings and literal-reference inventory without business writes.
G2 proves pure history and three native producers, not copied vectors. G3 uses
barriers to prove full-generation request consistency/resource lifetime. G4 uses
actual subprocess/OS faults at every write/sync/publish/swap/observation boundary;
in-memory exceptions do not substitute. G5 measures capacity and growth under
frozen load/environment; G6 proves governed reversible OC migration. All six must
be passed at final revisions; none is declared passed here.

Test ladder: pure unit→shared corpus→stateful/property model→native integration→
subprocess fault→real transport→OC journey→performance/soak→external install.
Finite-state/TLA+ models state their bounds and do not prove native code. Reduced
property/fuzz counterexamples become permanent fixtures. TypeScript focused/full
gates precede the corresponding Go phase unless the parity owner revises the plan.

Each of Python/TS/Go needs Windows/Linux/macOS rows for lock, sync, atomic publish,
crash, external consumer and real Host, with strict and process_crash separate.
WSL ext4 versus Windows mounts are separate rows. Unsupported is not a silent
fallback; process kill cannot certify power-loss durability. Sequential native
store handoff checks read/replay/append with one owner at a time.

Existing repository gates still apply: Python test/type/lint/package/porting and
release-bindings gates; TS complete package check/fresh consumers; Go format/vet/
race/fuzz/conformance/release consumers. These are obligations, not commands run
here. No old test or golden may be weakened to manufacture a managed pass.

## First-release performance and configurable starting budgets (D20)

P0 includes append-then-freeze lists, iterative DFS/deque BFS, ID/ref/parent/slot/
kind/reverse/preorder/subtree-end indexes, local selected-root interval traversal,
precomputed view ordinal/slot facts, sets for membership, immutable COW sharing,
bounded canonical/schema/validator caches, batch/checkpoint/page/backpressure and
retained-generation accounting. Keep full validation and canonical semantics.
Build target is near O(N+E+B), counting text/schema/full-path bytes B; local scoped
enumeration O(K), interval membership O(log R). Full digest/export/search still
scans/emits full content; shallow map copies remain O(N). Never claim all operations
are O(delta), and do not weaken safety checks for benchmark scores.

Provisional configurable starting values, not supported-capacity promises:
100000 nodes, 1000000 edges, depth 1024; definition 256 MiB, node text 1 MiB;
10000 external commands, 16 MiB inline transaction, 2000000 expanded operations,
64 MiB resolved payload; normal open/page response 8 MiB, page 200/max 1000;
compile workers 1, queue 16; prepare timeout 60s, renewable run TTL 15min;
cache 128 MiB/4 generations; watch 1024 batches or 16 MiB, inline ≤1 MiB;
checkpoint 1000 batches or 64 MiB tail plus recovery time budget.
Budget active/leased generations without forcibly retiring them; reject release
or apply backpressure when required. Static defaults do not silently change.

Cache identity includes graph/generation/view/detail/locale/selection/compiler/
manifest/contributor purity and current authority version for visible bytes, or
the cache is request-local. TTL is not revocation correctness. No mutable bound
node, old graph closure, principal or arbitrary Channels sharing by cache accident.
Bound by bytes+entries+generations, not full-tree per-user Cartesian copies.

Load matrices include 1k/10k/100k balanced/wide/deep/cyclic-uses/high-indegree trees,
large text/schema, edits/batches/ancestor rename/churn, and 10k/100k history.
Record seeds/digests/runtime/OS/FS/profile and five repetitions with distributions,
CPU/RSS/allocations/GC/operation counts and queue/validate/compile/hash/prepare/swap/
replay/checkpoint/diff/watch/cache metrics. Include metadata scanning, indexes,
receipt/tombstone/staging costs and peak old-lease+candidate+checkpoint memory.
Separate fast recovery, cold discovery and full scrub. SLOs freeze after real
WP02 data; growth counts/curves, not one wall-time sample, support complexity claims.

## Security, OC authority and final completion (D21–D24)

Management, invocation and Catalog/history permissions are distinct. Redact
text/schema/endpoints/counts/ETags by view; evidence never automatically becomes
privileged instructions. Allowlist binding egress/artifacts/protocol/config/secret
handles; review nonportable/supply-chain constraints. Hash detects corruption, not
author authentication; signed bundles are an optional explicit policy, no invented
default PKI. Logs carry bounded actor/correlation/version/stage/code, not secret
material; avoid node-ID metric-label cardinality. Monitor stuck resources/unknown
commit/readiness independently from best-effort exporters.

OC runtime and architecture use separate graph/credential identities. Team owns
draft/eval/decision; accepted graph+journal owns committed declarations/history.
Choose one authoring flow, not editable Blueprint plus editable graph mirrors.
Business outbox pins monotonic version/exact preview/key. Approval, commit, desired,
local observed, worker adoption and verified are separate states. Stable Profile
IDs and ProjectType/Attempt reverse impact must be explicit. Project owner retains
sole content authority; graph projects navigation/capability structure, not every
document. Reconciliation keeps its owner/Coverage/source/target/policy and repairs
applying/unknown windows without mistaking draft success for capability adoption.
Panel/DTO/Automation/bundles consume the same versioned projection; offline
generated artifacts are release evidence, not live authority. First seed is once;
restart never overwrites journal from source. Source changes need exact migration.

Adoption requires static/shadow/managed configuration, pinned sources/artifacts,
no duplicate writes in shadow, controlled new-session canary, current worker/Panel
version observations and backup/rollback. Failed prepare retains service. Freeze
new writes and route to authorized compatible LKG/static rollback without erasing
journal or asking old software to read unknown events. No local OC deployment is
available for this slice, so T14/C28–C30 remain planned.

WP00–WP15 remain the complete product plan: baseline, contracts, risk proofs,
model/index, transactions, events/history/diff, files, compiler/bindings, release/
leases, refs/Host, query/watch/management, native parity, OC, operations, performance,
final acceptance. This WP01 document slice completes none of their execution
exits. All D/T/C mappings, current native full gates/installation/real Hosts,
G1–G6 and independently authorized release/deployment are needed for product signoff.