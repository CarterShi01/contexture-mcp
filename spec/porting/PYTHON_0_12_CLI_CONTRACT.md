# Python 0.12 CLI contract for native bindings

This is a Phase-0 extraction from Python baseline
`3b274421360d5569a23922bfc72b71d5828cf995`. It specifies user-visible
workflow obligations for the TypeScript and Go ports. It does not require
Python import syntax, TOML field names, or a particular CLI parser library;
the commands, safety defaults, output channels, and observable outcomes are
the contract.

## Process contract

Executable name: `contexture`.

- `contexture --version` prints package version `0.12.0rc1` and exits zero.
- A `UsageError` prints `contexture: <message>` to stderr and exits `2`.
- An unhandled Contexture domain error prints the same prefix to stderr and
  exits `1`.
- Normal command output is stdout. In particular, stdout must remain protocol
  clean while `serve` uses stdio; informational notices for JSON output go to
  stderr.
- The command set is `new`, `list`, `check`, `call`, `inspect`, `serve`, and
  `demo`. A native port must not silently omit one.

## Commands

| Command | Inputs | Required outcome |
| --- | --- | --- |
| `new NAME [--into DIR] [--template NAME]` | A display/project name; destination defaults to cwd; template defaults to `project`. | Refuses empty/invalid derived names, unknown templates, and existing destination. Writes a runnable starter project and prints its relative/absolute destination plus next commands. |
| `list [TARGET]` | Optional explicit application/root target. | Builds the declaration without serving; prints roles, then skills and tools with ref and access door. |
| `check [TARGET]` | Optional explicit target. | Compiles without opening Channels or starting a server; prints `OK <name>: <n> role(s), <n> skill(s), <n> tool(s)`. |
| `call REF [--input JSON \| --input-file FILE] [--allow-write] [--target TARGET]` | One Tool ref and optional object input. | Executes through production binding. Input sources are mutually exclusive and must decode to an object. A writing Tool is refused unless `--allow-write` is explicit. Strings print raw; other values print JSON. |
| `inspect [REF ...] [--target TARGET] [--all] [--read] [--summary] [--json] [--no-discover] [--roster-budget CHARS]` | Ordered refs or `--all`; no project defaults to demo. | Replays the exact connection/discovery/open/read surface. Refusals and host-limit findings are rendered, not crashed; exits `1` when trace has failures, otherwise `0`. `--json` is a clean stdout document. |
| `serve [TARGET] [transport options]` | Optional target or discovered project. | Builds the application and starts MCP; unlike `inspect`, no project never silently starts the demo. |
| `demo [transport options]` | No target. | Starts maintained bundled demo using the same server assembly path as a project. |

## Shared `serve` and `demo` transport options

| Option | Contract |
| --- | --- |
| `--transport {stdio,streamable-http}` | Defaults to `stdio`. |
| `--host HOST` | Defaults to loopback (`127.0.0.1`) through server options. |
| `--port PORT` | Defaults to `8000` for HTTP through server options. |
| `--path PATH` | Defaults to `/mcp` through server options. |
| `--allow-host HOST` | Repeatable allowlist; needed when serving a non-loopback address. |
| `--allow-origin ORIGIN` | Repeatable HTTP origin allowlist. |
| `--allow-anonymous` | Explicitly permits unauthenticated non-loopback serving. |

Transport validation belongs in the server options object so programmatic and
CLI startup cannot drift. Authentication is deliberately a Host-supplied
verifier/configuration object, never a bare CLI token flag.

## Project discovery and generated project workflow

Python discovers the nearest `pyproject.toml` containing `[tool.contexture]`.
It supports a modern single lazy application target (`app`) and a legacy
decomposed form (`name`, `roots`, optional `channels`, optional `publish`). A
port may use native configuration, but must preserve these observable rules:

1. A project is found by walking ancestors from cwd.
2. Explicit target syntax is validated and imported/loaded from the declared
   project, never silently from a conflicting dependency.
3. `app` and legacy keys cannot be mixed.
4. Generated projects run `check`, `list`, `inspect`, `call`, and `serve`.
5. Project dependencies have one serving lifecycle and are not opened for
   `check` or disclosure-only workflows.

## Required native evidence

Each binding needs process-level tests from a generated project covering
`new → check → list → inspect → call`, stdout/stderr separation for JSON,
unsafe write refusal, malformed target/configuration refusal, and one startup
test for every supported transport. The scenario tests must execute native
code; copied Python command output is not evidence.
