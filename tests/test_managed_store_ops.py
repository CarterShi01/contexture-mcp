"""Offline operations use real locks and production isolated restore."""

from dataclasses import replace
from pathlib import Path
import tempfile

import pytest

from contexture.core.graph.canonical import CANONICAL_PROFILE
from contexture.server.managed_journal import FileJournal, JournalError
from contexture.server.managed_store_ops import (
    SoftwareCompatibility, StoreBudget, StoreOperations, check_software_compatibility,
)
from tests.test_managed_journal import SCHEMA, empty_projector, event, receipt


@pytest.fixture
def root():
    with tempfile.TemporaryDirectory(prefix="contexture-offline-") as directory:
        yield Path(directory) / "store"


def seed(root):
    with FileJournal.open(root, schema=SCHEMA, projector=empty_projector) as owner:
        owner.append([event()], actor_ref="actor", command_receipt=receipt())
        owner.checkpoint()


def contents(root):
    return {str(p.relative_to(root)): (p.read_bytes(), p.stat().st_ino, p.stat().st_mtime_ns)
            for p in root.rglob("*") if p.is_file()}


def test_offline_doctor_and_backup_never_modify_source(root):
    seed(root)
    before = contents(root)
    operations = StoreOperations(schema=SCHEMA, projector=empty_projector)
    # Synthetic CommandCompleted fixtures are not a semantically valid graph.
    in_place = operations.doctor(root, semantic=False)
    assert in_place.code == "ok" and in_place.storage_verified and not in_place.isolated_restore_verified
    assert operations.doctor(root).code == "corruption"  # semantic replay catches it
    assert contents(root) == before
    result = operations.doctor(root, isolated=True)
    assert result.code == "ok" and result.storage_verified
    assert result.isolated_restore_verified
    assert not result.serving_ready and not result.definition_loaded and not result.write_enabled
    artifact = operations.backup(root)
    assert artifact.manifest["journal_seq"] == "1"
    assert contents(root) == before
    assert "actor" not in repr(artifact) and "sha256:" not in repr(result)
    destination = root.parent / "restored"
    assert operations.restore(destination, artifact.snapshot).code == "ok"
    with FileJournal.open(destination, schema=SCHEMA, projector=empty_projector) as restored:
        assert restored.watermark == "1"
        assert restored.meta["store_id"] != artifact.manifest["store_id"]
        assert restored.get_receipt("actor", "key") == receipt()


def test_active_owner_and_missing_store_are_not_taken_over(root):
    operations = StoreOperations(schema=SCHEMA)
    assert operations.doctor(root).code == "store_missing"
    assert not root.exists()
    with FileJournal.open(root, schema=SCHEMA):
        before = contents(root)
        assert operations.doctor(root).code == "store_in_use"
        with pytest.raises(JournalError, match="active owner"):
            operations.backup(root)
        assert contents(root) == before


def test_pending_work_is_not_quarantined_or_repaired(root):
    seed(root)
    pending = root / "tmp" / "pending"
    pending.write_bytes(b"unfinished private payload")
    pending.chmod(0o600)
    before = contents(root)
    result = StoreOperations(schema=SCHEMA).doctor(root)
    assert result.code == "recovery_required" and not result.storage_verified
    assert contents(root) == before


@pytest.mark.parametrize("kind", ["file", "bytes", "entries", "snapshot"])
def test_budgets_are_admission_failures_not_partial_backups(root, kind):
    seed(root)
    budget = {"file": StoreBudget(max_file_bytes=1), "bytes": StoreBudget(max_bytes=1),
              "entries": StoreBudget(max_files=1), "snapshot": StoreBudget(max_snapshot_bytes=1)}[kind]
    before = contents(root)
    # Byte/snapshot budgets bound the in-memory backup envelope (isolated mode).
    assert StoreOperations(schema=SCHEMA, budget=budget).doctor(root, isolated=True).code == "limit_exceeded"
    assert contents(root) == before


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "permissions", "corrupt", "bucket", "empty-bucket"])
def test_unsafe_or_corrupt_assets_fail_without_recovery(root, kind):
    seed(root)
    record = next((root / "records").rglob("*.json"))
    if kind == "symlink":
        (root / "hints" / "unsafe").symlink_to(record)
    elif kind == "hardlink":
        (root / "hints" / "unsafe").hardlink_to(record)
    elif kind == "permissions":
        record.chmod(0o644)
    elif kind == "corrupt":
        record.write_bytes(b"not canonical JSON")
    elif kind == "empty-bucket":
        (root / "records" / "unknown").mkdir(mode=0o700)
    else:
        bucket = record.parent.with_name("00000000000000000001")
        bucket.mkdir(mode=0o700)
        record.rename(bucket / record.name)
    before = contents(root)
    result = StoreOperations(schema=SCHEMA).doctor(root)
    assert result.code in {"unsafe_path", "unsafe_permissions", "corruption"}
    assert not result.storage_verified and not result.write_enabled
    assert contents(root) == before


def test_independent_anchor_and_restore_refuses_overwrite(root):
    seed(root)
    operations = StoreOperations(schema=SCHEMA)
    artifact = operations.backup(root)
    anchor = {k: v for k, v in artifact.manifest.items() if k != "backup_digest"}
    for isolated in (False, True):
        assert operations.doctor(root, expected_anchor=anchor, isolated=isolated, semantic=False).code == "ok"
        assert operations.doctor(root, expected_anchor={**anchor, "journal_seq": "2"},
                                 isolated=isolated, semantic=False).code == "history_unavailable"
    before = contents(root)
    with pytest.raises(JournalError, match="must not exist"):
        operations.restore(root, artifact.snapshot)
    assert contents(root) == before


def test_exact_software_support_never_claims_serving_readiness(root):
    seed(root)
    artifact = StoreOperations(schema=SCHEMA).backup(root)
    support = SoftwareCompatibility("reader", "writer",
        frozenset({"contexture.file-journal/v1"}), frozenset({"contexture.journal-record/v1"}),
        frozenset({"CommandCompleted"}), frozenset({CANONICAL_PROFILE}),
        frozenset({"1"}), frozenset({"strict"}))
    result = check_software_compatibility(artifact.snapshot, support)
    assert result.code == "ok" and not result.storage_verified and not result.serving_ready
    for field in ("store_formats", "record_formats", "event_types", "canonical_profiles", "durability_profiles"):
        assert check_software_compatibility(
            artifact.snapshot, replace(support, **{field: frozenset()})).code == "format_unsupported"


def test_in_place_doctor_replays_real_graph_semantics_without_modifying(root):
    from tests.test_managed_owner import LATER, Host, owner, request, seed as graph_seed
    from contexture.application.managed_releases import PROJECTION_NAME, release_projection

    with FileJournal.open(root, schema=SCHEMA) as journal:
        manager = owner(journal, Host(), projections={PROJECTION_NAME: release_projection()})
        manager.commit_bootstrap(manager.preview_bootstrap(graph_seed(), expires_at=LATER))
        manager.commit(manager.preview(request(manager), expires_at=LATER))
        manager.checkpoint()  # snapshots are hints: ignored by doctor, never trusted
        manager.close()
    before = contents(root)
    result = StoreOperations(schema=SCHEMA).doctor(root)
    assert result.code == "ok" and result.storage_verified
    assert contents(root) == before