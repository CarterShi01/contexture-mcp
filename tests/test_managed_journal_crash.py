"""Real process-exit tests; these are not power-loss experiments."""

from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile

import pytest

from contexture.server.managed_journal import FileJournal

ROOT = Path(__file__).parents[1]
SCHEMA_PATH = ROOT / "spec/managed/schemas/managed.schema.json"
SCHEMA = json.loads(SCHEMA_PATH.read_text())
CHILD = r'''
import json, os, signal, sys
from pathlib import Path
from contexture.server.managed_journal import FileJournal, JournalError
root, schema_path, stage = sys.argv[1:]
schema = json.loads(Path(schema_path).read_text())
def fault(step):
	if step == stage:
		os.kill(os.getpid(), signal.SIGKILL)
try:
	with FileJournal.open(root, schema=schema, fault=fault) as journal:
		event = {"type":"CommandCompleted","graph_id":"g","revision":"0",
				 "command_digest":"sha256:"+"1"*64,"status":"noop"}
		receipt = {"schema":"contexture.command-receipt/v1","graph_id":"g",
				   "actor_ref":"actor","idempotency_key":"crash-key",
				   "request_digest":"sha256:"+"1"*64,"revision":"0","journal_seq":"1",
				   "status":"noop","durability":"strict","operation_result":{"inline":{"complete":True}}}
		journal.append([event], actor_ref="actor", command_receipt=receipt)
except JournalError as exc:
	print(exc.code, flush=True)
	sys.exit(23)
'''


@pytest.mark.parametrize("stage,published", [
	("record.temp_written", False), ("record.file_synced", False),
	("record.before_rename", False), ("record.renamed", True),
	("record.directory_synced", True),
])
def test_sigkill_exact_publication_boundaries(stage, published):
	with tempfile.TemporaryDirectory(prefix="contexture-journal-crash-") as temp:
		root = Path(temp) / "store"
		# Initialize before child so SIGKILL exercises record publication.
		with FileJournal.open(root, schema=SCHEMA): pass
		child = subprocess.run([sys.executable, "-B", "-c", CHILD, str(root), str(SCHEMA_PATH), stage],
							   cwd=ROOT, capture_output=True, text=True, timeout=60)
		assert child.returncode == -signal.SIGKILL, child.stderr
		with FileJournal.open(root, schema=SCHEMA) as owner:
			assert owner.watermark == ("1" if published else "0")
			assert (owner.get_receipt("actor", "crash-key") is not None) == published
			if published:
				assert owner.get_receipt("actor", "crash-key")["operation_result"] == {"inline": {"complete": True}}
			else:
				assert owner.recovery_actions
		assert (root / "writer.lock").exists()


def test_os_lock_excludes_a_real_second_process():
	with tempfile.TemporaryDirectory(prefix="contexture-journal-lock-") as temp:
		root = Path(temp) / "store"
		with FileJournal.open(root, schema=SCHEMA):
			child = subprocess.run([sys.executable, "-B", "-c", CHILD, str(root), str(SCHEMA_PATH), "none"],
								   cwd=ROOT, capture_output=True, text=True, timeout=60)
			assert child.returncode == 23 and child.stdout.strip() == "store_in_use", child.stderr
		# A terminated process releases the OS lock, never by deleting a PID file.
		child = subprocess.run([sys.executable, "-B", "-c", CHILD, str(root), str(SCHEMA_PATH), "record.renamed"],
							   cwd=ROOT, capture_output=True, text=True, timeout=60)
		assert child.returncode == -signal.SIGKILL, child.stderr
		with FileJournal.open(root, schema=SCHEMA) as owner:
			assert owner.watermark == "1"


def test_windows_mount_refused_without_creating_store():
	if not sys.platform.startswith("linux") or not Path("/mnt/c").is_dir():
		pytest.skip("Specific WSL drvfs boundary evidence")
	from contexture.server.managed_journal import JournalError
	target = Path("/mnt/c/Users/kexingshi/AppData/Local/Temp") / ("unsupported-journal-" + str(os.getpid()))
	with pytest.raises(JournalError) as error:
		FileJournal.open(target, schema=SCHEMA)
	assert error.value.code == "durability_unsupported"
	assert not target.exists()