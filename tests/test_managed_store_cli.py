"""Offline CLI journeys use real subprocesses and native store ownership."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile

import pytest

from contexture.server.managed_journal import FileJournal
from tests.test_managed_journal import ROOT, SCHEMA, event, receipt


@pytest.fixture
def paths():
    with tempfile.TemporaryDirectory(prefix="contexture-store-cli-") as directory:
        parent = Path(directory)
        root = parent / "source"
        with FileJournal.open(root, schema=SCHEMA) as owner:
            owner.append([event()], actor_ref="actor", command_receipt=receipt())
        yield root, parent / "backup.json", parent / "restored"


def run(command, root, *args):
    result = subprocess.run([sys.executable, "-B", "-m", "contexture.cli", "store",
        command, "--root", str(root), "--schema", str(ROOT / "spec/managed/schemas/managed.schema.json"),
        *map(str, args)], cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert "Traceback" not in result.stderr
    return result


def test_real_doctor_backup_restore_journey_and_nonoverwrite(paths):
    root, backup, restored = paths
    doctor = run("doctor", root)
    assert doctor.returncode == 0
    result = json.loads(doctor.stdout)["result"]
    assert result["storage_verified"] and not result["serving_ready"] and not result["write_enabled"]
    captured = run("backup", root, "--output", backup)
    assert captured.returncode == 0 and backup.exists()
    assert "events" not in captured.stdout and "actor_ref" not in captured.stdout
    before = backup.read_bytes()
    assert run("backup", root, "--output", backup).returncode == 1
    assert backup.read_bytes() == before
    assert not list(backup.parent.glob(".contexture-backup-*"))
    refused = run("restore", restored, "--input", backup)
    assert refused.returncode == 2 and not restored.exists()
    restored_result = run("restore", restored, "--input", backup, "--allow-write")
    assert restored_result.returncode == 0
    with FileJournal.open(restored, schema=SCHEMA) as owner:
        assert owner.get_receipt("actor", "key") == receipt()
    assert run("restore", restored, "--input", backup, "--allow-write").returncode == 1


def test_live_owner_and_invalid_input_are_explicit_and_redacted(paths):
    root, backup, _ = paths
    with FileJournal.open(root, schema=SCHEMA):
        result = run("doctor", root)
        assert result.returncode == 1 and not result.stdout
        assert json.loads(result.stderr)["result"]["code"] == "store_in_use"
    secret = "NEVER-ECHO-store-credential"
    result = run("doctor", root, "--unknown", secret)
    assert result.returncode == 2 and secret not in result.stdout + result.stderr
    assert not backup.exists()


def test_missing_source_is_not_created_and_backup_requires_output(paths):
    root, _, restored = paths
    assert run("doctor", restored).returncode == 1 and not restored.exists()
    assert run("backup", root).returncode == 2


@pytest.mark.parametrize("schema", [{}, {"$defs": {}}, {"$defs": [], "secret": "NEVER-ECHO-schema"}])
def test_invalid_registered_schema_is_redacted_before_any_operation(paths, schema):
    root, _, _ = paths
    declared = root.parent / "invalid-schema.json"
    declared.write_text(json.dumps(schema))
    result = run("doctor", root, "--schema", declared)
    assert result.returncode == 2 and not result.stdout
    assert "NEVER-ECHO-schema" not in result.stderr
    assert json.loads(result.stderr)["result"]["code"] == "schema_invalid"


def test_invalid_backup_is_redacted_and_never_creates_destination(paths):
    root, backup, restored = paths
    backup.write_text('{"secret":"NEVER-ECHO-backup",not-json}')
    result = run("restore", restored, "--input", backup, "--allow-write")
    assert result.returncode == 1 and not restored.exists()
    assert "NEVER-ECHO-backup" not in result.stderr
    assert json.loads(result.stderr)["result"]["code"] == "backup_invalid"
