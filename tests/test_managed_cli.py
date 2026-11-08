"""Actual CLI subprocesses with a trusted lifecycle and strict native journal."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

import pytest

from tests.test_managed_owner import LATER, SCHEMA, Host, boot, owner
from contexture.server.managed_journal import FileJournal

ROOT = Path(__file__).parents[1]
SECRET = "NEVER-ECHO-super-secret-token"
HOST = '''from contextlib import contextmanager
from pathlib import Path
from contexture.application.managed_management import ManagedManagementService
from contexture.server.managed_journal import FileJournal
from tests.test_managed_owner import SCHEMA, Host, owner
from dataclasses import replace

@contextmanager
def management():
    root = Path(__file__).parent
    print("NEVER-ECHO-super-secret-token")
    mode = (root / "mode").read_text()
    if mode == "fail":
        raise RuntimeError("NEVER-ECHO-super-secret-token")
    with FileJournal.open(root / "store", schema=SCHEMA) as journal:
        host = Host()
        host.denied = mode == "deny"
        manager = owner(journal, host)
        service = ManagedManagementService(manager, mode="read_only" if mode == "read_only" else "write")
        try:
            yield service
        finally:
            service.close()
            manager.close()
            (root / "closed").write_text("closed")
'''


@pytest.fixture
def project():
    with tempfile.TemporaryDirectory(prefix="contexture-managed-cli-") as temp:
        root = Path(temp)
        (root / "pyproject.toml").write_text('[tool.contexture.managed]\nfactory = "trusted_management_fixture:management"\n')
        (root / "trusted_management_fixture.py").write_text(HOST)
        (root / "mode").write_text("write")
        with FileJournal.open(root / "store", schema=SCHEMA) as journal:
            manager = owner(journal, Host())
            boot(manager)
            manager.close()
        yield root


def command(project, *args):
    return [sys.executable, "-B", "-m", "contexture.cli", "managed", "--project", str(project), *args]


def run(project, *args, raw=None):
    result = subprocess.run(command(project, *args), cwd=ROOT, input=raw,
                            capture_output=True, text=True, timeout=30)
    assert SECRET not in result.stdout + result.stderr
    assert "Traceback" not in result.stderr
    return result


def transaction(name="renamed", key="cli"):
    return {"schema": "contexture.transaction/v1", "graph_id": "g", "base_revision": "1",
        "idempotency_key": key, "commands": [{"op": "rename_node", "node_id": "r", "new_name": name}],
        "reason": "CLI review", "correlation_id": "trace"}


def test_real_cli_session_exact_preview_commit_retry_revert(project):
    with subprocess.Popen(command(project, "--allow-write", "session"), cwd=ROOT,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1) as process:
        def send(command, arguments):
            process.stdin.write(json.dumps({"command": command, "arguments": arguments}) + "\n")
            process.stdin.flush()
            line = process.stdout.readline()
            assert SECRET not in line
            return json.loads(line)
        proposal = send("preview", {"transaction": transaction(), "expires_at": LATER})
        assert proposal["stage"] == "preview"
        args = {"preview_id": proposal["result"]["preview_id"]}
        accepted = send("commit", args)
        assert accepted["result"]["revision"] == "2"
        assert send("commit", args) == accepted
        assert send("receipt", {"graph_id": "g", "idempotency_key": "cli"})["result"] == accepted["result"]
        assert send("head", {"graph_id": "g"})["result"]["definition"]["nodes"][0]["name"] == "renamed"
        assert send("history", {"graph_id": "g"})["result"]["complete"]
        assert send("diff", {"graph_id": "g", "before": "1", "after": "2"})["result"]["nodes"]
        reverted = send("revert", {"graph_id": "g", "target_revision": "1", "idempotency_key": "revert",
            "reason": "restore", "correlation_id": "trace", "expires_at": LATER})
        assert send("commit", {"preview_id": reverted["result"]["preview_id"]})["result"]["revision"] == "3"
        process.stdin.close()
        assert process.wait(timeout=30) == 0
        assert process.stderr.read() == ""
    assert (project / "closed").read_text() == "closed"
    # Separate actual process replays committed facts, not an in-memory fallback.
    result = run(project, "head", "--input", '{"graph_id":"g"}')
    assert result.returncode == 0 and json.loads(result.stdout)["result"]["revision"] == "3"


@pytest.mark.parametrize("mode", ["deny", "fail", "read_only"])
def test_cli_host_authority_and_diagnostics_are_not_payload_credentials(project, mode):
    (project / "mode").write_text(mode)
    args = ["head", "--input", '{"graph_id":"g"}'] if mode != "read_only" else [
        "--allow-write", "preview", "--input", json.dumps({"transaction": transaction(), "expires_at": LATER})]
    result = run(project, *args)
    assert result.returncode == 1 and not result.stdout
    assert json.loads(result.stderr)["error"]["code"] in {"permission_denied", "management_failed"}


@pytest.mark.parametrize("raw", ['{"graph_id":"g","graph_id":"g"}',
    '{"graph_id":"g","actor_ref":"NEVER-ECHO-super-secret-token"}',
    '{"n":9007199254740993}', '{"n":NaN}', '[1]', '{invalid'])
def test_cli_strict_json_no_identity_echo(project, raw):
    result = run(project, "head", raw=raw)
    assert result.returncode == 2 and not result.stdout
    assert json.loads(result.stderr)["error"]["code"] == "schema_invalid"


def test_cli_no_arbitrary_factory_or_implicit_owner_and_exclusive_lock(project):
    result = run(project, "head", "--factory", SECRET)
    assert result.returncode == 2 and not result.stdout
    with FileJournal.open(project / "store", schema=SCHEMA):
        result = run(project, "head", "--input", '{"graph_id":"g"}')
        assert result.returncode == 1
        assert json.loads(result.stderr)["error"]["code"] in {"store_in_use", "management_failed"}
    missing = project / "missing"
    result = run(missing, "head", "--input", '{"graph_id":"g"}')
    assert result.returncode == 1 and not missing.exists()


def test_write_confirmation_before_factory_and_local_handles_do_not_cross_process(project):
    result = run(project, "preview", "--input", json.dumps({"transaction": transaction(), "expires_at": LATER}))
    assert result.returncode == 2 and not (project / "closed").exists()
    proposal = run(project, "--allow-write", "preview", "--input",
        json.dumps({"transaction": transaction(), "expires_at": LATER}))
    assert proposal.returncode == 0
    handle = json.loads(proposal.stdout)["result"]["preview_id"]
    result = run(project, "--allow-write", "commit", "--input", json.dumps({"preview_id": handle}))
    assert result.returncode == 1 and json.loads(result.stderr)["error"]["code"] == "approval_stale"