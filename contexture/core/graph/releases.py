"""Pure accepted release facts, not a ReleaseManager or serving authority.

Candidate M19 hashes ReleaseSubject in contexture.command/v1. The schema tag
contexture.release-request/v1 is NOT in canonical.DOMAINS. No fallback domain is
registered here. Journal validation owns chain/order/durability; this reducer
checks supplied provenance and never looks up definitions, policy or resources.

Only terminal ready/failed attempts have events in the candidate schema. Pending,
preparing and consumed cannot be reconstructed by replay. Observations are
history, including old boots/epochs; there is deliberately no active/latest view.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
import re
from typing import ClassVar, Self, cast

from .canonical import (
    DOMAINS, FrozenJSON, canonical_digest, decode_json, freeze_json,
    parse_counter, thaw_json, validate_id,
)
from .errors import GraphError

Object = Mapping[str, object]
FrozenObject = Mapping[str, FrozenJSON]
RELEASE_EVENT_TYPES = frozenset({
    "ReleaseRequested", "ReleasePreparationSucceeded", "ReleasePreparationFailed",
    "ReleaseSuperseded", "ReplicaObserved",
})
_SECTIONS = (
    "desired_releases", "release_operations", "prepare_attempt_history", "observations_history",
)


def _fail(code: str = "schema_invalid") -> GraphError:
    # Never echo author values, diagnostic messages, IDs or malformed keys.
    return GraphError(code, "Invalid release fact or projection")


def _obj(value: object, required: str, optional: str = "") -> Object:
    if not isinstance(value, Mapping):
        raise _fail()
    keys = set(value)
    if (any(type(k) is not str for k in keys) or not set(required.split()) <= keys
            or keys - set((required + " " + optional).split())):
        raise _fail()
    return cast(Object, value)


def _array(value: object) -> tuple[object, ...]:
    if not isinstance(value, (tuple, list)):
        raise _fail()
    return tuple(value)


def _text(value: object, *, empty: bool = False) -> str:
    if type(value) is not str or (not value and not empty):
        raise _fail()
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise _fail() from None
    return value


def _counter(value: object, *, positive: bool = True) -> str:
    n = parse_counter(value)
    if positive and not n:
        raise _fail()
    return cast(str, value)


def _digest(value: object) -> str:
    text = _text(value)
    if re.fullmatch(r"sha256:[0-9a-f]{64}", text) is None:
        raise _fail()
    return text


def _uuid(value: object) -> str:
    text = _text(value)
    if re.fullmatch(r"[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}", text) is None:
        raise _fail()
    return text


def _time(value: object) -> str:
    text = _text(value)
    if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]+)?Z", text) is None:
        raise _fail()
    try:
        datetime.fromisoformat(text)
    except ValueError:
        raise _fail() from None
    return text


def _frozen(value: object) -> FrozenObject:
    return cast(FrozenObject, _detach(value))


def _detach(value: object) -> FrozenJSON:
    try:
        return freeze_json(value)
    except GraphError as exc:
        raise _fail(exc.code) from None


@dataclass(frozen=True, slots=True, init=False)
class _WireValue:
    """Closed detached typed value; constructors cannot bypass validation."""

    _data: FrozenObject
    required: ClassVar[str] = ""
    optional: ClassVar[str] = ""

    def __init__(self) -> None:
        raise TypeError("Use from_json")

    @classmethod
    def from_json(cls, value: object) -> Self:
        if isinstance(value, bytes):
            value = decode_json(value)
        obj = _obj(_detach(value), cls.required, cls.optional)
        cls._validate(obj)
        result = object.__new__(cls)
        object.__setattr__(result, "_data", _frozen(obj))
        return result

    @classmethod
    def _validate(cls, obj: Object) -> None:
        raise NotImplementedError

    def as_json(self) -> dict[str, object]:
        return cast(dict[str, object], thaw_json(self._data))


@dataclass(frozen=True, slots=True, init=False)
class ReleaseRequest(_WireValue):
    required = ("schema graph_id target_id revision definition_digest manifest_digest "
                "expected_desired_epoch idempotency_key policy reason")
    optional = "approval_ref"

    @classmethod
    def _validate(cls, obj: Object) -> None:
        if obj["schema"] != "contexture.release-request/v1" or obj["policy"] not in ("exact", "latest"):
            raise _fail()
        for key in ("graph_id", "target_id", "idempotency_key", "approval_ref"):
            if key in obj:
                validate_id(obj[key])
        _counter(obj["revision"])
        _counter(obj["expected_desired_epoch"], positive=False)
        _digest(obj["definition_digest"])
        _digest(obj["manifest_digest"])
        _text(obj["reason"])

    @property
    def graph_id(self) -> str:
        return cast(str, self._data["graph_id"])

    @property
    def target_id(self) -> str:
        return cast(str, self._data["target_id"])

    @property
    def expected_desired_epoch(self) -> str:
        return cast(str, self._data["expected_desired_epoch"])

    @property
    def revision(self) -> str:
        return cast(str, self._data["revision"])

    @property
    def definition_digest(self) -> str:
        return cast(str, self._data["definition_digest"])

    @property
    def manifest_digest(self) -> str:
        return cast(str, self._data["manifest_digest"])

    @property
    def idempotency_key(self) -> str:
        return cast(str, self._data["idempotency_key"])

    @property
    def policy(self) -> str:
        return cast(str, self._data["policy"])

    @property
    def reason(self) -> str:
        return cast(str, self._data["reason"])

    @property
    def approval_ref(self) -> str | None:
        return cast(str | None, self._data.get("approval_ref"))

    def subject(self) -> FrozenObject:
        return _frozen({k: v for k, v in self._data.items()
                        if k not in ("idempotency_key", "approval_ref")})

    def digest(self) -> str:
        """M19 request digest, not a hash of the transport request or approval."""
        if "contexture.command/v1" not in DOMAINS:
            raise _fail("semantic_unsupported")
        return canonical_digest(self.subject(), "contexture.command/v1")


@dataclass(frozen=True, slots=True, init=False)
class ReleaseIdentity(_WireValue):
    required = "graph_id target_id release_epoch"

    @classmethod
    def _validate(cls, obj: Object) -> None:
        validate_id(obj["graph_id"])
        validate_id(obj["target_id"])
        _counter(obj["release_epoch"])

    @property
    def key(self) -> tuple[str, str, str]:
        return (cast(str, self._data["graph_id"]), cast(str, self._data["target_id"]),
            cast(str, self._data["release_epoch"]))


@dataclass(frozen=True, slots=True, init=False)
class AttemptIdentity(_WireValue):
    required = "release instance_id boot_id attempt_id"

    @classmethod
    def _validate(cls, obj: Object) -> None:
        ReleaseIdentity.from_json(obj["release"])
        validate_id(obj["instance_id"])
        _uuid(obj["boot_id"])
        validate_id(obj["attempt_id"])


@dataclass(frozen=True, slots=True, init=False)
class Observation(_WireValue):
    required = ("release instance_id boot_id generation_id revision definition_digest "
                "manifest_digest observed_at")

    @classmethod
    def _validate(cls, obj: Object) -> None:
        ReleaseIdentity.from_json(obj["release"])
        validate_id(obj["instance_id"])
        _uuid(obj["boot_id"])
        _uuid(obj["generation_id"])
        _counter(obj["revision"])
        _digest(obj["definition_digest"])
        _digest(obj["manifest_digest"])
        _time(obj["observed_at"])


def _diagnostics(value: object, *, nonempty: bool = False) -> None:
    items = _array(value)
    if nonempty and not items:
        raise _fail()
    for item in items:
        obj = _obj(item, "code location message", "node_id publication_id")
        _text(obj["code"])
        location = _text(obj["location"], empty=True)
        if (location and not location.startswith("/")) or re.search(r"~(?![01])", location):
            raise _fail()
        _text(obj["message"], empty=True)
        for k in ("node_id", "publication_id"):
            if k in obj:
                validate_id(obj[k])


@dataclass(frozen=True, slots=True, init=False)
class AttemptState(_WireValue):
    required = "identity status"
    optional = "generation_id diagnostics"

    @classmethod
    def _validate(cls, obj: Object) -> None:
        AttemptIdentity.from_json(obj["identity"])
        if obj["status"] not in ("pending", "preparing", "ready", "failed", "consumed"):
            raise _fail()
        if "generation_id" in obj:
            _uuid(obj["generation_id"])
        if "diagnostics" in obj:
            _diagnostics(obj["diagnostics"])


@dataclass(frozen=True, slots=True, init=False)
class DesiredRelease(_WireValue):
    required = "request release_epoch"

    @classmethod
    def _validate(cls, obj: Object) -> None:
        request = ReleaseRequest.from_json(obj["request"])
        epoch = _counter(obj["release_epoch"])
        if int(epoch) != int(request.expected_desired_epoch) + 1:
            raise _fail("revision_conflict")


@dataclass(frozen=True, slots=True, init=False)
class ReleaseReceipt(_WireValue):
    required = "schema release journal_seq request_digest status"
    optional = "superseded_by"

    @classmethod
    def _validate(cls, obj: Object) -> None:
        if obj["schema"] != "contexture.release-receipt/v1" or obj["status"] not in ("requested", "superseded"):
            raise _fail()
        release = ReleaseIdentity.from_json(obj["release"])
        _counter(obj["journal_seq"])
        _digest(obj["request_digest"])
        if (obj["status"] == "superseded") != ("superseded_by" in obj):
            raise _fail()
        if "superseded_by" in obj and int(_counter(obj["superseded_by"])) <= int(release.key[2]):
            raise _fail("revision_conflict")


@dataclass(frozen=True, slots=True, init=False)
class ReleaseEvent(_WireValue):
    """The release-only subset of Event; no extra event tags or fields."""

    @classmethod
    def from_json(cls, value: object) -> Self:
        if isinstance(value, bytes):
            value = decode_json(value)
        obj = _detach(value)
        if not isinstance(obj, Mapping):
            raise _fail()
        tag = obj.get("type")
        if not isinstance(tag, str) or tag not in RELEASE_EVENT_TYPES:
            raise _fail("semantic_unsupported")
        fields = {
            "ReleaseRequested": "type request release_epoch",
            "ReleasePreparationSucceeded": "type attempt generation_id manifest_digest",
            "ReleasePreparationFailed": "type attempt diagnostics",
            "ReleaseSuperseded": "type release superseded_by",
            "ReplicaObserved": "type observation",
        }
        event = _obj(obj, fields[tag])
        if tag == "ReleaseRequested":
            ReleaseRequest.from_json(event["request"])
            _counter(event["release_epoch"])
        elif tag in ("ReleasePreparationSucceeded", "ReleasePreparationFailed"):
            AttemptIdentity.from_json(event["attempt"])
            if tag == "ReleasePreparationSucceeded":
                _uuid(event["generation_id"])
                _digest(event["manifest_digest"])
            else:
                _diagnostics(event["diagnostics"], nonempty=True)
        elif tag == "ReleaseSuperseded":
            ReleaseIdentity.from_json(event["release"])
            _counter(event["superseded_by"])
        else:
            Observation.from_json(event["observation"])
        result = object.__new__(cls)
        object.__setattr__(result, "_data", _frozen(event))
        return result


def _identity(request: ReleaseRequest, epoch: str) -> dict[str, object]:
    return {"graph_id": request.graph_id, "target_id": request.target_id, "release_epoch": epoch}


def empty_release_state() -> FrozenObject:
    """Four checkpoint sections, never a new wire schema or a full checkpoint."""
    return _frozen({k: [] for k in _SECTIONS})


def _key(value: object) -> tuple[str, str, str]:
    return ReleaseIdentity.from_json(value).key


def import_release_state(value: object) -> FrozenObject:
    """Validate/detach the four release sections of a trusted checkpoint.

    This is not checkpoint authentication or proof of historical equivalence.
    Earlier requests' full content and actor/key ownership are not retained in
    these four sections. The owner must retain receipts_index and verify anchors.
    Nonterminal attempts cannot be imported by this terminal-events-only slice.
    """
    obj = _obj(_detach(value), " ".join(_SECTIONS))
    operations: dict[tuple[str, str, str], Object] = {}
    epochs: dict[tuple[str, str], int] = {}
    superseded: set[tuple[str, str, str]] = set()
    previous_seq = 0
    for raw in _array(obj["release_operations"]):
        op = ReleaseReceipt.from_json(raw).as_json()
        key = _key(op["release"])
        seq = int(cast(str, op["journal_seq"]))
        if seq < previous_seq:
            raise _fail("journal_corrupt")
        previous_seq = seq
        if op["status"] == "requested":
            if key in operations or int(key[2]) != epochs.get(key[:2], 0) + 1:
                raise _fail("revision_conflict")
            operations[key] = op
            epochs[key[:2]] = int(key[2])
        else:
            prior = operations.get(key)
            if (prior is None or key in superseded
                    or prior["request_digest"] != op["request_digest"]
                    or int(cast(str, op["superseded_by"])) > epochs[key[:2]] + 1):
                raise _fail("journal_corrupt")
            superseded.add(key)
    # Supersession may precede the successor request in one accepted batch;
    # the outer journal/owner validates complete atomic batch semantics.
    desired: dict[tuple[str, str], Object] = {}
    for raw in _array(obj["desired_releases"]):
        d = DesiredRelease.from_json(raw).as_json()
        request = ReleaseRequest.from_json(d["request"])
        key = (request.graph_id, request.target_id, cast(str, d["release_epoch"]))
        if (key[:2] in desired or epochs.get(key[:2]) != int(key[2])
                or key not in operations or operations[key]["request_digest"] != request.digest()):
            raise _fail("journal_corrupt")
        desired[key[:2]] = d
    if set(desired) != set(epochs):
        raise _fail("journal_corrupt")
    attempts: set[bytes] = set()
    for raw in _array(obj["prepare_attempt_history"]):
        attempt = AttemptState.from_json(raw).as_json()
        identity = AttemptIdentity.from_json(attempt["identity"])
        key = _key(identity._data["release"])
        marker = _identity_bytes(identity._data)
        if (key not in operations or marker in attempts
                or attempt["status"] not in ("ready", "failed")
                or (attempt["status"] == "ready" and set(attempt) != {"identity", "status", "generation_id"})
                or (attempt["status"] == "failed" and set(attempt) != {"identity", "status", "diagnostics"})):
            raise _fail("journal_corrupt")
        if attempt["status"] == "failed":
            _diagnostics(attempt["diagnostics"], nonempty=True)
        attempts.add(marker)
    for raw in _array(obj["observations_history"]):
        observation = Observation.from_json(raw).as_json()
        key = _key(observation["release"])
        if key not in operations:
            raise _fail("journal_corrupt")
        _match_current(desired.get(key[:2]), key, observation)
    # Canonical checkpoint desired ordering; histories retain accepted order.
    return _frozen({**obj, "desired_releases": [desired[k] for k in sorted(desired)]})


def _identity_bytes(value: object) -> bytes:
    from .canonical import canonical_json

    return canonical_json(value)


def _match_current(desired: Object | None, key: tuple[str, str, str], value: Object) -> None:
    """Do not invent historical request metadata absent from checkpoint shape."""
    if desired is None or desired["release_epoch"] != key[2]:
        return
    request = cast(Object, desired["request"])
    for field in ("revision", "definition_digest", "manifest_digest"):
        if field in value and value[field] != request[field]:
            raise _fail("contract_mismatch")


def _provenance(record: Object, event: Object) -> tuple[str, str]:
    """Minimal verified-journal context; hash-chain validation is the caller's duty.

    Full record hashes, when supplied, are independently checked. A speculative
    owner record may omit hashes; it never creates durability evidence here.
    No requirement for mutable event arrays (owner uses deeply frozen tuples).
    """
    seq = _counter(record.get("journal_seq"))
    actor = validate_id(record.get("actor_ref"))
    if "events" in record:
        events = _array(record["events"])
        if not any(_identity_bytes(e) == _identity_bytes(event) for e in events):
            raise _fail("journal_corrupt")
    if record.get("record_hash") is not None:
        _digest(record["record_hash"])
        _digest(record.get("payload_digest"))
        if "events" not in record:
            raise _fail("journal_corrupt")
        payload = {k: record[k] for k in ("events", "command_receipt") if k in record}
        if (canonical_digest(payload, "contexture.journal-payload/v1") != record["payload_digest"]
                or canonical_digest({k: v for k, v in record.items() if k != "record_hash"},
                                    "contexture.journal-record/v1") != record["record_hash"]):
            raise _fail("journal_corrupt")
    return seq, actor


def _receipt_evidence(record: Object, request: ReleaseRequest, receipt: Object, actor: str) -> None:
    """Validate optional inline evidence; never fetch an OperationResult blob.

    command_receipt is the only candidate carrier; no release_receipt field is
    added to CommitRecord. Blob evidence needs a verified upstream resolver and
    is explicitly refused rather than silently treated as an inline receipt.
    """
    if "command_receipt" not in record:
        return
    obj = _obj(record["command_receipt"], "schema graph_id actor_ref idempotency_key request_digest "
               "revision journal_seq status durability operation_result")
    for k in ("graph_id", "actor_ref", "idempotency_key"):
        validate_id(obj[k])
    _counter(obj["revision"], positive=False)
    _counter(obj["journal_seq"])
    _digest(obj["request_digest"])
    if (obj["schema"] != "contexture.command-receipt/v1" or obj["status"] != "committed"
            or obj["durability"] not in ("strict", "process_crash")
            or obj["graph_id"] != request.graph_id or obj["actor_ref"] != actor
            or obj["idempotency_key"] != request._data["idempotency_key"]
            or obj["request_digest"] != request.digest()
            or obj["journal_seq"] != receipt["journal_seq"]):
        raise _fail("journal_corrupt")
    result = obj["operation_result"]
    if isinstance(result, Mapping) and "blob" in result:
        raise _fail("semantic_unsupported")
    inline = _obj(result, "inline")["inline"]
    supplied = ReleaseReceipt.from_json(inline)
    if supplied.as_json() != receipt:
        raise _fail("journal_corrupt")


def reduce_release_event(
    state: Mapping[str, object], event: Mapping[str, object], record: Mapping[str, object],
) -> FrozenObject:
    """EventProjection-compatible pure mapping -> mapping adapter (state,event,record).

    Does not import application. Only accepted release tags are routed here.
    Journal sequence/actor own receipt provenance; command digest is reconstructed
    from the complete schema request subject, not graph head or an invented hash.
    Four-section checkpoints do not retain actor-scoped idempotency_index: the
    journal/owner must check actor+key retries before accepting ReleaseRequested.
    """
    current = import_release_state(state)
    item = ReleaseEvent.from_json(event)._data
    tag = item.get("type")
    if not isinstance(tag, str) or tag not in RELEASE_EVENT_TYPES:
        raise _fail("semantic_unsupported")
    seq, actor = _provenance(record, item)
    out = cast(dict[str, object], thaw_json(current))
    desired = cast(list[dict[str, object]], out["desired_releases"])
    operations = cast(list[dict[str, object]], out["release_operations"])
    attempts = cast(list[dict[str, object]], out["prepare_attempt_history"])
    observations = cast(list[dict[str, object]], out["observations_history"])
    if operations and int(seq) < int(cast(str, operations[-1]["journal_seq"])):
        raise _fail("journal_corrupt")
    if tag == "ReleaseRequested":
        obj = _obj(item, "type request release_epoch")
        request = ReleaseRequest.from_json(obj["request"])
        epoch = _counter(obj["release_epoch"])
        prior = next((d for d in desired if ReleaseRequest.from_json(d["request"]).graph_id == request.graph_id
                      and ReleaseRequest.from_json(d["request"]).target_id == request.target_id), None)
        expected = "0" if prior is None else prior["release_epoch"]
        if request.expected_desired_epoch != expected or int(epoch) != int(cast(str, expected)) + 1:
            raise _fail("revision_conflict")
        receipt = ReleaseReceipt.from_json({"schema": "contexture.release-receipt/v1",
            "release": _identity(request, epoch), "journal_seq": seq,
            "request_digest": request.digest(), "status": "requested"}).as_json()
        _receipt_evidence(record, request, receipt, actor)
        if prior is not None:
            desired.remove(prior)
        desired.append({"request": request.as_json(), "release_epoch": epoch})
        operations.append(receipt)
    else:
        attempt = None
        observation = None
        success = False
        if tag in ("ReleasePreparationSucceeded", "ReleasePreparationFailed"):
            success = tag == "ReleasePreparationSucceeded"
            obj = _obj(item, "type attempt generation_id manifest_digest" if success
                       else "type attempt diagnostics")
            attempt = AttemptIdentity.from_json(obj["attempt"])
            key = _key(attempt._data["release"])
        elif tag == "ReleaseSuperseded":
            obj = _obj(item, "type release superseded_by")
            key = _key(obj["release"])
        else:
            obj = _obj(item, "type observation")
            observation = Observation.from_json(obj["observation"])
            key = _key(observation._data["release"])
        prior_op = next((op for op in operations if _key(op["release"]) == key
                         and op["status"] == "requested"), None)
        if prior_op is None:
            raise _fail("journal_corrupt")
        target = next((d for d in desired if _key(_identity(
            ReleaseRequest.from_json(d["request"]), cast(str, d["release_epoch"])))[:2] == key[:2]), None)
        if tag in ("ReleasePreparationSucceeded", "ReleasePreparationFailed"):
            assert attempt is not None
            # Terminal attempts are immutable history; even a late ready cannot
            # change desired, consume resources, or imply observed serving.
            if any(a["identity"] == attempt.as_json() for a in attempts):
                raise _fail("identity_conflict")
            terminal: dict[str, object] = {"identity": attempt.as_json(), "status": "ready" if success else "failed"}
            if success:
                _digest(obj["manifest_digest"])
                _match_current(target, key, obj)
                terminal["generation_id"] = _uuid(obj["generation_id"])
            else:
                _diagnostics(obj["diagnostics"], nonempty=True)
                terminal["diagnostics"] = obj["diagnostics"]
            attempts.append(AttemptState.from_json(terminal).as_json())
        elif tag == "ReleaseSuperseded":
            if any(_key(op["release"]) == key and op["status"] == "superseded" for op in operations):
                raise _fail("identity_conflict")
            operations.append(ReleaseReceipt.from_json({"schema": "contexture.release-receipt/v1",
                "release": obj["release"], "journal_seq": seq, "request_digest": prior_op["request_digest"],
                "status": "superseded", "superseded_by": obj["superseded_by"]}).as_json())
        else:
            assert observation is not None
            _match_current(target, key, observation._data)
            observations.append(observation.as_json())
    return import_release_state(out)