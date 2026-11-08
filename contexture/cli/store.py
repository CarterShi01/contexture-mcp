"""Explicit offline administrator commands; never create or recover the source."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import errno
from pathlib import Path
import sys
from typing import NoReturn, Sequence

from ..application.managed_management import encode_result
from ..server.managed_journal import JournalError, managed_schema
from ..server.managed_store_ops import (
    StoreBudget, StoreOperations, StoreResult, load_schema, publish_backup, read_artifact,
)


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise JournalError("schema_invalid", "Invalid offline store command")


def main(argv: Sequence[str]) -> int:
    parser = _Parser(prog="contexture store", description=__doc__)
    parser.add_argument("command", choices=("doctor", "backup", "restore"))
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--schema", type=Path,
                        help="trusted local schema (default: the schema shipped with contexture)")
    parser.add_argument("--max-store-mib", type=int, default=StoreBudget.max_bytes // (1024 * 1024),
                        help="refuse stores larger than this (admission bound, not capacity)")
    parser.add_argument("--max-files", type=int, default=StoreBudget.max_files)
    parser.add_argument("--isolated", action="store_true",
                        help="doctor: also verify an isolated restore (needs ~2-3x store size)")
    parser.add_argument("--no-semantic", action="store_true",
                        help="doctor: skip the graph-semantics replay")
    parser.add_argument("--durability", choices=("strict", "process_crash"), default="strict")
    parser.add_argument("--input", type=Path, help="restore backup artifact")
    parser.add_argument("--output", type=Path, help="new private backup artifact")
    parser.add_argument("--allow-write", action="store_true", help="explicit restore confirmation")
    try:
        args = parser.parse_args(list(argv))
        if ((args.command == "backup") != (args.output is not None)
                or (args.command == "restore") != (args.input is not None)
                or (args.command == "restore" and not args.allow_write)):
            raise JournalError("schema_invalid", "Explicit artifact and restore profile required")
        budget = StoreBudget(max_files=args.max_files, max_bytes=args.max_store_mib * 1024 * 1024,
                             max_snapshot_bytes=max(StoreBudget.max_snapshot_bytes,
                                                    3 * args.max_store_mib * 1024 * 1024))
        schema = managed_schema() if args.schema is None else load_schema(args.schema)
        operations = StoreOperations(schema=schema, durability=args.durability, budget=budget)
        extra: dict[str, object] = {}
        if args.command == "doctor":
            result = operations.doctor(args.root, isolated=args.isolated, semantic=not args.no_semantic)
        elif args.command == "backup":
            artifact = operations.backup(args.root)
            publish_backup(args.output, artifact, durability=args.durability)
            result = artifact.result
            extra["manifest"] = dict(artifact.manifest)
        else:
            snapshot = read_artifact(args.input, StoreBudget().max_snapshot_bytes)
            result = operations.restore(args.root, snapshot)
        wire = {"schema": "contexture.store-result/v1", "result": asdict(result), **extra}
        target = sys.stdout if result.code == "ok" else sys.stderr
        target.write(encode_result(wire).decode("utf-8") + "\n")
        return 0 if result.code == "ok" else 1
    except (JournalError, OSError, ValueError) as exc:
        if isinstance(exc, OSError):
            code = {errno.EEXIST: "restore_conflict", errno.ENOENT: "store_missing",
                    errno.EACCES: "permission_denied", errno.ENOSPC: "disk_full"}.get(
                        exc.errno if exc.errno is not None else -1, "storage_error")
        else:
            code = exc.code if isinstance(exc, JournalError) else "schema_invalid"
        status = 2 if code in {"schema_invalid", "limit_exceeded"} else 1
        wire = {"schema": "contexture.store-result/v1",
                "result": asdict(StoreResult("offline", code))}
        sys.stderr.write(encode_result(wire).decode("utf-8") + "\n")
        return status
