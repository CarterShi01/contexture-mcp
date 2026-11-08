"""Bounded native management candidate over an already configured live owner.

Host composition owns authentication, authority, journal locking and lifetime.
This service neither opens files nor constructs an owner. A read-only instance
is a debug door, not a way to acquire mutation authority. Preview handles are
local to this service lifetime; retain the service (e.g. CLI session) for commit.
No business handler, compiler, preparation or serving adoption is implied.
"""

from __future__ import annotations

from collections.abc import Mapping
from threading import RLock
from typing import Any, Literal
from uuid import uuid4

from .managed import GraphOwner
from .managed_releases import ReleaseIntentCoordinator
from ..core.graph.canonical import canonical_json, decode_json, parse_counter, validate_id
from ..core.graph.errors import GraphError
from ..core.graph.transactions import Preview

WRITE_COMMANDS = frozenset({"preview", "commit", "revert", "release"})
READ_COMMANDS = frozenset({"head", "status", "history", "diff", "receipt", "release-status"})
COMMANDS = READ_COMMANDS | WRITE_COMMANDS


def decode_request(raw: bytes, *, max_bytes: int = 16 * 1024 * 1024) -> object:
    """Preserve numeric lexemes and reject duplicate keys before decoding."""
    if len(raw) > max_bytes:
        raise GraphError("limit_exceeded", "Management input exceeds budget")
    return decode_json(raw)


def encode_result(value: object) -> bytes:
    return canonical_json(value)


def _object(value: object, required: str, optional: str = "") -> dict[str, Any]:
    if (not isinstance(value, Mapping) or any(type(k) is not str for k in value)
            or not set(required.split()) <= set(value)
            or set(value) - set((required + " " + optional).split())):
        raise GraphError("schema_invalid", "Closed management arguments required")
    return dict(value)


