# Contexture full product parity plan — TypeScript and Go

## Decision and outcome

This plan supersedes the narrower 0.12 contract-port objective for new work.
The deliverable is two independently installable, open-source Contexture
projects equivalent to the Python project in product capability, architecture,
public concepts, observable behaviour, documentation, examples, test coverage,
and release quality. Declaration syntax, package management, and transport
implementation may differ only where TypeScript or Go requires it.

**Equivalence is not a line-count target and not a 16-rule conformance claim.**
A binding is complete only when a user can learn, build, inspect, test, serve,
and ship the same kind of Contexture application in that language without
discovering an undocumented missing product layer.

The initial source baseline is Python revision
3b274421360d5569a23922bfc72b71d5828cf995 (Contexture 0.12.0rc1). Its code is
identical to e107a81a933c5eb5b4530e762311619be3a7a80f; the later revision also
records the public 0.12 project state. A later Python change requires an
explicit baseline upgrade and a reviewed parity delta.

The existing TypeScript and Go repositories are useful kernel prototypes, not
the completion baseline. No README, package metadata, or release note may call
either binding fully equivalent until every gate in this document passes.

## What must be equivalent

| Plane | Python source of truth | Required TS and Go result |
| --- | --- | --- |
| Public declaration API | contexture/__init__.py, application.py, core/model | The same concepts and invariants: Contexture, Role, Skill, Tool, Channels, Prompt, Resource, Principal, references, laziness, validation, and request facts. Use native syntax only where language semantics require it. |
| Kernel and architecture | core/, ADR 009–019 | Explicit, tested layers: shared foundation; model; MCP-interface declarations; SDK-neutral server-independent kernel. No flattened catch-all core. |
| Product interfaces | cli/, inspection.py, server/, web/ | Equivalent new, check, list, inspect, call, serve, and demo workflows; MCP and explicit REST surfaces; identity and root-selection behaviour. |
| Developer experience | README, handbook, demo, templates, typing | Installable package, generated starter application, runnable demo, API documentation, architecture guide, complete English-first documentation and Chinese translations. |
| Confidence and release | Python tests, type checks, package gates | Mapped test suite, native static checks, cross-language differential cases, external-consumer installation test, reproducible package checks, and truthful release metadata. |

Python-only implementation mechanics are not requirements: subclass constructors
become TypeScript builders/classes or Go constructors/factories; Python type
hint reflection becomes Zod/schema declarations or Go tagged structs. The
observable declaration meaning and all user-facing workflows are requirements.

## Required target architecture

Directories express architecture. Exact filename spelling may use the language's
normal convention, but every variance needs a manifest reason and an owning
test.

### TypeScript

~~~text
src/
├── index.ts                         public declaration facade
├── application.ts                   Contexture application declaration
├── inspection.ts                    transport-free session inspection
├── core/
│   ├── foundation/                  constants, errors, types, principal
│   ├── model/                       node, role, skill, tool, binding,
│   │                                channels, index, manager, disclosure,
│   │                                runtime, selection, telemetry, system API
│   └── mcp-interface/               prompt, resource, fixed gateway-tool facts
├── server/                          compile/bind/identity/options/launch/
│   └── surface/                     navigation, prompts, resources, tools
├── web/                             explicit REST route and surface adapter
├── cli/                             command parser, project loading, scaffold
│   └── templates/project/           generated TypeScript application
└── demo/                            maintained reference application
~~~

Core must not import an MCP SDK, HTTP framework, Node process/CLI APIs, or CLI
code. Node runtime primitives required for request-local context are permitted
inside model. MCP-interface may depend only on foundation, never model. Server
owns MCP SDK imports; web owns HTTP imports. Type-only imports are checked
separately from runtime imports.

### Go

~~~text
.
├── contexture.go                    public declaration facade and Contexture
├── inspection/                      transport-free inspection package
├── core/
│   ├── foundation/                  constants, errors, types, principal
│   ├── model/                       node, role, skill, tool, binding,
│   │                                channels, index, manager, disclosure,
│   │                                runtime, selection, telemetry, system API
│   └── mcpinterface/                prompt, resource, fixed gateway-tool facts
├── server/                          compile/bind/identity/options/launch/
│   └── surface/                     navigation, prompts, resources, tools
├── web/                             explicit REST route and surface adapter
├── cmd/contexture/                  command entry point and command package
│   └── templates/project/           generated Go application
└── demo/                            maintained reference application
~~~

