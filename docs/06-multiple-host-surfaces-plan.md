# Design 06 — One Controller Runtime, Multiple Host Surfaces

**Status:** accepted, implemented in v0.7.0

**Date:** 2026-08-23

## Outcome

One `Contexture` application is compiled once into one Controller runtime. Agent
hosts enter it through MCP progressive disclosure; human dashboards enter it
through explicitly published REST routes. Both paths resolve the same refs, use
the same binding and validation, bind the same request-scoped `Principal`, call
the same `Tool`, and reach the same business Model and Repository.

```text
                         Contexture application
                                  │
                              compile once
                                  ▼
                     ApplicationRuntime / Index
                    ref · binding · principal · call
                         ┌────────┴────────┐
                         ▼                 ▼
                    MCP Surface       REST Surface
                 discover/open/invoke  explicit routes
                         ▼                 ▼
                    Agent Host        Human dashboard
```

The REST surface is an input adapter, not a second Controller framework. It
must not contain business rules or expose an arbitrary-ref endpoint.

## Invariants

1. `Role`, `Skill`, and `Tool` remain the closed Controller set.
2. `Route` is a Web publication pointer, never a node or Role member.
3. A Route names one existing Tool ref explicitly; unlisted Tools are not HTTP
   reachable.
4. `GET` and `HEAD` may name only read-only Tools. Writing methods may name only
   writing Tools. A mismatch fails while the surface is built.
5. Tool input schema and runtime validation come from the same Binding on MCP
   and REST.
6. Identity is bound by the shared runtime for exactly one call. The MCP
   adapter obtains it from an access token; a Web authenticator obtains it from
   its request. Neither source reaches business Tool signatures.
7. Contexture carries identity and owns no permission vocabulary. Business
   Tools continue to authorize with `current_principal()`.
8. REST errors carry structured facts. Agent recovery prose remains an MCP
   concern and all existing MCP golden fixtures remain byte-for-byte stable.
9. Channels are compiled once, opened once for a serving lifespan, and shared
   by both surfaces in a combined host.
10. Core declarations remain importable without loading an HTTP or MCP SDK.

## Public shape

```python
from contexture.web import Route, RestSurface

routes = (
    Route("GET", "/v1/projects", "project/list-projects"),
    Route("POST", "/v1/projects", "project/upsert-project", status=201),
)

compiled = compile_application(app)
runtime = compiled.runtime()
rest = RestSurface(runtime, routes=routes, authenticate=authenticate)
asgi_app = rest.asgi_app()
```

`Route` stays under `contexture.web`; it is not exported from `contexture`.
There are no first-class `Query` or `Command` types because `Tool.read_only`
already owns that distinction.

The first version accepts JSON-object bodies and query parameters. Path
parameters and OpenAPI are added only after the invocation and identity seam is
proven; their absence does not justify a second hand-written business gateway.

## Internal changes

### 1. ApplicationRuntime

Add a transport-neutral runtime over one compiled `Index`. It resolves a Tool,
checks the read/write door, binds a supplied `Principal`, passes an opaque host
context to the Binding, and invokes it. Lookup and wrong-door errors remain
structured.

`SystemAPI` delegates execution to this runtime and retains only agent-facing
navigation and refusal rendering.

### 2. Binding identity seam

`TypeHintBinding` stops discovering identity from MCP. It validates and invokes
only. MCP reads its access token in its own surface and passes a `Principal` to
the runtime. REST does the same through its authenticator. This is the change
that makes identity transport-neutral rather than merely making invocation
callable from Python.

### 3. REST publication and ASGI adapter

`contexture.web` owns Route validation, HTTP decoding, response encoding and
problem details. It exports an ASGI application so a business can mount it in
an existing server; Contexture does not own the final HTML/JS View or require a
particular ASGI runner.

The adapter has no endpoint taking a caller-supplied Contexture ref or
Principal. Route refs are fixed at construction and identity comes only from a
trusted authenticator.

## Error contract

REST uses `application/problem+json` with stable machine types:

| Failure | Default HTTP status |
| --- | --- |
| malformed JSON/body shape | 400 |
| unauthenticated request when authenticator refuses | 401 |
| business `PermissionError` | 403 |
| route not published | 404 |
| Tool input validation | 422 |
| other business failure | 500 |

Applications may add domain-specific exception mappings, for example CAS to
409, without changing Controller execution.

## Verification

- Existing MCP unit, golden, stdio and Streamable HTTP suites remain green.
- The same Tool invoked through MCP and REST returns the same value.
- A write Tool cannot be mounted on GET and a read Tool cannot be mounted as a
  writing Route.
- A valid but unpublished Tool ref is unreachable over HTTP.
- Concurrent REST calls observe only their own Principal.
- A forged principal header is ignored.
- Route target and duplicate method/path failures happen at construction.
- Channels open before REST serving and close afterward.
- Layering tests keep protocol SDKs out of `core` and the authoring facade.

## Delivery sequence

1. Add the shared runtime and move principal binding to it without changing MCP.
2. Add Route and RestSurface with in-process ASGI tests.
3. Update the MVC design, README, typing facades and cross-language notes.
4. Release and commit Contexture.
5. Replace One Creator's hand-written Cockpit Role compiler with RestSurface.
6. Migrate every remaining Brain consumer; delete Brain only after repository,
   compose, distribution, tests and documentation reference audits are clean.
