"""Owner snapshots: startup applies only the journal tail (M4).

A snapshot is disposable acceleration anchored to the record chain; any doubt
falls back to full replay, and older history is materialized on demand.
"""

from __future__ import annotations

from pathlib import Path
import tempfile
from typing import Any, Iterator

import pytest

from contexture.application.managed_releases import PROJECTION_NAME, release_projection
from contexture.core.graph import history as history_module
from contexture.server.managed_journal import FileJournal
from tests.test_managed_owner import LATER, SCHEMA, Host, owner, request, seed


@pytest.fixture
def store() -> Iterator[Path]:
    with tempfile.TemporaryDirectory(prefix="contexture-snapshot-") as temp:
        yield Path(temp) / "store"


def make(journal: FileJournal, host: Host, **kwargs: Any) -> Any:
    return owner(journal, host, projections={PROJECTION_NAME: release_projection()}, **kwargs)


def populate(path: Path, host: Host, renames: int, *, snapshot_at: int | None = None) -> list[str]:
    names = []
    with FileJournal.open(path, schema=SCHEMA) as journal:
        manager = make(journal, host)
        manager.commit_bootstrap(manager.preview_bootstrap(seed(), expires_at=LATER))
        for i in range(renames):
            name = f"name-{i}"
            manager.commit(manager.preview(request(manager, key=f"k{i}", name=name), expires_at=LATER))
            names.append(name)
            if snapshot_at is not None and i + 1 == snapshot_at:
                receipt = manager.checkpoint()
                assert receipt["journal_seq"] == str(i + 2)
        manager.close()
    return names


def count_appends(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    calls = [0]
    original = history_module.History.append

    def counted(self: Any, *args: Any, **kwargs: Any) -> Any:
        calls[0] += 1
        return original(self, *args, **kwargs)

    monkeypatch.setattr(history_module.History, "append", counted)
    return calls


def test_startup_replays_only_the_tail_and_history_stays_complete(
        store: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    host = Host()
    names = populate(store, host, 30, snapshot_at=25)
    calls = count_appends(monkeypatch)
    with FileJournal.open(store, schema=SCHEMA) as journal:
        manager = make(journal, host)
        assert calls[0] == 5  # only records after the snapshot
        head = manager.read("g")
        assert head.revision == "31" and head.definition.nodes["r"].name == names[-1]
        # Old revisions, full history and revert still work (materialized lazily).
        assert manager.read("g", "2").definition.nodes["r"].name == names[0]
        assert len(manager.history("g").records) == 31
        preview = manager.preview_revert("g", "2", idempotency_key="undo", reason="r",
                                         correlation_id="c", expires_at=LATER)
        manager.commit(preview)
        assert manager.read("g").definition.nodes["r"].name == names[0]
        # Idempotency survives: receipts are rebuilt from every record.
        assert manager.receipt("g", "k0") is not None
        manager.close()


def test_snapshot_mismatch_or_opt_out_falls_back_to_full_replay(
        store: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    host = Host()
    populate(store, host, 6, snapshot_at=4)
    calls = count_appends(monkeypatch)
    with FileJournal.open(store, schema=SCHEMA) as journal:
        make(journal, host, use_snapshots=False).close()
    assert calls[0] == 6
    # Corrupt the anchor: the snapshot is ignored, never trusted.
    (hint,) = (store / "hints" / "owner-snapshot").iterdir()
    hint.chmod(0o600)
    hint.write_bytes(hint.read_bytes().replace(b'"record_hash":"sha256:', b'"record_hash":"sha256:0', 1))
    calls[0] = 0
    with FileJournal.open(store, schema=SCHEMA) as journal:
        manager = make(journal, host)
        assert calls[0] == 6 and manager.read("g").revision == "7"
        manager.close()


def test_snapshot_survives_more_commits_and_restores_release_projection(store: Path) -> None:
    host = Host()
    populate(store, host, 3, snapshot_at=3)
    with FileJournal.open(store, schema=SCHEMA) as journal:
        manager = make(journal, host)
        before = manager.projection(PROJECTION_NAME, graph_id="g")
        manager.commit(manager.preview(request(manager, key="later", name="later"), expires_at=LATER))
        assert manager.records_since_snapshot == 1
        manager.checkpoint()
        assert manager.records_since_snapshot == 0
        manager.close()
    with FileJournal.open(store, schema=SCHEMA) as journal:
        manager = make(journal, host)
        assert manager.projection(PROJECTION_NAME, graph_id="g") == before
        assert manager.read("g").definition.nodes["r"].name == "later"
        manager.close()
    assert len(list((store / "hints" / "owner-snapshot").iterdir())) == 2  # bounded retention
