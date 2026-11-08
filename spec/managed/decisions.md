# Managed protocol decision ledger — target v1 draft

Status: specified draft choices from D25; acceptance commit is unset.
This ledger distinguishes a source-design resolution from an empirically proven
capability. It neither authorizes deployment nor overrides the static contract.
The original prose slice ran no commands. Candidate schemas/vectors and focused
tests are now supplied without production changes or commits; see
[schema contracts](schema-contracts.md) for precise evidence and limits.

## Authority and baseline evidence

The full detailed design D00–D25 and implementation plan P00–P13 were read.
Their four-repository baseline is recorded in [manifest](manifest.json).
The baseline hashes are source-design declarations, not freshly inspected Git
HEADs/remotes/dirty paths in the original prose slice. The schema slice inspected
this worktree locally; it did not refresh or certify the four-repository baseline.
Read-only inspection found Python package 1.1.0, TS package 1.0.1 and both native
conformance files pinned to specification 0.16 at
`cda2721c7c40128cd0b7eef990e5909edabd3b17`. Their old “conformant” status is
limited to that contract; it is not inherited by this managed target.

The existing full-product parity plan still orders TypeScript focused/full gates
before the corresponding Go implementation phase. Independent documentation and
inventory can proceed in parallel; this slice does not silently change that rule.

## D25-resolved choices, not implementation claims

| Decision | Target choice and rationale | Contract |
| --- | --- | --- |
| M01 | Additive managed target; preserve static contract/profile/golden | model, protocol canonical |
| M02 | Stable IDs, ordered mixed roots, five typed slots, process variants not kinds | model identity/containment |
| M03 | Creation-only inherit/false/true; false ancestor wins; trusted exact migration only | model authority |
| M04 | Sequential intent commands; final normalized base-relative facts replay atomically | protocol commands/facts |
| M05 | Canonical decimal-string counters, cap 10^20−1; head/sequence/epoch/build distinct | model counters |
| M06 | JCS UTF-16 keys and binary64 safe-int lexical gate; old Catalog code-point profile retained | protocol canonical |
| M07 | Complete checkpoint covers receipts, desired/attempt/superseded/observations and blobs | file-journal checkpoint |
| M08 | Unknown blocks writes, desired exposure and every swap; two proven recovery exits | protocol unknown |
| M09 | Default strict local single owner, explicit process_crash, unsupported means refuse | file-journal profiles |
| M10 | Exactly five ref segments; fresh UUID per build; IDs use canonical unpadded base64url | protocol refs |
| M11 | Run ceiling intersects current authority and request selection; renew never expands | protocol leases |
| M12 | Desired intent/attempt/per-boot observation separate; explicit wait targets | protocol release |
| M13 | Whole batch semantic atomicity with bounded descriptors/fetch and independent pages | protocol paging |
| M14 | Portable bindings require explicit proven decoder subset; native factories can be nonportable | model binding |
| M15 | Managed authority ports shared across native/management, business doors remain restricted | protocol management |
| M16 | Full goal/P0 performance/native parity/OC journeys remain mandatory; no deployment inferred | conformance |

These are specified design choices derived from D25, not accepted schema/byte
vectors. Accepted revision, approvals and execution evidence remain unset.

## Source summary conflicts resolved by D25

1. D07's “complete NodeSpec” create is narrowed by D25.1: nonempty members/uses
	are rejected; later ordered commands build relationships. No dual behavior.
2. D10's event list is extended by the trusted CreationPolicyMigrated fact and
	D25 final patch-set order. Replay never interprets command temporaries.
3. D14's broad attempt state summary is narrowed by D25.3 durable intent versus
	per-boot attempt states; validation is a stage within preparation. Observation
	is separate, not a single fleet-level terminal success.
4. D09's general “schema numeric policy” is concretized by D25.2 lexical safe-int
	rejection plus correctly rounded binary64, not language-selected coercion.
5. D20's inline transaction budget does not cap stored graph size; D25.5 adds
	expanded-operation/resolved-payload caps and staged references without splitting
	atomic commits. A 256 MiB stored graph does not promise a 256 MiB import.

These are source precedence resolutions, not newly discovered product blockers.

## Additional closure decisions — M17–M21 candidate, NOT frozen

These are centralized engineering recommendations derived from D25. They have
no acceptance revision and do not claim Chinese design or native port acceptance.