def _text(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise GraphError("schema_invalid", "Nonempty text required")
    return value


class ManagedManagementService:
    """Explicit native read/write doors; all decisions remain in public ports.

    ``mode`` is trusted Host configuration, never a request field. It is an
    additional restriction, not authorization: GraphOwner reacquires current
    verified identity and its independent authority fence on every operation.
    Preview handles retain the exact owner Preview, including trusted context;
    payloads cannot recreate it or smuggle an identity into commit.

    Close this service before closing the Host owner/journal. close() drops only
    management handles and does not close an externally shared live owner.
    """

    def __init__(self, owner: GraphOwner, *, mode: Literal["read_only", "write"],
                 releases: ReleaseIntentCoordinator | None = None,
                 max_previews: int = 64, max_preview_bytes: int = 16 * 1024 * 1024,
                 max_request_bytes: int = 16 * 1024 * 1024,
                 max_response_bytes: int = 8 * 1024 * 1024):
        if mode not in {"read_only", "write"} or any(type(n) is not int or n <= 0 for n in
                (max_previews, max_preview_bytes, max_request_bytes, max_response_bytes)):
            raise GraphError("schema_invalid", "Explicit management profile required")
        if releases is not None and releases.owner is not owner:
            raise GraphError("contract_mismatch", "Release port must share the owner")
        self.owner, self.mode, self.releases = owner, mode, releases
        self.max_previews, self.max_preview_bytes = max_previews, max_preview_bytes
        self.max_request_bytes, self.max_response_bytes = max_request_bytes, max_response_bytes
        self._previews: dict[str, Preview] = {}
        self._preview_bytes = 0
        self._lock, self._closed = RLock(), False

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._previews.clear()
            self._preview_bytes = 0

    def execute(self, command: str, arguments: object) -> dict[str, Any]:
        """Return stage-separated facts, never a transport or handler result.

        For any append, a response-budget failure is still followed by receipt
        lookup, not blind retry. Limits do not split an atomic owner operation.
        """
        with self._lock:
            if self._closed:
                raise GraphError("resource_busy", "Management service is closed")
            if command not in COMMANDS:
                raise GraphError("unsupported_contract", "Management command unavailable")
            if command in WRITE_COMMANDS and self.mode != "write":
                raise GraphError("permission_denied", "Read-only management door")
            if isinstance(arguments, bytes):
                arguments = decode_request(arguments, max_bytes=self.max_request_bytes)
            elif len(canonical_json(arguments)) > self.max_request_bytes:
                raise GraphError("limit_exceeded", "Management input exceeds budget")
            result = self._dispatch(command, arguments)
            wire = {"schema": "contexture.management-result/v1", "command": command,
                    "stage": "preview" if command in {"preview", "revert"} else
                        "committed" if command == "commit" else
                        "requested" if command == "release" else "read",
                    "result": result}
            if len(canonical_json(wire)) > self.max_response_bytes:
                raise GraphError("too_large", "Management response exceeds budget")
            return wire

    def _retain(self, proposal: Preview) -> dict[str, Any]:
        # Account for the retained base/candidate, not only the review summary.
        size = sum(len(canonical_json(v)) for v in (
            proposal.prepared.base.definition.as_json(),
            proposal.prepared.change.candidate.as_json(),
            proposal.prepared.envelope.value, proposal.as_json()))
        if len(self._previews) >= self.max_previews or self._preview_bytes + size > self.max_preview_bytes:
            raise GraphError("resource_busy", "Management preview capacity exhausted")
        review = proposal.as_json()
        if len(canonical_json(review)) + 512 > self.max_response_bytes:
            raise GraphError("too_large", "Management preview exceeds response budget")
        handle = str(uuid4())
        self._previews[handle] = proposal
        self._preview_bytes += size
        return {"preview_id": handle, "review": review}

    def _dispatch(self, command: str, value: object) -> object:
        if command in {"head", "status"}:
            args = _object(value, "graph_id")
            state = self.owner.read(validate_id(args["graph_id"]))
            result: dict[str, object] = {"graph_id": state.definition.graph_id,
                "revision": state.revision, "definition_digest": state.definition_digest}
            if command == "head":
                result["definition"] = state.definition.as_json()
            else:
                result.update(writes_stopped=self.owner.writes_stopped,
                    management_mode=self.mode, release_available=self.releases is not None)
            return result
        if command == "history":
            args = _object(value, "graph_id", "after limit")
            after = parse_counter(args.get("after", "0"))
            limit = args.get("limit", 200)
            if type(limit) is not int or not 1 <= limit <= 1000:
                raise GraphError("schema_invalid", "History page limit outside profile")
            history = self.owner.history(validate_id(args["graph_id"]))
            records = [r for r in history.records if parse_counter(r.journal_seq) > after]
            page = records[:limit]
            return {"retained_lower_bound": history.retained_lower_bound,
                "items": [{"revision": r.state.revision, "journal_seq": r.journal_seq,
                           "definition_digest": r.state.definition_digest} for r in page],
                "complete": len(records) <= limit,
                "next_after": page[-1].journal_seq if len(records) > limit else None}
        if command == "diff":
            args = _object(value, "graph_id before after")
            history = self.owner.history(validate_id(args["graph_id"]))
            return history.diff(_text(args["before"]), _text(args["after"])).as_json()
        if command == "receipt":
            args = _object(value, "graph_id idempotency_key")
            return self.owner.receipt(validate_id(args["graph_id"]), validate_id(args["idempotency_key"]))
        if command == "preview":
            args = _object(value, "transaction expires_at")
            return self._retain(self.owner.preview(args["transaction"], expires_at=_text(args["expires_at"])))
        if command == "commit":
            args = _object(value, "preview_id", "approval_ref")
            proposal = self._previews.get(_text(args["preview_id"]))
            if proposal is None:
                raise GraphError("approval_stale", "Preview requires its original service lifetime")
            approval = validate_id(args["approval_ref"]) if "approval_ref" in args else None
            return self.owner.commit(proposal, approval_ref=approval)
        if command == "revert":
            args = _object(value, "graph_id target_revision idempotency_key reason correlation_id expires_at",
                           "node_ids approval_ref")
            nodes = args.get("node_ids")
            if "node_ids" in args and (not isinstance(nodes, list) or not nodes):
                raise GraphError("schema_invalid", "Explicit nonempty revert scope required")
            return self._retain(self.owner.preview_revert(validate_id(args["graph_id"]),
                _text(args["target_revision"]), idempotency_key=validate_id(args["idempotency_key"]),
                reason=_text(args["reason"]), correlation_id=validate_id(args["correlation_id"]),
                expires_at=_text(args["expires_at"]),
                node_ids=None if nodes is None else tuple(validate_id(n) for n in nodes),
                approval_ref=validate_id(args["approval_ref"]) if "approval_ref" in args else None))
        if self.releases is None:
            raise GraphError("unsupported_contract", "Host release port is not configured")
        if command == "release":
            args = _object(value, "request")
            return self.releases.request(args["request"]).as_json()
        args = _object(value, "graph_id target_id")
        status = self.releases.status(validate_id(args["graph_id"]), validate_id(args["target_id"]))
        # Release projection retains the original approval reference for replay;
        # management status is not a credential/reference echo endpoint.
        desired_releases = status.get("desired_releases", [])
        if not isinstance(desired_releases, list):
            raise GraphError("contract_mismatch", "Invalid release status projection")
        for desired in desired_releases:
            if not isinstance(desired, dict) or not isinstance(desired.get("request"), dict):
                raise GraphError("contract_mismatch", "Invalid desired release projection")
            desired["request"].pop("approval_ref", None)
        return status