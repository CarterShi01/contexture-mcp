"""WP01 candidate asset checks, not production implementation or parity evidence."""

from copy import deepcopy
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re

import pytest
import rfc8785
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource


ROOT = Path(__file__).resolve().parents[1]
MANAGED = ROOT / "spec" / "managed"
SCHEMA = json.loads((MANAGED / "schemas/managed.schema.json").read_text())
VECTORS = json.loads((MANAGED / "referencevectors/contracts.json").read_text())
REGISTRY = Registry().with_resource(SCHEMA["$id"], Resource.from_contents(SCHEMA))
D = "sha256:" + "0" * 64
UUID = "00000000-0000-4000-8000-000000000001"
REF = f"ctxg:v1:Zw:{UUID}:cg"


def validator(name):
    return Draft202012Validator(
        {"$ref": SCHEMA["$id"] + "#/$defs/" + name},
        registry=REGISTRY, format_checker=FormatChecker(),
    )


def valid(name, value):
    validator(name).validate(value)


def invalid(name, value):
    assert not validator(name).is_valid(value), (name, value)


def node(kind="role"):
    result = {"node_id": "r", "kind": kind, "name": "root", "description": "Root"}
    if kind != "tool":
        result["instructions"] = "Follow the contract."
    return result


def graph():
    return {"schema": "contexture.graph/v1", "semantic_version": "1",
            "graph_id": "g", "application_name": "example", "mode": "runtime",
            "nodes": [node()], "roots": [{"node_id": "r", "plane": "model"}],
            "presentations": {"default_locale": "en", "catalogs": []}}


def transaction():
    return {"schema": "contexture.transaction/v1", "graph_id": "g",
            "base_revision": "1", "idempotency_key": "k", "reason": "Edit",
            "correlation_id": "c", "commands": [
                {"op": "rename_node", "node_id": "r", "new_name": "renamed"}]}


def noop():
    return {"type": "CommandCompleted", "graph_id": "g", "revision": "1",
            "command_digest": D, "status": "noop"}


def receipt():
    return {"schema": "contexture.command-receipt/v1", "graph_id": "g",
            "actor_ref": "actor", "idempotency_key": "k", "request_digest": D,
            "revision": "1", "journal_seq": "1", "status": "noop",
            "durability": "strict", "operation_result": {"inline": {"noop": True}}}


def record():
    return {"schema": "contexture.journal-record/v1", "store_id": "s",
            "journal_seq": "1", "event_batch_id": "b", "prev_record_hash": None,
            "events": [noop()], "written_at": "2026-11-05T00:00:00Z",
            "actor_ref": "actor", "correlation_id": "c", "payload_digest": D,
            "record_hash": D, "command_receipt": receipt()}


def view():
    return {"view": "serving", "detail": "route", "locale": "en",
            "selection_digest": D, "authority_view_revision": "policy-1"}


def page():
    return {"schema": "contexture.relation-page/v1", "items": [], "complete": True,
            "view_descriptor": view(), "snapshot_identity": {
                "snapshot_id": "snap", "graph_id": "g", "revision": "1"},
            "page_digest": D}


def test_schema_self_validation_and_offline_refs():
    Draft202012Validator.check_schema(SCHEMA)

    def walk(value):
        if isinstance(value, dict):
            if "$ref" in value:
                assert value["$ref"].startswith("#/$defs/")
                assert value["$ref"].split("/")[-1] in SCHEMA["$defs"]
            if value.get("type") == "object":
                assert "additionalProperties" in value, value
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
    walk(SCHEMA)
    assert len(SCHEMA["$defs"]) >= 75


@pytest.mark.parametrize("value", ["0", "1", "99999999999999999999"])
def test_counters(value):
    valid("Counter", value)


@pytest.mark.parametrize("value", [0, 1, True, None, "", "00", "01", "-0", "+1",
                                       "1.0", "1e2", "1\n", "١", "１",
                                       "100000000000000000000"])
def test_counter_rejections(value):
    invalid("Counter", value)


@pytest.mark.parametrize("name,value", [
    ("GraphDefinition", graph()), ("NodeSpec", node()),
    ("NodeSpec", node("skill")), ("NodeSpec", node("tool")),
    ("TransactionEnvelope", transaction()), ("CommandReceipt", receipt()),
    ("CommitRecord", record()), ("Event", noop()), ("RelationPage", page()),
])
def test_examples_and_closed_unknown_fields(name, value):
    valid(name, value)
    invalid(name, dict(value, unexpected="refuse"))
    invalid(name, dict(value, actor={"admin": True}))


