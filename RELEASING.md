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

## Prepare a candidate

1. Start from current `master` with a clean worktree.
2. Set the same PEP 440 version in `pyproject.toml` and
   `contexture/core/constants.py`.
3. Move user-visible changes from Unreleased into a dated changelog section.
4. Update both READMEs and handbooks for changed user behavior.
5. Regenerate golden fixtures only when the reviewed protocol contract changed.
6. Run the complete release gate:

```bash
uv sync --extra dev
uv run --extra dev pytest -q
uv run --extra dev pyright
uv run --extra dev ruff check contexture tests
uv run --extra dev validate-pyproject pyproject.toml
uv build
uv run --extra dev twine check --strict dist/*
```

Inspect the wheel and source archive and confirm both contain `LICENSE`, typing
files, templates, and no tests, credentials, caches, or stale build tree.

## Rehearse on TestPyPI

After the release changes and workflow exist on GitHub, run **Publish to
TestPyPI** manually from the intended commit. Approve the `testpypi` environment
if configured. Verify the project page and download the exact artifact without
dependencies:

```bash
python -m pip download --no-deps \
  --index-url https://test.pypi.org/simple/ \
  'contexture-mcp==0.12.0rc1'
```

Install that downloaded wheel in a clean environment while resolving runtime
dependencies from normal PyPI, then run `contexture --version`, import the
public API, generate a project, and execute `contexture check`.

TestPyPI and PyPI are separate. Success on TestPyPI does not occupy the PyPI
name and does not configure the production publisher.

## Publish to PyPI

Merge the candidate to `master` and require green CI. Create an annotated tag
whose name exactly matches the package version and push only that tag:

```bash
git switch master
git pull --ff-only
git tag -a v0.12.0rc1 -m "Contexture 0.12.0rc1"
git push origin v0.12.0rc1
```

The tag starts `.github/workflows/publish-pypi.yml`. Its build job refuses a
tag/version mismatch. The publishing job downloads the already-built artifact,
requests a short-lived OIDC credential, waits for `pypi` environment approval,
and uploads with a provenance attestation.

After approval, verify:

```bash
uvx --from 'contexture-mcp==0.12.0rc1' contexture --version
```

Also verify the PyPI metadata, README links, files, classifiers, license, and
provenance. Run the generated-project path and record host verification before
promoting a stable release.

## Promote stable

Do not rename or mutate the candidate. Change both version locations to
`0.12.0`, update the changelog, repeat TestPyPI with the new immutable version,
merge, and tag `v0.12.0`. If a published release is unsafe, yank it and publish
a fixed version; keep the historical file record intact.
