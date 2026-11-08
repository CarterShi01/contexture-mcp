"""Bounded, offline managed-store operations; never repair or open the source.

These are trusted administrative adapters, not unauthenticated Host endpoints.
The caller stops the live owner before use. An existing OS lock is held through
snapshot capture and isolated restore verification. No lock is ever removed.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
import errno
import hashlib
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
from types import MappingProxyType
from typing import Any

from jsonschema import Draft202012Validator, SchemaError

from contexture.core.graph.canonical import (
	JOURNAL_BACKUP_DOMAIN, canonical_digest, canonical_json, decode_json,
)
from contexture.core.graph.errors import GraphError
from contexture.server.managed_journal import FileJournal, JournalError, ProjectionPort


@dataclass(frozen=True)
class StoreBudget:
	"""Admission bounds, not a performance or supported-capacity claim."""

	max_files: int = 100_000
	max_bytes: int = 64 * 1024 * 1024
	max_file_bytes: int = 16 * 1024 * 1024
	max_snapshot_bytes: int = 160 * 1024 * 1024

	def __post_init__(self) -> None:
		if any(type(v) is not int or v <= 0 for v in vars(self).values()):
			raise ValueError("Store budgets must be positive integers")


@dataclass(frozen=True)
class StoreResult:
	"""Closed diagnostics: no paths, exception text, identities or event bodies."""

	operation: str
	code: str
	storage_verified: bool = False
	definition_loaded: bool = False
	desired_prepared: bool = False
	serving_ready: bool = False
	write_enabled: bool = False
	isolated_restore_verified: bool = False


@dataclass(frozen=True)
class BackupArtifact:
	"""Privileged data, not diagnostics; never log/serialize this object wholesale."""

	snapshot: bytes = field(repr=False)
	manifest: Mapping[str, object] = field(repr=False)
	result: StoreResult


@dataclass(frozen=True)
class SoftwareCompatibility:
	"""Exact host-declared support. Package version ordering proves nothing."""

	reader_build: str
	writer_build: str
	store_formats: frozenset[str]
	record_formats: frozenset[str]
	event_types: frozenset[str]
	canonical_profiles: frozenset[str]
	semantic_versions: frozenset[str]
	durability_profiles: frozenset[str]


_SAFE_CODES = frozenset({
	"unsafe_path", "unsafe_permissions", "durability_unsupported", "store_in_use",
	"closed", "corruption", "schema_invalid", "backup_invalid", "history_unavailable",
	"restore_conflict", "restore_incomplete", "projection_required", "projection_invalid",
	"checkpoint_invalid", "commit_unknown", "limit_exceeded", "recovery_required",
	"format_unsupported", "semantic_unsupported", "store_missing",
})


def _failure(operation: str, exc: Exception) -> StoreResult:
	if isinstance(exc, JournalError):
		code = exc.code if exc.code in _SAFE_CODES else "storage_error"
	elif isinstance(exc, OSError):
		codes = {errno.ENOSPC: "disk_full", errno.EROFS: "read_only",
			errno.EACCES: "permission_denied", errno.EPERM: "permission_denied"}
		code = codes.get(exc.errno, "storage_error") if exc.errno is not None else "storage_error"
	else:
		code = "verification_failed"
	return StoreResult(operation, code)


_DIRECTORIES = frozenset({
	"records", "blobs", "checkpoints", "hints", "indexes", "tmp", "quarantine",
})
_RECORD = re.compile(r"records/([0-9]{20})/([0-9]{20})\.json\Z")
_BLOB = re.compile(r"blobs/sha256/([0-9a-f]{2})/([0-9a-f]{64})\.blob\Z")


_REBUILDABLE = frozenset({"hints", "checkpoints", "indexes", "quarantine"})


def _private(info: os.stat_result, *, directory: bool = False) -> None:
	if (stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400
			or not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
			or (not directory and info.st_nlink != 1)):
		raise JournalError("unsafe_path", "Private regular store assets required")
	if info.st_mode & 0o077 or info.st_uid != os.getuid():
		raise JournalError("unsafe_permissions", "Store assets must belong only to the owner")


@contextmanager
def _paused(root: Path, durability: str) -> Iterator[Path]:
	root = FileJournal.check_location(root, durability=durability)
	if not sys.platform.startswith("linux"):
		raise JournalError("durability_unsupported", "Offline locking is not supported on this platform")
	import fcntl
	lock = root / "writer.lock"
	try:
		before = lock.lstat()
	except FileNotFoundError:
		raise JournalError("store_missing", "Existing owner lock required") from None
	_private(before)
	fd = os.open(lock, os.O_RDONLY | os.O_NOFOLLOW)
	try:
		current = os.fstat(fd)
		if (current.st_dev, current.st_ino) != (before.st_dev, before.st_ino):
			raise JournalError("unsafe_path", "Owner lock changed")
		try:
			fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
		except OSError as exc:
			if exc.errno in {errno.EACCES, errno.EAGAIN}:
				raise JournalError("store_in_use", "Store has an active owner") from None
			raise
		yield root
	finally:
		os.close(fd)


def _json(raw: bytes) -> dict[str, Any]:
	try:
		value = decode_json(raw)
		if not isinstance(value, dict) or canonical_json(value) != raw:
			raise JournalError("corruption", "Canonical store object required")
		return value
	except GraphError:
		raise JournalError("corruption", "Invalid canonical store object") from None


def read_artifact(path: Path, limit: int) -> bytes:
	with path.open("rb") as stream:
		data = stream.read(limit + 1)
	if len(data) > limit:
		raise JournalError("limit_exceeded", "Input exceeds offline budget")
	return data


def load_schema(path: Path) -> Mapping[str, Any]:
	try:
		value = decode_json(read_artifact(path, 1024 * 1024))
	except GraphError:
		raise JournalError("schema_invalid", "Invalid registered schema JSON") from None
	if not isinstance(value, dict):
		raise JournalError("schema_invalid", "Registered schema object required")
	return value


def publish_backup(path: Path, artifact: BackupArtifact, *, durability: str = "strict") -> None:
	"""Exclusive publication belongs to the filesystem adapter, not its CLI."""
	parent = FileJournal.check_location(path.parent, durability=durability)
	destination = parent / path.name
	descriptor, temporary = tempfile.mkstemp(prefix=".contexture-backup-", dir=parent)
	published = False
	try:
		with os.fdopen(descriptor, "wb") as stream:
			if stream.write(artifact.snapshot) != len(artifact.snapshot):
				raise OSError(errno.EIO, "Incomplete backup write")
			stream.flush()
			os.fsync(stream.fileno())
		os.link(temporary, destination, follow_symlinks=False)
		published = True
		if durability == "strict":
			directory = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
			try:
				os.fsync(directory)
			finally:
				os.close(directory)
	except OSError:
		if published:
			raise JournalError("commit_unknown", "Backup publication durability unknown") from None
		raise
	finally:
		Path(temporary).unlink(missing_ok=True)


class StoreOperations:
	"""Offline full-chain verification through the production restore port.

	The original store is only read under its existing OS owner lock. Immutable
	checkpoints and hints are rebuildable; backups carry every authoritative
	record and blob instead of trusting a checkpoint or mutating recovery state.
	An independent anchor can detect suffix loss; a self-consistent chain alone
	cannot. These operations never prepare a release or claim serving readiness.
	"""

	def __init__(self, *, schema: Mapping[str, Any], durability: str = "strict",
				 projector: ProjectionPort | None = None, budget: StoreBudget | None = None):
		definitions = schema.get("$defs")
		required = {"FileJournalMeta", "CommitRecord", "BlobRef", "Checkpoint", "Event", "CommandReceipt"}
		if not isinstance(definitions, Mapping) or not required <= set(definitions):
			raise JournalError("schema_invalid", "Registered managed schema definitions required")
		try:
			Draft202012Validator.check_schema(schema)
		except SchemaError:
			raise JournalError("schema_invalid", "Invalid registered managed schema") from None
		self.schema = schema
		self.durability = durability
		self.projector = projector
		self.budget = budget if budget is not None else StoreBudget()

	def _capture(self, root: Path) -> bytes:
		contents: dict[str, bytes] = {}
		total = 0
		seen = 0
		stack = [root]
		while stack:
			directory = stack.pop()
			_private(directory.lstat(), directory=True)
			with os.scandir(directory) as entries:
				for entry in entries:
					seen += 1
					if seen > self.budget.max_files:
						raise JournalError("limit_exceeded", "Store entry budget exceeded")
					path = Path(entry.path)
					relative = path.relative_to(root).as_posix()
					info = entry.stat(follow_symlinks=False)
					if stat.S_ISDIR(info.st_mode):
						_private(info, directory=True)
						if directory == root and entry.name not in _DIRECTORIES:
							raise JournalError("format_unsupported", "Unknown store directory")
						if relative.startswith("records/") and not re.fullmatch(r"records/[0-9]{20}", relative):
							raise JournalError("corruption", "Invalid committed record directory")
						stack.append(path)
						continue
					_private(info)
					if relative.split("/", 1)[0] in _REBUILDABLE:
						continue  # never carried by backups; privacy checked above
					if info.st_size > self.budget.max_file_bytes:
						raise JournalError("limit_exceeded", "Store file budget exceeded")
					with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "rb") as stream:
						opened = os.fstat(stream.fileno())
						if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
							raise JournalError("unsafe_path", "Store file changed")
						raw = stream.read(self.budget.max_file_bytes + 1)
						finished = os.fstat(stream.fileno())
						if (finished.st_size, finished.st_mtime_ns) != (opened.st_size, opened.st_mtime_ns):
							raise JournalError("corruption", "Store asset changed during capture")
					total += len(raw)
					if len(raw) > self.budget.max_file_bytes or total > self.budget.max_bytes:
						raise JournalError("limit_exceeded", "Store byte budget exceeded")
					if relative.startswith("tmp/"):
						raise JournalError("recovery_required", "Pending temporary assets require owner recovery")
					if directory == root and entry.name not in {"META.json", "writer.lock"}:
						raise JournalError("format_unsupported", "Unknown store asset")
					contents[relative] = raw
		if not _DIRECTORIES <= {p.name for p in root.iterdir() if p.is_dir()}:
			raise JournalError("corruption", "Incomplete store layout")
		if "META.json" not in contents:
			raise JournalError("corruption", "Store metadata missing")
		meta = _json(contents["META.json"])
		records: list[tuple[int, dict[str, Any]]] = []
		blobs: dict[str, str] = {}
		for name, raw in contents.items():
			if name.startswith("records/"):
				match = _RECORD.fullmatch(name)
				if match is None:
					raise JournalError("corruption", "Invalid committed record path")
				bucket, sequence = map(int, match.groups())
				if sequence < 1 or bucket != (sequence - 1) // 10000:
					raise JournalError("corruption", "Invalid committed record bucket")
				record = _json(raw)
				if record.get("journal_seq") != str(sequence):
					raise JournalError("corruption", "Record sequence differs from filename")
				records.append((sequence, record))
			elif name.startswith("blobs/"):
				match = _BLOB.fullmatch(name)
				if match is None or match[1] != match[2][:2]:
					raise JournalError("corruption", "Invalid blob path")
				digest = "sha256:" + hashlib.sha256(raw).hexdigest()
				if digest != "sha256:" + match[2]:
					raise JournalError("corruption", "Blob content digest differs")
				blobs[digest] = raw.hex()
		records.sort(key=lambda item: item[0])
		if [number for number, _ in records] != list(range(1, len(records) + 1)):
			raise JournalError("corruption", "Committed record sequence is incomplete")
		subject = {"schema": "contexture.file-journal-backup/v1", "meta": meta,
			"records": [record for _, record in records], "blobs": blobs, "checkpoint": None}
		snapshot = canonical_json({**subject,
			"backup_digest": canonical_digest(subject, JOURNAL_BACKUP_DOMAIN)})
		if len(snapshot) > self.budget.max_snapshot_bytes:
			raise JournalError("limit_exceeded", "Backup envelope budget exceeded")
		return snapshot

	def _verify(self, snapshot: bytes) -> None:
		try:
			with tempfile.TemporaryDirectory(prefix="contexture-offline-verify-") as directory:
				with FileJournal.restore_snapshot(Path(directory) / "restored", snapshot,
						schema=self.schema, durability=self.durability, projector=self.projector) as restored:
					if self.projector is not None:
						restored.projections()
		except GraphError:
			raise JournalError("corruption", "Invalid replayed store state") from None

	def backup(self, root: Path | str, *, expected_anchor: Mapping[str, object] | None = None) -> BackupArtifact:
		"""Capture and verify under one lock; failure never returns an artifact."""
		with _paused(Path(root), self.durability) as source:
			snapshot = self._capture(source)
			value = _json(snapshot)
			records = value["records"]
			anchor = {"store_id": value["meta"]["store_id"], "journal_seq": str(len(records)),
				"record_hash": records[-1]["record_hash"] if records else None}
			if expected_anchor is not None and dict(expected_anchor) != anchor:
				raise JournalError("history_unavailable", "Independent store anchor differs")
			self._verify(snapshot)
			manifest = MappingProxyType({**anchor, "backup_digest": value["backup_digest"]})
			return BackupArtifact(snapshot, manifest, StoreResult(
				"backup", "ok", storage_verified=True, isolated_restore_verified=True))

	def doctor(self, root: Path | str, *, expected_anchor: Mapping[str, object] | None = None,
			   isolated: bool = False, semantic: bool = True) -> StoreResult:
		"""Verify a stopped store without modifying it.

		Default (scalable): walk the layout/privacy without buffering contents,
		open the store (full record chain and receipts), verify every referenced
		blob, then replay graph semantics with a GraphOwner (release events
		included). `isolated=True` additionally performs the historical
		backup + isolated restore check, which needs ~2-3x the store size.
		"""
		if isolated:
			try:
				self.backup(root, expected_anchor=expected_anchor)
			except (JournalError, OSError) as exc:
				return _failure("doctor", exc)
			return StoreResult("doctor", "ok", storage_verified=True, isolated_restore_verified=True)
		try:
			path = FileJournal.check_location(root, durability=self.durability)
			self._walk(path)
			# Doctor is a full scrub: never trust a sealed index.
			with FileJournal.open(path, schema=self.schema, durability=self.durability,
								  trust_index=False) as journal:
				records = journal.records()
				anchor = {"store_id": journal.meta["store_id"], "journal_seq": journal.watermark,
					"record_hash": records[-1]["record_hash"] if records else None}
				if expected_anchor is not None and dict(expected_anchor) != anchor:
					raise JournalError("history_unavailable", "Independent store anchor differs")
				for ref in journal._blob_refs(records):
					journal.get_blob(ref)
				if semantic and records:
					_semantic_replay(journal)
		except (JournalError, OSError) as exc:
			return _failure("doctor", exc)
		except GraphError as exc:
			return StoreResult("doctor", exc.code if exc.code in _SAFE_CODES else "corruption")
		return StoreResult("doctor", "ok", storage_verified=True)

	def _walk(self, root: Path) -> None:
		"""Layout, privacy and pending-work checks without reading file contents."""
		seen = 0
		stack = [root]
		while stack:
			directory = stack.pop()
			_private(directory.lstat(), directory=True)
			with os.scandir(directory) as entries:
				for entry in entries:
					seen += 1
					if seen > self.budget.max_files:
						raise JournalError("limit_exceeded", "Store entry budget exceeded")
					path = Path(entry.path)
					relative = path.relative_to(root).as_posix()
					info = entry.stat(follow_symlinks=False)
					if stat.S_ISDIR(info.st_mode):
						_private(info, directory=True)
						if directory == root and entry.name not in _DIRECTORIES:
							raise JournalError("format_unsupported", "Unknown store directory")
						if relative.startswith("records/") and not re.fullmatch(r"records/[0-9]{20}", relative):
							raise JournalError("corruption", "Invalid committed record directory")
						stack.append(path)
						continue
					_private(info)
					if relative.startswith("tmp/"):
						raise JournalError("recovery_required", "Pending temporary assets require owner recovery")
					if directory == root and entry.name not in {"META.json", "writer.lock"}:
						raise JournalError("format_unsupported", "Unknown store asset")

	def restore(self, root: Path | str, snapshot: bytes) -> StoreResult:
		if len(snapshot) > self.budget.max_snapshot_bytes:
			raise JournalError("limit_exceeded", "Backup envelope budget exceeded")
		try:
			with FileJournal.restore_snapshot(root, snapshot, schema=self.schema,
					durability=self.durability, projector=self.projector) as restored:
				if self.projector is not None:
					restored.projections()
		except GraphError:
			raise JournalError("backup_invalid", "Invalid restored store data") from None
		return StoreResult("restore", "ok", storage_verified=True, isolated_restore_verified=True)


class _OfflineReplay:
	"""Replay-only ports: GraphOwner construction replays without any policy."""

	def current(self) -> Any:
		raise JournalError("permission_denied", "Offline replay performs no operations")

	def actor_ref(self, context: Any) -> str:
		raise JournalError("permission_denied", "Offline replay performs no operations")

	def hold(self, context: Any) -> Any:
		raise JournalError("permission_denied", "Offline replay performs no operations")

	def decide(self, request: Any) -> Any:
		raise JournalError("permission_denied", "Offline replay performs no operations")


def _semantic_replay(journal: FileJournal) -> None:
	from contexture.application.managed import GraphOwner
	from contexture.application.managed_releases import PROJECTION_NAME, release_projection

	offline = _OfflineReplay()
	GraphOwner(journal, identities=offline, admission=offline, authority_fence=offline,
		clock=lambda: "1970-01-01T00:00:00Z", use_snapshots=False,
		projections={PROJECTION_NAME: release_projection()}).close()


def check_software_compatibility(snapshot: bytes, support: SoftwareCompatibility) -> StoreResult:
	"""Check exact declared format support, not package-version ordering or readiness."""
	value = _json(snapshot)
	meta, records = value.get("meta"), value.get("records")
	if not isinstance(meta, dict) or not isinstance(records, list):
		raise JournalError("backup_invalid", "Backup collections required")
	def supported(value: object, accepted: frozenset[str]) -> bool:
		return isinstance(value, str) and value in accepted
	if (not supported(meta.get("schema"), support.store_formats)
			or not supported(meta.get("canonical_profile"), support.canonical_profiles)
			or not supported(meta.get("durability"), support.durability_profiles)):
		return StoreResult("compatibility", "format_unsupported")
	for record in records:
		if not isinstance(record, dict) or not supported(record.get("schema"), support.record_formats):
			return StoreResult("compatibility", "format_unsupported")
		events = record.get("events")
		if not isinstance(events, list) or not events:
			raise JournalError("backup_invalid", "Record events required")
		for event in events:
			if not isinstance(event, dict) or not supported(event.get("type"), support.event_types):
				return StoreResult("compatibility", "format_unsupported")
			versions = []
			if event["type"] == "GraphSeeded":
				after = event.get("after")
				if not isinstance(after, dict):
					raise JournalError("backup_invalid", "Seed definition required")
				versions.append(after.get("semantic_version"))
			elif event["type"] == "SemanticMigrated":
				versions.extend([event.get("before"), event.get("after")])
			if any(not supported(version, support.semantic_versions) for version in versions):
				return StoreResult("compatibility", "semantic_unsupported")
	return StoreResult("compatibility", "ok")