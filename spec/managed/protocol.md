# Managed transactions and runtime protocol — target v1 draft

Status: specified design draft; no implementation, wire release or verification claim.
The [model](model.md), [FileJournal](file-journal.md), [conformance](conformance.md)
and [decision ledger](decisions.md) are complementary. D25 takes precedence over
earlier source-design summaries. Candidate schemas/vectors and the exact proposed
wire mappings are now in [schema contracts](schema-contracts.md); not accepted.

## Planes of truth

Graph head, durable desired release, preparation attempt, local serving
generation and per-boot observation are separate facts. Queries MUST identify
`head`, an exact revision, `desired`, or `serving`; an unqualified “current” is
not a version selector. For example head 12, desired epoch 8/revision 11 and
different replicas serving revisions 11 and 10 can coexist. Release rollback
to revision 10 creates epoch 9 without changing head 12.

Every logical result identifies request, stage, accepted/observed facts and
diagnostics. Commit-and-release composition returns distinct commit receipt,
release receipt and scoped observations. Failure after commit does not undo it.
Telemetry failure is not a failed durable receipt; durable uncertainty has its
own recovery protocol.

## Commands, candidate order and preview (D07–D08, D25.1)

TransactionEnvelope contains graph ID, base revision, actor-scoped idempotency
key, ordered typed commands, reason, correlation ID and optional approval ref.
Actor comes from context. Builders follow Draft→Previewed→Committed/Aborted;
editing invalidates preview. Stored proposals are not accepted stream records.
Unknown commit cannot be aborted or blindly retried under a new key.

| Command | Target semantics |
| --- | --- |
| `create_node` | Create normalized object without nonempty members/uses; attach/link later |
| `update_node` | Allowlisted set/unset description, instructions/template, namespaced metadata and binding descriptor assertions; not arbitrary JSON patch |
| `rename_node` | Name only, preserve ID, derive affected display paths |
| `attach_node` | Attach currently unowned object to parent+slot or root+plane+position |
| `move_node` | Remove original membership then insert at 0-based position in the resulting target sequence, including same-slot moves |
| `detach_node` | Temporary draft detachment; final attach/root/delete required |
| `reorder_members` | Parent+slot and exact same ID set, reordered; no implicit additions/deletions |
| `add_root`, `remove_root` | Explicit root/plane/position and root authority; removal must resolve final ownership |
| `add_use`, `remove_use` | Source/target, optional insert position; duplicate/missing relation refuses |
| `delete_nodes` | Explicit targets and reject-references or explicit-cascade policy; exact deletion/disconnection set |
| `restore_nodes` | Retained revision+IDs, restore same identity and static creation policy |
| `replace_subtree` | Exact anchor, retained/new/deleted ID mapping and target structure; ordinary diff/admission |
| `put_publication`, `remove_publication` | Typed publication and precise target/entry impact |
| `put_presentation`, `remove_presentation` | Locale+node and allowed human fields; missing removal refuses |
| `import`, `reconcile` | Explicit complete/scope target and ID matching; lower to the same commands and preview, not a bypass |

`BootstrapGraph(expected0,seed,approval)` and
`MigrateGraph(expectedhead,exactdiff,migration_id,approval)` are trusted envelopes
with independent Host permission and detailed preview. Creation-policy changes
exist only as `CreationPolicyMigrated` facts under those envelopes, never as
ordinary field updates or a generic bypass flag. Root reordering uses explicit
root placement/move operations with the same root authority and order checks.

Apply commands in array order. Every referenced source/target must already exist
in base or an earlier create: no forward creation references. Intermediate
unowned objects are permitted, occupied singleton replacement is not. Bounds
check insertion after removal. Final ownership/cycle/reference validation is
global. Repeated field writes are last-write-wins with the full command audit.
Delete/recreate same ID is forbidden; explicit restore preserves prior identity.
Never-committed create+delete vanishes without tombstone. No-op compares the full
persistent state, including tombstones/schema, not visible node counts alone.
Cross-graph “move” is separate explicit copy/reference migrations, not atomic.