| ID | Single candidate rule | Evidence/contract |
| --- | --- | --- |
| M17 | New writes use one PresentationsAssigned with complete normalized before/after; retains catalog/entry order, empty catalogs and default locale. GraphFieldsSet covers changed application_name/mode/extensions, with identical changed-key sets. Identity/schema/version are not metadata. | schema-contracts.md closure; event tests |
| M18 | UnseededState has identity+revision0, no definition; before_digest=H(contexture.graph/v1,null). GraphSeeded(before:null,after:complete normalized definition), trusted bootstrap provenance, exactly 0→1 with no tombstones. | referencevectors/closure.json; bootstrap replay tests |
| M19 | Trusted migration requires exact endpoint DefinitionDiff, exact sorted source ID partition, migration ID and approval/source provenance. SemanticMigrated marks a version-1 semantic migration (before=after=1 allowed); CreationPolicyMigrated alone updates retained identity policy atomically. Kind reuse stays forbidden. No policy/auth calls during replay. | resolve_migration/apply_event tests |
| M20 | BootstrapSubject/MigrationSubject/ReleaseSubject use contexture.command/v1 with distinct schema tags. Hash content digests of verified payload definitions, not inline/blob transport selectors. Context/token/key excluded. | schema and closure byte vectors |
| M21 | checkpoint_digest=H(contexture.checkpoint/v1,CheckpointSubject), complete normalized Checkpoint minus only checkpoint_digest. New distinct domain is proposed, not an amendment to accepted D25. | file-journal.md normalization; closure vector |

### Central integration decisions (candidate, not accepted)