def test_graph_modes_and_instruction_union():
    for kind in ("role", "skill"):
        n = node(kind)
        n["instruction_template"] = {"parts": [{"kind": "NodeRef", "target_id": "r"}]}
        invalid("NodeSpec", n)
        del n["instructions"]
        valid("NodeSpec", n)
        n["instruction_template"]["parts"][0]["ref"] = REF
        invalid("NodeSpec", n)
    n = node("skill")
    n["members"] = {}
    invalid("NodeSpec", n)
    g = graph()
    g["nodes"] = [node("tool")]
    invalid("GraphDefinition", g)
    g["mode"] = "disclosure_only"
    valid("GraphDefinition", g)
    g["nodes"][0]["input_contract"] = {}
    invalid("GraphDefinition", g)
    g = graph()
    g["roots"][0]["plane"] = "prompt"
    invalid("GraphDefinition", g)
    g["nodes"] = []
    g["roots"] = []
    invalid("GraphDefinition", g)


def test_defaults_absent_null_and_metadata_boundaries():
    n = node()
    before = deepcopy(n)
    valid("NodeSpec", n)
    assert n == before  # JSON Schema annotations never perform normalization.
    for field in ("mutable", "uses", "extensions", "members", "instructions"):
        invalid("NodeSpec", dict(n, **{field: None}))
    for mutability in (True, False, "inherit"):
        valid("NodeSpec", dict(n, mutable=mutability))
    for mutability in ("true", "false", 1, 0):
        invalid("NodeSpec", dict(n, mutable=mutability))
    valid("NodeSpec", dict(n, extensions={"example:metadata": None}))
    invalid("NodeSpec", dict(n, extensions={"not_namespaced": 1}))
    invalid("Members", {"pre_process": None})
    invalid("Members", {"pre_process": ["r"]})
    valid("OptionalValue", {"present": True, "value": None})
    valid("OptionalValue", {"present": False})
    invalid("OptionalValue", {"present": False, "value": None})


def test_command_protection_and_no_relationship_create_bypass():
    valid("Command", {"op": "create_node", "node": node()})
    for field, value in (("uses", ["other"]), ("members", {"children": ["other"]}),
                         ("members", {"pre_process": "other"})):
        invalid("Command", {"op": "create_node", "node": dict(node(), **{field: value})})
    for field in ("name", "kind", "node_id", "mutable", "members", "uses", "roots"):
        invalid("Command", {"op": "update_node", "node_id": "r",
                            "set_fields": {field: "illegal"}, "unset_fields": []})
    invalid("Command", {"op": "delete_nodes", "targets": ["r"],
                        "policy": "automatic_cascade"})
    invalid("Placement", {"parent_id": "r", "slot": "children", "position": -1})
    invalid("Placement", {"plane": "model", "position": 0, "parent_id": "r"})
    t = transaction()
    t["commands"] = [{"op": "CreationPolicyMigrated", "node_id": "r"}]
    invalid("TransactionEnvelope", t)


@pytest.mark.parametrize("vector", VECTORS["hash_vectors"], ids=lambda v: v["name"])
def test_exact_canonical_hash_vectors(vector):
    raw = vector["canonical_utf8"].encode("utf-8")
    value = json.loads(raw)
    valid(vector["definition"], value)
    assert rfc8785.dumps(value) == raw
    assert "sha256:" + hashlib.sha256(vector["domain"].encode() + b"\0" + raw).hexdigest() == vector["digest"]
    assert hashlib.sha256(raw).hexdigest() != vector["digest"][7:]


def test_exact_payload_and_record_hash_scopes():
    r = record()
    payload = {key: r[key] for key in ("events", "command_receipt") if key in r}
    valid("RecordPayload", payload)
    for field in ("schema", "actor_ref", "written_at", "record_hash", "payload_digest"):
        invalid("RecordPayload", dict(payload, **{field: r[field]}))
    invalid("RecordPayload", dict(payload, command_receipt=None))
    valid("RecordPayload", {"events": [noop()]})
    invalid("CommitRecord", dict(r, journal_seq="2"))
    valid("CommitRecord", dict(r, journal_seq="2", prev_record_hash=D))
    invalid("CommitRecord", dict(r, journal_seq="0"))
    invalid("Event", {"type": "UnknownFutureEvent"})
    invalid("Fact", {"type": "NodeFieldsSet", "node_id": "r", "before": {},
                     "after": {"mutable": {"present": True, "value": True}}})


