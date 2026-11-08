"""FileJournal contract, recovery and checkpoint evidence."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any
from unittest.mock import patch

import pytest

from contexture.core.graph.canonical import canonical_json
from contexture.server.managed_journal import CommitUnknown, FileJournal, JournalError

ROOT = Path(__file__).parents[1]
SCHEMA = json.loads((ROOT / "spec/managed/schemas/managed.schema.json").read_text())
DIGEST = "sha256:" + "1" * 64


@pytest.fixture
def store_path():
	# Native Linux filesystem, NOT the Windows-mounted checkout.
	with tempfile.TemporaryDirectory(prefix="contexture-journal-") as directory:
		yield Path(directory) / "store"


def open_store(path, **kwargs):
	return FileJournal.open(path, schema=SCHEMA, **kwargs)


def event(**kwargs):
	return {"type": "CommandCompleted", "graph_id": "g", "revision": "0",
			"command_digest": DIGEST, "status": "noop", **kwargs}


def receipt(seq="1", key="key", actor="actor", **kwargs):
	return {"schema": "contexture.command-receipt/v1", "graph_id": "g", "actor_ref": actor,
			"idempotency_key": key, "request_digest": DIGEST, "revision": "0",
			"journal_seq": seq, "status": "noop", "durability": "strict",
			"operation_result": {"inline": {"answer": 42}}, **kwargs}


def hash_subject(domain, value):
	return "sha256:" + hashlib.sha256(domain.encode() + b"\0" + canonical_json(value)).hexdigest()


def rehash(record):
	payload = {key: record[key] for key in ("events", "command_receipt") if key in record}
	record["payload_digest"] = hash_subject("contexture.journal-payload/v1", payload)
	record["record_hash"] = hash_subject("contexture.journal-record/v1", {
		key: value for key, value in record.items() if key != "record_hash"})


def empty_projector(records):
	"""An honest oracle ONLY for the CommandCompleted-only fixture stream."""
	assert all(e["type"] == "CommandCompleted" for r in records for e in r["events"])
	receipts = [r["command_receipt"] for r in records if "command_receipt" in r]
	refs = {}
	for r in receipts:
		if "blob" in r["operation_result"]:
			ref = r["operation_result"]["blob"]
			refs[(ref["digest"], ref["media_type"])] = ref
	return {"graphs": [], "desired_releases": [], "release_operations": [],
			"prepare_attempt_history": [], "observations_history": [],
			"receipts_index": sorted(receipts, key=lambda x: (x["actor_ref"], x["idempotency_key"])),
			"projection_versions": {"receipts": "1"}, "blob_refs": [refs[k] for k in sorted(refs)]}


def test_batch_identity_index_is_rebuilt_and_failed_memory_publication_preserves_lkg(store_path):
	class FailingSet(set):
		def add(self, value):
			super().add(value)
			raise MemoryError("fixture index allocation failure")

	with open_store(store_path) as journal:
		journal.append([event()], actor_ref="actor", command_receipt=receipt(),
					   event_batch_id="batch-one")
	with open_store(store_path) as journal:
		with pytest.raises(JournalError) as duplicate:
			journal.append([event()], actor_ref="actor", event_batch_id="batch-one")
		assert duplicate.value.code == "schema_invalid"
		assert journal.watermark == "1"
		before = journal.records()
		journal._event_batch_ids = FailingSet(journal._event_batch_ids)
		with pytest.raises(CommitUnknown):
			journal.append([event()], actor_ref="actor", command_receipt=receipt("2", "two"),
						   event_batch_id="batch-two")
		assert journal.records() == before and journal.watermark == "1"
		assert journal.get_receipt("actor", "two") is None
		with pytest.raises(CommitUnknown):
			journal.append([event()], actor_ref="actor")
	with open_store(store_path) as recovered:
		assert recovered.watermark == "2"
		assert recovered.get_receipt("actor", "two") == receipt("2", "two")
		with pytest.raises(JournalError) as duplicate:
			recovered.append([event()], actor_ref="actor", event_batch_id="batch-two")
		assert duplicate.value.code == "schema_invalid"


@pytest.mark.parametrize("failed_index", ["batch", "receipt", "record"])
def test_incremental_index_failure_rolls_back_all_readable_indexes(store_path, failed_index):
	class FailingSet(set):
		def add(self, value):
			super().add(value)
			raise MemoryError("batch allocation")

	from contexture.server.managed_journal import _ReceiptIndex, _RecordStore

	class FailingIndex(_ReceiptIndex):
		def __setitem__(self, key, value):
			super().__setitem__(key, value)
			raise MemoryError("receipt allocation")

	class FailingStore(_RecordStore):
		def append(self, value):
			super().append(value)
			raise MemoryError("record allocation")

	with open_store(store_path) as journal:
		journal.append([event()], actor_ref="actor", command_receipt=receipt(),
					   event_batch_id="batch-one")
		before = journal.records()
		if failed_index == "batch":
			journal._event_batch_ids = FailingSet(journal._event_batch_ids)
		elif failed_index == "receipt":
			index = FailingIndex(journal._records)
			index._seq = dict(journal._receipts._seq)
			journal._receipts = index
		else:
			store = FailingStore(journal._reload)
			store._hashes, store._cache = list(journal._records._hashes), journal._records._cache.copy()
			journal._records = store
			journal._receipts._records = store
		with pytest.raises(CommitUnknown):
			journal.append([event()], actor_ref="actor", command_receipt=receipt("2", "two"),
						   event_batch_id="batch-two")
		assert journal.watermark == "1" and journal.records() == before
		assert journal.get_receipt("actor", "key") == receipt()
		assert journal.get_receipt("actor", "two") is None
		assert journal._event_batch_ids == {"batch-one"}
		with pytest.raises(CommitUnknown):
			journal.append([event()], actor_ref="actor")
	with open_store(store_path) as reopened:
		assert reopened.watermark == "2"
		assert reopened.get_receipt("actor", "two") == receipt("2", "two")


def test_append_does_not_copy_accepted_record_or_receipt_indexes(store_path):
	from contexture.server.managed_journal import _RecordStore

	class NoCopyStore(_RecordStore):
		def __iter__(self):
			raise AssertionError("Full record index copied")

	with open_store(store_path) as journal:
		journal.append([event()], actor_ref="actor", command_receipt=receipt())
		store = NoCopyStore(journal._reload)
		store._hashes, store._cache = list(journal._records._hashes), journal._records._cache.copy()
		journal._records = journal._receipts._records = store
		for sequence in range(2, 102):
			journal.append([event()], actor_ref="actor",
						   command_receipt=receipt(str(sequence), f"key-{sequence}"))
		assert journal.watermark == "101"
		assert journal.get_receipt("actor", "key-101") == receipt("101", "key-101")
		# Bounded memory: only hashes for every record, bodies only in the LRU.
		assert len(store._hashes) == 101 and len(store._cache) <= store.CACHE


def test_lazy_record_bodies_are_reverified_against_the_open_chain(store_path):
	with open_store(store_path) as journal:
		for sequence in range(1, 4):
			journal.append([event()], actor_ref="actor", command_receipt=receipt(str(sequence), f"k{sequence}"))
		journal._records._cache.clear()
		assert [r["journal_seq"] for r in journal.records()] == ["1", "2", "3"]
		journal._records._cache.clear()
		path = journal._record_path(2)
		path.write_bytes(path.read_bytes().replace(b'"k2"', b'"kX"'))
		with pytest.raises(JournalError) as error:
			journal.records()
		assert error.value.code == "corruption"


def test_real_immutable_chain_receipts_and_reopen(store_path):
	with open_store(store_path) as journal:
		first = journal.append([event()], actor_ref="actor", command_receipt=receipt())
		second = journal.append([event()], actor_ref="actor", command_receipt=receipt("2", "two"))
		assert second["prev_record_hash"] == first["record_hash"]
		expected = deepcopy(first)
		rehash(expected)
		assert expected == first
		assert journal.watermark == "2"
		first["events"].clear()
		result = journal.get_receipt("actor", "key")
		result["operation_result"] = {}
		assert journal.get_receipt("actor", "key") == receipt()
		meta = journal.meta
		meta["store_id"] = "tampered"
		assert journal.meta["store_id"] != "tampered"
		assert journal.append([event()], actor_ref="actor", command_receipt=receipt("3"))["journal_seq"] == "1"
		with pytest.raises(JournalError, match="another request"):
			journal.append([event()], actor_ref="actor", command_receipt=receipt(request_digest="sha256:" + "2" * 64))
		inode = (store_path / "writer.lock").stat().st_ino
	with open_store(store_path) as recovered:
		assert recovered.watermark == "2"
		assert recovered.get_receipt("actor", "two") == receipt("2", "two")
		assert (store_path / "writer.lock").stat().st_ino == inode


@pytest.mark.parametrize("field,value", [
	("actor_ref", "other"), ("journal_seq", "2"), ("durability", "process_crash"),
	("graph_id", "wrong"), ("revision", "1"), ("status", "committed"),
	("request_digest", "sha256:" + "2" * 64), ("unknown", True),
])
def test_receipt_must_match_atomic_record(store_path, field, value):
	with open_store(store_path) as journal:
		bad = receipt(**{field: value})
		with pytest.raises(JournalError):
			journal.append([event()], actor_ref="actor", command_receipt=bad)
		assert journal.watermark == "0"
		assert not list((store_path / "records").rglob("*.json"))


@pytest.mark.parametrize("corruption", ["gap", "digest", "store", "sequence", "previous", "unknown",
										"actor", "receiptseq", "duplicate-key", "noncanonical", "rawduplicate", "bucket"])
def test_final_corruption_never_truncated_or_skipped(store_path, corruption):
	with open_store(store_path) as journal:
		for i in range(1, 4):
			journal.append([event()], actor_ref="actor", command_receipt=receipt(str(i), str(i)))
		paths = sorted((store_path / "records").rglob("*.json"))
	path = paths[1]
	record = json.loads(path.read_bytes())
	if corruption == "gap":
		path.unlink()
	elif corruption == "bucket":
		wrong = path.parent.with_name("00000000000000000001")
		wrong.mkdir(mode=0o700)
		path.rename(wrong / path.name)
	elif corruption == "noncanonical":
		path.write_bytes(json.dumps(record).encode())
	elif corruption == "rawduplicate":
		path.write_bytes(b'{"schema":"duplicate",' + path.read_bytes()[1:])
	else:
		if corruption == "digest": record["payload_digest"] = DIGEST
		if corruption == "store": record["store_id"] = "wrong"
		if corruption == "sequence": record["journal_seq"] = "01"
		if corruption == "previous": record["prev_record_hash"] = DIGEST
		if corruption == "unknown": record["ignored"] = True
		if corruption == "actor": record["command_receipt"]["actor_ref"] = "other"
		if corruption == "receiptseq": record["command_receipt"]["journal_seq"] = "3"
		if corruption == "duplicate-key": record["command_receipt"]["idempotency_key"] = "1"
		if corruption != "digest": rehash(record)
		path.write_bytes(canonical_json(record))
	before = {p: p.read_bytes() for p in (store_path / "records").rglob("*.json")}
	with pytest.raises(Exception):
		open_store(store_path)
	assert before == {p: p.read_bytes() for p in (store_path / "records").rglob("*.json")}


def test_bucket_boundaries_max_and_path_selectors(store_path):
	with open_store(store_path) as journal:
		for seq, bucket in [(1, 0), (10000, 0), (10001, 1), (10**20 - 1, (10**20 - 2) // 10000)]:
			assert journal._record_path(seq).relative_to(store_path).as_posix() == f"records/{bucket:020d}/{seq:020d}.json"
		for seq in [0, 10**20]:
			with pytest.raises(JournalError): journal._record_path(seq)
		for digest in ["../escape", "sha256:" + "A" * 64, "/absolute", "sha256:" + "a" * 63 + "/"]:
			with pytest.raises(JournalError): journal._blob_path(digest)


def test_lifetime_lock_is_real_and_never_deleted(store_path):
	with open_store(store_path):
		with pytest.raises(JournalError) as error:
			open_store(store_path)
		assert error.value.code == "store_in_use"
		assert (store_path / "writer.lock").exists()
	with open_store(store_path): pass



def test_removed_or_replaced_lock_stops_the_original_owner(store_path):
	# flock guards an inode, not a name. An operator "clearing a stale lock"
	# must not let two owners write: the original refuses its next write.
	first = open_store(store_path)
	first.append([event()], actor_ref="actor", command_receipt=receipt(seq="1", key="a"))
	os.unlink(store_path / "writer.lock")
	with pytest.raises(JournalError) as error:
		first.append([event()], actor_ref="actor", command_receipt=receipt(seq="2", key="b"))
	assert error.value.code == "store_in_use"
	with pytest.raises(JournalError):  # stays stopped
		first.append([event()], actor_ref="actor", command_receipt=receipt(seq="2", key="c"))
	second = open_store(store_path)  # a new lifetime owner on the new lock inode
	second.append([event()], actor_ref="actor", command_receipt=receipt(seq="2", key="d"))
	first.close(); second.close()
	with open_store(store_path) as journal:
		assert journal.watermark == "2"
		assert journal.get_receipt("actor", "a") and journal.get_receipt("actor", "d")
		assert journal.get_receipt("actor", "b") is None


def test_racing_writers_never_overwrite_an_acknowledged_record(store_path):
	# Even if two writers bypass locking entirely, publication is no-replace:
	# the loser gets a definite store_in_use and the winner's bytes survive.
	first = open_store(store_path)
	os.unlink(store_path / "writer.lock")
	acknowledged: dict[str, Any] = {}

	def interleave(step: str) -> None:
		# Between the loser's existence check and its rename, the winner publishes.
		if step == "record.before_rename" and not acknowledged:
			acknowledged.update(first.append([event()], actor_ref="actor",
				command_receipt=receipt(seq="1", key="winner")))

	second = open_store(store_path, fault=interleave)
	second._check_lock = lambda: None  # simulate a writer that ignores the lock name
	first._check_lock = lambda: None
	with pytest.raises(JournalError) as error:
		second.append([event()], actor_ref="actor", command_receipt=receipt(seq="1", key="loser"))
	assert error.value.code == "store_in_use" and acknowledged
	first.close(); second.close()
	with open_store(store_path) as journal:
		assert journal.watermark == "1"
		assert journal.records()[0]["record_hash"] == acknowledged["record_hash"]
		assert journal.get_receipt("actor", "loser") is None
		# A visible existing destination is also refused definitively.
		with pytest.raises(JournalError) as error:
			journal._publish(journal._record_path(1), b"{}", "record")
		assert error.value.code == "store_in_use"


def test_missing_no_replace_primitive_refuses_instead_of_falling_back(store_path):
	from contexture.server import managed_journal as module
	with open_store(store_path) as journal:
		with patch.object(module, "_renameat2", return_value=None):
			with pytest.raises(JournalError) as error:
				journal.append([event()], actor_ref="actor", command_receipt=receipt())
		assert error.value.code == "durability_unsupported"
		assert journal.watermark == "0"
		# Definitely unpublished, so the owner is not stuck in unknown.
		journal.append([event()], actor_ref="actor", command_receipt=receipt())
		assert journal.watermark == "1"

def test_blob_bytes_canonical_json_and_required_closure(store_path):
	with open_store(store_path) as journal:
		raw = "Unicode 原字节\r\n".encode()
		ref = journal.put_blob(raw, media_type="text/plain")
		assert ref["digest"] == "sha256:" + hashlib.sha256(raw).hexdigest()
		assert journal.put_blob(raw, media_type="text/plain") == ref
		assert journal.get_blob(ref) == raw
		with pytest.raises(JournalError): journal.put_blob(b'{ "x":1}', media_type="application/json")
		assert journal.get_blob(journal.put_blob(b'{"x":1}', media_type="application/json")) == b'{"x":1}'
		journal.append([event()], actor_ref="actor", command_receipt=receipt(operation_result={"blob": ref}))
		path = journal._blob_path(ref["digest"])
	path.write_bytes(b"corrupt")
	with pytest.raises(JournalError, match="digest or size"):
		open_store(store_path)


def test_temp_quarantine_and_stale_hints_indexes(store_path):
	with open_store(store_path) as journal:
		journal.append([event()], actor_ref="actor")
	for path, raw in [(store_path / "tmp/partial.tmp", b'{"partial"'),
					  (store_path / "hints/LATEST.json", b"bad hint"),
					  (store_path / "indexes/stale", b"false authority")]:
		path.write_bytes(raw)
		path.chmod(0o600)
	with open_store(store_path) as journal:
		assert journal.watermark == "1" and len(journal.recovery_actions) == 1
		assert not list((store_path / "tmp").iterdir())
		assert len(list((store_path / "quarantine").glob("*.artifact"))) == 1
		assert len(list((store_path / "quarantine").glob("*.json"))) == 1


@pytest.mark.parametrize("step", ["record.temp_written", "record.file_synced", "record.before_rename",
								  "record.renamed", "record.directory_synced"])
def test_fault_boundaries_unknown_freezes_and_reopen_recovers(store_path, step):
	def fail(stage):
		if stage == step: raise OSError("injected")
	with open_store(store_path) as journal:
		journal._fault = fail
		if step in {"record.renamed", "record.directory_synced"}:
			with pytest.raises(CommitUnknown):
				journal.append([event()], actor_ref="actor", command_receipt=receipt())
			assert journal.watermark == "0" and journal.get_receipt("actor", "key") is None
			with pytest.raises(CommitUnknown): journal.put_blob(b"not allowed")
			with pytest.raises(CommitUnknown): journal.append([event()], actor_ref="actor")
			with pytest.raises(CommitUnknown): journal.export_snapshot()
		else:
			with pytest.raises(OSError): journal.append([event()], actor_ref="actor", command_receipt=receipt())
			assert journal.watermark == "0"
	with open_store(store_path) as recovered:
		assert recovered.watermark == ("1" if step in {"record.renamed", "record.directory_synced"} else "0")


def test_checkpoint_full_projection_oracle_and_restore_new_identity(store_path):
	with open_store(store_path, projector=empty_projector) as journal:
		ref = journal.put_blob(b"result")
		journal.append([event()], actor_ref="actor", command_receipt=receipt(operation_result={"blob": ref}))
		checkpoint1 = journal.checkpoint()
		assert journal.read_checkpoint() == checkpoint1
		journal.append([event()], actor_ref="actor", command_receipt=receipt("2", "second"))
		checkpoint2 = journal.checkpoint()
		path = next((store_path / "checkpoints/00000000000000000002").glob("*.json"))
		path.write_bytes(b"invalid")
		assert journal.read_checkpoint() == checkpoint1
		# A rehashed but semantically different complete checkpoint is not trusted.
		forged = deepcopy(checkpoint2)
		forged["projection_versions"] = {"forged": "2"}
		forged["checkpoint_digest"] = hash_subject("contexture.checkpoint/v1", {k: v for k, v in forged.items() if k != "checkpoint_digest"})
		with pytest.raises(JournalError, match="full replay"):
			journal._check_checkpoint(forged)
		snapshot = journal.export_snapshot()
		original_id = journal.meta["store_id"]
		expected = journal.projections()
	clone_path = store_path.parent / "clone"
	with FileJournal.restore_snapshot(clone_path, snapshot, schema=SCHEMA, projector=empty_projector) as clone:
		assert clone.meta["store_id"] != original_id
		assert clone.get_blob(clone.meta["restored_from"]) == snapshot
		assert clone.watermark == "2"
		assert clone.projections() == expected
		assert clone.get_receipt("actor", "key")["operation_result"] == {"blob": ref}
		assert clone.read_checkpoint()["last_seq"] == "2"
	with open_store(clone_path, projector=empty_projector) as reopened:
		assert reopened.projections() == expected
	with pytest.raises(JournalError, match="must not exist"):
		FileJournal.restore_snapshot(clone_path, snapshot, schema=SCHEMA, projector=empty_projector)


def test_checkpoint_refuses_fake_reducer_and_backup_corruption(store_path):
	with open_store(store_path) as journal:
		journal.append([event()], actor_ref="actor")
		with pytest.raises(JournalError, match="projection port"):
			journal.checkpoint()
		snapshot = json.loads(journal.export_snapshot())
	snapshot["records"][0]["actor_ref"] = "tampered"
	destination = store_path.parent / "bad-clone"
	with pytest.raises(JournalError):
		FileJournal.restore_snapshot(destination, canonical_json(snapshot), schema=SCHEMA)
	assert not destination.exists()


def test_strict_no_silent_downgrade_and_symlinks_permissions(store_path):
	with open_store(store_path, durability="process_crash") as journal:
		assert journal.meta["durability"] == "process_crash"
	with pytest.raises(JournalError) as error: open_store(store_path)
	assert error.value.code == "durability_unsupported"
	with open_store(store_path, durability="process_crash"): pass
	target = store_path.parent / "outside"
	target.mkdir(mode=0o700)
	link = store_path / "blobs/link"
	link.symlink_to(target, target_is_directory=True)
	with pytest.raises(JournalError, match="Symlinks"):
		open_store(store_path, durability="process_crash")
	link.unlink()
	store_path.chmod(0o777)
	with pytest.raises(JournalError, match="private"):
		open_store(store_path, durability="process_crash")


def test_missing_meta_never_seeded_over_committed_state(store_path):
	with open_store(store_path) as journal:
		journal.append([event()], actor_ref="actor")
	(store_path / "META.json").unlink()
	with pytest.raises(JournalError, match="Missing META"):
		open_store(store_path)
	assert not (store_path / "META.json").exists()


def test_short_writes_complete_and_failed_rename_unknown(store_path):
	original_write = os.write
	def short(fd, data): return original_write(fd, data[:7])
	with open_store(store_path) as journal:
		with patch("os.write", side_effect=short):
			journal.append([event()], actor_ref="actor")
		with patch("contexture.server.managed_journal._rename_noreplace", side_effect=OSError("ambiguous syscall")):
			with pytest.raises(CommitUnknown): journal.append([event()], actor_ref="actor")
		assert journal.watermark == "1"
	with open_store(store_path) as recovered:
		assert recovered.watermark == "1"


def test_inline_data_not_interpreted_as_blob_and_required_missing_refused(store_path):
	with open_store(store_path) as journal:
		looks_like_blob = {"digest": DIGEST, "size": "9", "media_type": "text/plain"}
		journal.append([event()], actor_ref="actor", command_receipt=receipt(operation_result={"inline": looks_like_blob}))
		with pytest.raises(JournalError) as error:
			journal.append([event()], actor_ref="actor", command_receipt=receipt("2", "bad", operation_result={"blob": looks_like_blob}))
		assert error.value.code == "history_unavailable"
		assert journal.watermark == "1"


def test_full_graph_release_attempt_supersession_observation_checkpoint(store_path):
	from contexture.core.graph.events import UnseededState, bootstrap_event
	from contexture.core.graph.model import GraphDefinition

	definition = GraphDefinition.from_json({
		"schema": "contexture.graph/v1", "semantic_version": "1", "graph_id": "g",
		"application_name": "app", "mode": "disclosure_only",
		"nodes": [{"node_id": "r", "kind": "role", "name": "root", "description": "root", "instructions": ""}],
		"roots": [{"node_id": "r", "plane": "model"}],
		"presentations": {"default_locale": "en", "catalogs": []},
	})
	seed = bootstrap_event(UnseededState("g"), definition, command_digest=DIGEST,
						   reason="seed", approval_ref="trusted")
	uuid = "12345678-1234-4234-8234-123456789abc"
	request = {"schema": "contexture.release-request/v1", "graph_id": "g", "target_id": "r",
			   "revision": "1", "definition_digest": definition.digest(), "manifest_digest": DIGEST,
			   "expected_desired_epoch": "0", "idempotency_key": "release-1", "policy": "exact", "reason": "test"}
	release = {"graph_id": "g", "target_id": "r", "release_epoch": "1"}
	attempt = {"release": release, "instance_id": "instance", "boot_id": uuid, "attempt_id": "attempt"}
	observation = {"release": release, "instance_id": "instance", "boot_id": uuid,
				   "generation_id": uuid, "revision": "1", "definition_digest": definition.digest(),
				   "manifest_digest": DIGEST, "observed_at": "2026-11-05T00:00:00Z"}

	def full_projector(records):
		# Test-only independently replayed vocabulary, not a production manager.
		from contexture.core.graph.events import apply_event
		state = UnseededState("g")
		desired = []
		operations = []
		attempts = []
		observations = []
		for record in records:
			for item in record["events"]:
				if item["type"] == "GraphChangeCommitted": state = apply_event(state, item, tombstones=[])
				elif item["type"] == "ReleaseRequested":
					desired = [{"request": item["request"], "release_epoch": item["release_epoch"]}]
					operations.append({"schema": "contexture.release-receipt/v1", "release": {
						"graph_id": "g", "target_id": "r", "release_epoch": item["release_epoch"]},
						"journal_seq": record["journal_seq"], "request_digest": DIGEST, "status": "requested"})
				elif item["type"] == "ReleasePreparationSucceeded":
					attempts.append({"identity": item["attempt"], "status": "ready", "generation_id": item["generation_id"]})
				elif item["type"] == "ReleaseSuperseded":
					operations.append({"schema": "contexture.release-receipt/v1", "release": item["release"],
						"journal_seq": record["journal_seq"], "request_digest": DIGEST,
						"status": "superseded", "superseded_by": item["superseded_by"]})
				elif item["type"] == "ReplicaObserved": observations.append(item["observation"])
				else: raise AssertionError("Unexpected test stream")
		return {"graphs": [{"head": state.revision, "definition": state.definition.as_json(),
				"definition_digest": state.definition_digest, "tombstones": list(state.tombstones)}],
				"desired_releases": desired, "release_operations": operations,
				"prepare_attempt_history": attempts, "observations_history": observations,
				"receipts_index": [], "projection_versions": {"graphs": "1", "releases": "1"}, "blob_refs": []}

	with open_store(store_path, projector=full_projector) as journal:
		journal.append([seed], actor_ref="actor")
		journal.append([{"type": "ReleaseRequested", "request": request, "release_epoch": "1"}], actor_ref="actor")
		journal.append([{"type": "ReleasePreparationSucceeded", "attempt": attempt,
						 "generation_id": uuid, "manifest_digest": DIGEST},
						{"type": "ReplicaObserved", "observation": observation}], actor_ref="actor")
		checkpoint = journal.checkpoint()
		journal.append([{"type": "ReleaseSuperseded", "release": release, "superseded_by": "2"},
						{"type": "ReleaseRequested", "request": {**request, "expected_desired_epoch": "1",
						 "idempotency_key": "release-2"}, "release_epoch": "2"}], actor_ref="actor")
		expected = journal.projections()
		assert journal.read_checkpoint() == checkpoint
		assert len(expected["release_operations"]) == 3
		assert expected["observations_history"] == [observation]
		assert len(expected["prepare_attempt_history"]) == 1
		snapshot = journal.export_snapshot()
	with open_store(store_path, projector=full_projector) as journal:
		assert journal.projections() == expected
		assert journal.read_checkpoint() == checkpoint
	with FileJournal.restore_snapshot(store_path.parent / "full-clone", snapshot, schema=SCHEMA,
									  projector=full_projector) as clone:
		assert clone.projections() == expected


def test_native_sync_calls_include_new_bucket_parent_and_private_modes(store_path):
	with open_store(store_path) as journal:
		observed = []
		original = journal._sync_dir
		def sync(path):
			observed.append(path)
			return original(path)
		with patch.object(journal, "_sync_dir", side_effect=sync):
			journal.append([event()], actor_ref="actor")
		assert store_path / "records" in observed
		assert store_path / "records/00000000000000000000" in observed
		assert store_path / "tmp" in observed
		for path in store_path.rglob("*"):
			assert path.stat().st_mode & 0o077 == 0


def test_interrupted_restore_is_not_reopened_as_complete(store_path):
	with open_store(store_path) as journal:
		journal.append([event()], actor_ref="actor")
		snapshot = journal.export_snapshot()
	destination = store_path.parent / "partial-clone"
	with patch.object(FileJournal, "append", side_effect=OSError("restore interrupted")):
		with pytest.raises(OSError):
			FileJournal.restore_snapshot(destination, snapshot, schema=SCHEMA)
	with pytest.raises(JournalError) as error:
		open_store(destination)
	assert error.value.code == "restore_incomplete"


def test_directory_fsync_failure_after_rename_stops_allocation(store_path):
	with open_store(store_path) as journal:
		original = journal._sync_dir
		def fail_final(path):
			if path.name == "00000000000000000000" and list(path.glob("*.json")):
				raise OSError("directory durability failed")
			original(path)
		with patch.object(journal, "_sync_dir", side_effect=fail_final):
			with pytest.raises(CommitUnknown): journal.append([event()], actor_ref="actor")
		assert journal.watermark == "0"
		with pytest.raises(CommitUnknown): journal.checkpoint()
	with open_store(store_path) as journal:
		assert journal.watermark == "1"


def test_no_space_prepublication_and_traversal_refuse(store_path):
	with open_store(store_path) as journal:
		with patch("os.write", return_value=0):
			with pytest.raises(OSError): journal.append([event()], actor_ref="actor")
		assert journal.watermark == "0"
		journal.append([event()], actor_ref="actor")
		assert journal.watermark == "1"
	with pytest.raises(JournalError, match="Dot traversal"):
		open_store(store_path / ".." / "traversal")


def test_source_worktree_refused_and_projection_failure_pauses_writes(store_path):
	source = store_path.parent / "source"
	source.mkdir(mode=0o700)
	(source / ".git").write_text("gitdir: fake")
	with pytest.raises(JournalError, match="outside source"):
		open_store(source / "journal")
	def broken(records): raise RuntimeError("rebuild required")
	with open_store(store_path, projector=broken) as journal:
		journal.append([event()], actor_ref="actor")
		with pytest.raises(RuntimeError, match="rebuild"):
			journal.projections()
		assert journal.watermark == "1"
		with pytest.raises(CommitUnknown): journal.append([event()], actor_ref="actor")


def _sealed_store(store_path, count=30, seal_at=25):
	with open_store(store_path) as journal:
		for sequence in range(1, count + 1):
			journal.append([event()], actor_ref="actor", command_receipt=receipt(str(sequence), f"k{sequence}"))
			if sequence == seal_at:
				journal.seal()
		return journal.records()


def test_sealed_index_opens_with_tail_verification_only(store_path):
	records = _sealed_store(store_path)
	reads = []
	original = FileJournal._read_json

	def counted(self, path):
		if path.parent.parent.name == "records":
			reads.append(path.name)
		return original(self, path)

	with patch.object(FileJournal, "_read_json", counted):
		with open_store(store_path) as journal:
			opened = [n for n in reads if n.endswith(".json") and n[:-5].isdigit()]
	# Anchor (25) and the five tail records only, not the 24-record prefix.
	assert sorted(int(n[:-5]) for n in opened) == [25, 26, 27, 28, 29, 30]
	with open_store(store_path) as journal:
		assert journal.records() == records
		assert journal.get_receipt("actor", "k3") == receipt("3", "k3")
		journal.append([event()], actor_ref="actor", command_receipt=receipt("31", "k31"))
	with open_store(store_path, trust_index=False) as full:
		assert full.watermark == "31" and full.get_receipt("actor", "k3") == receipt("3", "k3")


def test_bad_index_falls_back_and_tampered_prefix_is_detected(store_path):
	_sealed_store(store_path)
	(index,) = (store_path / "hints" / "journal-index").iterdir()
	original = index.read_bytes()
	index.write_bytes(original.replace(b'"k3"', b'"kX"'))  # digest no longer matches
	with open_store(store_path) as journal:
		assert journal.watermark == "30"  # full verification instead
	index.write_bytes(original)
	path = store_path / "records" / ("0" * 20) / f"{3:020d}.json"
	path.write_bytes(path.read_bytes().replace(b'"k3"', b'"kY"'))
	with open_store(store_path) as journal:  # sealed: prefix not read at open
		with pytest.raises(JournalError) as error:
			journal.records()
		assert error.value.code == "corruption"
	with pytest.raises(JournalError):
		open_store(store_path, trust_index=False)  # a full scrub refuses outright