Deterministic preview pipeline: strict decode→default normalization→ordered
commands→identity/slots/root uniqueness→reachability/containment cycle→uses and
publication/NodeRef closure→kind/mode/locales→binding descriptor assertions→
mutability/impact→current admission→canonical candidate/diff. Pure validation
does not acquire resources, call registries remotely or execute contributors.
Uses cycles are legal and terminate via visited traversal. Diagnostics carry
stable code, node/publication ID and JSON pointer, sorted by location/code/ID
without revealing unauthorized state.

PreviewReceipt binds base revision/digest, candidate digest, command digest,
resolved-fact digest, complete diff/impact/report, approval subject and expiry.
Allocate generated IDs before preview and reuse them at commit. Commit enters
owner serialization, checks existing idempotent receipt first (with current read
permission), then base CAS, unexpired matching preview, current admission and
atomic accepted facts+receipt append, followed by projection/watermark update.
Same key+same request returns the original complete result, even if base changed
or preview expired. Same key+different digest returns `idempotency_conflict`.
Stale base returns `revision_conflict`; no automatic rebase or renewed approval.
No-op durably appends `CommandCompleted(noop)`, advances sequence but not graph
revision. Rejected validation is not `GraphChangeCommitted`.

## Resolved facts, records and pure replay (D10–D11)

