# Python 0.13–0.16 incremental parity manifest

This is the readable companion to
`PYTHON_0_13_TO_0_16_INCREMENTAL_MANIFEST.json`. The JSON file is the
authoritative ledger and `scripts/incremental_manifest.py` verifies it directly
against the pinned Git objects.

## Baseline policy

The Python repository has no `v0.13.0` through `v0.16.0` tags. Each baseline is
therefore the immutable commit that first sets the exact package version in
`pyproject.toml`, is descended from the preceding baseline, and contains the
matching changelog entry. Tree IDs are recorded so the complete source snapshot
is independently identifiable.

OC Goal remains excluded as an explicitly Python-only case study. This does not
exclude any framework capability, test, workflow, documentation, or release
asset introduced by these four releases.

## Frozen release chain

| Version | Commit | Tree | Product delta | State |
| --- | --- | --- | --- | --- |
| 0.12.0rc1 | `3b274421360d5569a23922bfc72b71d5828cf995` | — | Full-product parity baseline | verified |
| 0.13.0 | `1deeb6b87be905edf1ba9b83d8d431def0af702c` | `265563acf2a39ac1622b35a57ff08056da86d992` | Path-selected capability surfaces | verified |
| 0.14.0 | `a108b314bb3f37622fb082759f726468bbb09163` | `7e7ae7f64d4863096d6fc5f393033a79795c3170` | Optional Role publications | verified |
| 0.15.0 | `471d0f75c6be0e5cff104f0d0c61f10957da792a` | `45398b8d4bc50dc68c03b2c4cde8b8e949cadc8e` | Non-activating inspect | verified |
| 0.16.0 | `cda2721c7c40128cd0b7eef990e5909edabd3b17` | `3d47fd088581524f4884bf89609df1dc5134bc81` | Symmetric process members and instruction emphasis | verified |

## Acceptance loop

Each version is handled independently and in order:

1. Audit every path in that release's exact Git delta and derive observable
   requirements from its Python source, tests, fixtures, and documentation.
2. Implement and focus-test TypeScript; update English and Chinese user-facing
   documentation; run the complete TypeScript gate.
3. Implement and focus-test Go; update English and Chinese user-facing
   documentation; run the complete Go gate.
4. Record exact test and commit evidence in the JSON ledger, validate the ledger,
   commit, and push every affected repository before moving to the next version.
5. Do not publish a package or create a release tag without separate release
   authorization.

A `verified` release requires non-empty TypeScript and Go evidence for every
capability row. The final audit repeats both native full gates from clean
checkouts and checks that all repositories are clean and synchronized with
their remotes.
