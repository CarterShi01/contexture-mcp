"""Binding upgrades keep history replayable (M2/B1).

Registry evidence (PublicationContract) is admission and compile input, indexed
by exact binding descriptor. Accepted journals replay without any registry, so a
later registry can never make a store unreadable.
"""

from __future__ import annotations

from pathlib import Path
import tempfile
from typing import Any

import pytest

from contexture.application.managed import GraphOwner
from contexture.core.graph.errors import GraphError
from contexture.core.graph.model import BindingDescriptor, GraphDefinition, PublicationContract, contract_lookup
from contexture.server.managed_journal import FileJournal
from tests.test_managed_owner import LATER, SCHEMA, Host, owner


def binding(version: str, **extra: Any) -> dict[str, Any]:
    return {"kind": "registered", "binding_id": "reader", "version": version,
            "portability": {"kind": "portable", "contract_id": "reader"}, **extra}


def contract(version: str, effect: str = "read_only", **extra: Any) -> PublicationContract:
    return PublicationContract(BindingDescriptor.from_json(binding(version, **extra)),
                               effect, True)  # type: ignore[arg-type]


def seed_envelope() -> dict[str, Any]:
    return {"schema": "contexture.bootstrap/v1", "graph_id": "g", "expected_revision": "0",
        "seed": {"inline": {"schema": "contexture.graph/v1", "semantic_version": "1",
            "graph_id": "g", "application_name": "app", "mode": "runtime",
            "nodes": [{"node_id": "r", "kind": "role", "name": "root", "description": "root",
                       "instructions": "program", "mutable": True, "members": {"tools": ["t"]}},
                      {"node_id": "t", "kind": "tool", "name": "read", "description": "read",
                       "binding": binding("1")}],
            "roots": [{"node_id": "r", "plane": "model"}],
            "publications": [{"kind": "route", "publication_id": "route", "method": "GET",
                              "path": "/read", "opens_id": "t"}],
            "presentations": {"default_locale": "en", "catalogs": [{"locale": "en", "entries": []}]}}},
        "approval_ref": "approved", "idempotency_key": "seed", "reason": "seed", "correlation_id": "trace"}


def transaction(manager: GraphOwner, key: str, *commands: dict[str, Any]) -> dict[str, Any]:
    return {"schema": "contexture.transaction/v1", "graph_id": "g",
            "base_revision": manager.read("g").revision, "idempotency_key": key,
            "reason": "upgrade", "correlation_id": "trace", "commands": list(commands)}


UPGRADE = {"op": "update_node", "node_id": "t", "set_fields": {"binding": binding("2")}, "unset_fields": []}


def test_contract_lookup_is_by_exact_binding_not_node() -> None:
    both = contract_lookup([contract("1"), contract("2")])
    assert len(both) == 2
    # Mapping keys are not semantic; values are the evidence.
    assert contract_lookup({"anything": contract("1")}) == contract_lookup([contract("1")])
    with pytest.raises(GraphError) as error:
        contract_lookup([contract("1"), contract("1", "write")])
    assert error.value.code == "contract_mismatch"
    with pytest.raises(GraphError):
        contract_lookup(["not evidence"])  # type: ignore[list-item]


def test_single_transaction_upgrade_and_registry_retirement_reopen() -> None:
    with tempfile.TemporaryDirectory(prefix="contexture-upgrade-") as temp:
        path = Path(temp) / "store"
        host = Host()
        with FileJournal.open(path, schema=SCHEMA) as journal:
            # Registry during transition vouches for both versions.
            manager = owner(journal, host, publication_contracts=[contract("1"), contract("2")])
            manager.commit_bootstrap(manager.preview_bootstrap(seed_envelope(), expires_at=LATER))
            manager.commit(manager.preview(transaction(manager, "upgrade", UPGRADE), expires_at=LATER))
            state = manager.read("g")
            assert state.revision == "2"
            assert state.definition.nodes["t"].binding.version == "2"  # type: ignore[union-attr]
            manager.close()
        # v1 retired from the registry: the store still opens and replays.
        with FileJournal.open(path, schema=SCHEMA) as journal:
            manager = owner(journal, host, publication_contracts=[contract("2")])
            assert manager.read("g").revision == "2"
            # New edits are judged against the current registry only.
            manager.commit(manager.preview(transaction(manager, "rename", {
                "op": "rename_node", "node_id": "t", "new_name": "read2"}), expires_at=LATER))
            assert manager.read("g").revision == "3"
            manager.close()
        # Even with NO registry the journal replays; only new candidates refuse.
        with FileJournal.open(path, schema=SCHEMA) as journal:
            manager = owner(journal, host)
            assert manager.read("g").revision == "3"
            with pytest.raises(GraphError) as error:
                manager.preview(transaction(manager, "edit", {
                    "op": "rename_node", "node_id": "t", "new_name": "read3"}), expires_at=LATER)
            assert error.value.code == "binding_contract_required"
            manager.close()


def test_upgrade_requires_evidence_for_the_new_binding() -> None:
    with tempfile.TemporaryDirectory(prefix="contexture-upgrade-") as temp:
        with FileJournal.open(Path(temp) / "store", schema=SCHEMA) as journal:
            host = Host()
            manager = owner(journal, host, publication_contracts=[contract("1")])
            manager.commit_bootstrap(manager.preview_bootstrap(seed_envelope(), expires_at=LATER))
            with pytest.raises(GraphError) as error:
                manager.preview(transaction(manager, "upgrade", UPGRADE), expires_at=LATER)
            assert error.value.code == "binding_contract_required"
            # The route method still must match the registry effect for the new binding.
            manager.close()
        with FileJournal.open(Path(temp) / "store2", schema=SCHEMA) as journal:
            manager = owner(journal, host, publication_contracts=[contract("1"), contract("2", "write")])
            manager.commit_bootstrap(manager.preview_bootstrap(seed_envelope(), expires_at=LATER))
            with pytest.raises(GraphError):
                manager.preview(transaction(manager, "upgrade", UPGRADE), expires_at=LATER)
            manager.close()


def test_config_is_part_of_the_exact_descriptor() -> None:
    wire = seed_envelope()["seed"]["inline"]
    wire["nodes"][1]["binding"] = binding("1", config={"x": 1})
    with pytest.raises(GraphError) as error:
        GraphDefinition.from_json(wire, publication_contracts=[contract("1")])
    assert error.value.code == "binding_contract_required"
    GraphDefinition.from_json(wire, publication_contracts=[contract("1", config={"x": 1})])
    # Accepted-history mode skips registry evidence but not structure.
    GraphDefinition.from_json(wire, verify_contracts=False)
    wire["publications"].append(dict(wire["publications"][0], publication_id="dup"))
    with pytest.raises(GraphError):
        GraphDefinition.from_json(wire, verify_contracts=False)
