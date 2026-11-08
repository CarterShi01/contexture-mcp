"""Runner and suffix-reduction oracles; no native-storage qualification here."""

from copy import deepcopy
import sys

import pytest

from contexture.core.graph.errors import GraphError
from scripts.managed_durable_workloads import (
    CountedPort, Host, LATER, LogicJournal, churn_workload, history_workload,
    make_owner, request, seed,
)


def booted():
    journal = LogicJournal()
    port, host = CountedPort(journal), Host()
    owner = make_owner(port, host)
    owner.commit_bootstrap(owner.preview_bootstrap(seed(), expires_at=LATER))
    return journal, port, host, owner


@pytest.mark.skipif(sys.platform != "linux", reason="Linux measurement instrumentation")
def test_history_runner_full_oracle_and_exact_suffix_count():
    import tracemalloc
    tracemalloc.start()
    try:
        result = history_workload(32, 20261107, "logic", None)
    finally:
        tracemalloc.stop()
    assert not result["native_durable_executed"]
    assert result["changing_commits"] == 32 and result["noop_commits"] == 3
    assert result["retry_checks"] == 6
    assert result["counters"] == {"reducer_records": 36, "reducer_events": 36}
    assert result["replay_counters"] == {"reducer_records": 36, "reducer_events": 36}
    assert result["oracle"]["oracle_history_records"] == 36
    assert result["oracle"]["oracle_index_nodes"] == 144


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform != "linux", reason="Linux resource/RSS instrumentation")
async def test_churn_real_resources_retention_cleanup_and_authority_oracles():
    import tracemalloc
    tracemalloc.start()
    try:
        result = await churn_workload(2, 20261107, "logic", None)
    finally:
        tracemalloc.stop()
    assert not result["native_durable_executed"]
    assert result["counters"] == {
        "release_requests": 7, "cache_retry_equalities": 2, "watch_capacity_rejections": 2,
        "changing_commits": 4, "queue_backpressure_rejections": 2,
        "explicit_cancellations": 2, "old_cache_after_publish": 2,
        "old_lease_new_authority": 2, "watch_complete_batches": 4,
        "watch_fetch_equalities": 4, "cache_authority_rejections": 2,
        "watch_authority_rejections": 2, "request_cancellations": 2,
        "resource_acquires": 18, "resource_closes": 18,
    }
    assert result["peak_live"]["resource_handles"] == 8
    assert result["peak_live"]["resource_fds"] == 16
    assert result["final_resources"]["resource_handles"] == 0
    assert result["final_resources"]["watch_sessions"] == 0
    assert result["journal_records"] == 20
    assert result["full_replay_projection_equivalence"]
    assert result["replay_counters"] == {"reducer_records": 20, "reducer_events": 26}


@pytest.mark.parametrize("damage", ["sequence", "chain", "store", "receipt", "event"])
def test_suffix_reducer_rejects_bad_tail_without_mutating_accepted_prefix(damage):
    journal, _, _, owner = booted()
    try:
        accepted_history = owner._histories["g"]
        proposal = owner.preview(request(owner), expires_at=LATER)
        owner.commit(proposal)
        tail = journal.records(after="1")[0]
        # Independently restore exactly the prefix, not a permissive prefix skip.
        owner.close()
        prefix_owner = make_owner(CountedPort(LogicJournal(journal.records()[:1])), Host())
        if damage == "sequence":
            tail["journal_seq"] = "3"
        elif damage == "chain":
            tail["prev_record_hash"] = "sha256:" + "0" * 64
        elif damage == "store":
            tail["store_id"] = "other"
        elif damage == "receipt":
            tail["command_receipt"]["journal_seq"] = "1"
        else:
            tail["events"][0]["resolved_facts"][0]["before"]["name"]["value"] = "wrong"
        with pytest.raises(GraphError):
            prefix_owner._replay([tail], incremental=True)
        assert prefix_owner._histories["g"].head == accepted_history.head
        assert prefix_owner._watermark == "1" and prefix_owner._journal.watermark == "1"
        prefix_owner.close()
    finally:
        owner.close()


@pytest.mark.parametrize("damage", ["different_return", "extra_tail", "missing_tail"])
def test_confirmed_append_mismatch_stops_writes_and_preserves_lkg(damage):
    journal, port, _, owner = booted()
    try:
        before = owner._histories["g"]
        proposal = owner.preview(request(owner), expires_at=LATER)
        original = port.records
        def bad_records(*, after="0"):
            records = original(after=after)
            if after == "1":
                if damage == "missing_tail":
                    return []
                if damage == "extra_tail":
                    return [*records, deepcopy(records[-1])]
                records[0]["actor_ref"] = "other"
            return records
        port.records = bad_records
        with pytest.raises(GraphError, match="Confirmed append"):
            owner.commit(proposal)
        assert owner.writes_stopped
        assert owner._histories["g"] is before and owner._watermark == "1"
        assert owner._receipt(("tenant:actor", "key")) is None  # beyond the owner watermark
        owner.close()
        recovered = make_owner(CountedPort(LogicJournal(journal.records())), Host())
        assert recovered.read("g").revision == "2"
        assert recovered.receipt("g", "key")["status"] == "committed"
        recovered.close()
    finally:
        owner.close()


def test_cold_constructor_still_verifies_every_prefix_record():
    journal, _, _, owner = booted()
    owner.commit(owner.preview(request(owner), expires_at=LATER))
    owner.close()
    records = journal.records()
    records[0]["journal_seq"] = "9"
    with pytest.raises(GraphError, match="Replay sequence"):
        make_owner(CountedPort(LogicJournal(records)), Host())


def test_retained_values_and_full_replay_remain_exact_with_noops():
    journal, port, host, owner = booted()
    previous = owner.history("g")
    try:
        first = owner.preview(request(owner), expires_at=LATER)
        receipt = owner.commit(first)
        noop = owner.preview(request(owner, key="noop"), expires_at=LATER)
        completed = owner.commit(noop)
        assert completed["status"] == "noop"
        assert completed["revision"] == receipt["revision"] == "2"
        assert completed["journal_seq"] == "3" and receipt["journal_seq"] == "2"
        host.denied = True
        with pytest.raises(GraphError):
            owner.commit(noop)
        host.denied = False
        assert owner.commit(noop) == completed
        assert port.watermark == "3" and len(previous.records) == 1
        current = owner.history("g")
        owner.close()
        recovered = make_owner(CountedPort(LogicJournal(journal.records())), host)
        assert recovered.history("g") == current
        assert recovered.receipt("g", "noop") == completed
        assert recovered.work["reducer_records"] == 3
        recovered.close()
    finally:
        owner.close()


@pytest.mark.skipif(sys.platform != "linux", reason="Native runner is Linux-only")
def test_native_runner_refuses_repository_root_before_any_store_write(monkeypatch, capsys):
    from scripts.managed_durable_workloads import ROOT, main
    monkeypatch.setattr(sys, "argv", ["managed_durable_workloads.py", "--profile", "native",
        "--native-root", str(ROOT / "not-a-native-store"), "--output", str(ROOT / "not-a-proof")])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2
    assert "isolated native /tmp root required" in capsys.readouterr().err
    assert not (ROOT / "not-a-native-store").exists()
    assert not (ROOT / "not-a-proof").exists()
