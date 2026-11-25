"""Usage of business subjects: which objects a call touched, by whom, when.

`Telemetry` answers a question about the *graph*: how often was this Tool
called, did it fail. An application usually needs the same question asked about
its own *objects* — which projects were read this week, which document was
changed yesterday, which member nobody has opened in a month. The framework
cannot know what an object is, but every Tool can say which ones one call
touched. This module turns that into a small durable ledger:

* a Tool declares its subjects (`Subjects`) as a class attribute — not a node
  field, so declaring one changes no disclosure payload and no graph digest;
* the runtime reports one `Use` after every invocation, beside `Telemetry`,
  and never lets a reporting failure change the outcome of the call;
* an inbound adapter the framework does not run (an application's own HTTP
  handlers over the same module APIs) reports through the same `record_use`
  with the same vocabulary, so both doors land in one ledger;
* repeated touches of one subject by one principal within a short window count
  once, so polling and refreshing measure nothing;
* the ledger keeps per-day totals only (durable one: `contexture.server.SqliteUsageLedger`) — never arguments or results — and is
  queried by subject prefix with windows and a decaying score.

What a score *means* is the application's business. The framework only keeps
the evidence honest and cheap.
"""

from __future__ import annotations

import math
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Iterator, Literal, Mapping, Protocol

Action = Literal["read", "write"]
#: Who acted, as far as usage is concerned. A person at a client is `human`;
#: an application connected on a person's behalf is `agent`; work the system
#: does by itself is `system`.
Actor = Literal["human", "agent", "system"]

#: At most this many subjects are kept from one call; more is a listing, not a use.
MAX_SUBJECTS = 32
_MAX_SUBJECT = 512


def _utc(moment: datetime | None = None) -> datetime:
    value = moment or datetime.now(timezone.utc)
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def clean_subjects(values: Iterable[Any]) -> tuple[str, ...]:
    """Distinct, non-empty, bounded subject strings, in first-seen order."""

    seen: dict[str, None] = {}
    for value in values:
        if not isinstance(value, str):
            continue
        text = value.strip()
        if not text or len(text) > _MAX_SUBJECT or any(ch in text for ch in "\r\n\t"):
            continue
        seen.setdefault(text, None)
        if len(seen) >= MAX_SUBJECTS:
            break
    return tuple(seen)


class Subjects:
    """How a Tool names the business objects one call touched.

    Declared as a class attribute of the Tool::

        class ReadResource(Tool):
            subjects = Subjects.template("project://{project}")

    It is evaluated after the call, so it can read both the arguments the model
    passed and the result the Tool returned. Anything it cannot resolve is
    simply not reported; it never raises into the call.
    """

    __slots__ = ("_extract",)

    def __init__(self, extract: Callable[[Mapping[str, Any], Any], Iterable[Any]]) -> None:
        if not callable(extract):
            raise TypeError("Subjects needs a callable (arguments, result) -> subjects")
        self._extract = extract

    def __call__(self, arguments: Mapping[str, Any] | None, result: Any) -> tuple[str, ...]:
        try:
            return clean_subjects(self._extract(arguments or {}, result) or ())
        except Exception:
            return ()

    @classmethod
    def template(cls, pattern: str) -> "Subjects":
        """Fill ``{name}`` placeholders from the arguments; nothing when one is missing."""

        names = [part.split("}", 1)[0] for part in pattern.split("{")[1:]]

        def extract(arguments: Mapping[str, Any], _result: Any) -> Iterable[str]:
            values = {}
            for name in names:
                value = arguments.get(name)
                if value is None or (isinstance(value, str) and not value.strip()):
                    return ()
                values[name] = str(value).strip()
            return (pattern.format(**values),)

        return cls(extract)

    @classmethod
    def result(cls, key: str = "ref") -> "Subjects":
        """The subject(s) the result names under ``key`` (a string or a list of strings)."""

        def extract(_arguments: Mapping[str, Any], result: Any) -> Iterable[Any]:
            value = result.get(key) if isinstance(result, Mapping) else getattr(result, key, None)
            if isinstance(value, str):
                return (value,)
            if isinstance(value, (list, tuple)):
                return value
            return ()

        return cls(extract)

    @classmethod
    def all(cls, *parts: "Subjects") -> "Subjects":
        """Every subject any of the parts names."""

        def extract(arguments: Mapping[str, Any], result: Any) -> Iterable[str]:
            for part in parts:
                yield from part(arguments, result)

        return cls(extract)


@dataclass(frozen=True, slots=True)
class Use:
    """One touch of some subjects, as the ledger records it (no arguments, no result)."""

    subjects: tuple[str, ...]
    action: Action
    door: str
    node: str
    principal: str
    actor: Actor
    at: datetime
    failed: bool = False


