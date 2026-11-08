"""Single-owner FileJournal outer adapter (candidate managed v1 contract).

No graph manager may import this adapter: move the structural projection port
to the application layer when that layer is registered. The architecture gate
must explicitly approve server -> core.graph; this module does not bypass it.
The host supplies the registered schema document, without remote resolution.
"""

from __future__ import annotations

import errno
import hashlib
import os
import re
import stat
import sys
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from threading import RLock
from typing import Any, Protocol
from uuid import uuid4

from jsonschema import Draft202012Validator as SchemaValidator
from jsonschema_rs import Draft202012Validator, ValidationError

from contexture.core.graph.canonical import (
	CANONICAL_PROFILE,
	CHECKPOINT_DOMAIN,
	JOURNAL_BACKUP_DOMAIN,
	JOURNAL_PAYLOAD_DOMAIN,
	JOURNAL_RECORD_DOMAIN,
	MAX_COUNTER,
	canonical_digest,
	canonical_json,
	decode_json,
	parse_counter,
)

# Backward-compatible adapter aliases; canonical.py owns the exact registry.
PAYLOAD_DOMAIN = JOURNAL_PAYLOAD_DOMAIN
RECORD_DOMAIN = JOURNAL_RECORD_DOMAIN
PROJECTIONS = frozenset({
	"graphs", "desired_releases", "release_operations", "prepare_attempt_history",
	"observations_history", "receipts_index", "projection_versions", "blob_refs",
})
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_PAD = re.compile(r"[0-9]{20}\Z")


@lru_cache(maxsize=1)
def _renameat2() -> Callable[..., int] | None:
	"""glibc renameat2, or None where unavailable (callers must refuse)."""
	import ctypes

	try:
		function = ctypes.CDLL(None, use_errno=True).renameat2
	except (AttributeError, OSError):
		return None
	function.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
	function.restype = ctypes.c_int
	return function


def _rename_noreplace(source: Path, destination: Path) -> None:
	"""Atomically publish source at destination, never replacing an existing file.

	Windows os.rename already refuses an existing destination. On Linux use
	renameat2(RENAME_NOREPLACE): no check-then-rename race and no transient
	second hard link. FileExistsError is a DEFINITE non-publication.
	"""
	if os.name == "nt":
		os.rename(source, destination)
		return
	function = _renameat2()
	if function is None:
		raise JournalError("durability_unsupported", "No-replace rename primitive unavailable")
	import ctypes

	at_fdcwd, rename_noreplace = -100, 1
	if function(at_fdcwd, os.fsencode(source), at_fdcwd, os.fsencode(destination), rename_noreplace) != 0:
		code = ctypes.get_errno()
		if code in (errno.ENOSYS, errno.EINVAL):
			raise JournalError("durability_unsupported", "Filesystem lacks no-replace rename")
		raise OSError(code, os.strerror(code), str(destination))


class JournalError(RuntimeError):
	"""Storage error with a stable diagnostic code and no payload disclosure."""

	def __init__(self, code: str, message: str):
		super().__init__(message)
		self.code = code


class CommitUnknown(JournalError):
	def __init__(self) -> None:
		super().__init__("commit_unknown", "Publication may have happened; close and recover under lock")


class ProjectionPort(Protocol):
	"""Pure application-owned full replay, without authorization or effects.

	Returns exactly PROJECTIONS, including complete ordered historical lists.
	The storage adapter does not invent a graph/release semantic reducer.
	Checkpoint loading uses this oracle against the complete retained stream;
	there is deliberately no unproven prefix-skipping optimization.
	"""

	def __call__(self, records: Sequence[Mapping[str, Any]], /) -> Mapping[str, Any]: ...


def _digest(domain: str, value: object) -> str:
	return canonical_digest(value, domain)


def _bytes_digest(data: bytes) -> str:
	return "sha256:" + hashlib.sha256(data).hexdigest()


def _utc() -> str:
	return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _detach(value: object) -> Any:
	return decode_json(canonical_json(value))

@lru_cache(maxsize=8)
def _compiled_validators(schema_bytes: bytes) -> dict[str, Draft202012Validator]:
	schema = decode_json(schema_bytes)
	if not isinstance(schema, dict):
		raise JournalError("schema_invalid", "Registered schema must be an object")
	SchemaValidator.check_schema(schema)
	try:
		return {name: Draft202012Validator(
			{"$defs": schema["$defs"], "$ref": "#/$defs/" + name},
			offline=True, validate_formats=False)
			for name in ("FileJournalMeta", "CommitRecord", "BlobRef", "Checkpoint", "Event", "CommandReceipt")}
	except ValidationError as error:
		raise JournalError("schema_invalid", "Registered schema compilation failed") from error