Normalize facts from base versus final candidate, never command transient states:
creation/restoration→node field changes→complete roots/members/uses/publications/
locales results→deletions/tombstones. The minimal closed vocabulary is
`NodeCreated`, `NodeFieldsSet`, `NodeRestored`, `NodeDeleted`, `MembersAssigned`,
`RootsAssigned`, `UsesAssigned`, `PublicationPut/Removed`,
`SemanticMigrated` and trusted `CreationPolicyMigrated`. The M17–M21 candidate adds
`GraphSeeded`, `GraphFieldsSet` and aggregate `PresentationsAssigned` to close
bootstrap, metadata and all presentation ordering/default/empty-catalog states.
New writes use the aggregate; granular presentation facts remain strict old replay.
Exact normalization/provenance and hash subjects are centralized in
[schema contracts](schema-contracts.md#m17m21-closure-and-transactionfilejournal-integration).
Changed fields contain
complete normalized after values and required base preconditions. Apply one
patch-set in two-phase object/relationship construction then unified deletion;
all preconditions refer to base, after digest to the whole result. No intermediate
invalid state is externally visible. Replay MUST NOT rerun commands.

CommitRecord carries schema/store/sequence/batch ID/previous hash/events/optional
command receipt/time/actor/correlation/payload digest/record hash.
`GraphChangeCommitted` includes graph/base/result revisions, before/after digest,
resolved facts, command digest and reason. Record batches are indivisible to
readers/watch. Reducer checks semantic version, preconditions, chain and complete
result digest. No policy, clock, randomness, network, registry resolution or Tool
execution during replay. Missing/unknown required facts fail closed.

Other events include no-op completion, release request, prepare success/failure,
supersession, per-boot replica observation and recovery action. They update
receipt/release/history projections, not definition revision. Restore only the
latest accepted desired state; do not prepare every historical release. If an
accepted append outlives failed in-memory projection, stop writes and rebuild;
never delete the accepted record to “roll back”.

## Canonical bytes and hash scopes (D09, D25.2)

New profile ID: `contexture.canonical/jcs-binary64-safeint-v1`. Input is UTF-8
without BOM, rejecting duplicate keys, invalid Unicode, NaN and Infinity. Do not
normalize Unicode or instruction text. RFC 8785/JCS object sorting is UTF-16
code-unit order, distinct from node-array ID code-point ordering. Arrays retain
semantic order. Numbers use correctly rounded binary64/JCS serialization,
including negative-zero normalization. Before losing the original number lexeme,
reject mathematical integers with absolute value >2^53−1, including `1e20` and
`9007199254740993`; reject overflow and nonzero values rounded to zero. Fractional
values explicitly have binary64 semantics, not arbitrary decimal precision.
Exact-decimal/large-integer schemas incompatible with this representation refuse
managed import; never silently convert schema values to strings.

Legacy Catalog payload remains `contexture.node-catalog/v1` with its existing
code-point key ordering, normalization and own content digest. Wrap that payload
and digest in the managed envelope with the new profile: two hashes, not a
replacement legacy digest. Old wire/profile/golden bytes are untouched. A default
JSON writer is not evidence of either canonical profile. JSON Schema 2020-12
checks shape, semantic validation checks the graph; schema resolution is pinned
locally by manifest, never network `$ref` fetching.

Hash format is `sha256:` plus lowercase hex over UTF-8 domain prefix, NUL, then
canonical payload. Fixed prefixes and exact scopes:

| Prefix | Payload |
| --- | --- |
| `contexture.graph/v1` | Complete normalized definition without provenance revision/time/actor or its digest |
| `contexture.facts/v1` | Resolved facts only |
| `contexture.manifest/v1` | Complete normative execution manifest except its digest |
| `contexture.journal-record/v1` | Entire record except record_hash |
| `contexture.view/v1` | Complete authorized rendered view content |
| `contexture.page/v1` | Page items+complete+view descriptor, excluding cursor/identity |
| `contexture.command/v1` | Normalized request fields below |
| `contexture.preview/v1` | Approval subject binding base, candidate, facts and command digests |

Record `payload_digest` covers events+optional command_receipt, not the outer
record. Q01 now has one coherent additive candidate: domain
`contexture.journal-payload/v1`, with exact RecordPayload and PreviewSubject
objects in [schema contracts](schema-contracts.md#q01-exact-canonical-objects-and-domains).
It requires acceptance; ports must not treat it as review accepted.
Self-digest fields are omitted from their own respective inputs.
View digest excludes standalone boot/generation/cursor fields but includes actual
rendered generation-qualified refs: rebuilding may change it. Cross-generation
structural comparison uses definition/structural projection, not old view cache.
Record chain detects checked corruption, not authenticated authors or all suffix
deletion in the absence of an independent trusted anchor.

Request digest contains declared command schema, graph ID, base revision,
normalized typed commands with assigned IDs and reason. Actor scopes the key;
correlation, deadline, preview expiry and renewed approval token are excluded.
Approval binds complete preview; current authority revision is rechecked. The
receipt preserves full recoverable result (or immutable blob+digest), request
digest, graph revision, journal sequence and status/noop, not only the key.
Release facts are not runtime observation. Promised retry windows retain results.

## Generations, release and unknown (D14, D25.3)

ReleaseRequest includes graph/target, exact revision+definition+manifest digests,
expected desired epoch, key, exact/latest policy, reason and approval ref.
Durable authorized intent increments epoch. Exact cannot silently skip warming;
latest is explicit opt-in coalescing with retained superseded history. Durable
intent is requested/superseded; per-target+instance+boot+epoch+attempt ID tracks
pending/preparing/ready/failed/consumed. Validation is a preparation stage, not
an incompatible second attempt status vocabulary. Explicit retry adds an attempt
without incrementing the same request's epoch. Generation lifecycle is
Prepared→Serving→Draining→Retired, not a graph-revision active boolean.

Prepare may run with bounded concurrency. Each target coordinator serializes
desired changes and pointer swaps. After Ready, recheck durable desired epoch,
current admission, cancellation and resource health. Gate holds pointer/lease
operations only: no I/O, JSON work or user callbacks. New durable desired first
invalidates stale ready attempts; late prepare cannot overwrite known newer intent.
Network replicas can report observed lag, not global instantaneous convergence.

Swap is a local in-memory fact. Failure to append ReplicaObserved does not undo
the already-serving generation; retry observation. Every observation carries
instance/boot/generation/epoch/manifest. New boot starts with no active generation;
old observation is history, not startup adoption. Cold desired failure is not-ready
unless current authority explicitly permits a compatible healthy LKG, with lag
still reported. Failed candidate preserves existing service and unwinds resources.

Wait must specify `instance(instance_id,boot_id)` or adapter-provided
`all_of(explicit_targets)`; default is calling owner's current local boot, not
fleet. Timeout returns pending+receipt without undo. Supersession preserves both
prior observations and superseding fact. Release rollback checks current authority
and historical artifacts, creates new epoch and never rewinds head or Tool effects.

CommitUnknown permits only old serving requests and authorized diagnostic/receipt
recovery reads. Side-effect-free prepare may finish, but no swap, provisional
desired exposure, mutation append or release append is allowed. Under owner lock,
confirm either the record is durable (recover receipt and watermark), or it was
not published and no accepted facts exist (keep watermark; same-key retry allowed).
Missing receipt alone proves neither outcome. Indeterminate state remains frozen.

## Canonical refs and request/run leases (D15–D16, D25.4)

Managed refs are exactly `ctxg:v1:<graph64>:<generation_uuid>:<node64>`: five
colon-separated segments, no extras. IDs encode original UTF-8 bytes as unpadded
base64url. Decode/reencode must match exactly: reject padding, empty segments,
slashes, invalid UTF-8 and aliases. Base64url is case-sensitive. Generation is a
lowercase standard 36-character UUID, fresh on each build and bound to boot.
Whole ref is at most 1024 ASCII bytes; author graph/node IDs are 1..256 bytes.
Refs are not secrets and do not authorize. Renderer alone emits cards, uses,
process contracts and publication references; authors must not synthesize them.

Request order: verified identity→gate acquire generation/request lease→selection
intersected with current identity ceiling→ref/plane/door checks→validator→handler→
result/error projection→finally release. Awaiting current authority does not switch
the pinned generation. Every lookup/navigation/result uses that same generation.
Fixed five gateway names and ordinary argument shapes remain; managed initialization
declares profile. Static wire remains static. Model prompt-plane guessing and
noncanonical refs fail before handler without global-root diagnostic leakage.
Completion keeps existing rank order. Stable public names/URIs/routes resolve at
admission to current or explicitly leased generation, not an old API closure.
Direct legacy route clients execute the deployment-declared current generation
and report it; old browser requests do not acquire historic approval retroactively.

RunLease binds stable actor, tenant, graph, target, generation, initial stable-ID
subtree ceilings and policy-view version. Effective scope is initial ceiling ∩
current authority expressible on the pinned graph ∩ request selection. If current
policy cannot verify old IDs, fail closed. Renewal only extends TTL; upgrade creates
a new lease under current authority without carrying old write approval. Tokens
bind owner boot and are not identity or business Tool arguments. Native callers
use explicit RunLeaseContext; HTTP uses protected `Contexture-Run`, MCP negotiated
request `_meta.contexture/run` injected by the Host. Both carriers together refuse.
Ordinary MCP supports per-request pinning without an implicit persistent Session.

The local Python candidate retains the disabled-header default.
`ManagedMCPServer(..., run_context_resolver=..., enable_run_header=True)` explicitly
enables protected HTTP routing. Configured gateway Tools declare the candidate
`contexture/run-carriers` metadata, including supported carriers and their
mutual exclusion, without adding a run-management Tool. A header is one printable
ASCII opaque Host handle, at most 4096 bytes; duplicate, empty, oversized and
mixed header/metadata selectors refuse. `X-Contexture-Run` is not an alias.
Supplying a selector never silently selects an ordinary fresh request.

`ManagedRestSurface` uses an explicitly enabled `RouteRunResolver` to obtain the
same runtime's validated run request scope; it additionally checks the public
read-only `is_run_pinned` lifetime marker before route lookup/invocation.
`supported_run_carriers` exposes SDK negotiation without a new public discovery
endpoint. Identity, current policy, actor/tenant/boot/expiry and scope ceilings
remain enforced by the real runtime/RunLeaseService, not a header or marker.
Successful direct REST replies carry `Contexture-Generation` from that exact
request pin; errors do not advertise a generation or infer an active one later.
These are candidate adapter semantics, not an accepted common token wire format
or an authorization to expose run creation/renewal to model arguments.

Unleased refs match current generation only. Matching valid existing run can use
a retained Draining generation and start new requests there, but Draining accepts
no new runs. Wrong actor/tenant/graph/boot, expiry or missing generation refuses.
Same revision rebuilt with a new UUID still makes an unleased old ref stale.
Expiry blocks new calls; already admitted work retains resources until actually
finished. Current revocation still applies; pin/TTL is not permanent authority.
Trusted resolve-by-ID may return a newly authorized ref, never automatic write
retry or transfer of exact-plan approval. Persistent Tasks store definition and
manifest/Profile metadata; restart creates a new generation/lease or blocks for
explicit upgrade if the artifact cannot be reconstructed.

Resources have independent reference counts. Only explicitly share-safe handles
cross generations; candidate failure unwinds acquisitions in reverse order.
Retiring one generation never automatically closes Host-wide Channels. Background
consumers/state migration need a separate explicit lifecycle. Shutdown stops new
admission/writes/watch, drains, cooperatively cancels, waits for real leases, closes
resources then releases owner lock. Deadline expiry reports leaked/stuck resources,
not fake cancellation completion or forced close of shared handles. Native
uncooperative work needs isolation/terminable process policy; cancellation is not
side-effect rollback.

## Diff, history, revert and versions (D11, D17)

Definition diff aligns stable IDs: moves are not delete/create; JSON-pointer
field before/after distinguishes absent from null. Groups have fixed semantic
order and entries ID/field code-point order. Ordered membership diff contains
full before/after lists, not spurious moves for every shifted ordinal. Effective
impact includes derived subtree paths/planes/process/views/NodeRefs; uses cycles
terminate. Execution diff compares manifest/schema/validator/bindings/resources;
release diff compares desired and explicit observations. Text comparison does not
infer semantic equivalence. Optimized and full diff must agree.

Event history records process; snapshot endpoint diff records net state (rename
back can be empty). Revert(base,target,scope) makes a new exact candidate under
current CAS/admission/mutability/reference closure, without deleting unrelated
concurrent work. Release rollback is separate. Neither inverts business Tools.
History reports retained lower bound and missing revision/record/schema/blob;
wall time is a filter, journal order authority. Readable history does not prove
historical executable artifacts exist. Pure deterministic event/definition/snapshot
upcasters preserve original bytes/profile checks; semantic migration is an explicit
new revision with source mapping, not reauthorization of old events.

## Query, paging, watch and large payloads (D18, D25.5)

Queries cover definition head/revision, nodes/roots/members/ancestors/subtrees,
uses/dependents/impact, history/diff, releases/replicas and receipts. Authoring
definition and exact serving generation are explicitly different views. Catalog
keeps application/model/surface and summary/definition meanings; managed envelope
adds graph/revision/manifest/generation/view descriptor/payload/catalog digest.
Standard metadata and OC extensions are not a second editable tree.

Page defaults are provisional 200 items (max 1000), 8 MiB response budget.
Return items, next cursor, snapshot identity, complete and accurate authorized
total count only when permitted. Snapshot node pages use stable preorder; refs
may point to later pages of that snapshot. Metadata-only is ROUTE-level, not a
misnamed legacy summary; large relations have separate independent APIs.
Do not silently split one semantic event batch or truncate ordinary Role open.
Too-wide ordinary open returns `too_large` and an explicitly negotiated paged
entry. Q02's concrete candidate is the explicitly negotiated optional read-only
`contexture_managed_page` tool, with registered request/response schemas in
[schema contracts](schema-contracts.md#q02-explicitly-negotiated-managed-paging-entry).
It changes neither ordinary open nor a non-negotiating client's five gateways.

Paged open comprises owner_active, at most two process_cards always alongside
their designations, member_page and uses_page with separate cursors. Programs
remain ACTIVE; member cards remain ROUTE; INSPECT gains none of these programs.
All slots have declared order; duplicate process references in member pages do
not double-count members. A process designation without its authorized card
refuses composition. Node pages do not inline unbounded uses/dependents/members.
Large single schema/instructions still meet receive limits; authorized immutable
blob descriptors can deliver legal large schemas/diffs/previews, not infinite
single items inside ordinary pages.

Cursor is opaque and tamper-resistant, bound to store/graph, snapshot revision or
journal position, view/selection digest, authority-view version, filter/limit
profile and expiry. Signing keys are Host-owned/rotatable; lost key requires
resync, not journal repair. Every page rechecks current authority; changed view
invalidates cursor and clears inaccessible cached entries. Do not expose hidden
counts/global sequence/digests through cursors, ETags, errors or fetch descriptors.

Take snapshot and resume position at one owner durable watermark. Watch then
delivers complete at-least-once batches after it, deduplicated by batch ID and
consumer checkpoint. Topics distinguish definition commit, release and serving.
Even filtered-empty changes advance opaque positions without hidden counts.
Overflow by entries or bytes closes with `resync_required`; retention gaps return
`history_unavailable`. MCP list_changed is only for actual top-level native
tool/prompt/resource list changes, not a substitute for internal graph versioning.

Watch uses atomic BatchDescriptor containing batch ID, opaque sequence cursor,
authorized facts digest, size and inline payload or authorized fetch ref.
`max_watch_inline` is at most 1 MiB. Over-budget batches are fetched completely,
then applied once before checkpoint advancement. Transport chunks are not partial
semantic commits. Restricted views never disclose the raw full-batch digest.

Large import/replace stages a complete canonical blob with digest/size through
BlobPort. A typed payload ref causes owner preview/commit to load/verify all content
and total definition budget. External max_commands=10000 and inline transaction
16 MiB are distinct from max_expanded_operations=2000000 and resolved payload
64 MiB (provisional configurable defaults). Expansion is fully validated before
one atomic commit. Blob backing does not waive resolved payload limits; required
blobs publish durably first. Stored definition max 256 MiB does not imply a same-size
single import is allowed. Reject over budget before append. Staging is not accepted
state; unreferenced staged data follows explicit GC policy.

Huge preview/diff/impact/receipt returns immutable result manifest+digest and
authorized page/blob access. Approval binds the complete candidate/facts, never
one page; review policy decides required material acquisition without pretending
someone read it all. Indexes, receipts, tombstones, stages/drafts, leases and
candidate caches all count toward RAM/disk/recovery quotas. Pressure rejects new
work instead of evicting promised retry facts or resources still in use.

## Public ports, management and errors (D19–D21)

Responsibility ports: GraphStore load_head/load_revision/append_if_head/get_receipt;
Journal append/read_batches/checkpoint/watermark; GraphCompiler validate/compile;
BindingRegistry resolve_pinned; Admission decide; ReleaseManager request/status/
wait/rollback; ManagedRuntime acquire/invoke/run_lease; Projection query/page/watch;
FileOps lock/publish/sync/read. They need not be separate services. Adapters MUST
NOT build independent transaction state machines.

Management HTTP is optional and separately authenticated/authorized from model
invocation: definition/query, transaction preview/commit, receipts/history/diff/
revert, release/wait, pages/watch, backup/restore/diagnostics. This trusted surface
does not relax business REST's no-arbitrary-ref rule. No per-node top-level MCP
CRUD expansion or debug backdoor. Debug is loopback read-only and obtains actual
generation from ManagedHost, not a permanently cached constructor snapshot.
CLI target includes graph init/export/validate/preview/commit/history/diff/revert;
release prepare/request/status/rollback; catalog/watch; store verify/checkpoint/
backup/restore/doctor. Online writes use owner; offline writes hold exclusive lock
with service stopped and cannot claim runtime adoption. JSON/human output, stable
exit categories, native templates/demos and secret redaction are product duties.

Stable errors: schema_invalid/semantic_unsupported; identity_conflict/slot_invalid/
cycle/dangling_reference; mutation_protected/permission_denied/approval_stale;
revision_conflict/idempotency_conflict; commit_unknown; store_in_use/journal_corrupt/
durability_unsupported; binding_unavailable/contract_mismatch/prepare_failed;
release_superseded/generation_unavailable/stale_reference; cursor_invalid/
resync_required/history_unavailable; resource_busy/too_large/limit_exceeded.
HTTP categories: invalid 400, denied 403, conflict 409, expired/history 410,
size/rate 413/429, not-ready 503. MCP returns stable tool errors without hidden IDs.
Retryable is stage-scoped, not permission to rerun business writes.

## Explicit invariants and version axes (D02, D09)

| Source invariant | Required target fact |
| --- | --- |
| I01 | Every committed revision is a complete valid forest, never a partial batch |
| I02 | Journal owns managed definition/release history; head/snapshot/index are rebuildable |
| I03 | Tool schema, validator, effect and handler come from one pinned binding/generation |
| I04 | One request uses one generation from selection through response |
| I05 | Endpoint active Catalog and runtime report the same generation/manifest |
| I06 | Stale CAS cannot overwrite head; same key/different request conflicts |
| I07 | Replay only reduces facts, never authorizes or executes resources/Tools/releases |
| I08 | Head, desired and per-boot observed are independent facts |
| I09 | Failed candidate preserves serving; uncertain durability stops writes and swap |
| I10 | Held leases prevent resource close; pins/TTLs do not preserve revoked authority |
| I11 | Move/cascade/replace/identity reuse cannot bypass static creation policy |
| I12 | Every native binding executes shared observations; copied assets are not parity |

Package/API version, managed wire profile, journal format, graph semantic version
and binding ABI are independent compatibility axes. A package version bump cannot
establish event readability, execution portability or managed-wire equivalence.