def test_command_subject_excludes_context_and_preview_expiry():
    t = transaction()
    subject = {k: t[k] for k in ("schema", "graph_id", "base_revision", "commands", "reason")}
    valid("CommandSubject", subject)
    for field in ("idempotency_key", "actor_ref", "correlation_id", "approval_ref", "expires_at"):
        invalid("CommandSubject", dict(subject, **{field: "not-hashed"}))
    preview = json.loads(VECTORS["hash_vectors"][1]["canonical_utf8"])
    invalid("PreviewSubject", dict(preview, expires_at="2026-11-05T00:00:00Z"))
    invalid("PreviewSubject", dict(preview, candidate_digest=None))


def test_negotiated_paging_entry_and_independent_cursors():
    base = SCHEMA["$id"]
    capability = {"schema": "contexture.paging-capability/v1",
                  "capability": "contexture.managed-paging/v1",
                  "tool_name": "contexture_managed_page",
                  "request_schema": base + "#/$defs/ManagedPageRequest",
                  "response_schema": base + "#/$defs/ManagedPageResponse",
                  "default_limit": 200, "max_limit": 1000}
    valid("PagingCapability", capability)
    invalid("PagingCapability", dict(capability, tool_name="open"))
    request = {"schema": "contexture.managed-page-request/v1", "operation": "open", "ref": REF}
    valid("ManagedPageRequest", request)
    valid("ManagedPageRequest", dict(request, member_cursor="m", uses_cursor="u"))
    for update in ({"limit": 0}, {"limit": 1001}, {"limit": "200"},
                   {"cursor": "wrong"}, {"run_token": "wrong-carrier"}):
        invalid("ManagedPageRequest", dict(request, **update))
    request["operation"] = "uses"
    valid("ManagedPageRequest", dict(request, cursor="u"))
    invalid("ManagedPageRequest", dict(request, member_cursor="m"))
    p = page()
    invalid("RelationPage", dict(p, next_cursor="not-complete"))
    invalid("RelationPage", dict(p, complete=False))
    valid("RelationPage", dict(p, complete=False, next_cursor="opaque"))
    invalid("RelationPage", dict(p, total_count=10))
    invalid("RelationPage", dict(p, next_cursor=None))
    card = {"node_id": "r", "ref": REF, "kind": "role", "name": "root", "description": "Root"}
    response = {"schema": "contexture.managed-page-response/v1", "operation": "open",
                "owner_active": {"card": card, "instructions": "Follow"},
                "process_cards": [], "member_page": p, "uses_page": p,
                "member_count": "0", "group_counts": {key: "0" for key in
                    ("pre_process", "children", "post_process", "skills", "tools")}}
    valid("ManagedPageResponse", response)
    response["process_cards"] = [{"designation": "pre_process", "card": card}] * 3
    invalid("ManagedPageResponse", response)
    invalid("RouteCard", dict(card, instructions="Never leak program"))
    invalid("RouteCard", dict(card, input_schema={}))


def test_watch_whole_batch_or_fetch_not_both_and_no_raw_sequence():
    batch = {"batch_id": "opaque-authorized-batch", "seq_cursor": "opaque",
             "facts_digest": D, "size": "0", "payload": {
                 "schema": "contexture.authorized-batch/v1", "changes": []}}
    valid("BatchDescriptor", batch)  # filtered-empty batch still advances opaque cursor
    fetch = {"token": "authorized-fetch", "digest": D, "size": "100"}
    invalid("BatchDescriptor", dict(batch, authorized_fetch_ref=fetch))
    del batch["payload"]
    invalid("BatchDescriptor", batch)
    batch["authorized_fetch_ref"] = fetch
    valid("BatchDescriptor", batch)
    invalid("BatchDescriptor", dict(batch, journal_seq="1"))
    invalid("BatchDescriptor", dict(batch, raw_record_hash=D))
    watch = {"schema": "contexture.watch/v1", "type": "batch", "subscription_id": "s",
             "topic": "definition", "batch": batch}
    valid("WatchEnvelope", watch)
    invalid("WatchEnvelope", dict(watch, partial=True))
    valid("WatchEnvelope", {"schema": "contexture.watch/v1", "type": "closed",
                            "subscription_id": "s", "code": "resync_required"})