@dataclass(frozen=True, slots=True)
class SubjectUsage:
    """What the ledger knows about one subject."""

    subject: str
    last_read_at: str | None
    last_write_at: str | None
    last_actor: str | None
    #: ``{"7d": {"read": 3, "write": 1}, ...}`` per requested window.
    counts: Mapping[str, Mapping[str, int]]
    #: Σ uses × weight × 0.5^(age in days / half-life); only for ordering.
    score: float

    def as_dict(self) -> dict[str, object]:
        return {
            "subject": self.subject, "last_read_at": self.last_read_at, "last_write_at": self.last_write_at,
            "last_actor": self.last_actor, "counts": {key: dict(value) for key, value in self.counts.items()},
            "score": round(self.score, 4),
        }


class UsageLedger(Protocol):
    """Where uses are kept. Implementations must be safe to call from any thread."""

    def add(self, use: Use) -> None: ...

    def query(
        self, prefix: str = "", *, windows: tuple[int, ...] = (1, 3, 7, 30), actor: str | None = None,
        half_life_days: float = 3.0, write_weight: float = 2.0, now: datetime | None = None,
    ) -> list[SubjectUsage]: ...

    def flush(self) -> None: ...


@dataclass
class _Totals:
    """In-memory tables shared by both implementations (the SQLite one persists them)."""

    #: (subject, actor, action, day) -> [count, failed]
    daily: dict[tuple[str, str, str, str], list[int]] = field(default_factory=dict)
    #: (subject, actor, action) -> (at, principal)
    last: dict[tuple[str, str, str], tuple[str, str]] = field(default_factory=dict)


class _Coalescer:
    """Counts a principal's repeated touch of a subject once per window."""

    def __init__(self, window: timedelta) -> None:
        self.window = window
        self._counted: dict[tuple[str, str, str], datetime] = {}

    def counts(self, principal: str, subject: str, action: str, at: datetime) -> bool:
        key = (principal, subject, action)
        previous = self._counted.get(key)
        if previous is not None and timedelta(0) <= at - previous < self.window:
            return False
        self._counted[key] = at
        if len(self._counted) > 50_000:
            # Forget the oldest half; at worst a repeat counts once more.
            for stale in sorted(self._counted, key=self._counted.__getitem__)[:25_000]:
                del self._counted[stale]
        return True


def _score_rows(rows: Iterable[tuple[str, str, str, str, int]], last: Mapping[tuple[str, str, str], tuple[str, str]],
                prefix: str, windows: tuple[int, ...], actor: str | None, half_life_days: float,
                write_weight: float, now: datetime) -> list[SubjectUsage]:
    today = _utc(now).date()
    counts: dict[str, dict[str, dict[str, int]]] = {}
    scores: dict[str, float] = {}
    half_life = max(half_life_days, 0.01)
    for subject, row_actor, action, day, count in rows:
        if prefix and not subject.startswith(prefix):
            continue
        if actor and row_actor != actor:
            continue
        try:
            age = (today - date.fromisoformat(day)).days
        except ValueError:
            continue
        if age < 0:
            age = 0
        per = counts.setdefault(subject, {f"{days}d": {"read": 0, "write": 0} for days in windows})
        for days in windows:
            if age < days:
                per[f"{days}d"][action] = per[f"{days}d"].get(action, 0) + count
        weight = write_weight if action == "write" else 1.0
        scores[subject] = scores.get(subject, 0.0) + count * weight * math.pow(0.5, age / half_life)
    subjects = set(counts)
    for (subject, row_actor, _action) in last:
        if (not prefix or subject.startswith(prefix)) and (not actor or row_actor == actor):
            subjects.add(subject)
    result = []
    for subject in subjects:
        reads = [last[key] for key in last if key[0] == subject and key[2] == "read" and (not actor or key[1] == actor)]
        writes = [last[key] for key in last if key[0] == subject and key[2] == "write" and (not actor or key[1] == actor)]
        newest = max((value for value in (*reads, *writes)), default=None, key=lambda value: value[0])
        newest_actor = None
        if newest is not None:
            newest_actor = next((key[1] for key, value in last.items() if key[0] == subject and value == newest), None)
        result.append(SubjectUsage(
            subject=subject,
            last_read_at=max((value[0] for value in reads), default=None),
            last_write_at=max((value[0] for value in writes), default=None),
            last_actor=newest_actor,
            counts=counts.get(subject, {f"{days}d": {"read": 0, "write": 0} for days in windows}),
            score=scores.get(subject, 0.0),
        ))
    result.sort(key=lambda item: (-item.score, item.subject))
    return result


