# Managed schema contracts and design review — additive v1 candidate

Status: coherent proposed mapping, **not review accepted**, no accepted revision.
Authority: detailed design D00–D25, especially D25; legacy spec/golden unchanged.
The definitions in [the schema](schemas/managed.schema.json) are locally registered
Draft 2020-12 contracts. Its HTTPS `$id` is an identifier, not a fetch endpoint.
Resolve only this manifest-registered document; never fetch remote `$ref`s.

## Model shape sent to implementation owners

NodeSpec is a **flat tagged object**, not `{definition:{...}}`: common node_id,
kind, name, description, mutable, uses, extensions; Role adds role_variant,
members and exactly one instructions/instruction_template; Skill has only that
instruction union; Tool has binding/input_contract only in runtime mode. The
existing prose did not specify a nested wire container, so flat shape is a
recommended additive v1 decision, not a claim about a production Python class.
At inspection the parallel graph directory contained canonical/errors/references,
not an existing NodeSpec model to override.

`mutable` is boolean true/false or string `inherit`, never strings `true`/`false`.
Members is an object with optional pre_process/post_process **IDs**, not arrays
and not null. The three list slots default to empty arrays. An omitted singleton
stays absent. Typed template parts use kind=Text/text or kind=NodeRef/target_id/
optional label. Presentations is explicit default_locale plus an ordered catalogs
array of locale/entries; entries refer to node IDs. Publication tags are
prompt/resource/route. Portability is portable+contract_id or native+language/
version/factory. These concrete spellings require coordinating owner acceptance.

Every fixed object is closed with additionalProperties=false. Arbitrary JSON is
allowed only at declared JSON data boundaries (extensions/config/schema/result/
diff field values), never as an escape hatch for core authoring fields. Extension
keys use namespace:key; unknown **execution-affecting** extensions still require
semantic manifest support. JSON Schema validation does not insert defaults.

Normalization MUST precede hashing: Graph publications/extensions -> []/{}, node
mutable/uses/extensions -> inherit/[]/{}, Role role_variant/members -> ordinary/
{children:[],skills:[],tools:[]}, binding config/secret_refs -> {}/[]. Normalize
only fields legal for the actual kind: never put role defaults on Skill/Tool.
No implicit graph mode, semantic version, locale, policy, authority or publication
model_may_open defaults. Managed page limit defaults to 200. Persisted META must
explicitly include durability even though fresh-store policy defaults to strict.
Optional absent stays absent; no general null coercion. Core nullable field is
prev_record_hash for sequence 1 only. Explicit null remains valid *data* in JSON
extensions/results/OptionalValue.value and differs from `{present:false}`.

## Q01: exact canonical objects and domains

Recommendation: add `contexture.journal-payload/v1` as a ninth distinct domain.
Reusing facts or record domain would obscure two different preimages. This is
an additive candidate requiring acceptance, not a silent amendment to D25's list.