def test_release_facts_do_not_claim_observed_and_wait_needs_explicit_targets():
    release = {"graph_id": "g", "target_id": "t", "release_epoch": "1"}
    r = {"schema": "contexture.release-request/v1", "graph_id": "g", "target_id": "t",
         "revision": "1", "definition_digest": D, "manifest_digest": D,
         "expected_desired_epoch": "0", "idempotency_key": "k", "policy": "exact", "reason": "Release"}
    valid("ReleaseRequest", r)
    invalid("ReleaseRequest", dict(r, policy="skip"))
    invalid("ReleaseRequest", dict(r, revision="0"))
    rr = {"schema": "contexture.release-receipt/v1", "release": release,
          "journal_seq": "1", "request_digest": D, "status": "requested"}
    valid("ReleaseReceipt", rr)
    invalid("ReleaseReceipt", dict(rr, status="observed"))
    invalid("ReleaseReceipt", dict(rr, status="superseded"))
    valid("ReleaseReceipt", dict(rr, status="superseded", superseded_by="2"))
    valid("WaitPredicate", {"kind": "instance", "instance_id": "i", "boot_id": UUID})
    invalid("WaitPredicate", {"kind": "all_of", "targets": []})
    invalid("WaitPredicate", {"kind": "fleet"})
    observation = {"release": release, "instance_id": "i", "boot_id": UUID,
                   "generation_id": UUID, "revision": "1", "definition_digest": D,
                   "manifest_digest": D, "observed_at": "2026-11-05T00:00:00Z"}
    valid("Observation", observation)
    del observation["boot_id"]
    invalid("Observation", observation)


@pytest.mark.parametrize("vector", VECTORS["file_layout"], ids=lambda v: v["seq"])
def test_exact_bucket_record_paths(vector):
    valid("FileLayoutVector", vector)
    seq = int(vector["seq"])
    bucket = f"{(seq - 1) // 10000:020d}"
    assert vector["bucket"] == bucket
    assert vector["record_path"] == f"records/{bucket}/{seq:020d}.json"
    assert len(str(seq)) <= 20
    invalid("FileLayoutVector", dict(vector, record_path="../" + vector["record_path"]))


@pytest.mark.parametrize("vector", VECTORS["blob_layout"])
def test_exact_blob_paths(vector):
    valid("Digest", vector["digest"])
    hex_digest = vector["digest"][7:]
    assert vector["path"] == f"blobs/sha256/{hex_digest[:2]}/{hex_digest}.blob"
    assert not any(part in ("", ".", "..") for part in vector["path"].split("/"))


def _raw_candidate_oracle(raw):
    """Independent test-only lexical oracle; never imports production graph code."""
    if raw.startswith(b"\xef\xbb\xbf"):
        raise ValueError("BOM")

    def pairs(items):
        obj = {}
        for key, value in items:
            if key in obj:
                raise ValueError("duplicate key")
            obj[key] = value
        return obj

    def number(lexeme):
        decimal = Decimal(lexeme)
        if decimal == decimal.to_integral_value() and abs(decimal) > 2**53 - 1:
            raise ValueError("unsafe mathematical integer")
        binary = float(decimal)
        if binary in (float("inf"), -float("inf")) or (decimal != 0 and binary == 0):
            raise ValueError("overflow/underflow")
        return binary

    def constant(_):
        raise ValueError("non-JSON constant")

    return rfc8785.dumps(json.loads(raw.decode("utf-8"), object_pairs_hook=pairs,
                                   parse_int=number, parse_float=number,
                                   parse_constant=constant))


RAW = json.loads((MANAGED / "rawcanonical/vectors.json").read_text())


@pytest.mark.parametrize("vector", RAW["vectors"], ids=lambda v: v["name"])
def test_language_neutral_raw_canonical_vectors(vector):
    raw = bytes.fromhex(vector["raw_utf8_hex"])
    if "error" in vector:
        with pytest.raises((ValueError, UnicodeError, rfc8785.CanonicalizationError)):
            _raw_candidate_oracle(raw)
    else:
        assert _raw_candidate_oracle(raw) == bytes.fromhex(vector["canonical_utf8_hex"])


