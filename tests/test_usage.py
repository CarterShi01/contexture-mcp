"""Subject usage: which business objects a call touched, kept as small honest totals."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from contexture import Contexture, Principal, Role, Tool
from contexture.server import SqliteUsageLedger, compile_application
from contexture.usage import (
    InMemoryUsageLedger, LedgerTelemetry, Subjects, Use, bound_door, clean_subjects, default_actor,
)

NOW = datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)


class ReadDoc(Tool):
    subjects = Subjects.template("project://{project}")

    def __init__(self) -> None:
        super().__init__(name="read-doc", description="Read one document.", read_only=True)

    async def invoke(self, project: str, path: str = "") -> dict:
        return {"project": project, "path": path}


class CreateTask(Tool):
    subjects = Subjects.all(Subjects.template("project://{project}"), Subjects.result("ref"))

    def __init__(self) -> None:
        super().__init__(name="create-task", description="Create a Task.")

    async def invoke(self, project: str, title: str) -> dict:
        return {"ref": "work://task/7", "title": title}


class Broken(Tool):
    subjects = Subjects.template("project://{project}")

    def __init__(self) -> None:
        super().__init__(name="broken", description="Fails.")

    async def invoke(self, project: str) -> dict:
        raise RuntimeError("boom")


class Silent(Tool):
    def __init__(self) -> None:
        super().__init__(name="silent", description="Declares no subjects.", read_only=True)

    async def invoke(self) -> str:
        return "ok"


class Root(Role):
    def __init__(self) -> None:
        super().__init__(name="root", description="Usage fixture.", instructions="Read and write.",
                         tools=[ReadDoc(), CreateTask(), Broken(), Silent()])


def use(subject: str, action: str = "read", *, principal: str = "alice", actor: str = "human",
        at: datetime = NOW, failed: bool = False) -> Use:
    return Use(subjects=(subject,), action=action, door="rest", node="n", principal=principal, actor=actor,  # type: ignore[arg-type]
               at=at, failed=failed)


class SubjectsTests(unittest.TestCase):
    def test_template_result_and_cleaning(self) -> None:
        self.assertEqual(Subjects.template("project://{project}")({"project": " site "}, None), ("project://site",))
        self.assertEqual(Subjects.template("project://{project}")({"project": ""}, None), ())
        self.assertEqual(Subjects.template("doc://{project}/{path}")({"project": "a"}, None), ())
        self.assertEqual(Subjects.result("ref")({}, {"ref": ["a://1", "a://1", "a://2"]}), ("a://1", "a://2"))
        self.assertEqual(Subjects.result("ref")({}, "not a mapping"), ())
        # An extractor that raises reports nothing rather than failing the call.
        self.assertEqual(Subjects(lambda arguments, result: 1 / 0)({}, None), ())
        self.assertEqual(clean_subjects(["", " x ", "a\nb", "x" * 600, 3, "x"]), ("x",))
        self.assertEqual(len(clean_subjects(f"s://{index}" for index in range(100))), 32)

    def test_default_actor_reads_the_two_identities(self) -> None:
        self.assertEqual(default_actor(Principal(subject="alice")), "human")
        self.assertEqual(default_actor(Principal(subject="alice", client_id="claude-code")), "agent")
        self.assertEqual(default_actor(None), "system")


class LedgerTests(unittest.TestCase):
    def ledgers(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        sqlite = SqliteUsageLedger(Path(directory.name) / "usage.sqlite", batch=1000, interval=3600)
        self.addCleanup(sqlite.close)
        return (InMemoryUsageLedger(), sqlite)

    def test_repeats_within_the_window_count_once_and_keep_the_latest_time(self) -> None:
        for ledger in self.ledgers():
            ledger.add(use("project://site", at=NOW))
            ledger.add(use("project://site", at=NOW + timedelta(minutes=5)))     # same person, same object: merged
            ledger.add(use("project://site", at=NOW + timedelta(minutes=11)))    # past the window: counts
            ledger.add(use("project://site", principal="bob", at=NOW + timedelta(minutes=12)))  # another person
            ledger.add(use("project://site", "write", at=NOW + timedelta(minutes=13)))
            ledger.add(use("project://site", "write", at=NOW + timedelta(minutes=14), failed=True))
            [row] = ledger.query("project://", now=NOW + timedelta(hours=1))
            self.assertEqual(row.counts["1d"], {"read": 3, "write": 1}, type(ledger).__name__)
            self.assertEqual(row.last_read_at, (NOW + timedelta(minutes=12)).isoformat())
            self.assertEqual(row.last_write_at, (NOW + timedelta(minutes=13)).isoformat())
            self.assertEqual(row.last_actor, "human")

    def test_windows_prefix_actor_and_a_decaying_score(self) -> None:
        for ledger in self.ledgers():
            ledger.add(use("project://old", at=NOW - timedelta(days=20)))
            for offset in range(4):
                ledger.add(use("project://busy", principal=f"p{offset}", at=NOW - timedelta(days=offset)))
            ledger.add(use("project://agent", principal="claude", actor="agent", at=NOW))
            ledger.add(use("team://role/x", at=NOW))
            rows = {row.subject: row for row in ledger.query("project://", now=NOW)}
            self.assertEqual(set(rows), {"project://old", "project://busy", "project://agent"})
            busy = rows["project://busy"]
            self.assertEqual((busy.counts["1d"]["read"], busy.counts["3d"]["read"], busy.counts["7d"]["read"]), (1, 3, 4))
            self.assertEqual(rows["project://old"].counts["7d"]["read"], 0)
            self.assertEqual(rows["project://old"].counts["30d"]["read"], 1)
            # Recent and frequent outranks old; the order is by score, then subject.
            ordered = [row.subject for row in ledger.query("project://", now=NOW)]
            self.assertEqual(ordered[0], "project://busy")
            self.assertLess(rows["project://old"].score, rows["project://agent"].score)
            self.assertEqual([row.subject for row in ledger.query("project://", actor="agent", now=NOW)], ["project://agent"])

    def test_old_days_expire(self) -> None:
        ledger = InMemoryUsageLedger(retention_days=10)
        ledger.add(use("project://gone", at=NOW - timedelta(days=11)))
        ledger.add(use("project://kept", at=NOW))
        counted = {row.subject: row.counts["30d"]["read"] for row in ledger.query(now=NOW)}
        self.assertEqual(counted, {"project://gone": 0, "project://kept": 1})

    def test_sqlite_survives_a_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "usage.sqlite"
            first = SqliteUsageLedger(path)
            first.add(use("project://site", "write"))
            first.close()
            second = SqliteUsageLedger(path)
            try:
                [row] = second.query("project://", now=NOW)
                self.assertEqual(row.counts["1d"]["write"], 1)
                # Prefixes are literal: % and _ are not wildcards.
                self.assertEqual(second.query("project_", now=NOW), [])
            finally:
                second.close()


class RuntimeUsageTests(unittest.IsolatedAsyncioTestCase):
    def runtime(self, telemetry):
        return compile_application(Contexture(name="usage", roots=(Root,)), telemetry=telemetry).runtime()

    async def test_the_runtime_reports_declared_subjects_beside_node_telemetry(self) -> None:
        ledger = InMemoryUsageLedger()
        telemetry = LedgerTelemetry(ledger)
        runtime = self.runtime(telemetry)
        await runtime.invoke_read_only("root/read-doc", {"project": "site"}, principal=Principal(subject="alice"))
        await runtime.invoke("root/create-task", {"project": "site", "title": "x"},
                             principal=Principal(subject="alice", client_id="codex"))
        await runtime.invoke_read_only("root/silent")
        # The binding wraps the Tool's own exception; what matters is that the call failed.
        with self.assertRaises(Exception):
            await runtime.invoke("root/broken", {"project": "broken"}, principal=Principal(subject="alice"))
        rows = {row.subject: row for row in ledger.query(now=datetime.now(timezone.utc))}
        self.assertEqual(rows["project://site"].counts["1d"], {"read": 1, "write": 1})
        self.assertEqual(rows["project://site"].last_actor, "agent")
        self.assertEqual(rows["work://task/7"].counts["1d"]["write"], 1)
        # A failed call is not a use; a Tool without subjects reports none.
        self.assertNotIn("project://broken", {subject for subject, row in rows.items() if row.counts["1d"]["write"]})
        self.assertEqual(set(rows) - {"project://site", "work://task/7", "project://broken"}, set())
        # Node telemetry is unchanged.
        self.assertEqual(telemetry.usage("root/read-doc").call_count, 1)
        self.assertEqual(telemetry.usage("root/broken").error_count, 1)

    async def test_an_adapter_the_framework_does_not_run_records_into_the_same_ledger(self) -> None:
        ledger = InMemoryUsageLedger()
        telemetry = LedgerTelemetry(ledger)
        with bound_door("rest"):
            telemetry.record_use(subjects=["project://site"], action="read", node="GET /v1/projects",
                                 principal=Principal(subject="alice"))
        captured = []
        ledger.add = captured.append  # type: ignore[method-assign]
        telemetry.record_use(subjects=["project://site"], action="read", node="x", principal=None)
        self.assertEqual(captured[0].door, "mcp")
        self.assertEqual(captured[0].actor, "system")
        telemetry.record_use(subjects=[], action="read", node="x", principal=None)
        telemetry.record_use(subjects=["a://b"], action="delete", node="x", principal=None)  # type: ignore[arg-type]
        self.assertEqual(len(captured), 1)

    async def test_a_failing_ledger_never_changes_the_outcome(self) -> None:
        class Exploding(InMemoryUsageLedger):
            def add(self, use: Use) -> None:
                raise OSError("disk full")

        runtime = self.runtime(LedgerTelemetry(Exploding()))
        result = await runtime.invoke_read_only("root/read-doc", {"project": "site"}, principal=Principal(subject="a"))
        self.assertEqual(result, {"project": "site", "path": ""})

    async def test_plain_telemetry_ignores_subjects(self) -> None:
        result = await self.runtime(None).invoke_read_only("root/read-doc", {"project": "site"})
        self.assertEqual(result["project"], "site")


if __name__ == "__main__":
    unittest.main()
