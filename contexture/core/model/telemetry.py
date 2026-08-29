"""Framework-owned usage telemetry for compiled Contexture nodes.

Telemetry is deliberately beside disclosure, never inside its payload.  The
runtime records when an agent enters a Role or Skill and when it invokes a
Tool; business code does not implement a health protocol on the base node.
Concrete health, connection and queue state remain ordinary business Tools.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from typing import Iterator, Protocol


@dataclass(frozen=True, slots=True)
class NodeUsage:
    """The intentionally small aggregate the framework keeps for one node."""

    ref: str
    call_count: int = 0
    error_count: int = 0
    last_used_at: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "ref": self.ref,
            "call_count": self.call_count,
            "error_count": self.error_count,
            "last_used_at": self.last_used_at,
        }


class Telemetry(Protocol):
    """The replaceable side channel a runtime reports node usage to."""

    def record(self, ref: str, *, failed: bool = False) -> None: ...

    def usage(self, ref: str) -> NodeUsage: ...


class InMemoryTelemetry:
    """A process-local collector suitable as the framework default."""

    def __init__(self) -> None:
        self._records: dict[str, NodeUsage] = {}
        self._lock = Lock()

    def record(self, ref: str, *, failed: bool = False) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            previous = self._records.get(ref, NodeUsage(ref=ref))
            self._records[ref] = NodeUsage(
                ref=ref,
                call_count=previous.call_count + 1,
                error_count=previous.error_count + int(failed),
                last_used_at=now,
            )

    def usage(self, ref: str) -> NodeUsage:
        with self._lock:
            return self._records.get(ref, NodeUsage(ref=ref))


def report(telemetry: Telemetry, ref: str, *, failed: bool = False) -> None:
    """Report without ever changing the outcome of the observed operation."""

    try:
        telemetry.record(ref, failed=failed)
    except Exception:
        # Telemetry is evidence about a call, not a dependency of that call.
        # A failing exporter must never turn successful business work into an
        # error or replace the business exception that was already raised.
        return


_CURRENT_TELEMETRY: ContextVar[Telemetry | None] = ContextVar(
    "contexture_runtime_telemetry", default=None
)


def current_telemetry() -> Telemetry:
    """Return the telemetry collector serving the current Tool invocation."""

    telemetry = _CURRENT_TELEMETRY.get()
    if telemetry is None:
        raise RuntimeError(
            "No Contexture telemetry is active. Use current_telemetry() only "
            "inside Tool.invoke()."
        )
    return telemetry


@contextmanager
def bound_telemetry(telemetry: Telemetry) -> Iterator[None]:
    token = _CURRENT_TELEMETRY.set(telemetry)
    try:
        yield
    finally:
        _CURRENT_TELEMETRY.reset(token)


__all__ = [
    "InMemoryTelemetry",
    "NodeUsage",
    "Telemetry",
    "bound_telemetry",
    "current_telemetry",
    "report",
]
