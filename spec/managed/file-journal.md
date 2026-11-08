# FileJournal — target v1 draft

Status: specified protocol draft, not a supported-platform or durability claim.
Default profile: `strict`. Explicit alternative: `process_crash`. Unsupported
capabilities MUST return `durability_unsupported`, never silently downgrade.
See [protocol](protocol.md), [conformance](conformance.md) and [decisions](decisions.md).

## Ownership and format boundary

Target format is `contexture.file-journal/v1`. One owner process holds an OS
exclusive lock for the entire local store lifetime and serializes every append,
including appends for different graphs. A command mutates one graph; sharing a
store does not imply cross-graph transactions. Other clients use the owner API.
This is the default no-database implementation, not a multiwriter file service.

The lock uses a stable inode/handle. PID existence and lease timeout are not
mutual exclusion. Never unlink/recreate a locked file to recover ownership.
Validate paths, symlinks/reparse points and directory permissions. Do not derive
storage paths from graph/node display names. Keep the store outside source trees.
Noncooperating external writers/tampering are outside the ownership guarantee.

## Logical layout and atomic initialization (D12)

These are format roles, not a claim that runtime directories already exist:
META contains store ID, format/canonical profile, durability requirement and
creation provenance. Initialize atomically. Records are complete canonical files
with 20-digit sequence names in deterministic buckets of 10000 records. Counter
wire remains unpadded decimal strings capped at 10^20−1. Checkpoints and blobs are
immutable by ID/digest. Hints/indexes (LATEST, bucket/receipt lookup) are rebuildable,
not competing authorities. Same-filesystem tmp/quarantine hold unpublished work
and explicitly diagnosed recovery artifacts. Q03 now has exact proposed
bucket/record/blob/checkpoint paths and boundary vectors in
[schema contracts](schema-contracts.md#q03-exact-filejournal-paths).
This coherent additive candidate is not review accepted; bindings must not
independently choose incompatible names or infer filesystem durability from paths.

## Commit and durable watermark (D12.1)

1. Hold owner lock and enter serialized commit. Consult verified head/receipt,
	check CAS/preview/current admission. No second accepted stream or writable head.
2. Publish required immutable blobs durably first. Build the complete normalized
	record; write same-filesystem temporary file, detect every short write, flush
	and sync the file. Do not rely on language buffers alone.
3. Atomically publish to a new final sequence name without overwrite. Use a
	tested no-replace primitive, or tested exclusive-owner conflict detection.
	Existing sequence conflicts are corruption/ownership errors, never replacement.
4. Perform profile-specific durability confirmation: POSIX parent directory sync
	and newly created bucket's parent sync, or proven platform-native equivalent.
5. Advance owner durable watermark, update consistent projections, deliver watch
	and return receipt only once confirmed. Hint/checkpoint failure after this point
	does not reverse accepted commit. Projection failure pauses writes for rebuild.

Atomic visibility at publish and confirmed durability at sync are different
boundaries. Raw directory readers seeing a renamed file do not infer acceptance;
all active readers/reconcilers use owner-confirmed watermark. Recovered complete
final records may be accepted after validation and required sync even if a client
never received a receipt: retry/query the same key, never execute a second intent.
Append/sync uncertainty freezes sequence allocation and release/swap as specified
in [protocol](protocol.md); a missing in-memory receipt does not prove absence.

## Recovery classification (D12.2, D25.3)

| Observation | Required response |
| --- | --- |
| Unpublished partial temp | Not accepted; quarantine/clean under lock with recovery record |
| Complete final after publish before sync | Validate and confirm per profile; may recover an unacknowledged receipt |
| Short write, disk full, permission/sync error | Distinguish certain uncommitted from unknown; stop rather than allocate past uncertainty |
| Missing/corrupt/duplicate middle record or broken chain | Read-only diagnostics/not-ready; no skip, renumber or history truncation |
| Invalid final tail record | Explicit recovery decision, not ordinary disposable temp |
| Missing/bad latest hint | Discover checkpoints/buckets and verify continuity, never infer an empty store |
| Owner lock unavailable | store_in_use; no second writer, lock deletion or timeout takeover |
| Restored clone | Explicit new store/instance identities with provenance, not two appenders sharing identity |

Under lock, unknown has only two exits: confirm published record durable, recover
receipt/projections and advance watermark; or confirm not published with no
accepted facts, retain old watermark and permit same-key retry. If neither can be
proved, remain frozen. No provisional desired exposure or swap during unknown.
Recovery must check contiguous sequences/hash links, including later buckets that
a stale hint did not mention. A trusted valid hint accelerates discovery, not a
claim of latestness. Missing/invalid hint triggers bucket-metadata discovery and
validation; cold-start metadata cost is measured, not declared history-independent.

The chain cannot prove all externally deleted suffixes without an independent
anchor. Trusted checkpoint, backup anchor or signed external receipt can improve
detection; do not claim cryptographic protection against the store administrator.
The durability promise assumes a supported filesystem honors its sync primitives.

## Complete checkpoint and replay (D11, D25.2–3)

Checkpoint includes schema/store, last sequence/hash, every graph's head+complete
definition+digest+identity tombstones, desired releases, release operations,
prepare-attempt history and supersession, observation history, complete receipt
index, projection versions, blob references and checkpoint digest. Receipts retain
the recoverable operation result/request digest/revision/sequence/status, not just
keys; a retained-record index is valid only while all promised results are available.

Checkpoint normalization/hash uses M21's single proposed contexture.checkpoint/v1 domain and
closed CheckpointSubject (all fields minus checkpoint_digest). Exact list ordering,
duplicate refusal, full request preimages and fixed vectors are in
[closure contracts](schema-contracts.md#m17m21-closure-and-transactionfilejournal-integration).
This is candidate evidence, not an accepted format or a durability observation.

Capture one consistent immutable state at a selected durable watermark. Background
serialization is allowed; validate anchor and whole projection digest before
durable publication, then update LATEST hint. Checkpoint+tail must equal full replay
for all projections, not only definitions. Trust in a local checkpoint rests on
owner generation/replay evidence, not merely its self-consistent hash. Original
event bytes and old profile checks remain available across pure explicit upcasters.

Choose newest verifiable checkpoint and contiguous tail, falling back to an older
checkpoint or full replay when checkpoint alone is bad. Required committed record
or blob corruption never becomes a skip. Fast recovery validates checkpoint+tail,
not unread prefix; full-prefix scrub is a separate operation with explicit cost.
Replay applies resolved facts without authorization, registry, resources, clock,
Tool calls or historical prepare side effects. Old-boot observations remain history.

Blobs are immutable by digest; textual payloads preserve original UTF-8 bytes and
canonical JSON payload refs identify their canonical bytes. Required blobs precede
the referring durable record. Missing/privacy-deleted blobs truthfully make affected
history unavailable. Store only secret handles, not secret material. Checkpoint,
blob GC and backup preserve the complete retained reference closure.

## Platform profiles, retention and operations (D12.3, D20–D21)

`strict` requires demonstrated file+directory durability, atomic no-overwrite
publication and owner locking on the exact OS/filesystem/adapter combination.
`process_crash` is explicitly selected and persisted in META, startup report and
receipt; it promises process-exit recovery only, not hardware power-loss survival.
Reopening cannot silently lower recorded requirements. SIGKILL/subprocess exit
tests are not power-loss evidence. Linux, macOS and Windows require separate
native primitive/evidence matrices. Windows cannot assume POSIX directory fsync;
macOS flush guarantees need independent evidence. WSL local ext4 and Windows
mounts are separate environments. No platform row is claimed supported here.

NFS, SMB, OneDrive/cloud-synchronized folders, multi-machine directories and
simultaneous Windows/WSL writers are unsupported. Cross-language file reading and
sequential locked handoff do not mean concurrent multi-language writing.

Initial format deletes no events automatically. Provisional checkpoint trigger is
1000 batches or 64 MiB tail, whichever first, plus a measured recovery budget.
Retention requires configured lower bounds/archive index, dry-run authorization,
backup/restore evidence and protection for active/desired/rollback/leased material
and promised retry windows. Checkpoint is not permission to delete history.
Packed logs/compaction require a separately reviewed format migration. Account
for file/inode, indexes/receipts/tombstones, staged blobs, old generations and
backup cost; reject new work rather than silently discard required state.

Consistent backup pauses new mutations, finishes current append, fixes watermark,
copies META/checkpoint/records/blobs, produces digest manifest and validates
restoration before resuming. Serving reads can continue. A directory copy alone
is not consistency evidence. Restore first to isolated storage, never overwrite
live; cutover requires old owner stopped, authorization, new identity/provenance
and owner lock. CLI produces a backup receipt, not a fictional serving observation.

Readiness independently reports storage_verified, definition_loaded,
desired_prepared and serving_ready. Authorized read-only diagnostics can remain
available when not-ready, without enabling writes. Runbooks must distinguish
disk-full/read-only/lock/unsupported-profile/corruption. Never seed over failure,
delete locks/logs or treat an unreadable store as empty. Software downgrade reads
new events only if compatible; otherwise freeze writes and route to separately
retained static/LKG materials without rewriting the journal.

## Local Python offline operations candidate

`contexture.server.managed_store_ops.StoreOperations` supplies bounded native
`doctor`, `backup`, and isolated `restore` operations. The trusted administrator
must stop the source owner first. These functions acquire its **existing**
`writer.lock` without creating or replacing it and retain that OS lock through
full-chain capture and production `FileJournal.restore_snapshot` verification.
The source is not opened for writing, recovered, quarantined, repaired, or seeded.
Pending temporary assets require explicit owner recovery instead.

Backups retain all authoritative records and blobs. They deliberately rebuild
derived checkpoints/indexes from complete replay rather than trust a potentially
stale checkpoint. `StoreBudget` limits entries, individual files, captured bytes
and encoded snapshot bytes; crossing a limit does not return a partial artifact.
An optional exact independent anchor (`store_id`, `journal_seq`, `record_hash`)
detects missing suffixes that a self-consistent remaining chain cannot prove.
Restore always requires a nonexistent destination and creates new store identity.

`doctor` returns a closed `StoreResult` with explicit refusal codes and no paths,
actors, raw event bodies or exception text. A successful storage/restore check
does **not** mean definition loaded, desired prepared, serving ready, or writes
enabled. Backup bytes and their manifest are privileged data, not diagnostics.
`check_software_compatibility` checks exact host-declared format/event/profile
support, not package-version ordering; it is not an integrity or readiness gate.

Current operations use the existing Linux-local filesystem support checks.
They do not certify Windows/macOS or hardware power-loss survival, expose an
unauthenticated management endpoint, or upgrade this draft to an accepted format.

The local CLI exposes the same ports without importing an application or
executing business handlers:

```sh
contexture store doctor --root /private/store --schema /trusted/managed.schema.json
contexture store backup --root /private/store --schema /trusted/managed.schema.json --output /private/backups/new.json
contexture store restore --root /private/new-store --schema /trusted/managed.schema.json --input /private/backups/new.json --allow-write
```

The source owner must be stopped. Backup output is exclusively published under a
private existing parent and never overwrites an artifact. Restore confirmation
is mandatory and never permits replacing a store. JSON results distinguish
storage verification from readiness; errors redact paths, raw inputs and secrets.
These are local administrator operations, not remote or model-facing write doors.