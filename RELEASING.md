# Releasing Contexture

Only project maintainers perform these steps. Package files are immutable on
PyPI: never reuse a version and do not delete a release to “try again.” Publish
a new release candidate instead.

## One-time trusted publisher setup

Enable 2FA on separate PyPI and TestPyPI accounts. In GitHub, create the
environments `testpypi` and `pypi`; require maintainer approval on `pypi`.

Register these pending publishers:

| Index | Project | Owner | Repository | Workflow | Environment |
| --- | --- | --- | --- | --- | --- |
| TestPyPI | `contexture-mcp` | `CarterShi01` | `contexture-mcp` | `publish-testpypi.yml` | `testpypi` |
| PyPI | `contexture-mcp` | `CarterShi01` | `contexture-mcp` | `publish-pypi.yml` | `pypi` |

- PyPI: <https://pypi.org/manage/account/publishing/>
- TestPyPI: <https://test.pypi.org/manage/account/publishing/>

A pending publisher does **not** reserve the name. The first successful upload
creates the project and occupies `contexture-mcp`. The repository owner, name,
workflow filename, environment, and package metadata must match exactly. No API
token or repository secret is used.

## Prepare the 1.0.0 release

1. Start from current `master` with a clean worktree.
2. Confirm the PEP 440 version is `1.0.0` in both `pyproject.toml` and
   `contexture/core/constants.py`.
3. Move user-visible changes from Unreleased into a dated changelog section.
4. Update both READMEs and handbooks for changed user behavior.
5. Regenerate golden fixtures only when the reviewed protocol contract changed.
6. Run the complete release gate:

```bash
uv sync --extra dev
uv run --extra dev pytest -q
uv run --extra dev pyright
uv run --extra dev ruff check contexture tests scripts
uv run --extra dev validate-pyproject pyproject.toml
uv run python scripts/verify_porting_contract.py
uv run python scripts/product_manifest.py --check
uv run python scripts/incremental_manifest.py
uv run python scripts/verify_porting_contract.py --release-bindings
uv build
uv run --extra dev twine check --strict dist/*
```

Inspect the wheel and source archive and confirm both contain `LICENSE`, typing
files, templates, and no tests, credentials, caches, or stale build tree.

7. Record current real-Host verification from these exact release artifacts.
   Historical candidate runs are useful regression evidence but do not satisfy
   the 1.0.0 Host gate.

## Rehearse 1.0.0 on TestPyPI

After the release changes and workflow exist on GitHub, run **Publish to
TestPyPI** manually from the intended commit. Approve the `testpypi` environment
if configured. Verify the project page and download the exact artifact without
dependencies:

```bash
python -m pip download --no-deps \
  --index-url https://test.pypi.org/simple/ \
  'contexture-mcp==1.0.0'
```

Install that downloaded wheel in a clean environment while resolving runtime
dependencies from normal PyPI, then run `contexture --version`, import the
public API, generate a project, and execute `contexture check`.

TestPyPI and PyPI are separate. Success on TestPyPI does not occupy the PyPI
name and does not configure the production publisher.

## Publish 1.0.0 to PyPI

Merge the candidate to `master` and require green CI. Create an annotated tag
whose name exactly matches the package version and push only that tag:

```bash
git switch master
git pull --ff-only
git tag -a v1.0.0 -m "Contexture 1.0.0"
git push origin v1.0.0
```

The tag starts `.github/workflows/publish-pypi.yml`. Its build job refuses a
tag/version mismatch. The publishing job downloads the already-built artifact,
requests a short-lived OIDC credential, waits for `pypi` environment approval,
and uploads with a provenance attestation.

After approval, verify:

```bash
uvx --from 'contexture-mcp==1.0.0' contexture --version
```

Also verify the PyPI metadata, README links, files, classifiers, license, and
provenance. Run the generated-project path and record host verification.

## Verify the coordinated release

After the sibling repositories publish the same semantic version, resolve all
three public artifacts without a source checkout:

```bash
uvx --from 'contexture-mcp==1.0.0' contexture --version
npm view '@contexture/mcp@1.0.0' version dist.integrity
GOPROXY=https://proxy.golang.org go list -m \
  github.com/CarterShi01/contexture-mcp-go@v1.0.0
```

Record the three immutable artifact identifiers together. If a published
release is unsafe, deprecate or yank it where supported and publish a fixed
version; never move a tag or replace an existing registry file.
