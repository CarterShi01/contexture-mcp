# Security policy

## Supported versions

Security fixes are developed on `master` and released for the latest published
minor line. Release candidates receive fixes while they are being evaluated.
Older pre-1.0 minor lines are not maintained after a newer stable line ships.

| Version | Supported |
| --- | --- |
| current `master` / current release candidate | yes |
| latest stable minor | yes |
| older minor releases | no |

## Report a vulnerability

Please use GitHub's private vulnerability reporting for
`CarterShi01/contexture-mcp` (Security → Advisories → Report a vulnerability).
Do not open a public issue and do not include secrets, production tokens, or
personal data in a report.

Include the affected version, deployment/transport, minimal reproduction,
impact, and any suggested mitigation. You should receive an acknowledgement
within seven days. Timing of a fix and disclosure depends on severity and
whether the issue also affects an upstream dependency.

## Security boundary

Contexture progressively discloses capabilities; it does not provide business
authorization. Applications must make permission decisions from their verified
identity and domain policy. Exposing Streamable HTTP beyond loopback requires
an explicit authentication or anonymous-access decision and appropriate
Host/Origin controls.

Dependency vulnerabilities in `mcp`, `mcp-types`, AnyIO, or their transitive
dependencies should identify the upstream advisory when known.