Central integration registers this domain and M21's tenth
`contexture.checkpoint/v1` domain and the existing adapter's eleventh
`contexture.file-journal-backup/v1` domain in the shared canonical allowlist,
alongside exactly the eight existing managed domains; see M22 in
[decisions](decisions.md#central-integration-decisions-candidate-not-accepted).
The FileJournal adapter delegates domain hashing to that registry, retaining
separate raw-byte blob SHA256. Unknown schema tags/domains remain rejected.
Registration is executable candidate support, not Q01/Q06 acceptance. Scoped
payloads follow M23's complete-candidate and exact outside-boundary algorithm in
[model](model.md#scoped-complete-candidate-replacement), never partial JSON patches.

* payload_digest = H(journal-payload, RecordPayload): **exactly** `{events:E}`
  when no receipt, or `{events:E,command_receipt:R}` when present. Do not insert
  null, schema, actor, time, sequence or either outer digest. Receipt includes
  its complete normalized operation result/blob descriptor. Events stay ordered.
* record_hash = H(journal-record, complete normalized CommitRecord minus only
  record_hash). In particular payload_digest, provenance and prev_record_hash
  participate. First record has explicit prev_record_hash=null; others hash link.
* command_digest/request_digest for an ordinary transaction = H(command,
  CommandSubject), exactly schema, graph_id, base_revision, commands, reason.
  IDs are already assigned; commands/defaults normalized. Exclude key, actor,
  correlation, approval_ref, deadline and preview expiry. Actor scopes the key.
* preview digest = H(preview, PreviewSubject), exactly schema=contexture.preview/v1,
  graph_id, base_revision, base_digest, candidate_digest, command_digest,
  resolved_fact_digest. `approval_subject` is this object, not the digest alone.
  Expiry, tokens, diagnostics, paged diff and report ordering do not change it.
  A commit MUST compare outer receipt fields with the subject and recomputed
  full candidate/facts, and recheck current authority. This hash is not approval.
* facts_digest = H(facts, ordered complete resolved_facts array), not an event or
  record wrapper. Definition = complete normalized GraphDefinition; no provenance.
* page_digest = H(page, PageContent), exactly items, complete, view_descriptor.
  Snapshot identity, cursor and total_count are excluded, not authorization.

Here H(short-domain,P) uses the full contexture.* /v1 domain from protocol,
NUL and JCS(P); public form sha256:lowercasehex. Fixed payload/preview preimages
and expected hashes are in [reference vectors](referencevectors/contracts.json).
Raw inputs are bytes encoded in [rawcanonical vectors](rawcanonical/vectors.json),
not reserialized JSON pretending to preserve duplicate keys or numeric lexemes.
Canonical tests must independently reject duplicates, invalid Unicode/BOM,
nonzero underflow/overflow and mathematical unsafe integer lexemes **before**
binary64 conversion. Schema alone cannot implement that lexical rule.

## Q02: explicitly negotiated managed paging entry

Recommendation: one optional **read-only** tool `contexture_managed_page`, with
PagingCapability, ManagedPageRequest and ManagedPageResponse schema entry points.
The fixed five ordinary gateways and static tools are unchanged. Only a managed
Host and client both negotiating `contexture.managed-paging/v1` may expose/use it.
Native/HTTP adapters negotiate the same schema identity; MCP advertises the
descriptor under initialize capabilities.experimental[capability] and requires
the client's matching capability before installing the extra tool. Exact native
tool-list changes use MCP list_changed. No capability means ordinary full open
or too_large, **never** truncation or an unadvertised sixth tool. A too_large
diagnostic may name the capability, not hidden targets or authority data.

Request requires schema/operation/ref; optional limit 1..1000 defaults 200.
Operations open/members/uses/dependents have independent continuation. Open uses
member_cursor and uses_cursor, while relation calls use cursor. All cursors bind
the complete request view/filter/limit/snapshot/current authority profile; changed
inputs require refusal/resync, not a new unannounced snapshot. Refs are returned
generation-qualified refs, with run context carried by the Host, not Tool args.

Open is Role-only. Response includes owner_active, at most two designated
process_cards with actual authorized ROUTE cards, member_page, uses_page and
deduplicated complete member_count. Every slot retains pre/children/post/skills/
tools ordering; process cards may reappear in members but count once. Relation
pages contain ROUTE cards only. Designation uniqueness, owner kind, same snapshot
and card/ref consistency are semantic checks, not inferred schema guarantees.
All slots' full counts and complete/not-complete must be available; no half-group
is described as complete. Member continuation does not silently advance uses.
INSPECT never gains owner program/process contracts through this capability.

## Q03: exact FileJournal paths

Recommendation: relative POSIX protocol spelling (native separators are adapter
details), ASCII lowercase extension/digest, no escaping or identity path segments:

| Asset | Exact relative path / formula |
| --- | --- |
| META | META.json |
| Lifetime owner lock | writer.lock (stable inode/handle; never unlink for takeover) |
| Bucket | records/{pad20(floor((s-1)/10000))}/ |
| Record | records/{bucket}/{pad20(s)}.json, 1 <= s <= 99999999999999999999 |
| Blob | blobs/sha256/{hex[0:2]}/{hex}.blob |
| Checkpoint | checkpoints/{pad20(last_seq)}/{checkpoint_digest_hex}.json |
| Latest hint | hints/LATEST.json |
| Disposable indexes | indexes/ (not accepted state) |
| Temp | tmp/{lowercase_uuid}.tmp, same filesystem |
| Quarantine | quarantine/{lowercase_uuid}.artifact, recovery evidence required |

Blob digest is **plain SHA256 of exact stored bytes**, not a graph/facts domain
hash. BlobRef.size is exact byte count as a counter string. JSON payload blobs
contain JCS bytes and media_type=application/json; textual blobs preserve UTF-8
bytes. Domain digest of decoded content and blob byte digest are different
identities and must not be interchanged. No graph/node ID, colon in digest, URI,
percent encoding, absolute path, dot segment, mixed-case hex or caller-supplied
path is accepted as a storage selector. Validate decoded path components,
symlinks/reparse and containment at the FileOps boundary, not just string regex.
Bucket/record boundary and maximum vectors are in contracts.json. Existing
durability/owner/watermark/unknown rules remain mandatory, not weakened by naming.

## Design contract review and unresolved scope

| Design | Schema coverage and additional semantic duty |
| --- | --- |
| D03–D05/D25.1 | GraphDefinition/NodeSpec/Members/Publication/Presentations: ID byte budget, forest, at least one model root, cross-slot names, variants, locale normalization/fallback and read-only argument-free Resource still need semantic validation |
| D06–D08 | Full ordinary command union; trusted envelopes separate; create empty relations, update allowlist; current admission, root rights, exact delete closure, no forward refs/recreation, insertion-after-removal and CAS not proven by shape |
| D09/D25.2 | Counter/Digest and exact hash subjects; default normalization, raw lexical gate, JCS UTF16 order, node code-point array order and dual Catalog profile not replaced by schema |
| D10–D11 | Closed final Fact/Event/CommitRecord/Checkpoint/receipt; preconditions refer to base, atomic patch application, after-digest, full historical projections and no-effect replay are runtime obligations |
| D12/D25.3 | META/path vectors/prev hash; lock, no-replace/sync/unknown, hint discovery and complete checkpoint trust require actual fault evidence |
| D13–D16 | Binding/ExecutionManifest/release/observation/ref/wait: portable decoder subset, pinned handler/effect/defaults, local epoch serialization, leases/current authority remain gates |
| D17–D19/D25.5 | Diff distinguishes absent/null; paging/watch envelopes and complete batch fetch; no hidden digests/counts, authorized projection, byte budgets and backpressure require actual Host tests |

**Still not accepted:** M17–M21 now give single candidate rules and vectors for
trusted bootstrap/migration/release request preimages, revision-zero before digest
and checkpoint digest/normalization (below). Remaining unspecified: canonical result
manifest/error/RunLease/Catalog envelope schemas; publication reorder command
semantics; exact full locale validation/normalization algorithm;
ordered presentation catalogs/entry canonical ordering; durable pending/consumed
attempt event representation; authorization fetch protocol and signing algorithm;
META creation/clone provenance expansion and exact LATEST/bucket-index schemas.
These are not permission to drop the corresponding design requirements. Locale's
schema is only a permissive hyphenated-tag shape gate; full tag validity and
normalization (including private-use/extensions/grandfathered tags) still need a
coordinated algorithm, not a reduced locale subset. NodeFieldsSet after/before
values are field-typed; diff/authorized changes/results have explicit JSON data
boundaries needing port-independent semantic validators and access control.
Future acceptance must close these issues coherently, synchronize Chinese design,
English schema/vectors and three-language ledgers, and record accepted revision.

No production Python edited, no old golden/spec changed, no commit/push performed.
Focused schema/vector execution is not native reducer/release/file durability,
three-language parity, review acceptance, or WP01 complete freeze.

## Executed evidence (2026-11-05)

Native WSL2 Linux 6.18.58.1, CPython 3.14.7 at the requested
/tmp/contexture-stdio-baseline-20261105/bin/python; jsonschema 4.26.0,
referencing 0.37.0, rfc8785 0.1.4, pytest 9.1.1. No dependency sync needed.
Command: python -B -m pytest -q -p no:cacheprovider
tests/test_managed_spec.py tests/test_managed_canonical.py
tests/test_managed_references.py. Final observed result: **246 passed in 3.56s**,
exit 0. New specification tests contribute 83 cases; 163 canonical/reference
cases belong to the parallel implementation slice, not this slice's authorship.
The new tests use an independent test-only lexical oracle and RFC8785 writer;
they do not import production graph code. Schema self-validation and all local
refs succeeded (83 definitions); editor diagnostics reported no errors.

An initial focused negative case caught regex `$` accepting a trailing newline;
counter/name/ref/namespace/locale patterns now use absolute-end assertions and
regression cases. This correction did not weaken or remove a test.

Read-only Git audit: HEAD remains 27f20868fe74ed4ebe7807c2f5f65572301c8745,
index has no staged paths, tracked diff lists only pre-existing parallel
pyproject.toml and uv.lock; git diff --check exit 0. This slice wrote only
spec/managed assets and tests/test_managed_spec.py, not those parallel files.
Managed assets are untracked at that HEAD, so ordinary git diff --check alone
does not validate their content; executed JSON/schema/tests provide that evidence.
No static full gate, native Go/TS producer, actual Host, crash or power-loss
experiment was run here, and no accepted revision has been assigned.

## M17–M21 closure and transaction/FileJournal integration

Status: candidate-not-review-accepted. This section supersedes earlier statements
that the corresponding preimages/vocabulary are missing; it does not freeze them.

### Complete facts and deterministic reducer

New normal form: creations/restorations by code-point ID; for each retained ID,
CreationPolicyMigrated then NodeFieldsSet (if needed); GraphFieldsSet; RootsAssigned;
MembersAssigned by ID; UsesAssigned by ID; PublicationPut/Removed by publication ID;
one PresentationsAssigned; deletions by ID. Trusted non-noop migrations prepend
SemanticMigrated. Every field/list compares the immutable base, never a transient.
GraphFieldsSet has exactly changed application_name/mode/extensions keys in both
before and after (plain complete values, not OptionalValue: these fields always
exist normalized). schema/semantic_version/graph_id cannot be smuggled through it.
application_name is stable ordinary-authoring metadata: the ordinary command
owner must keep its rename authority restriction; a pure endpoint resolver is
not permission to issue such a command. Mode remains subject to full validation.

PresentationsAssigned carries complete normalized Presentations on both sides;
all catalog/entry order, empty catalogs, normalized default and locale tags are
preserved. New normalization never synthesizes a default catalog or sorts arrays.
Granular PresentationPut/Removed facts were removed before any release; they are
unknown fact types and refuse. PresentationsAssigned is the only encoding.
PublicationPut.position is the FINAL ordinal, Removed.position the BASE ordinal.
Every changed or shifted retained publication gets Put, all final positions form
exactly 0..N-1. Thus no new PublicationOrderAssigned is needed. Old/new preconditions,
closed fields, exact omissions/duplicates/order and complete after digest are
checked by reconstructing, globally validating, then renormalizing the endpoints.

### Bootstrap and trusted migration API (pure, not authority)

UnseededState(graph_id) exposes revision="0", tombstones=(), no definition.
definition_digest is H(contexture.graph/v1,null), canonical UTF-8 bytes `null`,
digest sha256:b6c3eff852aaaefee276783389d2c7115dc1fc3ced6a1a90216daa936bf5ef3a.
Identity is independently bound by event/subject graph_id, not a fictional zero
definition. bootstrap_event(base, seed, command_digest, reason, approval_ref)
emits GraphChangeCommitted 0→1 with exactly GraphSeeded(before:null,after:seed).
Its migration={kind:bootstrap,approval_ref,source_digest:before_digest} is required.
apply_event(UnseededState,event,tombstones=[]) creates RevisionState1 only after
whole normalized nonempty seed validation. No no-op, prior tombstone, second seed,
alternative null digest, schema/version or graph identity mismatch is accepted.

resolve_migration(base,candidate,migration_id,exact_diff,source_mapping) returns
ResolvedChange plus migration_id/source_mapping; exact_diff equals definition_diff
endpoints byte-for-byte, source_mapping is the exact sorted retained/new/deleted ID
partition. No arbitrary mapping or ID-kind rewrite. SemanticMigrated before/after
are endpoint semantic versions; version1→version1 explicitly marks a semantic
code/policy migration and is legal. The current model decoder supports version1
only; future versions require separately coordinated pure decoder/upcaster work,
not relabeling unsupported input as version1. CreationPolicyMigrated is separate
from NodeFieldsSet; it updates any retained tombstone's policy for that live ID in
the same revision. Deleted-only identity policy is not freely rewritten.

change_event(...,migration={kind:migration,migration_id,approval_ref,source_digest,
exact_diff}) binds that change to source and exact diff. apply_event/apply_facts
accept matching provenance and regenerate all migration facts; missing, inexact or
mixed ordinary/trusted facts refuse. This is data validation, NOT authentication:
approval_ref is an opaque retained reference. The future manager MUST separately
verify host bootstrap/migration permission, exact preview approval and current
authority/fencing BEFORE append. Replay invokes no policy/approval registry.
History.bootstrap retains the real first event; History.append accepts migrations.
Ordinary resolve_facts and revert still reject mutable/kind/variant rewrites.

### Trusted and release request digests

All three use H(contexture.command/v1,subject), separated by required schema tags.
BootstrapSubject: schema=contexture.bootstrap/v1,graph_id,expected_revision="0",
seed_digest,reason. MigrationSubject: schema=contexture.migration/v1,graph_id,
expected_revision,candidate_digest,exact_diff,migration_id,reason. ReleaseSubject:
schema=contexture.release-request/v1,graph_id,target_id,revision,definition_digest,
manifest_digest,expected_desired_epoch,policy,reason. No additional fields/defaults.
Seed/candidate digests refer to complete verified normalized definitions, identical
for inline versus verified blob transport. Full exact_diff and assigned migration
ID are hashed. Exclude actor/key/correlation/deadline/approval reference/token and
preview expiry. Actor remains the idempotency namespace, NOT authorization by hash.
PreviewSubject is unchanged; command_digest there is the applicable subject digest.
The reducer accepts this opaque validated digest; reconstructing requests belongs
to the manager (request records are not replayed as commands).

### Checkpoint normalization and domain

H(contexture.checkpoint/v1,CheckpointSubject) uses all Checkpoint fields except
checkpoint_digest, with no omission/default/null substitutions. This new domain
is a candidate extension of D25, like journal-payload. CheckpointSubject is closed.
Complete projection lists are required even empty. Definitions are normalized;
each graph's tombstones sort by code-point node_id. graphs sort by graph_id;
desired_releases sort by (graph_id,target_id); receipts_index by (actor_ref,
idempotency_key), unique across the store. blob_refs sort by (digest,media_type),
with exact-byte size and no duplicate pair. release_operations,
prepare_attempt_history and observations_history preserve accepted record order
then within-record event order (not wall-time or lexical ID); never deduplicate
historical transitions or supersessions. Projection-version object keys use JCS.
Nested definition roots/publications/catalogs/entries retain semantic array order.
The owner normalizes/checks duplicate identities and anchor/counters before hash;
the schema/vector does not itself implement a complete checkpoint reducer.

The independent fixed preimages/hashes are in
[closure vectors](referencevectors/closure.json). Prefix+NUL+JCS applies uniformly;
null bootstrap uses existing graph domain, trusted/release use existing command
domain, checkpoint uses its new domain. The currently separately owned canonical
module's domain allowlist has NOT been edited by this slice: FileJournal owner
must coordinate adding checkpoint/journal-payload there before calling that helper.
No fallback to plain SHA256, facts domain, graph={} or domain registration by side
effect is permitted. Plain SHA256 remains exclusively the BlobRef byte identity.

## Closure execution evidence and next-owner handoff (2026-11-05)

Environment: WSL Ubuntu, CPython 3.14.7, existing isolated Python environment
/tmp/contexture-stdio-baseline-20261105, Node22 supplied explicitly for pyright.
Final focused events/history/spec run: **176 passed in 6.73s**. Full unfiltered
managed run: **541 passed, 1 failed in 20.87s**. The sole failure is
tests/test_managed_transactions.py::test_contract_gaps_refuse_not_lossy_or_implicit:
its second assertion expects whole-graph import default-locale change to refuse;
the new complete PresentationsAssigned now correctly represents that candidate.
The first assertion (new locale via put_presentation) still passes because the
separately owned transaction lowering retains that gap. Do not remove aggregate
support to satisfy the old unsupported expectation. No test was skipped, deleted
or deselected; the transaction test file was not edited.

Focused ruff on events/diff/history and the three owned test files: all checks
passed. Repository-configured full pyright with the explicit Python/Node22:
0 errors, 0 warnings, 0 informations. Git diff --check passed (new untracked assets
are instead covered by executable schema/tests). No old spec/golden, transaction,
policy, model, canonical or dependency file edited by this slice; no commit/push.
This is not a full static product gate, native parity or filesystem durability.

Transaction owner follow-up: use resolve_facts for full presentation/metadata
endpoint changes; explicitly lower put/remove catalog mutations and partial scope
without overwriting unrelated catalogs. The canonical fact set is aggregate, not
an instruction to silently sort/append translations. Use resolve_migration only
after a separate trusted-envelope admission path, and pass its matching provenance
to change_event. For bootstrap use UnseededState + bootstrap_event + apply_event,
not RevisionState(seed,"0"). Manager owns request-subject reconstruction, exact
preview/current authority checks, CAS/idempotency and atomic accepted append.
FileJournal owner uses the specified checkpoint subject/domain/order and vectors,
but must add the new domain to its separately owned canonical allowlist and supply
actual complete projection/checkpoint+tail/full-replay and fault evidence.