Go may use mcpinterface rather than Python's underscore spelling because it is
a Go package path; it is still the counterpart of core/mcp_interface. The
module-root facade may type-alias or forward only declaration-facing APIs and
must not import server, web, CLI, or MCP SDK packages. Core remains SDK and
net/http free; server owns MCP SDK usage; web owns HTTP usage.

## Parity manifest: the work ledger

Before changing production structure, create a versioned
spec/porting/PYTHON_0_12_PRODUCT_MANIFEST.json and a readable Markdown view.
It is the authoritative task ledger and must contain, for every source module
and test module:

- Python path, public symbols, public import path, and responsibility;
- its TypeScript path and Go path, or a documented not-applicable reason;
- observable behaviour and focused TS/Go test paths;
- documentation, example, and template coverage;
- status: missing, designed, implemented, or verified;
- Python baseline revision and a content hash for the source file.

Not-applicable is valid only for Python syntax machinery, Python typing stubs,
or an explicitly replaced native transport primitive. It is never valid for a
user workflow merely because the old ports do not implement it. CI fails if a
Python module is unmapped, a mapped binding path is absent, or a verified entry
lacks named evidence.

The pinned 0.12 inventory also records one exact validation-only case-study
snapshot that was retired before 1.0. Those frozen paths are omitted rather
than treated as a product plane. The retirement set is exact and applies only
to the pinned baseline; no current or future case study, test, documentation,
workflow, or release asset is generically exempt from parity review.

The manifest must include these groups:

| Python group | Required binding counterpart |
| --- | --- |
| __init__, application | public facade and lazy application declaration |
| core/constants, errors, types, principal | foundation package |
| core/model/{node,role,skill,tool,binding,channels,index,manager,disclosure,root_selection,runtime,graph_context,system_api,telemetry} | Separate, discoverable model modules; merge only where a manifest proves a one-to-one responsibility and preserves public API. |
| core/mcp_interface/{prompt,resource,tool} | prompt/resource/fixed gateway declarations |
| server/{application,binding,identity,instructions,launch,messages,options,root_selector,server,surface/*} | corresponding server and surface modules |
| web/{route,surface} | corresponding explicit REST modules |
| inspection | transport-free inspection API and CLI rendering |
| cli/{main,project,scaffold,usage,templates} | native CLI, project loader, scaffolder, templates |
| demo/* | maintained equivalent demo and integration test |
| tests/* and tests/typing/consumer.py | focused native tests and external-consumer type/compile tests |

## Execution phases

Work TypeScript first so API and product decisions settle once. Go begins each
phase after TypeScript has focused and full gates green. Independent inventory
and documentation work may proceed in both repositories. Each change contains
one architectural move or one user-visible feature plus tests.

### Phase 0 — baseline and observability

1. Freeze the Python revision and generate the product manifest.
2. Record every public import, CLI command, flag, exit status, template file,
   demo path, and documented workflow.
3. Map every Python test to an exact port, native equivalent, or language
   mechanic replacement.
4. Add non-normative cross-language scenario fixtures for CLI output,
   inspection traces, project generation, server startup, REST, and MCP.
   Python produces expected results; each port executes them itself.
5. Reclassify existing TS/Go code honestly. Copied golden assets alone are not
   execution evidence.

**Exit evidence:** reviewed complete manifest, green Python package/test gates,
and no unclassified public Python module or command.

### Phase 1 — restore architecture without behaviour loss

1. Move current TS and Go kernel into the target layer directories.
2. Split compressed files until model, MCP-interface, server surface, and web
   responsibilities are individually discoverable.
3. Introduce public facades and only necessary compatibility shims.
4. Add layer tests equivalent to Python tests, including static import scans and
   Go package dependency checks.
5. Rebase the existing immutability, lifecycle, disclosure-only, and
   local-golden corrections into the new modules; retain their regression tests.

**Exit evidence:** the target trees exist; implemented behaviour remains green;
layer violations fail; READMEs link directly to Node, Role, Skill, Tool,
Binding, and server surface definitions.

### Phase 2 — declaration and model parity

Port and test node cards/references; Role/Skill/Tool construction;
manager/registration; Index diagnostics; disclosure/root selection; Channels;
bindings; runtime graph context; Principal; Telemetry; and System API recovery
messages. Preserve ordered outputs, lazy construction, immutable compiled
snapshots, and contract text.

TypeScript uses explicit schemas and scoped asynchronous context. Go uses typed
factories, tagged structs, context.Context, defensive copies, and typed errors.

**Exit evidence:** mapped Python model tests, R1–R17 evidence, mutation,
concurrency, lifecycle, and error-path tests all pass. No model group is
unmapped or justified only by copied assets.

### Phase 3 — server, MCP, identity, and web parity

Port server compilation/application flow, MCP binding, fixed gateway surfaces,
instructions/messages, launch configuration, identity, trusted root selection,
and explicit REST routes. Exercise real official SDK clients or in-process
transports for stdio and streamable HTTP. Business Tools must never become
arbitrary top-level MCP registrations.

The web layer may use a native HTTP framework only if the route allowlist,
read-only/write handling, error text, and binding reuse remain equivalent.

**Exit evidence:** every Python server, server/surface, and web entry is
verified; server/REST integration tests use real transport; request isolation
and identity-ceiling tests pass.

### Phase 4 — inspection and complete CLI parity

Implement transport-free inspection and all supported commands:

~~~text
contexture new
contexture check
contexture list
contexture inspect
contexture call
contexture serve
contexture demo
contexture --version
~~~

Match inputs, safety defaults, JSON output, stdout/stderr separation, exit
statuses, diagnostics, and project-target resolution. Configuration is native:
a TypeScript package/configuration module and a Go module/application entry
point, but workflows and generated-project capabilities are equivalent.

**Exit evidence:** observable CLI results are compared to Python scenarios;
invalid flags and unsafe calls are tested; a generated project succeeds through
check, list, inspect, call, and serve.

### Phase 5 — demo, templates, documentation, and consumer experience

1. Port the maintained demo, fixtures, Role, Skills, Tools, and server setup.
2. Ship native new-project templates with install/run/test instructions and a
   working declaration.
3. Rewrite README, Chinese README, architecture guide, handbook, API reference,
   contribution/security/release guides, and changelog from actual behaviour.
4. Run every README example in CI.

**Exit evidence:** an external consumer installs each packed module, generates a
project, runs the local workflow, starts the demo, and uses documented imports.

### Phase 6 — differential verification and release readiness

1. Verify mapped evidence for every Python test module.
2. Run scenario suites against Python, TypeScript, and Go; compare strings
   byte-for-byte and structured payloads semantically.
3. Run gates:

~~~text
TypeScript: format check, lint, typecheck, unit/integration tests, build,
            packed npm external-consumer test, dependency/layer check.
Go:         gofmt check, go vet, go test -race ./..., static package/layer
            check, module consumer test, release dry run.
~~~

4. Verify licence, package contents, repository links, version compatibility,
   release notes, and dependency policy.
5. Complete a Sol audit by manifest row. Verified means current source, direct
   test output, and user-visible documentation evidence.

**Exit evidence:** 100% manifest coverage; all gates from clean checkouts; no
unsupported Python public workflow; release checklists pass.

## Public API and compatibility rules

- Preserve Python public concept names wherever TS and Go can express them.
  Native constructors/builders may replace subclass syntax, documented beside
  the Python equivalent.
- Do not elevate an internal prototype API merely because it already exists.
  Public APIs are selected from the Python facade and manifest.
- Any change to an already published binding API needs compatibility policy,
  deprecation, and consumer testing.
- TypeScript declaration files and Go exported-symbol documentation are product
  surface. Both need external consumer compile fixtures.
- No public release while the manifest has a missing, designed, or unverified
  public entry.

## Automation and model roles

Sol owns baseline, manifest, architecture, differential test design, and final
acceptance. Terra implements one bounded manifest slice at a time, including
tests, documentation, and an atomic commit. Luna may only do mechanical,
directly-oracle-backed inventory or relocation work. CI publishes manifest
coverage and blocks merges missing architecture, consumer, scenario, or test
evidence.

Human input is needed only for a product-semantic decision absent from Python,
registry ownership, credentials, or release authorisation.

## Current release gate

1. Verify the reviewed product and incremental manifests and the immutable
   cross-language fixture/golden byte gate.
2. Run all three package gates and inspect the exact wheel, npm tarball, and Go
   external-module consumer results.
3. Record current real-Host evidence from the exact release artifacts.
4. Confirm registry namespaces, trusted publishers, and protected release
   environments.
5. Do not push a release tag, publish, or claim product equivalence before all
   evidence is green and a maintainer separately authorizes the release.
