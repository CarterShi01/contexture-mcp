"""Bounded WP14 history/resource workloads, not a durability or deployed SLO claim.

Native mode opens strict FileJournal only on isolated native Linux storage. Logic
mode is an explicitly nonpersistent test port, useful for runner/oracle validation
when native storage execution is unavailable; it NEVER qualifies durable history.
Existing benchmark generators and lifecycle fixtures provide the input/adapters.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from contextlib import asynccontextmanager
from dataclasses import asdict, replace
import gc
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import random
import statistics
import subprocess
import sys
import time
import tracemalloc
import traceback
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.managed_benchmark import Case, generate, oracle, check_index  # noqa: E402
from contexture.application.managed import GraphOwner  # noqa: E402
from contexture.application.managed_release_manager import ReleaseManager  # noqa: E402
from contexture.application.managed_runtime import ManagedRuntime  # noqa: E402
from contexture.application.managed_run_leases import RunLeaseService  # noqa: E402
from contexture.application.managed_watch import ManagedWatchService, WatchProfile  # noqa: E402
from contexture.application.managed_releases import PROJECTION_NAME, release_projection  # noqa: E402
from contexture.core.graph.canonical import (  # noqa: E402
    canonical_digest, canonical_json, JOURNAL_PAYLOAD_DOMAIN, JOURNAL_RECORD_DOMAIN,
)
from contexture.core.graph.errors import GraphError  # noqa: E402
from contexture.core.graph.index import GraphIndex  # noqa: E402
from contexture.core.graph.model import GraphDefinition  # noqa: E402
from contexture.core.graph.policy import AdmissionDecision  # noqa: E402
from contexture.server.managed_journal import FileJournal  # noqa: E402
from tests.test_managed_owner import Host, LATER, SCHEMA, request, seed  # noqa: E402
from tests.test_managed_runtime import Authority, Execution, Provider, definition  # noqa: E402
from tests.test_managed_catalog import service as catalog_service, read as catalog_read  # noqa: E402
from tests.test_managed_run_leases import ManualClock  # noqa: E402
from tests.test_managed_serving import uid  # noqa: E402


class LogicJournal:
    """Test-only JournalPort: no files, locks, fsync or native storage claims."""

    # The reducer's closed receipt enum requires a protocol value. This value
    # is simulated here, NOT a claim that this test port survives process crash.
    durability = "process_crash"

    def __init__(self, records=()):
        self.data = json.loads(json.dumps(records))

    @property
    def watermark(self):
        return str(len(self.data))

    def records(self, *, after="0"):
        return json.loads(json.dumps(self.data[int(after):]))

    def get_receipt(self, actor_ref, idempotency_key):
        for record in self.data:
            receipt = record.get("command_receipt")
            if receipt and (receipt["actor_ref"], receipt["idempotency_key"]) == (
                    actor_ref, idempotency_key):
                return json.loads(json.dumps(receipt))
        return None

    def append(self, events, *, actor_ref, command_receipt=None,
               correlation_id=None, event_batch_id=None, written_at=None):
        sequence = len(self.data) + 1
        record = dict(schema="contexture.journal-record/v1", store_id=uid(7000),
            journal_seq=str(sequence), event_batch_id=event_batch_id or uid(sequence),
            prev_record_hash=self.data[-1]["record_hash"] if self.data else None,
            events=json.loads(canonical_json(events)), actor_ref=actor_ref,
            correlation_id=correlation_id, written_at=written_at)
        if command_receipt is not None:
            record["command_receipt"] = json.loads(canonical_json(command_receipt))
        record["payload_digest"] = canonical_digest(
            {k: record[k] for k in ("events", "command_receipt") if k in record},
            JOURNAL_PAYLOAD_DOMAIN)
        record["record_hash"] = canonical_digest(record, JOURNAL_RECORD_DOMAIN)
        self.data.append(record)
        return json.loads(json.dumps(record))

    def close(self):
        pass


class CountedPort:
    def __init__(self, journal):
        self.journal, self.counters = journal, Counter()
        self.durability = journal.durability

    @property
    def watermark(self):
        return self.journal.watermark

    def records(self, *, after="0"):
        result = self.journal.records(after=after)
        self.counters["records_calls"] += 1
        self.counters["records_returned"] += len(result)
        return result

    def append(self, *args, **kwargs):
        self.counters["append_calls"] += 1
        return self.journal.append(*args, **kwargs)

    def get_receipt(self, *args):
        return self.journal.get_receipt(*args)


class CountedOwner(GraphOwner):
    def __init__(self, *args, **kwargs):
        self.work = Counter()
        super().__init__(*args, **kwargs)

    def _replay(self, records, **kwargs):
        self.work["reducer_records"] += len(records)
        self.work["reducer_events"] += sum(len(r["events"]) for r in records)
        return super()._replay(records, **kwargs)


def make_owner(journal, host, *, releases=False):
    return CountedOwner(journal, identities=host, admission=host, authority_fence=host,
        clock=lambda: host.now, approvals=host,
        projections={PROJECTION_NAME: release_projection()} if releases else None)


def emit(event, **fields):
    print(json.dumps(dict(event=event, **fields)), flush=True)


def fingerprints():
    paths = [*sorted((ROOT / "contexture").rglob("*.py")),
        *sorted((ROOT / "scripts").glob("*.py")),
        *sorted((ROOT / "tests").glob("test_managed_*.py")),
        ROOT / "tests" / "test_canonical_integer_profile.py",
        ROOT / "pyproject.toml", ROOT / "uv.lock",
        ROOT / "spec" / "managed" / "schemas" / "managed.schema.json"]
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def environment(profile, storage=None):
    def command(args):
        return subprocess.check_output(args, text=True, cwd=ROOT).strip()
    return dict(python=sys.version, executable=sys.executable, os=platform.platform(),
        release=platform.release(), logical_cpus=os.cpu_count(),
        source_filesystem=command(["findmnt", "-T", str(ROOT), "-n", "-o", "FSTYPE,SOURCE,TARGET"]),
        storage_filesystem=command(["findmnt", "-T", str(storage), "-n", "-o",
            "FSTYPE,SOURCE,TARGET"]) if storage else None,
        profile=profile, durability="strict" if profile == "native" else
            "NONE; process_crash receipt enum is simulated by the test port",
        git_head=command(["git", "--no-pager", "rev-parse", "HEAD"]),
        dirty_status_sha256=hashlib.sha256(command(["git", "--no-pager", "status",
            "--porcelain"]).encode()).hexdigest(),
        dependencies={name: importlib.metadata.version(name) for name in
            ("jsonschema", "jsonschema-rs", "rfc8785", "pytest", "mcp")},
        qualification="NOT EXECUTED: native durability" if profile == "logic" else
            "requested strict profile; qualify only individually successful samples; no deployed SLO")


def stats():
    import resource
    current, peak = tracemalloc.get_traced_memory()
    blocks = sum(s.count for s in tracemalloc.take_snapshot().statistics("filename"))
    return dict(traced_live_bytes=current, traced_peak_bytes=peak,
        traced_live_allocation_blocks=blocks,
        process_peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024,
        open_fds=len(os.listdir("/proc/self/fd")),
        gc_collections=[g["collections"] for g in gc.get_stats()])


def open_store(profile, path):
    return LogicJournal() if profile == "logic" else FileJournal.open(path, schema=SCHEMA)


def history_seed(input_seed):
    case = Case(nodes=4, roots=1, seed=input_seed)
    raw = generate(case)
    raw["graph_id"] = "g"
    return raw


def changes(count, input_seed):
    rng = random.Random(input_seed)
    for i in range(1, count + 1):
        yield i, f"n{rng.randrange(4):06d}", f"v{i:06d}-{rng.getrandbits(32):08x}"


def history_oracle(manager, records, count, input_seed):
    """Raw input edits, not resolve_facts/apply_event/diff, are the oracle."""
    expected = GraphDefinition.from_json(history_seed(input_seed)).as_json()
    nodes = {n["node_id"]: n for n in expected["nodes"]}
    history = manager.history("g")
    manager._admission.calls.clear()
    assert len(records) == len(history.records) == 1 + count + count // 10
    assert manager.export("g", "1") == canonical_json(expected)
    check_index(GraphIndex.build(history.records[0].state.definition), oracle(expected))
    checked, cursor = 1, 1
    previous_hash = None
    for sequence, record in enumerate(records, 1):
        assert record["journal_seq"] == str(sequence)
        assert record["prev_record_hash"] == previous_hash
        assert record["store_id"] == records[0]["store_id"]
        subject = {k: v for k, v in record.items() if k != "record_hash"}
        assert record["record_hash"] == canonical_digest(subject, JOURNAL_RECORD_DOMAIN)
        assert record["payload_digest"] == canonical_digest(
            {k: record[k] for k in ("events", "command_receipt") if k in record},
            JOURNAL_PAYLOAD_DOMAIN)
        previous_hash = record["record_hash"]
    for i, identity, name in changes(count, input_seed):
        before = nodes[identity]["name"]
        nodes[identity]["name"] = name
        event = records[cursor]["events"][0]
        assert event["type"] == "GraphChangeCommitted"
        assert len(event["resolved_facts"]) == 1
        fact = event["resolved_facts"][0]
        assert fact["type"] == "NodeFieldsSet" and fact["node_id"] == identity
        assert fact["before"] == {"name": {"present": True, "value": before}}
        assert fact["after"] == {"name": {"present": True, "value": name}}
        snapshot = history.records[cursor].state
        assert snapshot.revision == str(i + 1)
        assert canonical_json(snapshot.definition.as_json()) == canonical_json(expected)
        check_index(GraphIndex.build(snapshot.definition), oracle(expected))
        receipt = records[cursor]["command_receipt"]
        assert receipt["status"] == "committed" and receipt["revision"] == str(i + 1)
        assert receipt["journal_seq"] == str(cursor + 1)
        assert receipt["actor_ref"] == "tenant:actor"
        assert receipt["durability"] == manager._journal.durability
        assert receipt["operation_result"] == {"inline": dict(graph_id="g",
            revision=str(i + 1), definition_digest=canonical_digest(expected, "contexture.graph/v1"))}
        command = dict(schema="contexture.transaction/v1", graph_id="g", base_revision=str(i),
            commands=[dict(op="rename_node", node_id=identity, new_name=name)], reason="test")
        assert receipt["request_digest"] == canonical_digest(command, "contexture.command/v1")
        assert manager.receipt("g", f"edit-{i}") == receipt
        cursor += 1
        checked += 1
        if i % 10 == 0:
            noop = records[cursor]
            assert noop["events"][0]["type"] == "CommandCompleted"
            assert history.records[cursor].state.revision == str(i + 1)
            assert canonical_json(history.records[cursor].state.definition.as_json()) == canonical_json(expected)
            check_index(GraphIndex.build(history.records[cursor].state.definition), oracle(expected))
            assert noop["command_receipt"]["status"] == "noop"
            assert noop["command_receipt"]["revision"] == str(i + 1)
            assert noop["command_receipt"]["journal_seq"] == str(cursor + 1)
            assert noop["command_receipt"]["operation_result"] == receipt["operation_result"]
            command["base_revision"] = str(i + 1)
            assert noop["command_receipt"]["request_digest"] == canonical_digest(command, "contexture.command/v1")
            assert manager.receipt("g", f"noop-{i}") == noop["command_receipt"]
            cursor += 1
            checked += 1
        if i in (1, count // 2, count):
            assert manager.export("g", str(i + 1)) == canonical_json(expected)
        manager._admission.calls.clear()
    assert manager.export("g") == canonical_json(expected)
    return dict(oracle_history_records=checked, oracle_index_nodes=checked * 4,
        oracle_receipts=count + count // 10, old_revision_queries=4,
        final_export_sha256=hashlib.sha256(canonical_json(expected)).hexdigest())


def history_workload(count, input_seed, profile, path):
    journal = open_store(profile, path)
    port, host = CountedPort(journal), Host()
    manager = make_owner(port, host)
    raw = history_seed(input_seed)
    digest = hashlib.sha256(canonical_json(dict(seed=raw,
        edits=list(changes(count, input_seed)), noop_every=10))).hexdigest()
    start, cpu = time.perf_counter(), time.process_time()
    envelope = seed()
    envelope["seed"]["inline"] = raw
    manager.commit_bootstrap(manager.preview_bootstrap(envelope, expires_at=LATER))
    retry_checks = 0
    for i, identity, name in changes(count, input_seed):
        proposal = manager.preview(request(manager, key=f"edit-{i}", commands=[
            dict(op="rename_node", node_id=identity, new_name=name)]), expires_at=LATER)
        receipt = manager.commit(proposal)
        assert receipt["status"] == "committed" and receipt["revision"] == str(i + 1)
        if i % 10 == 0:
            noop = manager.preview(request(manager, key=f"noop-{i}", commands=[
                dict(op="rename_node", node_id=identity, new_name=name)]), expires_at=LATER)
            noop_receipt = manager.commit(noop)
            watermark = port.watermark
            assert manager.commit(noop) == noop_receipt and port.watermark == watermark
            assert manager.commit(proposal) == receipt and port.watermark == watermark
            retry_checks += 2
        # Fixture audit arrays are not a resource cache; bound them explicitly.
        host.calls.clear()
        if i in (1, 10, count) or i % 1000 == 0:
            emit("history_progress", changing_commits=i, counters=dict(manager.work),
                 port_counters=dict(port.counters), resources=stats())
    write_metrics = dict(wall_seconds=time.perf_counter() - start,
                         cpu_seconds=time.process_time() - cpu)
    write_work, write_port = dict(manager.work), dict(port.counters)
    records = port.records()
    checks = history_oracle(manager, records, count, input_seed)
    manager.close()
    journal.close()
    del manager
    gc.collect()
    host.calls.clear()
    start, cpu = time.perf_counter(), time.process_time()
    # Logic clone is deliberately NOT a disk cold reopen. Native uses real
    # close/open plus full storage verification and owner semantic replay.
    reopened = LogicJournal(records) if profile == "logic" else FileJournal.open(path, schema=SCHEMA)
    recovered_port = CountedPort(reopened)
    host.denied = True
    recovered = make_owner(recovered_port, host)
    replay_metrics = dict(wall_seconds=time.perf_counter() - start,
                          cpu_seconds=time.process_time() - cpu)
    assert not host.calls  # full replay never authorizes/executes
    host.denied = False
    assert history_oracle(recovered, recovered_port.records(), count, input_seed) == checks
    replay_work = dict(recovered.work)
    recovered.close()
    reopened.close()
    files = list(path.rglob("*")) if profile == "native" else []
    return dict(workload="history", changing_commits=count, noop_commits=count // 10,
        input_sha256=digest, retry_checks=retry_checks, counters=write_work,
        port_counters=write_port, replay_counters=replay_work, oracle=checks,
        metrics={"writes": write_metrics, "close_reopen_full_replay": replay_metrics},
        storage=dict(files=sum(p.is_file() for p in files),
            bytes=sum(p.stat().st_size for p in files if p.is_file())),
        resources=stats(), native_durable_executed=profile == "native",
        reopen_kind="disk-close-open-full-replay" if profile == "native" else
            "NONPERSISTENT TEST CLONE; not cold storage",
        oracle_equivalence=True)


class ResourceExecution(Execution):
    def __init__(self):
        super().__init__()
        self.live = {}
        self.counts = Counter()
        self.peak = 0
        self.block_next = False
        self.entered, self.proceed = asyncio.Event(), asyncio.Event()

    def prepare(self, *, binding, node_id, context):
        return self.resource(binding, node_id, context)

    @asynccontextmanager
    async def resource(self, binding, node, context):
        key = (context.generation_uuid, node)
        assert key not in self.live
        pair = os.pipe()
        self.live[key] = pair
        self.counts["resource_acquires"] += 1
        self.peak = max(self.peak, len(self.live))
        try:
            if self.block_next and node == "p":
                self.block_next = False
                self.entered.set()
                await self.proceed.wait()
            yield pair
        finally:
            for fd in self.live.pop(key):
                os.close(fd)
            self.counts["resource_closes"] += 1


async def settle(predicate):
    async with asyncio.timeout(20):
        while not predicate():
            await asyncio.sleep(0.001)


def expect_error(code, call):
    try:
        call()
    except GraphError as error:
        assert error.code == code
    else:
        raise AssertionError(f"Expected {code}")


async def churn_workload(cycles, input_seed, profile, path):
    fd_baseline = len(os.listdir("/proc/self/fd"))
    journal = open_store(profile, path)
    port, host, execution = CountedPort(journal), Host(), ResourceExecution()
    original = host.decide
    def decide(req):
        if req.operation == "graph.watch":
            assert host.fences > 0
            return AdmissionDecision(not host.denied, host.context.authority_revision,
                                     frozenset({"r", "w", "p", "reserved"}))
        return original(req)
    host.decide = decide
    manager = make_owner(port, host, releases=True)
    provider = Provider(execution)
    envelope = seed()
    envelope["seed"]["inline"] = definition(provider).as_json()
    manager.commit_bootstrap(manager.preview_bootstrap(envelope, expires_at=LATER))
    releases = ReleaseManager(manager, graph_id="g", target_id="t",
        instance_id="wp14", boot_id=uid(900), provider=provider, max_retirements=2)
    authority = Authority(host)
    runtime = ManagedRuntime(releases.serving, contexts=host, authority=authority,
                             adapters=provider.execution_adapters())
    clock = ManualClock()
    runs = RunLeaseService(runtime, max_runs=2, clock=clock)
    catalog = catalog_service((manager, host), runtime, clock=clock,
                              max_snapshots=2, max_cursors=32, max_bytes=8192)
    watch = ManagedWatchService(manager, key=b"wp14-watch-key-" + b"x" * 32,
        clock=clock, max_sessions=2, max_handles=16, max_retained_bytes=4096)
    wp = WatchProfile(max_batches=1, inline_bytes=1, max_bytes=4096)
    counts, peaks, epoch = Counter(), Counter(), 0
    start, cpu = time.perf_counter(), time.process_time()

    def publish(policy="exact"):
        nonlocal epoch
        snapshot = manager.read("g")
        plan = provider.compile(snapshot)
        receipt = releases.request(dict(schema="contexture.release-request/v1",
            graph_id="g", target_id="t", revision=snapshot.revision,
            definition_digest=snapshot.definition_digest,
            manifest_digest=plan.manifest.manifest_digest, expected_desired_epoch=str(epoch),
            idempotency_key=f"publish-{epoch}", policy=policy,
            reason="WP14 fixed churn", approval_ref="approved"))
        epoch += 1
        counts["release_requests"] += 1
        return receipt

    def sample_live(label):
        values = dict(resource_handles=len(execution.live),
            resource_fds=2 * len(execution.live), open_fds=len(os.listdir("/proc/self/fd")),
            runs=runs.counts["runs"], catalog_snapshots=catalog.counts["snapshots"],
            catalog_bytes=catalog.counts["bytes"], catalog_cursors=catalog.counts["cursors"],
            watch_sessions=len(watch._sessions),
            watch_handles=sum(len(s.positions) + len(s.fetches) for s in watch._sessions.values()),
            watch_bytes=sum(s.size for s in watch._sessions.values()),
            running=int(releases.status().running_epoch is not None),
            pending=int(releases.status().pending_epoch is not None),
            **releases.serving.counts)
        bounds = dict(resource_handles=8, resource_fds=16, runs=2,
            catalog_snapshots=2, catalog_bytes=8192, catalog_cursors=32,
            watch_sessions=2, watch_handles=16, watch_bytes=4096, running=1,
            pending=1, preparing=1, retiring=2, active=1, cleanup_errors=0)
        assert all(values[k] <= bound for k, bound in bounds.items()), (values, bounds)
        for k, v in values.items():
            peaks[k] = max(peaks[k], v)
        emit("churn_live", label=label, values=values, bounds=bounds, resources=stats())

    child_task = None
    try:
        assert (await releases.wait(publish(), timeout=20)).status == "observed"
        for cycle in range(cycles):
            run = await runs.create(ttl=5, selection=frozenset({"r"}))
            cached = await catalog.start("g", source="serving", ttl=5)
            old_nodes = await catalog_read(catalog, cached)
            assert await catalog_read(catalog, cached) == old_nodes
            counts["cache_retry_equalities"] += 1
            first = watch.start("g", profile=wp, ttl=5)
            watch.start("g", profile=wp, ttl=5)
            expect_error("resync_required", lambda: watch.start("g", profile=wp))
            counts["watch_capacity_rejections"] += 1
            entered, stop = asyncio.Event(), asyncio.Event()
            old_generation = runs._validate(run).lease.generation
            async def child():
                async with runtime.request(run_context=run) as req:
                    assert (await req.projection_snapshot()).generation_uuid == old_generation.context.generation_uuid
                    entered.set()
                    await stop.wait()
            child_task = asyncio.create_task(child())
            await asyncio.wait_for(entered.wait(), 20)
            execution.block_next = True
            execution.entered.clear()
            for edit in range(2):
                manager.commit(manager.preview(request(manager,
                    key=f"churn-{cycle}-{edit}", commands=[dict(op="rename_node", node_id="r",
                    new_name=f"seed-{input_seed}-cycle-{cycle}-edit-{edit}")]), expires_at=LATER))
                counts["changing_commits"] += 1
                if edit == 0:
                    running = publish("latest")
                    await asyncio.wait_for(execution.entered.wait(), 20)
            publish("latest")
            final = publish("exact")
            before = port.watermark
            expect_error("resource_busy", lambda: publish("exact"))
            assert port.watermark == before
            counts["queue_backpressure_rejections"] += 1
            sample_live("blocked-with-exact-pending")
            assert releases.cancel(str(epoch - 2))
            counts["explicit_cancellations"] += 1
            assert (await releases.wait(final, timeout=20)).status == "observed"
            assert (await releases.wait(running, timeout=20)).status == "superseded"
            await settle(lambda: old_generation.resource_state == "draining")
            assert len(execution.live) == 8
            assert await catalog_read(catalog, cached) == old_nodes
            counts["old_cache_after_publish"] += 1
            async with runtime.request(run_context=run) as req:
                snapshot = await req.projection_snapshot()
                assert snapshot.generation_uuid == old_generation.context.generation_uuid
                assert authority.calls[-1].current_index.definition.nodes["r"].name.endswith("edit-1")
            counts["old_lease_new_authority"] += 1
            cursor = first["cursor"]
            for edit in range(2):
                result = watch.poll(cursor, graph_id="g", profile=wp)
                assert watch.poll(cursor, graph_id="g", profile=wp) == result
                assert len(result["batches"]) == 1
                batch = result["batches"][0]
                payload = watch.fetch(batch["authorized_fetch_ref"], graph_id="g", profile=wp)
                material = payload
                assert next(n for n in material["nodes"] if n["node_id"] == "r")["name"] == (
                    f"seed-{input_seed}-cycle-{cycle}-edit-{edit}")
                assert batch["facts_digest"] == canonical_digest(material, "contexture.view/v1")
                cursor = result["cursor"]
                counts["watch_complete_batches"] += 1
                counts["watch_fetch_equalities"] += 1
            assert watch.poll(cursor, graph_id="g", profile=wp)["batches"] == []
            sample_live("published-old-leases-and-fetch-cache")
            authority.scope = frozenset({"w"})
            try:
                await catalog_read(catalog, cached)
            except GraphError as error:
                assert error.code == "resync_required"
            else:
                raise AssertionError("Old cached scope survived revocation")
            counts["cache_authority_rejections"] += 1
            authority.scope = frozenset({"r", "w", "p", "reserved"})
            host.context = replace(host.context, authority_revision=str(cycle + 2))
            expect_error("resync_required", lambda: watch.poll(cursor, graph_id="g", profile=wp))
            counts["watch_authority_rejections"] += 1
            clock.now += 6
            assert runs.sweep() == 1
            catalog.sweep()
            watch.sweep()
            assert old_generation.resource_state == "draining"  # child still entered
            assert len(execution.live) == 8
            child_task.cancel()
            try:
                await child_task
            except asyncio.CancelledError:
                pass
            counts["request_cancellations"] += 1
            await settle(lambda: old_generation.resource_state == "retired")
            assert len(execution.live) == 4
            sample_live("retired-and-swept")
            assert runs.counts["runs"] == catalog.counts["snapshots"] == len(watch._sessions) == 0
            host.calls.clear()
            authority.calls.clear()
        assert execution.calls == []  # compile/cache/watch/leases never run business handlers
    finally:
        if sys.exc_info()[0] is not None:
            traceback.print_exc()
        if child_task is not None and not child_task.done():
            child_task.cancel()
            await asyncio.gather(child_task, return_exceptions=True)
        execution.proceed.set()
        runs.shutdown()
        catalog.shutdown()
        watch.shutdown()
        await releases.shutdown()
        records = port.records()
        manager.close()
        journal.close()
    assert not execution.live
    assert execution.counts["resource_acquires"] == execution.counts["resource_closes"]
    assert len(os.listdir("/proc/self/fd")) == fd_baseline
    assert releases.serving.counts == dict(preparing=0, retiring=0, cleanup_errors=0, active=0)
    counts.update(execution.counts)
    churn_metrics = dict(wall_seconds=time.perf_counter() - start,
                         cpu_seconds=time.process_time() - cpu)
    event_counts = Counter(e["type"] for record in records for e in record["events"])
    assert event_counts == {
        "GraphChangeCommitted": 1 + 2 * cycles,
        "ReleaseRequested": 1 + 3 * cycles,
        "ReleaseSuperseded": 3 * cycles,
        "ReleasePreparationSucceeded": 1 + cycles,
        "ReleasePreparationFailed": cycles,
        "ReplicaObserved": 1 + cycles,
    }
    expected_projections = manager._external.copy()
    expected_receipts = manager._receipts.copy()
    reopened = LogicJournal(records) if profile == "logic" else FileJournal.open(path, schema=SCHEMA)
    replay_port = CountedPort(reopened)
    host.denied = True
    host.calls.clear()
    start, cpu = time.perf_counter(), time.process_time()
    recovered = make_owner(replay_port, host, releases=True)
    replay_metrics = dict(wall_seconds=time.perf_counter() - start,
                          cpu_seconds=time.process_time() - cpu)
    assert not host.calls
    assert recovered._external == expected_projections and recovered._receipts == expected_receipts
    assert recovered._histories["g"] == manager._histories["g"]
    replay_counts = dict(recovered.work)
    recovered.close()
    reopened.close()
    return dict(workload="churn", cycles=cycles, counters=dict(counts),
        journal_records=len(records), journal_event_counts=dict(event_counts),
        replay_counters=replay_counts, full_replay_projection_equivalence=True,
        reducer_counters=dict(manager.work), port_counters=dict(port.counters),
        retained_identity_metadata=dict(attempt_ids=len(releases.serving._attempts),
            generation_ids=len(releases.serving._generations),
            graph_history_records=len(manager._histories["g"].records),
            receipts=len(manager._receipts)),
        peak_live=dict(peaks), final_resources=dict(resource_handles=0, resource_fds=0,
            open_fds=len(os.listdir("/proc/self/fd")), runs=runs.counts, catalog=catalog.counts,
            watch_sessions=len(watch._sessions), serving=releases.serving.counts),
        input_sha256=hashlib.sha256(canonical_json(dict(seed=input_seed, cycles=cycles,
            template=envelope, scenario="cancel-latest/exact-old-run-cache-watch-v1"))).hexdigest(),
        metrics={"churn": churn_metrics, "churn_full_replay": replay_metrics},
        resources=stats(), native_durable_executed=profile == "native", oracle_equivalence=True)


def main():
    import resource
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=["native", "logic"], default="native")
    parser.add_argument("--sizes", type=int, nargs="+", default=[32, 128])
    parser.add_argument("--cycles", type=int, default=3)
    parser.add_argument("--repetitions", type=int, default=5)
    parser.add_argument("--seed", type=int, default=20261107)
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--memory-mib", type=int, default=2048)
    parser.add_argument("--native-root", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--worker", choices=["history", "churn", "replay"], help=argparse.SUPPRESS)
    parser.add_argument("--count", type=int, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if sys.platform != "linux":
        parser.error("Linux Python required; no storage profile fallback")
    if not (1 <= args.repetitions <= 5 and 1 <= args.cycles <= 1000
            and 1 <= args.timeout <= 3600 and 256 <= args.memory_mib <= 8192
            and all(10 <= n <= 100000 for n in args.sizes)):
        parser.error("Bounded sizes 10..100000, cycles 1..1000, repetitions 1..5 required")
    if args.profile == "native":
        if (args.native_root is None or not str(args.native_root).startswith("/tmp/")
                or args.native_root.resolve().is_relative_to(ROOT)):
            parser.error("Explicit isolated native /tmp root required; no repository/9p Journal")
        if args.worker in ("history", "churn") and args.native_root.exists():
            parser.error("Native sample must create a fresh isolated store, never reuse historical data")
        if args.worker == "replay" and not args.native_root.exists():
            parser.error("Fresh-process replay requires the already written store")
        # The adapter independently enforces native FS, permissions and strict
        # durability; this is not a switch to disable any platform guard.
        if not args.native_root.exists():
            args.native_root.mkdir(mode=0o700, parents=False)
    elif args.native_root is not None:
        parser.error("Logic validation must not open native storage")
    before = fingerprints()
    if args.worker:
        tracemalloc.start()
        if args.worker == "history":
            result = history_workload(args.count, args.seed, args.profile, args.native_root)
        elif args.worker == "churn":
            result = asyncio.run(churn_workload(args.cycles, args.seed, args.profile, args.native_root))
        else:
            if args.profile != "native":
                parser.error("Fresh disk replay worker is native-only")
            host = Host()
            host.denied = True
            started, cpu = time.perf_counter(), time.process_time()
            journal = FileJournal.open(args.native_root, schema=SCHEMA)
            port = CountedPort(journal)
            manager = make_owner(port, host)
            metrics = dict(wall_seconds=time.perf_counter() - started,
                           cpu_seconds=time.process_time() - cpu)
            assert not host.calls
            host.denied = False
            checks = history_oracle(manager, port.records(), args.count, args.seed)
            result = dict(workload="fresh_process_full_replay", oracle=checks,
                counters=dict(manager.work), port_counters=dict(port.counters),
                metrics={"fresh_process_full_replay": metrics}, resources=stats(),
                native_durable_executed=True, oracle_equivalence=True)
            manager.close()
            journal.close()
        result["source_sha256_before"], result["source_sha256_after"] = before, fingerprints()
        result["receipt_durability_is_simulated"] = args.profile == "logic"
        assert result["source_sha256_before"] == result["source_sha256_after"]
        args.output.write_text(json.dumps(result, indent=2) + "\n")
        return
    if args.output.exists():
        parser.error("Output already exists; historical proof is never overwritten")
    args.output.mkdir(parents=True)
    env = environment(args.profile, args.native_root)
    receipt = dict(schema="wp14.durable-workloads/v1", environment=env,
        parameters=vars(args) | {"native_root": str(args.native_root), "output": str(args.output)},
        source_sha256_before=before, samples=[], errors=[],
        limitations=["Shared Dev Box, not deployed SLO", "Logic samples NEVER certify native durability",
            "Full owner/storage replay retained on reopen; no checkpoint shortcut",
            "Immutable History tuple extension still has linear pointer-copy cost",
            "Watch/cache metadata are native internal services, not accepted portable Host wire"])
    jobs = [("history", n) for n in args.sizes] + [("churn", args.cycles)]
    for workload, count in jobs:
        for repetition in range(args.repetitions):
            tag = f"{workload}-{count}-rep-{repetition + 1}"
            output = args.output / f"{tag}.json"
            command = [sys.executable, "-B", str(Path(__file__).resolve()), "--profile", args.profile,
                "--sizes", str(count if workload == "history" else 10), "--cycles", str(args.cycles),
                "--count", str(count), "--seed", str(args.seed), "--worker", workload,
                "--memory-mib", str(args.memory_mib), "--output", str(output)]
            if args.profile == "native":
                command += ["--native-root", str(args.native_root / tag)]
            def limit():
                budget = args.memory_mib * 1024 * 1024
                resource.setrlimit(resource.RLIMIT_AS, (budget, budget))
            with (args.output / f"{tag}.stdout.log").open("w") as stdout, (
                    args.output / f"{tag}.stderr.log").open("w") as stderr:
                try:
                    worker_started = time.perf_counter()
                    subprocess.run(command, stdout=stdout, stderr=stderr, cwd=ROOT,
                        timeout=args.timeout, check=True, preexec_fn=limit)
                    result = json.loads(output.read_text())
                    result["worker_wall_seconds"] = time.perf_counter() - worker_started
                    assert result["source_sha256_before"] == before
                    if args.profile == "native" and workload == "history":
                        replay_command = list(command)
                        replay_command[replay_command.index("--worker") + 1] = "replay"
                        replay_output = args.output / f"{tag}.replay.json"
                        replay_command[replay_command.index("--output") + 1] = str(replay_output)
                        with (args.output / f"{tag}.replay.stdout.log").open("w") as replay_stdout, (
                                args.output / f"{tag}.replay.stderr.log").open("w") as replay_stderr:
                            subprocess.run(replay_command, stdout=replay_stdout, stderr=replay_stderr,
                                cwd=ROOT, timeout=args.timeout, check=True, preexec_fn=limit)
                        replay = json.loads(replay_output.read_text())
                        assert replay["source_sha256_before"] == before
                        assert replay["oracle"] == result["oracle"]
                        result["fresh_process_replay"] = dict(command=replay_command, result=replay)
                    receipt["samples"].append(dict(tag=tag, command=command, result=result))
                except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
                    receipt["errors"].append(dict(tag=tag, command=command, error=str(error),
                        stdout=f"{tag}.stdout.log", stderr=f"{tag}.stderr.log",
                        raw_log_sha256={p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                            for p in sorted(args.output.glob(f"{tag}.*.log"))}))
            if receipt["samples"] and receipt["samples"][-1]["tag"] == tag:
                receipt["samples"][-1]["raw_log_sha256"] = {
                    p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in sorted(args.output.glob(f"{tag}.*.log"))}
            receipt["source_sha256_after"] = fingerprints()
            receipt["sources_unchanged"] = receipt["source_sha256_after"] == before
            (args.output / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
            emit("sample_finished", tag=tag, errors=len(receipt["errors"]))
            # One bounded failure is evidence, not permission for a long stalled matrix.
            if receipt["errors"] or not receipt["sources_unchanged"]:
                raise SystemExit(1)
    summary = {}
    for workload, count in jobs:
        rows = [s["result"] for s in receipt["samples"] if s["tag"].startswith(f"{workload}-{count}-")]
        summary[f"{workload}-{count}"] = {
            phase: {metric: dict(distribution=[r["metrics"][phase][metric] for r in rows],
                median=statistics.median(r["metrics"][phase][metric] for r in rows))
                for metric in ("wall_seconds", "cpu_seconds")}
            for phase in rows[0]["metrics"]}
    receipt["summary"] = summary
    largest = max(args.sizes)
    rows = [s["result"] for s in receipt["samples"]
            if s["tag"].startswith(f"history-{largest}-")]
    elapsed = statistics.median(r["worker_wall_seconds"] for r in rows)
    receipt["history_forecast"] = dict(
        measured_changing_commits=largest, measured_worker_wall_median=elapsed,
        measured_reducer_records=[r["counters"]["reducer_records"] for r in rows],
        measured_reducer_events=[r["counters"]["reducer_events"] for r in rows],
        qualification="diagnostic extrapolation, NOT measured large/native capacity; "
            "remaining tuple/map copies and prefix validation can make linear extrapolation optimistic",
        projections={str(n): dict(linear_seconds=elapsed * n / largest,
            quadratic_seconds=elapsed * (n / largest) ** 2,
            fits_worker_cap_under_linear_assumption=elapsed * n / largest < args.timeout)
            for n in (10000, 100000)})
    (args.output / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")


if __name__ == "__main__":
    main()