class InMemoryUsageLedger:
    """A process-local ledger: the framework default and the test double."""

    def __init__(self, *, coalesce_minutes: float = 10.0, retention_days: int = 90) -> None:
        self._totals = _Totals()
        self._coalescer = _Coalescer(timedelta(minutes=coalesce_minutes))
        self._retention = retention_days
        self._lock = threading.Lock()

    def add(self, use: Use) -> None:
        at = _utc(use.at)
        stamp, day = at.isoformat(), at.date().isoformat()
        with self._lock:
            for subject in use.subjects:
                key = (subject, use.actor, use.action)
                if use.failed:
                    self._totals.daily.setdefault((*key, day), [0, 0])[1] += 1
                    continue
                previous = self._totals.last.get(key)
                if previous is None or previous[0] <= stamp:
                    self._totals.last[key] = (stamp, use.principal)
                if self._coalescer.counts(use.principal, subject, use.action, at):
                    self._totals.daily.setdefault((*key, day), [0, 0])[0] += 1
            self._expire(at.date())

    def _expire(self, today: date) -> None:
        cutoff = (today - timedelta(days=self._retention)).isoformat()
        for key in [key for key in self._totals.daily if key[3] < cutoff]:
            del self._totals.daily[key]

    def query(self, prefix: str = "", *, windows: tuple[int, ...] = (1, 3, 7, 30), actor: str | None = None,
              half_life_days: float = 3.0, write_weight: float = 2.0, now: datetime | None = None) -> list[SubjectUsage]:
        with self._lock:
            rows = [(subject, row_actor, action, day, values[0])
                    for (subject, row_actor, action, day), values in self._totals.daily.items() if values[0]]
            last = dict(self._totals.last)
        return _score_rows(rows, last, prefix, windows, actor, half_life_days, write_weight, now or _utc())

    def flush(self) -> None:
        return None


def default_actor(principal: Any) -> Actor:
    """`agent` when an application connected for someone, `human` for a person, else `system`.

    Uses the two identities a `Principal` carries: `client_id` is the
    application that connected, `subject` the person it acts for. Applications
    with their own notion (a session kind in the claims) pass `actor_of`.
    """

    if principal is None:
        return "system"
    if getattr(principal, "client_id", None):
        return "agent"
    return "human" if getattr(principal, "subject", None) else "system"


def _principal_id(principal: Any) -> str:
    if principal is None:
        return ""
    return str(getattr(principal, "subject", None) or getattr(principal, "client_id", None) or "")


_DOOR: ContextVar[str] = ContextVar("contexture_usage_door", default="mcp")


@contextmanager
def bound_door(door: str) -> Iterator[None]:
    """Name the inbound door for uses reported inside (default ``mcp``)."""

    token = _DOOR.set(door)
    try:
        yield
    finally:
        _DOOR.reset(token)


class LedgerTelemetry:
    """Telemetry that also keeps a usage ledger.

    Hand it to an application wherever a `Telemetry` is accepted. Node-level
    telemetry goes to `inner` unchanged; subject-level uses go to `ledger`.
    """

    def __init__(self, ledger: UsageLedger, *, inner: Any = None,
                 actor_of: Callable[[Any], Actor] = default_actor) -> None:
        from .telemetry import InMemoryTelemetry
        self.ledger = ledger
        self.inner = inner if inner is not None else InMemoryTelemetry()
        self.actor_of = actor_of

    # -- Telemetry -------------------------------------------------------------------
    def record(self, ref: str, *, failed: bool = False) -> None:
        self.inner.record(ref, failed=failed)

    def usage(self, ref: str) -> Any:
        return self.inner.usage(ref)

    def record_inspection(self, ref: str) -> None:
        recorder = getattr(self.inner, "record_inspection", None)
        if callable(recorder):
            recorder(ref)

    def inspection_usage(self, ref: str) -> Any:
        return self.inner.inspection_usage(ref)

    # -- usage -----------------------------------------------------------------------
    def record_use(self, *, subjects: Iterable[str], action: Action, node: str, principal: Any,
                   door: str | None = None, failed: bool = False, at: datetime | None = None) -> None:
        """Record one touch; used by the runtime and by any inbound adapter it does not run."""

        cleaned = clean_subjects(subjects)
        if not cleaned or action not in ("read", "write"):
            return
        self.ledger.add(Use(subjects=cleaned, action=action, door=door or _DOOR.get(), node=node,
                            principal=_principal_id(principal), actor=self.actor_of(principal),
                            at=_utc(at), failed=failed))


def report_use(telemetry: Any, tool: Any, ref: str, arguments: Mapping[str, Any] | None, result: Any, *,
               read_only: bool, principal: Any, failed: bool = False) -> None:
    """Report the subjects of one invocation, if the Tool declares them and telemetry keeps a ledger."""

    try:
        recorder = getattr(telemetry, "record_use", None)
        declared = getattr(type(tool), "subjects", None)
        if not callable(recorder) or not isinstance(declared, Subjects):
            return
        subjects = declared(arguments, None if failed else result)
        if subjects:
            recorder(subjects=subjects, action="read" if read_only else "write", node=ref,
                     principal=principal, failed=failed)
    except Exception:
        # Usage is evidence about a call, never a dependency of it.
        return


__all__ = [
    "Action", "Actor", "InMemoryUsageLedger", "LedgerTelemetry", "SubjectUsage", "Subjects",
    "Use", "UsageLedger", "bound_door", "clean_subjects", "default_actor", "report_use",
]
