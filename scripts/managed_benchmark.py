"""Reproducible, bounded WP14 core measurements; not a capacity/SLO certificate.

Run with native Linux Python. Each repetition is a fresh subprocess. Timings
include tracemalloc overhead; RSS is process high-water, NOT a stage allocation.
The independent oracle counts its own visits, never inferred core instructions.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from dataclasses import asdict, dataclass
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
import tempfile
import time
import tracemalloc
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from contexture.core.graph.bindings import (  # noqa: E402
    AppDependencyContract, BindingRegistration, RegistryGeneration,
)
from contexture.core.graph.canonical import canonical_json  # noqa: E402
from contexture.core.graph.catalog import metadata_catalog  # noqa: E402
from contexture.core.graph.compiler import GraphCompiler  # noqa: E402
from contexture.core.graph.events import RevisionState  # noqa: E402
from contexture.core.graph.index import GraphIndex  # noqa: E402
from contexture.core.graph.model import GraphDefinition, Portability  # noqa: E402
from contexture.core.graph.policy import (  # noqa: E402
    AdmissionDecision, AdmissionRequest, VerifiedContext,
)
from contexture.core.graph.transactions import Limits, prepare  # noqa: E402

SHAPES = ("balanced", "wide", "deep", "cyclic", "high_indegree", "text_schema")
SLOTS = ("pre_process", "children", "post_process", "skills", "tools")


@dataclass(frozen=True)
class Case:
    nodes: int = 1000
    shape: str = "balanced"
    seed: int = 20261106
    roots: int = 4
    fanout: int = 4
    depth: int = 1024
    text_bytes: int = 256
    schema_fields: int = 32

    def __post_init__(self) -> None:
        if self.shape not in SHAPES or not 2 <= self.nodes <= 100000:
            raise ValueError("Known shape and 2..100000 nodes required")
        if not (1 <= self.roots < self.nodes and 1 <= self.fanout <= 1024
                and 1 <= self.depth <= 100000 and 0 <= self.text_bytes <= 1048576
                and 0 <= self.schema_fields <= 1024):
            raise ValueError("Case parameters outside explicit harness bounds")
        if self.nodes * max(self.text_bytes, 1) > 128 * 1024 * 1024:
            raise ValueError("Generated text exceeds 128 MiB harness input budget")


def generate(case: Case) -> dict[str, Any]:
    """Flat node pool; bounded deep chains form a forest, never nested JSON."""
    rng = random.Random(case.seed)
    nodes: list[dict[str, Any]] = []
    roots: list[dict[str, str]] = []
    root_count = min(case.roots, case.nodes - 1)
    for i in range(case.nodes):
        identity = f"n{i:06d}"
        node: dict[str, Any] = dict(node_id=identity, kind="role", name=identity,
            description="benchmark", instructions="x" * (
                case.text_bytes if case.shape == "text_schema" else 8),
            mutable=True, members={"children": []})
        nodes.append(node)
        if case.shape == "deep":
            parent = None if i % (case.depth + 1) == 0 else i - 1
        elif i < root_count:
            parent = None
        elif case.shape == "wide":
            parent = (i - root_count) % root_count
        else:
            parent = (i - root_count) // case.fanout
        if parent is None:
            roots.append({"node_id": identity, "plane": "model"})
        else:
            nodes[parent]["members"]["children"].append(identity)
    if case.shape == "cyclic":
        for i, node in enumerate(nodes):
            node["uses"] = [nodes[(i + 1) % case.nodes]["node_id"]]
    if case.shape == "high_indegree":
        for node in nodes[1:]:
            node["uses"] = [nodes[0]["node_id"]]
    if case.shape == "text_schema":
        # A real runtime Tool schema, not inert metadata pretending to validate.
        tool = nodes[-1]
        tool.pop("instructions")
        tool.pop("members")
        tool["kind"] = "tool"
        tool["binding"] = {"kind": "registered", "binding_id": "benchmark-tool",
            "version": "1", "portability": {"kind": "portable", "contract_id": "bench-v1"}}
        tool["input_contract"] = {"type": "object", "properties": {
            f"field{i}": {"type": "string", "maxLength": 1024}
            for i in range(case.schema_fields)}, "additionalProperties": False}
        for owner in nodes[:-1]:
            children = owner["members"]["children"]
            if tool["node_id"] in children:
                children.remove(tool["node_id"])
                owner["members"]["tools"] = [tool["node_id"]]
                break
    rng.shuffle(nodes)  # Array/lexical order must not become traversal order.
    return dict(schema="contexture.graph/v1", semantic_version="1", graph_id="wp14",
        application_name="WP14 benchmark", mode="runtime" if case.shape == "text_schema"
        else "disclosure_only", nodes=nodes, roots=roots,
        presentations={"default_locale": "en", "catalogs": []})


def placements(node: dict[str, Any]) -> list[tuple[str, int, str]]:
    result: list[tuple[str, int, str]] = []
    for slot in SLOTS:
        value = node.get("members", {}).get(slot, [])
        values = [value] if slot in ("pre_process", "post_process") and value else value
        result.extend((slot, ordinal, target) for ordinal, target in enumerate(values))
    return result


def oracle(raw: dict[str, Any]) -> dict[str, Any]:
    """Independent iterative raw-wire traversal, no GraphIndex/model helpers.

    Counts are oracle visits/relations, not instrumentation of index internals.
    Full paths are deliberately NOT materialized for every deep-chain node.
    """
    nodes = {n["node_id"]: n for n in raw["nodes"]}
    order: list[str] = []
    locations: dict[str, dict[str, Any]] = {}
    containment: list[dict[str, str]] = []
    uses: list[dict[str, str]] = []
    reverse: dict[str, list[str]] = {n: [] for n in nodes}
    pending: list[tuple[str, str | None, str | None, int, int, int, str, int, bool]] = [
               (r["node_id"], None, None, i, i, i, r["plane"], 0, False)
               for i, r in reversed(list(enumerate(raw["roots"])))]
    while pending:
        identity, parent, slot, ordinal, member, root, plane, depth, exiting = pending.pop()
        if exiting:
            locations[identity]["subtree_end"] = len(order)
            continue
        if identity in locations:
            raise AssertionError("Oracle encountered duplicate/cyclic containment")
        locations[identity] = dict(parent_id=parent, slot=slot, ordinal=ordinal,
            member_ordinal=member, root_ordinal=root, plane=plane, depth=depth,
            preorder=len(order))
        order.append(identity)
        pending.append((identity, parent, slot, ordinal, member, root, plane, depth, True))
        children = placements(nodes[identity])
        for pos, (child_slot, child_ordinal, target) in reversed(list(enumerate(children))):
            pending.append((target, identity, child_slot, child_ordinal, pos, root,
                            plane, depth + 1, False))
    assert len(order) == len(nodes)
    for identity in order:
        for slot, _, target in placements(nodes[identity]):
            containment.append(dict(source_id=identity, slot=slot, target_id=target))
        for target in nodes[identity].get("uses", []):
            uses.append(dict(source_id=identity, target_id=target))
            reverse[target].append(identity)
    return dict(order=order, locations=locations, containment=containment, uses=uses,
                reverse=reverse, counts={"node_visits": len(order),
                    "containment_edges": len(containment), "uses_edges": len(uses)})


def check_index(index: GraphIndex, expected: dict[str, Any]) -> None:
    assert list(index.preorder) == expected["order"]
    for identity, location in expected["locations"].items():
        assert all(getattr(index.locations[identity], k) == v for k, v in location.items())
        assert list(index.reverse_uses[identity]) == expected["reverse"][identity]
    # Exactly compare independent counts with actual indexed records/edges.
    assert len(index.locations) == expected["counts"]["node_visits"]
    assert sum(v.parent_id is not None for v in index.locations.values()) == expected[
        "counts"]["containment_edges"]
    assert sum(map(len, index.reverse_uses.values())) == expected["counts"]["uses_edges"]
    for identity in (index.preorder[0], index.preorder[len(index.preorder) // 2], index.preorder[-1]):
        loc = expected["locations"][identity]
        assert [n.node_id for n in index.subtree(identity)] == expected["order"][
            loc["preorder"]:loc["subtree_end"]]


def check_catalog(raw: dict[str, Any], index: GraphIndex, expected: dict[str, Any],
                  material: Any, visible: frozenset[str]) -> None:
    nodes = {n["node_id"]: n for n in raw["nodes"]}
    assert material.nodes == tuple(canonical_json(dict(node_id=n, kind=nodes[n]["kind"],
        name=nodes[n]["name"], description=nodes[n]["description"],
        projected_root=expected["locations"][n]["parent_id"] not in visible))
        for n in expected["order"] if n in visible)
    for key in ("containment", "uses"):
        assert getattr(material, key) == tuple(canonical_json(e) for e in expected[key]
            if e["source_id"] in visible and e["target_id"] in visible)


def forbidden(*args: Any, **kwargs: Any) -> Any:
    raise AssertionError("Benchmark must never execute business handlers")


def registry_for(raw: dict[str, Any]) -> RegistryGeneration | None:
    if raw["mode"] != "runtime":
        return None
    tool = next(n for n in raw["nodes"] if n["kind"] == "tool")
    return RegistryGeneration(build="wp14", registrations=[BindingRegistration(
        binding_id="benchmark-tool", version="1", kind="registered",
        portability=Portability.from_json({"kind": "portable", "contract_id": "bench-v1"}),
        effect="read_only", abi="call-v1", artifact_digest="sha256:" + "a" * 64,
        input_schema=tool["input_contract"], validator_contract_id="bench-v1",
        decoder_contract_id="bench-v1", default_contract_id="bench-v1",
        coercion_contract_id="bench-v1", handler=forbidden, validator=forbidden,
        decoder=forbidden)])


class AllowBenchmark:
    def decide(self, request: AdmissionRequest) -> AdmissionDecision:
        return AdmissionDecision(True, request.context.authority_revision,
            frozenset((*request.impact, *request.reference_targets)))


def edit_envelope(definition: GraphDefinition, node_id: str,
                  command: dict[str, Any] | None = None) -> dict[str, Any]:
    return dict(schema="contexture.transaction/v1", graph_id=definition.graph_id,
        base_revision="1", idempotency_key="wp14", reason="benchmark only",
        correlation_id="wp14", commands=[command or dict(op="rename_node",
            node_id=node_id, new_name="renamed-root")])


def compile_definition(definition: GraphDefinition, registry: RegistryGeneration | None) -> Any:
    return GraphCompiler.compile(definition, registry=registry,
        compiler_contract_version="wp14-v1", compiler_build="wp14-benchmark",
        app_dependency_contract=AppDependencyContract("wp14-v1", "wp14"),
        environment_compatibility=("native-linux",), locale="en",
        no_execution_registry_digest="sha256:" + "e" * 64 if registry is None else None)


def measure(operation: Callable[[], Any]) -> tuple[Any, dict[str, Any]]:
    import resource

    gc.collect()
    before_gc = gc.get_stats()
    baseline, _ = tracemalloc.get_traced_memory()
    tracemalloc.reset_peak()
    start_cpu, start = time.process_time(), time.perf_counter()
    value = operation()
    elapsed, cpu = time.perf_counter() - start, time.process_time() - start_cpu
    current, peak = tracemalloc.get_traced_memory()
    usage = resource.getrusage(resource.RUSAGE_SELF)
    return value, dict(wall_seconds=elapsed, cpu_seconds=cpu,
        traced_baseline_bytes=baseline, traced_live_delta_bytes=current - baseline,
        traced_peak_increment_bytes=peak - baseline,
        process_peak_rss_bytes=usage.ru_maxrss * 1024,
        gc_collections=[a["collections"] - b["collections"]
                        for a, b in zip(gc.get_stats(), before_gc)],
        minor_faults_cumulative=usage.ru_minflt, major_faults_cumulative=usage.ru_majflt)


def sample(case: Case, progress: Path | None = None) -> dict[str, Any]:
    raw = generate(case)
    payload = json.dumps(raw, ensure_ascii=False, separators=(",", ":")).encode()
    if len(payload) > 256 * 1024 * 1024:
        raise ValueError("Definition exceeds 256 MiB harness budget")
    expected = oracle(raw)
    registry = registry_for(raw)
    tracemalloc.start()
    metrics: dict[str, Any] = {}

    def stage(name: str, operation: Callable[[], Any]) -> Any:
        value, metrics[name] = measure(operation)
        if progress is not None:
            progress.write_text(json.dumps(dict(case=asdict(case),
                input_sha256=hashlib.sha256(payload).hexdigest(), input_bytes=len(payload),
                oracle_counts=expected["counts"], completed_metrics=metrics,
                complete=False)) + "\n")
        return value

    definition = stage("decode_validate", lambda: GraphDefinition.from_json(payload))
    index = stage("index_build", lambda: GraphIndex.build(definition))
    check_index(index, expected)
    digest = stage("definition_digest", definition.digest)
    plan = stage("compile", lambda: compile_definition(definition, registry))
    check_index(plan.index, expected)
    del plan
    visible = frozenset(index.preorder)
    material = stage("catalog", lambda: metadata_catalog(index, visible,
        kinds=frozenset({"role", "skill", "tool"}), max_items=case.nodes * 4,
        max_bytes=256 * 1024 * 1024))
    check_catalog(raw, index, expected, material, visible)
    catalog_bytes = material.size
    del material
    base = RevisionState(definition, "1")
    prepared = stage("transaction_prepare_ancestor_rename", lambda: prepare(
        base, edit_envelope(definition, index.preorder[0]),
        context=VerifiedContext("benchmark", "benchmark", "1", frozenset({"graph.roots"})),
        admission=AllowBenchmark(), limits=Limits(max_depth=max(1024, case.depth))))
    assert prepared.change.candidate.nodes[index.preorder[0]].name == "renamed-root"
    assert definition.nodes[index.preorder[0]].name != "renamed-root"
    edited = json.loads(payload)
    next(n for n in edited["nodes"] if n["node_id"] == index.preorder[0])["name"] = "renamed-root"
    assert prepared.change.candidate.digest() == GraphDefinition.from_json(edited).digest()
    check_index(GraphIndex.build(prepared.change.candidate), oracle(edited))
    snapshot = tracemalloc.take_snapshot()
    live_allocations = sum(s.count for s in snapshot.statistics("filename"))
    tracemalloc.stop()
    return dict(case=asdict(case), input_sha256=hashlib.sha256(payload).hexdigest(),
        definition_digest=digest, input_bytes=len(payload), catalog_bytes=catalog_bytes,
        oracle_counts=expected["counts"], max_depth=max(v["depth"] for v in expected["locations"].values()),
        metrics=metrics, final_traced_live_allocation_blocks=live_allocations,
        oracle_equivalence=True)


def summarize(samples: list[dict[str, Any]]) -> dict[str, Any]:
    if not samples or len({s["input_sha256"] for s in samples}) != 1:
        raise ValueError("Samples must have identical deterministic inputs")
    result: dict[str, Any] = {}
    for stage in samples[0]["metrics"]:
        result[stage] = {}
        for metric in ("wall_seconds", "cpu_seconds", "traced_peak_increment_bytes",
                       "process_peak_rss_bytes"):
            values = [s["metrics"][stage][metric] for s in samples]
            result[stage][metric] = dict(median=statistics.median(values),
                minimum=min(values), maximum=max(values), distribution=values)
    return result


def environment() -> dict[str, Any]:
    def command(args: list[str]) -> str:
        return subprocess.check_output(args, cwd=ROOT, text=True).strip()

    paths = [ROOT / "scripts/managed_benchmark.py", *sorted((ROOT / "contexture/core/graph").glob("*.py"))]
    return dict(python=sys.version, executable=sys.executable, os=platform.platform(),
        machine=platform.machine(), node=command(["node", "--version"]),
        filesystem=command(["findmnt", "-T", str(ROOT), "-n", "-o", "FSTYPE,SOURCE,TARGET"]),
        cpu=platform.processor(), logical_cpus=os.cpu_count(),
        git_head=command(["git", "--no-pager", "rev-parse", "HEAD"]),
        dependencies={n: importlib.metadata.version(n) for n in ("jsonschema", "rfc8785")},
        source_sha256={str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                       for p in paths}, profile="pure-core-no-journal-no-durability",
        memory_method="tracemalloc phase peaks; Linux resource.ru_maxrss cumulative KiB * 1024",
        timing_method="fresh process per repetition; traced CPU/wall; GC collected before each stage",
        count_method="independent raw-wire oracle counts exactly compared with index records; not core instruction counts")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sizes", nargs="+", type=int, default=[1000])
    parser.add_argument("--shapes", nargs="+", choices=SHAPES, default=["balanced"])
    parser.add_argument("--repetitions", type=int, default=5)
    parser.add_argument("--large-repetitions", type=int, default=1)
    parser.add_argument("--seed", type=int, default=20261106)
    parser.add_argument("--depth", type=int, default=1024)
    parser.add_argument("--text-bytes", type=int, default=256)
    parser.add_argument("--schema-fields", type=int, default=32)
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--memory-mib", type=int, default=6144)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--worker", help=argparse.SUPPRESS)
    parser.add_argument("--progress", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if sys.platform != "linux":
        parser.error("Native Linux Python required for resource RSS receipt")
    if args.worker:
        import resource

        if not 256 <= args.memory_mib <= 8192:
            parser.error("Worker address-space budget must be 256..8192 MiB")
        budget = args.memory_mib * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (budget, budget))
        print(json.dumps(sample(Case(**json.loads(args.worker)), args.progress)))
        return
    if not (1 <= args.repetitions <= 20 and 1 <= args.large_repetitions <= 20
            and 1 <= args.timeout <= 3600 and 256 <= args.memory_mib <= 8192):
        parser.error("Repetitions 1..20, timeout 1..3600s, memory 256..8192 MiB required")
    if args.output is None:
        parser.error("--output is required; receipts are never silently discarded")
    cases = [Case(nodes=n, shape=s, seed=args.seed, depth=args.depth,
                  text_bytes=args.text_bytes, schema_fields=args.schema_fields)
             for n in args.sizes for s in args.shapes]
    receipt: dict[str, Any] = dict(schema="wp14.benchmark/v1", environment=environment(),
        worker_limits={"timeout_seconds": args.timeout, "address_space_mib": args.memory_mib},
        cases=[], limitations=["No SLO freeze/capacity promise", "No journal/durability/soak/history",
            "No queue/prepare/swap/lease/checkpoint/watch/cache Host measurements",
            "Oracle visits are not a complexity proof; full validation remains enabled"])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    for case in cases:
        row: dict[str, Any] = dict(case=asdict(case), samples=[], errors=[])
        receipt["cases"].append(row)
        repetitions = args.large_repetitions if case.nodes >= 100000 else args.repetitions
        for repetition in range(repetitions):
            with tempfile.TemporaryDirectory(prefix="wp14-worker-") as temporary:
                progress = Path(temporary) / "progress.json"
                try:
                    proc = subprocess.run([sys.executable, "-B", str(Path(__file__).resolve()),
                        "--memory-mib", str(args.memory_mib), "--progress", str(progress),
                        "--worker", json.dumps(asdict(case))], capture_output=True, text=True,
                        timeout=args.timeout, check=True)
                    row["samples"].append(json.loads(proc.stdout))
                except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
                    partial = json.loads(progress.read_text()) if progress.exists() else None
                    row["errors"].append(dict(repetition=repetition, error=str(exc),
                        stderr=str(getattr(exc, "stderr", ""))[-6000:], partial=partial))
            row["summary"] = summarize(row["samples"]) if row["samples"] else {}
            args.output.write_text(json.dumps(receipt, indent=2) + "\n")
            print(f"{case.nodes} {case.shape} repetition={repetition + 1} "
                  f"ok={len(row['samples'])} errors={len(row['errors'])}", flush=True)
    after = environment()
    receipt["source_unchanged"] = after["source_sha256"] == receipt["environment"]["source_sha256"]
    args.output.write_text(json.dumps(receipt, indent=2) + "\n")
    if any(row["errors"] for row in receipt["cases"]) or not receipt["source_unchanged"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()