def test_local_markdown_links_and_no_acceptance_claim():
    for path in MANAGED.glob("*.md"):
        for target in re.findall(r"\]\(([^)]+)\)", path.read_text()):
            if "://" not in target:
                assert (path.parent / target.split("#")[0]).is_file(), (path, target)
    manifest = json.loads((MANAGED / "manifest.json").read_text())
    assert manifest["accepted_spec_revision"] is None
    assert VECTORS["status"] == "candidate-not-review-accepted"


def test_complete_checkpoint_requires_every_projection():
    checkpoint = {"schema": "contexture.checkpoint/v1", "store_id": "s",
                  "last_seq": "1", "last_record_hash": D, "graphs": [],
                  "desired_releases": [], "release_operations": [],
                  "prepare_attempt_history": [], "observations_history": [],
                  "receipts_index": [receipt()], "projection_versions": {"graph": "1"},
                  "blob_refs": [], "checkpoint_digest": D}
    valid("Checkpoint", checkpoint)
    for field in ("receipts_index", "desired_releases", "release_operations",
                  "prepare_attempt_history", "observations_history", "blob_refs"):
        partial = deepcopy(checkpoint)
        del partial[field]
        invalid("Checkpoint", partial)
    invalid("Checkpoint", dict(checkpoint, receipts_index=[{"idempotency_key": "k"}]))


COMMAND_EXAMPLES = [
    {"op": "create_node", "node": node()},
    {"op": "update_node", "node_id": "r", "set_fields": {"description": "Changed"}, "unset_fields": []},
    {"op": "rename_node", "node_id": "r", "new_name": "renamed"},
    *[{"op": op, "node_id": "r", "target": {"plane": "model", "position": 0}}
      for op in ("attach_node", "move_node")],
    {"op": "detach_node", "node_id": "r"},
    {"op": "reorder_members", "parent_id": "r", "slot": "children", "node_ids": ["a", "b"]},
    {"op": "add_root", "node_id": "r", "plane": "model", "position": 0},
    {"op": "remove_root", "node_id": "r"},
    *[{"op": op, "source_id": "r", "target_id": "a"} for op in ("add_use", "remove_use")],
    {"op": "delete_nodes", "targets": ["r"], "policy": "reject_references", "deleted_ids": ["r"], "disconnections": []},
    {"op": "restore_nodes", "revision": "1", "node_ids": ["r"]},
    {"op": "replace_subtree", "anchor_id": "r", "id_mapping": {"retained_ids": ["r"], "new_ids": [], "deleted_ids": []}, "payload": {"inline": graph()}},
    {"op": "put_publication", "publication": {"kind": "prompt", "publication_id": "p", "name": "entry", "description": "Entry", "opens_id": "r", "model_may_open": False}},
    {"op": "remove_publication", "publication_id": "p"},
    {"op": "put_presentation", "locale": "en", "presentation": {"node_id": "r", "title": "Root"}},
    {"op": "remove_presentation", "locale": "en", "node_id": "r"},
    *[{"op": op, "scope_ids": ["r"], "id_mapping": {"retained_ids": ["r"], "new_ids": [], "deleted_ids": []}, "payload": {"inline": graph()}}
      for op in ("import", "reconcile")],
]


@pytest.mark.parametrize("command", COMMAND_EXAMPLES, ids=lambda c: c["op"])
def test_every_ordinary_command_branch_is_live_and_closed(command):
    valid("Command", command)
    invalid("Command", dict(command, bypass=True))
    required = next(branch["required"] for branch in SCHEMA["$defs"]["Command"]["oneOf"]
                    if command["op"] in branch["properties"]["op"].get("enum", [branch["properties"]["op"].get("const")]))
    for key in required:
        candidate = deepcopy(command)
        del candidate[key]
        invalid("Command", candidate)


