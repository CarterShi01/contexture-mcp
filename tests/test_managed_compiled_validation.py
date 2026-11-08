"""Compiled storage schema validation preserves the registered Draft 2020-12 contract."""

import json
import sys
import tempfile
from copy import deepcopy
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from contexture.core.graph.canonical import CANONICAL_PROFILE, canonical_json
from contexture.server.managed_journal import (
    FileJournal,
    JournalError,
    _compiled_validators,
)

SCHEMA = json.loads((Path(__file__).parents[1] / "spec/managed/schemas/managed.schema.json").read_text())
DIGEST = "sha256:" + "1" * 64


@pytest.mark.parametrize("name,value", [
    ("BlobRef", {"digest": DIGEST, "size": "3", "media_type": "text/plain"}),
    ("Event", {"type": "CommandCompleted", "graph_id": "g", "revision": "0",
               "command_digest": DIGEST, "status": "noop"}),
    ("FileJournalMeta", {"schema": "contexture.file-journal/v1",
                         "store_id": "00000000-0000-4000-8000-000000000001",
                         "canonical_profile": CANONICAL_PROFILE, "durability": "strict",
                         "created_at": "2026-11-07T00:00:00Z"}),
    ("CommandReceipt", {"schema": "contexture.command-receipt/v1", "graph_id": "g",
                        "actor_ref": "actor", "idempotency_key": "key", "request_digest": DIGEST,
                        "revision": "0", "journal_seq": "1", "status": "noop",
                        "durability": "strict", "operation_result": {"inline": {"answer": 42}}}),
])
def test_compiled_validity_equals_existing_validator_for_registered_shapes_and_mutations(name, value):
    compiled = _compiled_validators(canonical_json(SCHEMA))[name]
    reference = Draft202012Validator({"$defs": SCHEMA["$defs"], "$ref": "#/$defs/" + name})
    assert reference.is_valid(value)
    corpus = [value, None, [], {}, True, 1, "invalid", {**value, "unknown_core_field": 1}]
    for field in value:
        missing = deepcopy(value)
        del missing[field]
        corpus.append(missing)
        for replacement in (None, False, 1, "01", {}, []):
            corpus.append({**value, field: replacement})
    for candidate in corpus:
        assert compiled.is_valid(candidate) == reference.is_valid(candidate)


@pytest.mark.parametrize("keyword,reference", [
    ("$ref", "https://example.invalid/schema"),
    ("$ref", "file:///jsonschema-probe-does-not-exist.json"),
    ("$dynamicRef", "https://example.invalid/schema"),
])
def test_registered_schema_rejects_external_references_before_compilation(keyword, reference):
    journal = FileJournal()
    journal._schema = {**SCHEMA, "$defs": {**SCHEMA["$defs"], "untrusted": {keyword: reference}}}
    with pytest.raises(JournalError) as failure:
        journal._make_validators()
    assert failure.value.code == "schema_invalid"


def test_cached_schema_compilation_is_bounded_and_never_mutates_owner_validator_tables():
    _compiled_validators.cache_clear()
    first = FileJournal()
    first._schema = deepcopy(SCHEMA)
    first._validators = first._make_validators()
    second = FileJournal()
    second._schema = deepcopy(SCHEMA)
    second._validators = second._make_validators()
    assert first._validators is not second._validators
    assert first._validators["BlobRef"] is second._validators["BlobRef"]
    first._validators.clear()
    assert second._validators["BlobRef"].is_valid(
        {"digest": DIGEST, "size": "3", "media_type": "text/plain"})
    assert _compiled_validators.cache_info().hits >= 1
    assert _compiled_validators.cache_info().maxsize == 8


@pytest.mark.skipif(sys.platform != "linux", reason="Native Linux FileJournal profile")
def test_compiled_and_reference_validators_agree_on_real_records_and_complete_checkpoints():
    operation = {"type": "CommandCompleted", "graph_id": "g", "revision": "0",
                 "command_digest": DIGEST, "status": "noop"}
    receipt = {"schema": "contexture.command-receipt/v1", "graph_id": "g", "actor_ref": "actor",
               "idempotency_key": "key", "request_digest": DIGEST, "revision": "0",
               "journal_seq": "1", "status": "noop", "durability": "strict",
               "operation_result": {"inline": {"answer": 42}}}

    def projector(records):
        return {"graphs": [], "desired_releases": [], "release_operations": [],
                "prepare_attempt_history": [], "observations_history": [],
                "receipts_index": [record["command_receipt"] for record in records],
                "projection_versions": {}, "blob_refs": []}

    with tempfile.TemporaryDirectory(prefix="compiled-journal-schema-", dir="/tmp") as directory:
        with FileJournal.open(directory, schema=SCHEMA, projector=projector) as journal:
            record = journal.append([operation], actor_ref="actor", command_receipt=receipt)
            checkpoint = journal.checkpoint()
            for name, value in (("CommitRecord", record), ("Checkpoint", checkpoint)):
                compiled = journal._validators[name]
                reference = Draft202012Validator({"$defs": SCHEMA["$defs"], "$ref": "#/$defs/" + name})
                assert reference.is_valid(value) and compiled.is_valid(value)
                for field in value:
                    missing = deepcopy(value)
                    del missing[field]
                    assert compiled.is_valid(missing) == reference.is_valid(missing)
                    wrong = {**value, field: None}
                    assert compiled.is_valid(wrong) == reference.is_valid(wrong)
