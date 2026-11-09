"""Native Windows evidence for the strict FileJournal adapter."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from contexture.server.managed_journal import FileJournal, JournalError


pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="native Windows contract")
ROOT = Path(__file__).parents[1]
SCHEMA = json.loads((ROOT / "spec/managed/schemas/managed.schema.json").read_text())
EVENT = {"type": "CommandCompleted", "graph_id": "g", "revision": "0",
		 "command_digest": "sha256:" + "1" * 64, "status": "noop"}


def test_windows_strict_round_trip_lock_and_crash_recovery(tmp_path):
	root = tmp_path / "store"
	with FileJournal.open(root, schema=SCHEMA) as journal:
		ref = journal.put_blob(b"windows-strict", media_type="text/plain")
		assert journal.get_blob(ref) == b"windows-strict"
		journal.append([EVENT], actor_ref="actor")
		child = subprocess.run([
			sys.executable, "-B", "-c",
			"import json,sys; from pathlib import Path; "
			"from contexture.server.managed_journal import FileJournal,JournalError; "
			"schema=json.loads(Path(sys.argv[2]).read_text()); "
			"\ntry:\n FileJournal.open(sys.argv[1],schema=schema)"
			"\nexcept JournalError as error:\n print(error.code); raise SystemExit(23)",
			str(root), str(ROOT / "spec/managed/schemas/managed.schema.json"),
		], cwd=ROOT, capture_output=True, text=True, timeout=60)
		assert child.returncode == 23 and child.stdout.strip() == "store_in_use"
	with FileJournal.open(root, schema=SCHEMA) as journal:
		assert journal.watermark == "1"

	crash = tmp_path / "crash"
	with FileJournal.open(crash, schema=SCHEMA):
		pass
	child = subprocess.run([
		sys.executable, "-B", "-c",
		"import json,os,sys; from pathlib import Path; "
		"from contexture.server.managed_journal import FileJournal; "
		"schema=json.loads(Path(sys.argv[2]).read_text()); event=json.loads(sys.argv[3]); "
		"fault=lambda stage: os._exit(42) if stage=='record.renamed' else None; "
		"journal=FileJournal.open(sys.argv[1],schema=schema,fault=fault); "
		"journal.append([event],actor_ref='actor')",
		str(crash), str(ROOT / "spec/managed/schemas/managed.schema.json"), json.dumps(EVENT),
	], cwd=ROOT, capture_output=True, text=True, timeout=60)
	assert child.returncode == 42
	with FileJournal.open(crash, schema=SCHEMA) as journal:
		assert journal.watermark == "1"


def test_windows_refuses_inherited_or_foreign_allow_dacl(tmp_path):
	inherited = tmp_path / "inherited"
	inherited.mkdir()
	with pytest.raises(JournalError) as error:
		FileJournal.open(inherited, schema=SCHEMA)
	assert error.value.code == "unsafe_permissions"

	root = tmp_path / "store"
	with FileJournal.open(root, schema=SCHEMA):
		pass
	granted = subprocess.run(
		["icacls", str(root), "/grant", "*S-1-5-32-545:(OI)(CI)(RX)", "/T", "/C"],
		capture_output=True, text=True, timeout=60,
	)
	assert granted.returncode == 0, granted.stderr
	with pytest.raises(JournalError) as error:
		FileJournal.open(root, schema=SCHEMA)
	assert error.value.code == "unsafe_permissions"