def test_all_resolved_fact_branches_and_field_value_types():
    n = node()
    tombstone = {"node_id": "r", "kind": "role", "mutable": "inherit", "deleted_revision": "2"}
    publication = COMMAND_EXAMPLES[14]["publication"]
    presentation = {"node_id": "r", "title": "Root"}
    facts = [
        {"type": "NodeCreated", "node": n},
        {"type": "NodeRestored", "node": n, "tombstone": tombstone},
        {"type": "NodeFieldsSet", "node_id": "r", "before": {"description": {"present": True, "value": "Old"}}, "after": {"description": {"present": True, "value": "New"}}},
        {"type": "NodeDeleted", "node_id": "r", "before": n, "tombstone": tombstone},
        {"type": "MembersAssigned", "node_id": "r", "before": {}, "after": {"children": ["c"]}},
        {"type": "RootsAssigned", "before": [], "after": [{"node_id": "r", "plane": "model"}]},
        {"type": "UsesAssigned", "node_id": "r", "before": [], "after": ["s"]},
        {"type": "PublicationPut", "before": {"present": False}, "after": publication, "position": 0},
        {"type": "PublicationRemoved", "before": publication, "position": 0},
        {"type": "PresentationPut", "locale": "en", "before": {"present": False}, "after": presentation},
        {"type": "PresentationRemoved", "locale": "en", "before": presentation},
        {"type": "SemanticMigrated", "before": "0", "after": "1", "migration_id": "m", "source_mapping": {"retained_ids": ["r"], "new_ids": [], "deleted_ids": []}},
        {"type": "CreationPolicyMigrated", "node_id": "r", "before": False, "after": True, "migration_id": "m"},
    ]
    for fact in facts:
        valid("Fact", fact)
        invalid("Fact", dict(fact, future_field="refuse"))
    for field, value in (("description", None), ("binding", {}), ("instructions", 1),
                         ("name", "bad/name"), ("instruction_template", {"parts": [{"kind": "RawRef"}]})):
        invalid("NodeFieldValues", {field: {"present": True, "value": value}})
    change = {"type": "GraphChangeCommitted", "graph_id": "g", "semantic_version": "1",
              "base_revision": "1", "result_revision": "2", "before_digest": D,
              "after_digest": D, "resolved_facts": facts[:1], "command_digest": D, "reason": "Change"}
    valid("Event", change)
    invalid("Event", dict(change, resolved_facts=[{"type": "ToolInvoked"}]))


@pytest.mark.parametrize("name,value", [("Name", "role\n"), ("ManagedRef", REF + "\n"),
                                       ("Locale", "en\n"), ("Extensions", {"a:b\n": 1})])
def test_absolute_regex_end_rejects_trailing_newline(name, value):
    invalid(name, value)


def test_complete_locale_shape_does_not_reduce_to_simple_tags():
    for locale in ("en", "zh-Hans-CN", "en-US-u-ca-gregory", "x-private", "i-klingon"):
        valid("Locale", locale)  # Actual normalization/validity is a separate semantic gate.


CLOSURE = json.loads((MANAGED / "referencevectors/closure.json").read_text())


@pytest.mark.parametrize("vector", CLOSURE["hash_vectors"], ids=lambda v: v["name"])
def test_closure_fixed_preimages_and_independent_digest_oracle(vector):
    raw = vector["canonical_utf8"].encode("utf-8")
    value = json.loads(raw)
    valid(vector["definition"], value)
    assert rfc8785.dumps(value) == raw
    assert "sha256:" + hashlib.sha256(vector["domain"].encode() + b"\0" + raw).hexdigest() == vector["digest"]
    if vector["definition"] in ("BootstrapSubject", "MigrationSubject", "ReleaseSubject"):
        for field in ("actor_ref", "approval_ref", "correlation_id", "idempotency_key", "expires_at"):
            invalid(vector["definition"], dict(value, **{field: "excluded"}))
    if vector["definition"] == "CheckpointSubject":
        invalid("CheckpointSubject", dict(value, checkpoint_digest=D))
        for key in value:
            missing = dict(value)
            del missing[key]
            invalid("CheckpointSubject", missing)


def test_closure_fact_shapes_and_candidate_manifest_coverage():
    for fact in ({"type": "GraphSeeded", "before": None, "after": graph()},
                 {"type": "GraphFieldsSet", "before": {"extensions": {}},
                  "after": {"extensions": {"test:null": None}}},
                 {"type": "PresentationsAssigned", "before": {"default_locale": "en", "catalogs": []},
                  "after": {"default_locale": "fr", "catalogs": [{"locale": "fr", "entries": []}]}}):
        valid("Fact", fact)
        invalid("Fact", dict(fact, bypass=True))
    invalid("Fact", {"type": "GraphFieldsSet", "before": {}, "after": {}})
    invalid("Fact", {"type": "GraphFieldsSet", "before": {"graph_id": "g"}, "after": {"graph_id": "h"}})
    manifest = json.loads((MANAGED / "manifest.json").read_text())
    assert "spec/managed/referencevectors/closure.json" in {a["path"] for a in manifest["assets"]}
    assert CLOSURE["status"] == "candidate-not-review-accepted"
    assert manifest["accepted_spec_revision"] is None