M22 registers exactly eleven digest domains in the shared canonical implementation:
`contexture.graph/v1`, `contexture.facts/v1`, `contexture.manifest/v1`,
`contexture.journal-record/v1`, `contexture.view/v1`, `contexture.page/v1`,
`contexture.command/v1`, `contexture.preview/v1`,
`contexture.journal-payload/v1`, `contexture.checkpoint/v1`, and
`contexture.file-journal-backup/v1` (the existing adapter's backup envelope).
The backup subject is the complete canonical envelope minus only backup_digest:
schema, meta, ordered records, digest-keyed hex blobs, and checkpoint (or null).
All use `SHA256(UTF8(domain) || NUL || JCS(normalized subject))`; adapters use
this same explicit allowlist. Schema tags are not implicit digest registrations;
unknown names and versions refuse. Raw blob SHA256 remains a distinct operation.
This registration does not accept Q01/Q06 or change any legacy digest profile.

M23 defines scoped complete-candidate replacement by the endpoint closure and
exact outside-boundary algorithm in [model.md](model.md#scoped-complete-candidate-replacement).
Payloads are complete graph definitions, never partial patches. The algorithm
matches the candidate transaction implementation; it is recorded for review,
not an accepted managed spec revision or native parity claim.

Granular PresentationPut/Removed facts were removed before release (no accepted
journal used them); aggregate PresentationsAssigned is the single encoding. Existing
PublicationPut/Removed final-position encoding already covers every ordered list,
so PublicationOrderAssigned is unnecessary: every shifted retained object gets Put.
Unsupported future schema/version decoding is explicit, not a smaller product goal.

## Genuine freeze questions and missing evidence

| ID | Issue | Required disposition before normative wire/format acceptance |
| --- | --- | --- |
| Q01 | Proposed contexture.journal-payload/v1 plus exact RecordPayload/PreviewSubject/CommandSubject | Concrete schemas and fixed preimages/hashes supplied; owner acceptance and Chinese design synchronization pending, not review accepted |
| Q02 | Proposed optional read-only contexture_managed_page negotiated as contexture.managed-paging/v1 | Capability/request/response specified; default five gateways unchanged; real Host and owner acceptance pending |
| Q03 | Proposed exact records/pad20 bucket+sequence, SHA256 blob and checkpoint path formulas | Boundary/max vectors and META supplied; hint/index/provenance metadata and format acceptance pending |
| Q04 | D24 explicitly leaves portable primitive binding decoders/default/coercion subset unproven | Specify subset plus native observations; use explicitly nonportable descriptors outside it, do not claim portable execution |
| Q05 | Supported filesystem durability, current admission authority-version/fencing, management scopes and actual SLO depend on deployment evidence | Owner/platform acceptance with exact environment; unsupported strict must refuse; no invented default credentials/SLO |
| Q06 | No accepted managed schemas/fixtures/vectors revision accompanies this slice | Coordinated acceptance and D/T/C ledger update required; prose alone cannot freeze interoperable bytes |

No additional irreconcilable core-semantic contradiction was identified between
D00–D25 and the plan after applying D25 precedence. Q01–Q03 now each have one
concrete recommendation in schema-contracts.md, not permission for divergent ports
or a claim of acceptance. Additional precise unresolved contracts are recorded there.
Q04 now has an explicit finite primitive subset and native differential evidence
in [portable binding primitives](portable-binding-primitives.md): 27 identical
input/observation cases execute captured Python, TypeScript and Go callables.
This does not accept arbitrary native ABI portability or replace review.
Q06 normative acceptance remains unset; neither is a reason to shrink scope.

## Cross-repository delta and evidence limits

The legacy pins cover 0.16 and do not establish current Python Presentation/
locale-Catalog, Node Catalog v1 schema/semantic/canonical vectors, contributors,
developer views or release metadata parity. Python static model explicitly says
Node Catalog is a Python extension and other bindings need an independent claim.
Record these as required delta inventories, not as proven missing code: this
slice read metadata/contracts, not every native module or current test result.
Managed capabilities are a separate candidate target for all three languages.
Current local native evidence is recorded in the verification progress ledger;
it is not inherited from the accepted static pin or normative managed acceptance.
The design-provided HEAD hashes and package pins do not certify worktree state.

OC has no production deployment in this assignment. Native fixture-backed
Team/Project/Reconciliation/worker-admission/Panel/source-lock journeys are
implemented and locally exercised in the verification ledger; external identity,
installed-worker adoption and production cutover remain deferred, not passed.
Code/DTO appearance alone must not be reported as adoption or verification.
Schema/vector paths and tests now exist and are indexed in the manifest. They do
not establish implementation, native parity, durability or review acceptance.
No fabricated native/Host/OC receipt is supplied.

## Review-driven revisions (2026-11-08, Python reference, candidate)

Recorded on branch `fix/managed-runtime-review` after an independent review.
They revise the candidate target; none is an accepted spec revision. TS/Go
must port each item before claiming parity (see "Port obligations").

| ID | Revision | Rationale |
| --- | --- | --- |
| R01 | Mutability: a locked node protects only its own surface (definition with its locked members, path, parent/slot, plane, metadata, publications, presentations) and its locked members' presence/order. Mutable members of a locked non-barrier parent are fully editable/movable/deletable; new members of a locked parent must be effectively mutable; ordinary moves may not flip effective mutability (root promotion/demotion may). Fixed check order and rule diagnostics (model.md). | The previous fingerprint included member cards, so a `mutable: true` child under a default-locked root could not be renamed or deleted: the dynamic tree goal was unreachable for ordinary graphs. |
| R02 | `protected_dependents`: locked nodes whose one-hop agent context changes (mutable member/use/template target card, definition or publications) are reported in the protection report and `PreviewReceipt.protected_dependents`, and are part of admission `impact`. | Governance through current admission/approval instead of refusing every change near a locked node. |
| R03 | Registry evidence (`PublicationContract`) is indexed by exact binding descriptor (a collection, mapping values or resolver), checked only for new candidates (transaction final candidate, bootstrap seed, revert candidate). Replay, transaction bases and recovery never consult it. | A Tool binding upgrade made the journal unreplayable with any registry; replay must be a pure function of the journal. |
| R04 | `History(records)` replays every link (and a bootstrap seed) and rejects records whose state is not their event's result; `append` verifies only the new link; revision lookup is O(log n). | The previous constructor checked only sequence and graph id. |
| R05 | Granular `PresentationPut/Removed` facts are removed; `PresentationsAssigned` is the single encoding. | No accepted journal used them; two encodings of one change. |
| R06 | `execution_diff(ExecutionManifest, ExecutionManifest)` and `surface_diff(StructuralPlan, StructuralPlan)` replace caller-JSON placeholders. | Real execution/effective-surface comparison. |
| R07 | FileJournal publication never replaces a name (renameat2 RENAME_NOREPLACE on Linux; refuse if unavailable); the writer verifies before every write that `writer.lock` still names its locked inode. Existing destination => definite `store_in_use`. | Deleting `writer.lock` allowed a second owner to silently overwrite an acknowledged record. Go already used RENAME_NOREPLACE. |
| R08 | Persistent collections (design deviation disposition): Python shares unchanged `NodeSpec` objects between revisions (validated node reuse, memoized JCS bytes) and keeps `rpds.List` only for history records. No persistent map dependency is used for nodes. | Measured: the copy/compare cost, not list structure, dominated; recommend accepting node sharing as an internal optimization with no wire change. |
| R09 | Owner snapshots: anchored (store id, journal_seq, record_hash, package version) JSON in journal `hints/`, never authority; startup restores them and replays only the tail; older history is materialized from the journal on demand; any mismatch => full replay. | Startup must not require replay from genesis (original research input). |
| R10 | Execution adapters may be attested per exact binding contract (`node_id=None`). | Tool nodes created at runtime against a deployed binding must execute without rebuilding the runtime. |
| R11 | Offline doctor verifies in place by default (chain, receipts, blobs, graph-semantics replay); isolated restore is opt-in. | Scalable verification; the in-memory envelope bounded stores at ~40k records. |

### Port obligations (TypeScript, Go)

R01-R05 and R07 change observable contracts or shared bytes and are required for
parity; R06, R08-R11 are API/implementation obligations for product parity.
Additionally: TS/Go owner-level revert with current-protection recheck, bounded
authorization retry across a concurrent local swap (as Python `_authorize`),
TS `fs-ext` as an optional dependency, and loading shared vectors from files with
digest checks instead of inline copies.