class FileJournal:
	"""Lifetime exclusive read/write owner over a local, non-shared directory.

	Fault hook stages (synchronous, under the owner mutex):
	KIND.temp_written -> KIND.file_synced -> KIND.before_rename ->
	KIND.renamed -> KIND.directory_synced, for meta/record/blob/checkpoint/hint.
	directory.created and directory.synced cover every newly created bucket's
	parent. Hooks can raise or os._exit; a record failure at/after the rename
	attempt freezes writes, including blob/checkpoint/backup operations. The
	watermark is not advanced until directory confirmation. No blind retry.

	Strict currently supports Linux ext4/xfs/btrfs with file+directory fsync.
	This is primitive capability detection, not a certified hardware matrix.
	Windows strict and macOS refuse; Windows process_crash uses a native byte
	range lock and explicitly promises no power-loss survival. WSL drvfs/9p,
	network/FUSE/cloud-synced stores are refused even in process_crash mode.
	External privileged directory replacement/tampering is outside the owner
	guarantee. Every adapter path is lstat-checked, never caller-selected.
	"""

	def __init__(self) -> None:
		self.root: Path = Path()
		self.durability: str = "strict"
		self._fault: Callable[[str], None] | None = None
		self._projector: ProjectionPort | None = None
		self._schema: dict[str, Any] = {}
		self._validators: dict[str, Draft202012Validator] = {}
		self._meta: dict[str, Any] = {}
		self._mutex = RLock()
		self._lock_fd: int | None = None
		self._lock_identity: tuple[int, int] | None = None
		self._closed = False
		self._unknown = False
		self._records: list[dict[str, Any]] = []
		self._event_batch_ids: set[str] = set()
		self._receipts: dict[tuple[str, str], dict[str, Any]] = {}
		self.recovery_actions: list[dict[str, str]] = []

	@classmethod
	def check_location(cls, root: Path | str, *, durability: str = "strict") -> Path:
		"""Validate an existing private local store without creating or opening it."""
		if ".." in Path(root).parts:
			raise JournalError("unsafe_path", "Dot traversal in store path is forbidden")
		checker = cls()
		checker.root = Path(os.path.abspath(root))
		checker.durability = durability
		if any((parent / ".git").exists() for parent in (checker.root, *checker.root.parents)):
			raise JournalError("unsafe_path", "Journal stores must stay outside source worktrees")
		checker._check_platform()
		if not checker.root.exists():
			raise JournalError("store_missing", "Existing store required")
		checker._check_path(checker.root, directory=True)
		return checker.root

	@classmethod
	def open(
		cls, root: Path | str, *, schema: Mapping[str, Any],
		durability: str = "strict", projector: ProjectionPort | None = None,
		fault: Callable[[str], None] | None = None,
		_restore_bytes: bytes | None = None,
	) -> FileJournal:
		"""Open/create a private store; never seed over an unreadable META.

		schema is the host's local registered managed.schema.json document.
		No remote references are permitted. An existing profile must match
		exactly; callers cannot silently weaken or relabel durability.
		"""
		owner = cls()
		if ".." in Path(root).parts:
			raise JournalError("unsafe_path", "Dot traversal in store path is forbidden")
		owner.root = Path(os.path.abspath(root))
		if any((parent / ".git").exists() for parent in (owner.root, *owner.root.parents)):
			raise JournalError("unsafe_path", "Journal stores must stay outside source worktrees")
		owner.durability = durability
		owner._fault = fault
		owner._projector = projector
		owner._schema = _detach(schema)
		try:
			owner._validators = owner._make_validators()
			owner._check_platform()
			owner._mkdir(owner.root)
			owner._check_path(owner.root, directory=True)
			owner._acquire()
			for name in ("records", "blobs", "checkpoints", "hints", "indexes", "tmp", "quarantine"):
				owner._mkdir(owner.root / name)
			meta = owner.root / "META.json"
			if meta.exists():
				owner._meta = owner._read_json(meta)
				owner._validate("FileJournalMeta", owner.meta)
				if owner.meta["durability"] != durability:
					raise JournalError("durability_unsupported", "Persisted durability must match requested profile")
				if _restore_bytes is not None:
					raise JournalError("restore_conflict", "Restore must create an isolated fresh store")
			else:
				# Final records/checkpoints/blobs without META are not an empty store.
				if any(any((owner.root / name).iterdir()) for name in ("records", "checkpoints", "blobs")):
					raise JournalError("corruption", "Missing META with existing durable assets")
				owner._meta = {"schema": "contexture.file-journal/v1", "store_id": str(uuid4()),
							  "canonical_profile": CANONICAL_PROFILE, "durability": durability,
							  "created_at": _utc()}
				if _restore_bytes is not None:
					owner._meta["restored_from"] = owner.put_blob(_restore_bytes, media_type="application/json")
				owner._validate("FileJournalMeta", owner.meta)
				owner._publish(meta, canonical_json(owner.meta), "meta")
			owner._recover()
			if _restore_bytes is None:
				owner._verify_restore_origin()
			return owner
		except BaseException:
			owner.close()
			raise

	def _make_validators(self) -> dict[str, Draft202012Validator]:
		def inspect(value: Any) -> None:
			if isinstance(value, dict):
				for key, child in value.items():
					if key in {"$ref", "$dynamicRef"} and (not isinstance(child, str) or not child.startswith("#/$defs/")):
						raise JournalError("schema_invalid", "Only local registered schema references are allowed")
					inspect(child)
			elif isinstance(value, list):
				for child in value:
					inspect(child)
		inspect(self._schema)
		return dict(_compiled_validators(canonical_json(self._schema)))

	@property
	def meta(self) -> dict[str, Any]:
		"""Detached persisted metadata; callers cannot mutate owner identity."""
		return _detach(self._meta)

	def _validate(self, name: str, value: Any) -> None:
		if not self._validators[name].is_valid(value):
			raise JournalError("schema_invalid", f"Closed {name} contract rejected")

	def _hit(self, step: str) -> None:
		if self._fault is not None:
			self._fault(step)

	def _check_platform(self) -> None:
		if self.durability not in {"strict", "process_crash"}:
			raise JournalError("durability_unsupported", "Unknown durability profile")
		if any(part.lower().startswith(("onedrive", "dropbox", "google drive")) for part in self.root.parts):
			raise JournalError("durability_unsupported", "Cloud-synchronized storage is unsupported")
		if sys.platform == "win32":
			import ctypes
			drive = self.root.drive + "\\"
			if not self.root.drive or ctypes.windll.kernel32.GetDriveTypeW(drive) != 3:
				raise JournalError("durability_unsupported", "Only local fixed Windows volumes are supported")
			if self.durability == "strict":
				raise JournalError("durability_unsupported", "Windows strict directory durability is unproven")
			# mode=0700/0600 does not establish a private Windows DACL. Do not
			# pretend it does. Enable this profile only with a reviewed native
			# ACL boundary; the native locking branch is ready, not certified.
			raise JournalError("durability_unsupported", "Windows private DACL boundary is not yet verified")
		if not sys.platform.startswith("linux"):
			raise JournalError("durability_unsupported", "No proven primitives for this platform")
		# Longest lexical mount prefix; do not resolve away symlinks.
		mounts: list[tuple[int, str]] = []
		for line in Path("/proc/self/mountinfo").read_text().splitlines():
			left, right = line.split(" - ", 1)
			mount = re.sub(r"\\([0-7]{3})", lambda m: chr(int(m[1], 8)), left.split()[4])
			if self.root.is_relative_to(Path(mount)):
				mounts.append((len(mount), right.split()[0]))
		filesystem = max(mounts, default=(0, "unknown"))[1]
		allowed = {"ext4", "xfs", "btrfs"}
		if self.durability == "process_crash":
			allowed |= {"tmpfs", "overlay"}
		if filesystem not in allowed:
			raise JournalError("durability_unsupported", f"Unsupported local filesystem: {filesystem}")

	def _check_path(self, path: Path, *, directory: bool = False) -> None:
		if not path.is_relative_to(self.root) and path not in self.root.parents:
			raise JournalError("unsafe_path", "Path escaped store boundary")
		for component in (*reversed(path.parents), path):
			try:
				info = component.lstat()
			except FileNotFoundError:
				continue
			if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
				raise JournalError("unsafe_path", "Symlinks and reparse points are forbidden")
			if component == path and directory and not stat.S_ISDIR(info.st_mode):
				raise JournalError("unsafe_path", "Expected a directory")
			if component.is_relative_to(self.root):
				if os.name == "posix" and (info.st_uid != os.geteuid() or info.st_mode & 0o077):
					raise JournalError("unsafe_permissions", "Store assets must be private to the owner")
				if not stat.S_ISDIR(info.st_mode) and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1):
					raise JournalError("unsafe_path", "Only single-link regular files are allowed")

	def _mkdir(self, path: Path) -> None:
		self._check_path(path, directory=True)
		if path.exists():
			return
		if not path.parent.exists():
			self._mkdir(path.parent)
		try:
			path.mkdir(mode=0o700)
		except FileExistsError:
			# Two initial openers can reach mkdir before lifetime locking;
			# validate the winning directory, then compete for the OS lock.
			self._check_path(path, directory=True)
			return
		self._hit("directory.created")
		self._sync_dir(path.parent)
		self._hit("directory.synced")

	def _sync_dir(self, path: Path) -> None:
		self._check_path(path, directory=True)
		if self.durability == "strict":
			fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
			try:
				os.fsync(fd)
			finally:
				os.close(fd)

	def _acquire(self) -> None:
		path = self.root / "writer.lock"
		self._check_path(path)
		fd = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
		try:
			if os.name == "nt":
				import msvcrt
				if os.fstat(fd).st_size == 0:
					os.write(fd, b"\0")
					os.fsync(fd)
				os.lseek(fd, 0, os.SEEK_SET)
				msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
			else:
				import fcntl
				fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
		except OSError as exc:
			os.close(fd)
			if exc.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
				raise JournalError("store_in_use", "Store has an exclusive lifetime owner") from None
			raise
		self._lock_fd = fd
		self._check_path(path)
		self._lock_identity = (os.fstat(fd).st_dev, os.fstat(fd).st_ino)
		self._check_lock()
		self._sync_dir(self.root)

	def _check_lock(self) -> None:
		"""The lock PATH must still name the inode we hold.

		flock protects an inode, not a name. If an operator deletes or replaces
		writer.lock, a second process could lock a new inode and write too.
		Refusing here makes the original owner stop before its next write.
		"""
		try:
			info = (self.root / "writer.lock").lstat()
		except FileNotFoundError:
			info = None
		if info is None or (info.st_dev, info.st_ino) != self._lock_identity:
			self._unknown = True
			raise JournalError("store_in_use", "Writer lock was removed or replaced; owner stopped")

	def close(self) -> None:
		with self._mutex:
			if self._lock_fd is not None:
				os.close(self._lock_fd)
				self._lock_fd = None
			self._closed = True

	def __enter__(self) -> FileJournal:
		return self

	def __exit__(self, *args: object) -> None:
		self.close()

	@contextmanager
	def _guard(self, *, write: bool = False) -> Iterator[None]:
		with self._mutex:
			if self._closed or self._lock_fd is None:
				raise JournalError("closed", "Store owner is closed")
			if write and self._unknown:
				raise CommitUnknown()
			if write:
				self._check_lock()
			yield

	def _read_bytes(self, path: Path) -> bytes:
		self._check_path(path)
		fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
		try:
			info = os.fstat(fd)
			if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
				raise JournalError("unsafe_path", "Expected a single-link regular file")
			with os.fdopen(fd, "rb", closefd=False) as stream:
				return stream.read()
		finally:
			os.close(fd)

	def _read_json(self, path: Path) -> dict[str, Any]:
		raw = self._read_bytes(path)
		try:
			value = decode_json(raw)
			if not isinstance(value, dict) or canonical_json(value) != raw:
				raise JournalError("corruption", "Asset is not a canonical JSON object")
			return value
		except ValueError:
			raise JournalError("corruption", "Invalid raw canonical JSON") from None

	def _publish(self, final: Path, raw: bytes, kind: str) -> None:
		"""Immutable publish with exclusive-owner conflict detection.

		Publication never replaces an existing name (renameat2 NOREPLACE on
		Linux, rename on Windows), so even a second writer that bypassed the
		lifetime lock cannot overwrite an acknowledged record. Other rename
		exceptions remain ambiguous (e.g. interrupted syscall) => unknown.
		"""
		self._mkdir(final.parent)
		# Also confirm an already existing bucket: an earlier failed mkdir
		# confirmation must not let a later append bypass its parent sync.
		self._sync_dir(final.parent.parent)
		self._check_path(final)
		temp = self.root / "tmp" / (str(uuid4()) + ".tmp")
		self._check_path(temp)
		fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
		attempted = False
		try:
			try:
				view = memoryview(raw)
				while view:
					size = os.write(fd, view)
					if size <= 0:
						raise OSError(errno.ENOSPC, "Short write made no progress")
					view = view[size:]
				self._hit(kind + ".temp_written")
				os.fsync(fd)
				self._hit(kind + ".file_synced")
			finally:
				os.close(fd)
			if final.exists():
				# An exclusive owner never finds its next name taken: another
				# writer exists. Refuse definitively and stop writing.
				self._unknown = True
				raise JournalError("store_in_use", "Immutable destination already exists")
			self._hit(kind + ".before_rename")
			attempted = True
			try:
				_rename_noreplace(temp, final)
			except FileExistsError:
				# Definite: another writer published this name. Our bytes were
				# NOT published; stop all writes rather than report unknown.
				self._unknown = True
				raise JournalError("store_in_use", "Another writer published the same immutable name") from None
			except JournalError:
				# Unsupported primitive: definitely nothing was published.
				attempted = False
				raise
			self._hit(kind + ".renamed")
			self._sync_dir(final.parent)
			# The rename removes a tmp directory entry as well.
			self._sync_dir(temp.parent)
			self._hit(kind + ".directory_synced")
		except JournalError as exc:
			if exc.code in ("store_in_use", "durability_unsupported", "corruption"):
				raise
			if attempted and kind == "record":
				self._unknown = True
				raise CommitUnknown() from exc
			if attempted:
				self._unknown = True
			raise
		except BaseException as exc:
			if attempted and kind == "record":
				self._unknown = True
				raise CommitUnknown() from exc
			if attempted:
				# Auxiliary publication uncertainty must not allow later records
				# to reference a blob whose durability is unknown.
				self._unknown = True
			raise

	def _record_path(self, sequence: int) -> Path:
		if not 1 <= sequence <= MAX_COUNTER:
			raise JournalError("limit_exceeded", "Journal counter exhausted")
		return self.root / "records" / f"{(sequence - 1) // 10000:020d}" / f"{sequence:020d}.json"

	def _blob_path(self, digest: str) -> Path:
		if not isinstance(digest, str) or not digest.startswith("sha256:") or not _HEX.fullmatch(digest[7:]):
			raise JournalError("unsafe_path", "Invalid SHA256 blob selector")
		return self.root / "blobs" / "sha256" / digest[7:9] / (digest[7:] + ".blob")

	def _blob_refs(self, value: Any) -> list[dict[str, Any]]:
		# Only declared BlobRef locations are references. Arbitrary inline
		# results/config/extensions can coincidentally have digest/size/type
		# keys; interpreting such data as paths would change user semantics.
		if isinstance(value, (list, tuple)):
			return [ref for child in value for ref in self._blob_refs(child)]
		if not isinstance(value, dict):
			return []
		schema = value.get("schema")
		if schema == "contexture.journal-record/v1":
			refs = self._blob_refs(value.get("command_receipt"))
			refs.extend(event["evidence"] for event in value["events"] if event["type"] == "RecoveryActionRecorded")
			return refs
		if schema == "contexture.command-receipt/v1":
			result = value["operation_result"]
			return [result["blob"]] if "blob" in result else []
		if schema == "contexture.checkpoint/v1":
			return list(value["blob_refs"]) + self._blob_refs(value["receipts_index"])
		return []

	def put_blob(self, data: bytes, *, media_type: str = "application/octet-stream") -> dict[str, Any]:
		with self._guard(write=True):
			if type(data) is not bytes:
				raise JournalError("schema_invalid", "Blob input must be exact bytes")
			if media_type == "application/json":
				if canonical_json(decode_json(data)) != data:
					raise JournalError("schema_invalid", "JSON blob must contain raw canonical bytes")
			ref = {"digest": _bytes_digest(data), "size": str(len(data)), "media_type": media_type}
			self._validate("BlobRef", ref)
			path = self._blob_path(ref["digest"])
			if path.exists():
				if self.get_blob(ref) != data:
					raise JournalError("corruption", "Content-addressed blob conflict")
				# Confirm existing file and directory before a new reference.
				self._confirm_file(path)
			else:
				self._publish(path, data, "blob")
			return _detach(ref)

	def get_blob(self, ref: Mapping[str, Any]) -> bytes:
		with self._guard():
			wire = _detach(ref)
			self._validate("BlobRef", wire)
			try:
				raw = self._read_bytes(self._blob_path(wire["digest"]))
			except FileNotFoundError:
				raise JournalError("history_unavailable", "Required retained blob is missing") from None
			if str(len(raw)) != wire["size"] or _bytes_digest(raw) != wire["digest"]:
				raise JournalError("corruption", "Blob byte digest or size mismatch")
			if wire["media_type"] == "application/json" and canonical_json(decode_json(raw)) != raw:
				raise JournalError("corruption", "JSON blob is not canonical")
			return raw

	def _confirm_file(self, path: Path) -> None:
		self._check_path(path)
		fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
		try:
			os.fsync(fd)
		finally:
			os.close(fd)
		self._sync_dir(path.parent)
		if path.parent != self.root:
			self._sync_dir(path.parent.parent)

	def _verify_record(self, record: dict[str, Any], sequence: int, previous: str | None) -> None:
		self._validate("CommitRecord", record)
		if (record["store_id"] != self.meta["store_id"] or record["journal_seq"] != str(sequence)
				or record["prev_record_hash"] != previous):
			raise JournalError("corruption", "Record store, sequence or previous hash mismatch")
		payload = {key: record[key] for key in ("events", "command_receipt") if key in record}
		subject = {key: value for key, value in record.items() if key != "record_hash"}
		if record["payload_digest"] != _digest(PAYLOAD_DOMAIN, payload) or record["record_hash"] != _digest(RECORD_DOMAIN, subject):
			raise JournalError("corruption", "Record or payload digest mismatch")
		receipt = record.get("command_receipt")
		if receipt is not None:
			if (receipt["journal_seq"] != str(sequence) or receipt["actor_ref"] != record["actor_ref"]
					or receipt["durability"] != self.durability):
				raise JournalError("corruption", "Receipt actor, sequence or durability mismatch")
			graph_events = [event for event in record["events"] if event["type"] in {"GraphChangeCommitted", "CommandCompleted"}]
			if graph_events:
				event = graph_events[-1]
				revision = event.get("result_revision", event.get("revision"))
				status = "noop" if event["type"] == "CommandCompleted" else "committed"
				if (receipt["graph_id"] != event["graph_id"] or receipt["revision"] != revision
						or receipt["request_digest"] != event["command_digest"] or receipt["status"] != status):
					raise JournalError("corruption", "Receipt does not match accepted graph event")
		for ref in self._blob_refs(record):
			self.get_blob(ref)

	def _recover(self) -> None:
		"""Full-chain discovery ignores hints/indexes, including later buckets."""
		for path in self.root.rglob("*"):
			self._check_path(path, directory=path.is_dir())
		if "restored_from" in self.meta:
			self.get_blob(self.meta["restored_from"])
		paths: list[tuple[int, Path]] = []
		for bucket in (self.root / "records").iterdir():
			if not bucket.is_dir() or not _PAD.fullmatch(bucket.name):
				raise JournalError("corruption", "Unexpected record bucket")
			for path in bucket.iterdir():
				if path.suffix != ".json" or not _PAD.fullmatch(path.stem):
					raise JournalError("corruption", "Unexpected committed record filename")
				seq = int(path.stem)
				if path != self._record_path(seq):
					raise JournalError("corruption", "Record in wrong deterministic bucket")
				paths.append((seq, path))
		previous = None
		batches: set[str] = set()
		for expected, (seq, path) in enumerate(sorted(paths), 1):
			if seq != expected:
				raise JournalError("corruption", "Missing or duplicate committed record")
			record = self._read_json(path)
			self._verify_record(record, seq, previous)
			if record["event_batch_id"] in batches:
				raise JournalError("corruption", "Duplicate event batch identity")
			batches.add(record["event_batch_id"])
			receipt = record.get("command_receipt")
			if receipt is not None:
				key = (receipt["actor_ref"], receipt["idempotency_key"])
				if key in self._receipts:
					raise JournalError("corruption", "Duplicate accepted receipt key")
				self._receipts[key] = receipt
			self._confirm_file(path)
			self._records.append(record)
			previous = record["record_hash"]
		self._event_batch_ids = batches
		# Unpublished work is never mistaken for a committed invalid tail.
		for path in sorted((self.root / "tmp").iterdir()):
			if not path.is_file():
				raise JournalError("corruption", "Unexpected temporary directory")
			digest = _bytes_digest(self._read_bytes(path))
			target = self.root / "quarantine" / (str(uuid4()) + ".artifact")
			evidence = {"schema": "contexture.quarantine/v1", "source": path.name,
						"artifact": target.name, "digest": digest, "action": "unpublished-temp"}
			# Evidence is durable before moving work, so even a crash during
			# quarantine has a diagnosed original source and byte digest.
			self._publish(target.with_suffix(".json"), canonical_json(evidence), "recovery")
			_rename_noreplace(path, target)
			self._sync_dir(target.parent)
			self._sync_dir(path.parent)
			self.recovery_actions.append(evidence)
		# Indexes are in-memory and entirely rebuilt from accepted records.
		# Arbitrary stale disposable files never become authority.

	def _verify_restore_origin(self) -> None:
		if "restored_from" not in self.meta:
			return
		original: Any = decode_json(self.get_blob(self.meta["restored_from"]))
		if not isinstance(original, dict) or original.get("schema") != "contexture.file-journal-backup/v1":
			raise JournalError("corruption", "Restore provenance is not a backup manifest")
		source = original.get("records")
		if not isinstance(source, list) or len(self._records) < len(source):
			raise JournalError("restore_incomplete", "Interrupted restore must remain isolated")
		for expected, actual in zip(source, self._records):
			for key in ("events", "command_receipt", "event_batch_id", "actor_ref", "written_at", "correlation_id", "journal_seq"):
				if expected.get(key) != actual.get(key):
					raise JournalError("corruption", "Restored records differ from original backup provenance")

	@property
	def watermark(self) -> str:
		with self._guard():
			return str(len(self._records))

	def records(self, *, after: str = "0") -> tuple[dict[str, Any], ...]:
		with self._guard():
			offset = parse_counter(after)
			if offset > len(self._records):
				raise JournalError("schema_invalid", "Cursor exceeds durable watermark")
			return tuple(_detach(record) for record in self._records[offset:])

	def get_receipt(self, actor_ref: str, idempotency_key: str) -> dict[str, Any] | None:
		with self._guard():
			receipt = self._receipts.get((actor_ref, idempotency_key))
			return None if receipt is None else _detach(receipt)

	def append(
		self, events: Sequence[Mapping[str, Any]], *, actor_ref: str,
		command_receipt: Mapping[str, Any] | None = None,
		correlation_id: str | None = None, event_batch_id: str | None = None,
		written_at: str | None = None,
	) -> dict[str, Any]:
		"""Persist admitted resolved facts, not commands or authorization.

		The manager supplies CAS/current admission and any semantic reducer.
		An existing same-actor/key/result returns the exact original record;
		a different request digest refuses before publication.
		"""
		with self._guard(write=True):
			sequence = len(self._records) + 1
			receipt = _detach(command_receipt) if command_receipt is not None else None
			key: tuple[str, str] | None = None
			if receipt is not None:
				self._validate("CommandReceipt", receipt)
				key = (receipt["actor_ref"], receipt["idempotency_key"])
				if receipt["actor_ref"] != actor_ref:
					raise JournalError("schema_invalid", "Receipt and record actor must agree")
				prior = self._receipts.get(key)
				if prior is not None:
					# The idempotency identity is the request, not renewed
					# correlation/time/approval or a newly supplied result.
					if prior["request_digest"] != receipt["request_digest"]:
						raise JournalError("idempotency_conflict", "Actor/key already accepted another request")
					return _detach(self._records[int(prior["journal_seq"]) - 1])
			record = {"schema": "contexture.journal-record/v1", "store_id": self.meta["store_id"],
					  "journal_seq": str(sequence), "event_batch_id": event_batch_id or str(uuid4()),
					  "prev_record_hash": self._records[-1]["record_hash"] if self._records else None,
					  "events": _detach(events), "written_at": written_at or _utc(),
					  "actor_ref": actor_ref, "correlation_id": correlation_id or str(uuid4())}
			if receipt is not None:
				record["command_receipt"] = receipt
			payload = {key: record[key] for key in ("events", "command_receipt") if key in record}
			record["payload_digest"] = _digest(PAYLOAD_DOMAIN, payload)
			record["record_hash"] = _digest(RECORD_DOMAIN, record)
			self._verify_record(record, sequence, record["prev_record_hash"])
			if record["event_batch_id"] in self._event_batch_ids:
				raise JournalError("schema_invalid", "Event batch identity already accepted")
			for ref in self._blob_refs(record):
				self._confirm_file(self._blob_path(ref["digest"]))
			returned = _detach(record)
			self._publish(self._record_path(sequence), canonical_json(record), "record")
			try:
				# Private indexes are fenced by _mutex; append only the verified
				# suffix and undo it on failure before any reader can observe it.
				self._event_batch_ids.add(record["event_batch_id"])
				if receipt is not None:
					self._receipts[(receipt["actor_ref"], receipt["idempotency_key"])] = receipt
				self._records.append(record)
			except BaseException as exc:
				self._unknown = True
				set.discard(self._event_batch_ids, record["event_batch_id"])
				if key is not None:
					dict.pop(self._receipts, key, None)
				while len(self._records) >= sequence:
					list.pop(self._records)
				raise CommitUnknown() from exc
			return returned

	def _project(self, records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
		if self._projector is None:
			raise JournalError("projection_required", "Complete application-owned pure projection port is required")
		# Defensive copies: the reducer cannot mutate accepted in-memory facts.
		try:
			value = _detach(self._projector(tuple(_detach(record) for record in records)))
		except BaseException:
			# Accepted facts stay accepted; all further writes require reopen
			# and projection rebuild, never an append beyond broken indexes.
			self._unknown = True
			raise
		if not isinstance(value, dict) or set(value) != PROJECTIONS:
			self._unknown = True
			raise JournalError("projection_invalid", "Projection port must return every complete projection")
		return value

	def _checkpoint_subject(self, projections: Mapping[str, Any], count: int) -> dict[str, Any]:
		if not 1 <= count <= len(self._records):
			raise JournalError("checkpoint_invalid", "Checkpoint anchor must be an accepted positive watermark")
		result = {"schema": "contexture.checkpoint/v1", "store_id": self.meta["store_id"],
				  "last_seq": str(count), "last_record_hash": self._records[count - 1]["record_hash"],
				  **_detach(projections)}
		return result

	def _check_checkpoint(self, value: dict[str, Any]) -> None:
		self._validate("Checkpoint", value)
		subject = {key: item for key, item in value.items() if key != "checkpoint_digest"}
		if value["checkpoint_digest"] != _digest(CHECKPOINT_DOMAIN, subject):
			raise JournalError("checkpoint_invalid", "Checkpoint subject digest mismatch")
		count = parse_counter(value["last_seq"])
		if (value["store_id"] != self.meta["store_id"] or not 1 <= count <= len(self._records)
				or value["last_record_hash"] != self._records[count - 1]["record_hash"]):
			raise JournalError("checkpoint_invalid", "Checkpoint store/record anchor mismatch")
		projections = {key: value[key] for key in PROJECTIONS}
		self._check_projection_order(projections)
		expected_receipts = [record["command_receipt"] for record in self._records[:count] if "command_receipt" in record]
		expected_receipts.sort(key=lambda item: (item["actor_ref"], item["idempotency_key"]))
		if projections["receipts_index"] != expected_receipts:
			raise JournalError("checkpoint_invalid", "Checkpoint lost accepted receipt results")
		# A self-consistent checkpoint hash is not replay evidence. Verify ALL
		# projections against full-prefix replay before accepting or publishing.
		if canonical_json(projections) != canonical_json(self._project(self._records[:count])):
			raise JournalError("checkpoint_invalid", "Complete checkpoint projections differ from full replay")
		refs = {(ref["digest"], ref["media_type"]): ref for ref in self._blob_refs(self._records[:count])}
		for ref in projections["blob_refs"]:
			self.get_blob(ref)
			refs.pop((ref["digest"], ref["media_type"]), None)
		if refs:
			raise JournalError("checkpoint_invalid", "Checkpoint omitted retained blob reference closure")

	def _check_projection_order(self, value: Mapping[str, Any]) -> None:
		def ordered(items: list[Any], key: Callable[[Any], Any]) -> None:
			keys = [key(item) for item in items]
			if keys != sorted(keys) or len(keys) != len(set(keys)):
				raise JournalError("checkpoint_invalid", "Checkpoint ordering or uniqueness violation")
		ordered(value["graphs"], lambda item: item["definition"]["graph_id"])
		for graph in value["graphs"]:
			ordered(graph["tombstones"], lambda item: item["node_id"])
			if graph["definition_digest"] != _digest("contexture.graph/v1", graph["definition"]):
				raise JournalError("checkpoint_invalid", "Checkpoint graph definition digest mismatch")
		ordered(value["desired_releases"], lambda item: (item["request"]["graph_id"], item["request"]["target_id"]))
		ordered(value["receipts_index"], lambda item: (item["actor_ref"], item["idempotency_key"]))
		ordered(value["blob_refs"], lambda item: (item["digest"], item["media_type"]))

	def checkpoint(self) -> dict[str, Any]:
		"""Publish full projections at the selected confirmed watermark.

		This intentionally refuses without an independent application reducer.
		No receipt-only faux graph/release engine is supplied by the adapter.
		"""
		with self._guard(write=True):
			projections = self._project(self._records)
			subject = self._checkpoint_subject(projections, len(self._records))
			value = {**subject, "checkpoint_digest": _digest(CHECKPOINT_DOMAIN, subject)}
			self._check_checkpoint(value)
			path = self.root / "checkpoints" / f"{len(self._records):020d}" / (value["checkpoint_digest"][7:] + ".json")
			if path.exists():
				if self._read_json(path) != value:
					raise JournalError("corruption", "Immutable checkpoint conflict")
				self._confirm_file(path)
			else:
				self._publish(path, canonical_json(value), "checkpoint")
			# Hints are deliberately not written: exact hint schema is not
			# frozen. Discovery below is complete; stale hints are ignored.
			return _detach(value)

	def read_checkpoint(self) -> dict[str, Any] | None:
		"""Newest verifiable checkpoint; invalid checkpoint alone falls back.

		Required record/blob failures are never caught here as disposable
		checkpoint failures. Reopen has already scrubbed the complete chain.
		"""
		with self._guard():
			candidates: list[tuple[int, Path]] = []
			for bucket in (self.root / "checkpoints").iterdir():
				self._check_path(bucket, directory=True)
				if not _PAD.fullmatch(bucket.name):
					continue
				for path in bucket.iterdir():
					if path.suffix == ".json" and _HEX.fullmatch(path.stem):
						candidates.append((int(bucket.name), path))
			for count, path in sorted(candidates, reverse=True):
				try:
					value = self._read_json(path)
				except JournalError as exc:
					if exc.code not in {"schema_invalid", "corruption"}:
						raise
					continue
				if value.get("last_seq") != str(count) or value.get("checkpoint_digest") != "sha256:" + path.stem:
					continue
				try:
					self._check_checkpoint(value)
				except JournalError as exc:
					if exc.code not in {"checkpoint_invalid", "schema_invalid"}:
						raise
					continue
				return _detach(value)
			return None

	def projections(self) -> dict[str, Any]:
		"""Replay every retained fact through the pure port, no side effects.

		Full replay is intentional until a separately reviewed port accepts
		checkpoint state + tail and proves its equivalence for all projections.
		"""
		with self._guard():
			return _detach(self._project(self._records))

	def export_snapshot(self) -> bytes:
		"""Consistent canonical backup envelope, while holding the owner mutex.

		The envelope includes exact META/records/blob bytes encoded as hex,
		rather than an inconsistent live directory copy. No history is deleted.
		Its format is adapter-local, not a new managed wire-schema declaration.
		"""
		with self._guard(write=True):
			refs = self._blob_refs(self._records)
			if "restored_from" in self.meta:
				refs.append(self.meta["restored_from"])
			checkpoint = self.read_checkpoint() if self._projector is not None else None
			if checkpoint is not None:
				refs.extend(self._blob_refs(checkpoint))
			blobs = {ref["digest"]: self.get_blob(ref).hex() for ref in refs}
			subject = {"schema": "contexture.file-journal-backup/v1", "meta": _detach(self.meta),
					   "records": _detach(self._records), "blobs": blobs, "checkpoint": checkpoint}
			return canonical_json({**subject, "backup_digest": _digest(JOURNAL_BACKUP_DOMAIN, subject)})

	@classmethod
	def restore_snapshot(
		cls, root: Path | str, snapshot: bytes, *, schema: Mapping[str, Any],
		durability: str = "strict", projector: ProjectionPort | None = None,
	) -> FileJournal:
		"""Restore to a new isolated identity, never overwrite/resume in place.

		All record and receipt references are validated before creating the
		destination. New records retain original resolved facts and result
		sequences, but are re-chained under a fresh store identity. META's
		restored_from points at the exact original backup bytes. Interrupted
		restore is NOT auto-resumed; its partial destination stays isolated.
		"""
		value: Any = decode_json(snapshot)
		required = {"schema", "meta", "records", "blobs", "checkpoint", "backup_digest"}
		if (not isinstance(value, dict) or set(value) != required
				or value["schema"] != "contexture.file-journal-backup/v1" or canonical_json(value) != snapshot):
			raise JournalError("backup_invalid", "Invalid closed canonical backup envelope")
		subject = {key: item for key, item in value.items() if key != "backup_digest"}
		if value["backup_digest"] != _digest(JOURNAL_BACKUP_DOMAIN, subject):
			raise JournalError("backup_invalid", "Backup manifest digest mismatch")
		destination = Path(os.path.abspath(root))
		if destination.exists():
			raise JournalError("restore_conflict", "Restore destination must not exist")
		# Detached verifier performs no destination I/O; validate the original
		# chain with the same production checks and in-memory blob reader.
		verifier = cls()
		verifier._schema = _detach(schema)
		verifier._validators = verifier._make_validators()
		verifier._meta = value["meta"]
		verifier.durability = durability
		verifier._validate("FileJournalMeta", verifier.meta)
		if verifier.meta["durability"] != durability:
			raise JournalError("durability_unsupported", "Restore cannot relabel durability")
		if not isinstance(value["blobs"], dict) or not isinstance(value["records"], list):
			raise JournalError("backup_invalid", "Invalid backup collections")
		blob_bytes: dict[str, bytes] = {}
		for digest, encoded in value["blobs"].items():
			if not isinstance(encoded, str):
				raise JournalError("backup_invalid", "Invalid blob encoding")
			try:
				raw = bytes.fromhex(encoded)
			except ValueError:
				raise JournalError("backup_invalid", "Invalid blob encoding") from None
			if encoded != raw.hex() or _bytes_digest(raw) != digest:
				raise JournalError("backup_invalid", "Backup blob hash mismatch")
			blob_bytes[digest] = raw

		def read_blob(ref: Mapping[str, Any]) -> bytes:
			verifier._validate("BlobRef", ref)
			raw = blob_bytes.get(ref["digest"])
			if raw is None or str(len(raw)) != ref["size"]:
				raise JournalError("backup_invalid", "Backup lacks required blob bytes")
			if ref["media_type"] == "application/json" and canonical_json(decode_json(raw)) != raw:
				raise JournalError("backup_invalid", "Backup JSON blob is not canonical")
			return raw

		# A private verifier subclass is unnecessary; explicit dependency
		# injection is confined to this detached verifier, never live owner.
		verifier.get_blob = read_blob  # type: ignore[method-assign]
		previous = None
		keys: set[tuple[str, str]] = set()
		batches: set[str] = set()
		for sequence, record in enumerate(value["records"], 1):
			verifier._verify_record(record, sequence, previous)
			if record["event_batch_id"] in batches:
				raise JournalError("backup_invalid", "Duplicate backup event batch")
			batches.add(record["event_batch_id"])
			receipt = record.get("command_receipt")
			if receipt is not None:
				key = (receipt["actor_ref"], receipt["idempotency_key"])
				if key in keys:
					raise JournalError("backup_invalid", "Duplicate backup receipt")
				keys.add(key)
			previous = record["record_hash"]
		if "restored_from" in verifier.meta:
			read_blob(verifier.meta["restored_from"])
		verifier._records = value["records"]
		verifier._projector = projector
		if value["checkpoint"] is not None:
			verifier._check_checkpoint(value["checkpoint"])
		owner = cls.open(destination, schema=schema, durability=durability, projector=projector, _restore_bytes=snapshot)
		try:
			for digest, raw in blob_bytes.items():
				owner.put_blob(raw)
			for record in value["records"]:
				owner.append(record["events"], actor_ref=record["actor_ref"],
							 command_receipt=record.get("command_receipt"),
							 correlation_id=record["correlation_id"], event_batch_id=record["event_batch_id"],
							 written_at=record["written_at"])
			owner._verify_restore_origin()
			if projector is not None and owner._records:
				owner.checkpoint()
			return owner
		except BaseException:
			owner.close()
			raise