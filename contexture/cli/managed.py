"""Explicit managed CLI candidate; trusted project composition, no offline owner.

Configure [tool.contexture.managed] factory = "host.module:management". This
trusted zero-argument factory returns a synchronous context manager yielding a
ManagedManagementService. It connects to the live owner or owns the exclusive
FileJournal lifecycle with the service stopped; the CLI never chooses a store.
No factory/import/identity/authority is accepted in command JSON. Use session
for JSON-lines preview then commit in the same Host lifecycle. One-shot preview
is review-only: its local handle intentionally cannot cross process lifetimes.
"""

from __future__ import annotations

import argparse
from contextlib import AbstractContextManager, redirect_stderr, redirect_stdout
import io
from pathlib import Path
import sys
import tomllib
from typing import NoReturn, Sequence, cast

from ..application.managed_management import (
    COMMANDS, WRITE_COMMANDS, ManagedManagementService, decode_request, encode_result,
)
from .project import _declared

MAX_INPUT = 16 * 1024 * 1024
_CODES = frozenset({"schema_invalid", "semantic_unsupported", "unsupported_contract",
    "identity_conflict", "slot_invalid", "cycle", "dangling_reference", "mutation_protected",
    "permission_denied", "approval_stale", "revision_conflict", "idempotency_conflict",
    "commit_unknown", "store_in_use", "journal_corrupt", "durability_unsupported",
    "binding_unavailable", "contract_mismatch", "prepare_failed", "release_superseded",
    "generation_unavailable", "stale_reference", "cursor_invalid", "resync_required",
    "history_unavailable", "resource_busy", "too_large", "limit_exceeded", "not_ready"})


class _Usage(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        # argparse's default echoes arbitrary argv (possibly credentials).
        raise _Usage()


class _Discard(io.TextIOBase):
    """Discard Host diagnostics without retaining secrets or unbounded logs."""

    def write(self, text: str) -> int:
        return len(text)


def _failure(exc: Exception) -> tuple[dict[str, object], int]:
    code = getattr(exc, "code", None)
    code = code if isinstance(code, str) and code in _CODES else "management_failed"
    if isinstance(exc, _Usage):
        code = "schema_invalid"
    status = 2 if code in {"schema_invalid", "unsupported_contract", "limit_exceeded", "too_large"} else 1
    error: dict[str, object] = {"code": code,
                                "message": "Managed command refused; consult trusted Host diagnostics."}
    if (code != "management_failed" and not isinstance(exc, _Usage)
            and type(exc).__module__.startswith("contexture.")):
        # Framework-authored, bounded text only (never Host exception text).
        # Rule diagnostics name nodes the caller was already admitted to read:
        # every transaction preview first requires full-graph read admission.
        error["message"] = str(exc)
        to_dict = getattr(exc, "to_dict", None)
        if getattr(exc, "diagnostics", None) and callable(to_dict):
            error["diagnostics"] = cast(dict[str, object], to_dict())["diagnostics"]
    return {"schema": "contexture.management-error/v1", "error": error}, status


def _factory(project: Path) -> AbstractContextManager[ManagedManagementService]:
    project = project.resolve(strict=True)
    with (project / "pyproject.toml").open("rb") as stream:
        config = tomllib.load(stream)["tool"]["contexture"]["managed"]
    if not isinstance(config, dict) or set(config) != {"factory"} or not isinstance(config["factory"], str):
        raise _Usage()
    if str(project) not in sys.path:
        sys.path.append(str(project))
    factory = _declared(config["factory"], project=project, key="managed.factory",
                        shape="host.module:management")
    if not callable(factory):
        raise _Usage()
    lifecycle = factory()
    if not isinstance(lifecycle, AbstractContextManager):
        raise _Usage()
    return lifecycle


def _execute(service: ManagedManagementService, command: str, arguments: object,
             allow_write: bool) -> dict:
    if command in WRITE_COMMANDS and not allow_write:
        # This is intent confirmation, never a substitute for Host admission.
        raise _Usage()
    return service.execute(command, arguments)


def main(argv: Sequence[str]) -> int:
    """JSON stdout, redacted JSON stderr and bounded JSON-lines sessions.

    Exit 0: operation completed at its stated stage; 2: invalid input/profile;
    1: authority/conflict/storage/Host failure. A session emits one result or
    error per input on stdout and exits nonzero if any command failed. Startup,
    parse and shutdown errors use stderr. No traceback or exception text crosses
    this adapter. Host diagnostics need an independent trusted logging sink.
    """
    parser = _Parser(prog="contexture managed", description=__doc__)
    parser.add_argument("--project", required=True, type=Path,
                        help="explicit trusted project declaring managed.factory")
    parser.add_argument("--allow-write", action="store_true")
    parser.add_argument("command", choices=sorted(COMMANDS | {"session"}))
    parser.add_argument("--input", help="strict JSON object; defaults to bounded stdin")
    output, errors = sys.stdout, sys.stderr
    status, encoded = 0, ""
    try:
        args = parser.parse_args(list(argv))
        if args.command == "session" and args.input is not None:
            raise _Usage()
        if args.command in WRITE_COMMANDS and not args.allow_write:
            raise _Usage()
        # Capture no Host print/exception repr, including imports and cleanup.
        with redirect_stdout(_Discard()), redirect_stderr(_Discard()):
            with _factory(args.project) as service:
                if not isinstance(service, ManagedManagementService):
                    raise _Usage()
                if args.command != "session":
                    raw = args.input.encode("utf-8") if args.input is not None else sys.stdin.buffer.read(MAX_INPUT + 1)
                    result = _execute(service, args.command, decode_request(raw), args.allow_write)
                    encoded = encode_result(result).decode("utf-8")
                    # Publish only after successful Host lifecycle cleanup below.
                else:
                    status = 0
                    while True:
                        raw = sys.stdin.buffer.readline(MAX_INPUT + 1)
                        if not raw:
                            break
                        try:
                            request = decode_request(raw)
                            if (not isinstance(request, dict) or set(request) != {"command", "arguments"}
                                    or not isinstance(request["command"], str)):
                                raise _Usage()
                            result = _execute(service, request["command"], request["arguments"], args.allow_write)
                        except Exception as exc:
                            result, failure = _failure(exc)
                            status = max(status, failure)
                        output.write(encode_result(result).decode("utf-8") + "\n")
                        output.flush()
                        if len(raw) > MAX_INPUT:
                            # Do not parse a remainder as another command.
                            break
        if args.command == "session":
            return status
        output.write(encoded + "\n")
        output.flush()
        return 0
    except Exception as exc:
        result, status = _failure(exc)
        errors.write(encode_result(result).decode("utf-8") + "\n")
        return status