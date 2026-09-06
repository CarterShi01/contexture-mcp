# Contributing

Thank you for improving Contexture. Small, reviewable changes with an explicit
behavioral reason are easiest to merge.

## Set up

Prerequisites are Git, Python 3.11 or newer, and
[uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/CarterShi01/contexture-mcp.git
cd contexture-mcp
uv sync --extra dev
uv run --extra dev pytest -q
```

Create a branch from current `master`. Do not commit credentials, local host
configuration, virtual environments, or built distributions.

## Required checks

Before opening a pull request, run:

```bash
uv run --extra dev pytest -q
uv run --extra dev pyright
uv run --extra dev ruff check contexture tests
uv run --extra dev validate-pyproject pyproject.toml
uv build
uv run --extra dev twine check --strict dist/*
```

CI repeats the test suite on Python 3.11, 3.12, 3.13, and 3.14 and consumes the
built wheel outside the source tree.

## Architecture and contracts

- Business declarations use `contexture`; advanced hosting uses
  `contexture.server`. Concrete submodules are internal.
- `core` never imports the MCP SDK or a higher layer. The layering tests enforce
  this at syntax and runtime levels.
- A Tool binding owns both schema generation and validated invocation.
- The four MCP gateway tools stay fixed; business capabilities travel in
  progressive-disclosure payloads.
- Disclosure is not authorization. Identity is framework context; permission
  decisions stay in the application and host.

Read [the specification](spec/README.md) before changing wire behavior and the
[ADRs](docs/adr/) before moving an architectural boundary. A new decision that
reverses or materially extends an accepted ADR should be recorded in a new ADR,
not by rewriting history.

If behavior under `spec/golden/` intentionally changes, regenerate with:

```bash
uv run --extra dev python tests/golden.py --update
git diff -- spec/golden
```

Review every changed byte. Never update golden files merely to make a failing
test pass.

## Public API and documentation

Changes to `contexture.__all__` or `contexture.server.__all__` require an API
snapshot update, a changelog entry, and migration notes when compatibility is
affected. Keep runtime exports and `.pyi` files aligned. New public behavior
needs both English and Simplified Chinese user documentation; language-neutral
behavior belongs in `spec/`.

## Pull requests

Describe the user-visible problem, the chosen boundary, tests added, and any
compatibility effect. Link an issue when one exists. A focused pull request may
leave unrelated cleanup alone.

Report vulnerabilities privately as described in [SECURITY.md](SECURITY.md),
not in a public issue.
