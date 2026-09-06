# Design 08 — Request-selected root surfaces

**Status:** implemented in 0.11.0

**Date:** 2026-09-06

## 0. Outcome

One compiled Contexture application can serve several callers through the same
MCP endpoint while each caller receives an explicit subset of the application's
top-level roots. A selected root remains intact: every Role, Skill and Tool
under it follows the existing progressive-disclosure rules without any
node-level filtering.

For the HTTP MCP host, the caller states the requested roots in transport
configuration, outside model-controlled tool arguments:

```yaml
mcp_servers:
  oc-app:
    url: "${OC_APP_BASE_URL}/mcp"
    protocol: stateless
    headers:
      Authorization: "Bearer ${OC_TOKEN}"
      Contexture-Roots: "team,work"
```

The same OC App Index, Tool bindings, Channels and process serve this request
and an unscoped Conversation request. The request creates only a small immutable
view over that Index.

```text
                              compiled OC Index
                                     │
                    request root selection (immutable)
                   ┌─────────────────┼──────────────────┐
                   ▼                 ▼                  ▼
             Conversation          Team              Cron
               all roots          team             team, work
                   │                 │                  │
          discover/open/invoke  discover/open/invoke  discover/open/invoke
```

This feature is a capability projection. It is not a business permission
system. It can attenuate what a caller can address, but an allowed Tool remains
responsible for authorizing its operation and for deciding which business data
it returns.

## 1. Scope

The selection unit is one complete root tree.

If `team` is selected:

- `team` appears in discovery and server routing instructions;
- every ref whose first segment is `team` can be opened or invoked;
- opening `team` returns its complete immediate sibling set;
- no selector is evaluated against `team/ops`, Profile Roles, Skills or Tools.

If `project` is not selected, no `project` ref is disclosed or callable through
that request. Contexture must refuse a direct `project/...` ref before resolving
or describing it.

This design does not add arbitrary node predicates, field redaction, per-object
data filters, tenant policy or a new compile level.

## 2. One Creator findings

OC App currently declares one multi-headed Runtime application with these model
roots:

```text
goal, project, project-intent, project-adoption, work, hands,
infrastructure, team, information, notebook, desk, memory,
account, schedule, audit
```

The four Hermes instances consume that one application differently:

| Hermes instance | Intended OC root surface | Current mechanism |
| --- | --- | --- |
| Conversation | all roots | Uses normal Contexture discover/open/invoke and has no Role-scope hook. |
| Team | `team` | Omits `contexture_discover`; a fail-closed pre-tool hook accepts only `team` and `team/...`. |
| Cron | `team`, `work` | Omits `contexture_discover`; a fail-closed pre-tool hook checks the first ref segment. |
| Information | `information`, `team`, `goal`, `project`, `work`, `notebook`, `memory` | Omits `contexture_discover`; a fail-closed pre-tool hook checks seven root prefixes. |

The three hooks duplicate the same parser and enforcement algorithm. Their
root lists live in Hermes profile code rather than beside Contexture's address
resolution. Each instance service then validates that the hook exists and is
fail-closed, so the duplication includes configuration, startup checks and
tests as well as the hook itself.

The current mechanism constrains calls but does not provide a complete view
boundary:

1. Contexture server instructions are compiled once from the global Disclosure
   and contain a breadth-first Role roster. A scoped Hermes can therefore
   receive names and descriptions outside its allowed roots before making a
   tool call.
2. The hook blocks `contexture_discover`, so a scoped caller cannot use the
   framework's normal entrance and must know a fixed ref in advance.
3. The hook sees only MCP Tool calls. Prompt, Resource, completion and any new
   protocol entrance need separate client-side disabling or another hook.
4. A direct denied ref is resolved by Contexture only after the external hook
   has independently parsed the ref syntax. The framework and each hook can
   drift on separators, gateway names and future protocol changes.
5. The local hook protects only calls made by that Hermes process. The bearer
   credential itself carries business permissions but does not encode the same
   root boundary.

The hooks are good evidence for the missing abstraction: all three ask one
framework-shaped question — which compiled root trees belong to this request.

## 3. Why the selection belongs in Disclosure

Contexture already has the right phase boundaries:

```text
ControllerManager   what exists
Index               immutable global facts: refs, parentage, bindings, uses
Disclosure          how much of the Index a consumer receives
Surface             which protocol doors expose that Disclosure
```

Root selection changes neither the declaration nor any compiled fact. Building
a filtered Manager or Index would duplicate nodes, bindings and Channels and
would make one OC application look like several applications. Filtering cards
only in `contexture_discover` would leave direct `open` and `invoke` as bypasses.

The correct kernel object is an immutable Disclosure view over the same Index.
Contexture already proves most of this shape with `prompt_roots`: a complete
root tree can be absent from model navigation while remaining canonical in one
Index. Request selection generalizes the root membership test, but stays
orthogonal to Prompt ownership:

```text
request root selection    which roots this request may reach at all
prompt_roots              which roots belong to the person-controlled plane
reserved Prompt refs      which visible nodes a model may see but not open
business permission       whether an allowed Tool may perform an operation
```

`Disclosure.unrestricted()` may remove the Prompt-plane restriction for a
person, but it must never remove the request's root selection.

## 4. Core model

### 4.1 Resolved value object

Add a small transport-neutral value object under `contexture.core.model`:

```python
@dataclass(frozen=True, slots=True)
class RootSelection:
    # None means the compatibility surface: every root in this Index.
    names: frozenset[str] | None = None

    @classmethod
    def all(cls) -> "RootSelection": ...

    @classmethod
    def only(cls, names: Iterable[str]) -> "RootSelection": ...

    def resolve(self, index: Index) -> "RootSelection": ...
    def contains_ref(self, ref: str) -> bool: ...
    def intersect(self, ceiling: "RootSelection") -> "RootSelection": ...
```

`resolve` validates names against root refs, rejects an explicitly empty
selection and returns a normalized immutable set. Discovery still preserves
Index registration order; the request's input order has no rendering meaning.

The object is named `RootSelection`, not `Permission` or `Policy`. It knows
only Contexture refs and set intersection.

### 4.2 Disclosure view

Extend `Disclosure` with a resolved `selection`:

```python
@dataclass(frozen=True, slots=True)
class Disclosure:
    index: Index
    prompt_roots: frozenset[str] = frozenset()
    selection: RootSelection = RootSelection.all()

    def select(self, selection: RootSelection) -> "Disclosure":
        # Narrowing is monotonic; a derived view cannot restore a removed root.
        return Disclosure(
            self.index,
            self.prompt_roots,
            self.selection.intersect(selection).resolve(self.index),
        )
```

Use two separate predicates:

```python
surface_can_reach(ref)  = selection contains first ref segment
model_can_see(ref)      = surface_can_reach(ref) and root not in prompt_roots
```

Every public resolution path first checks `surface_can_reach`. The check is
segment-based (`ref == root` or the first segment equals root), never a raw
string prefix, so `team-old` cannot enter the `team` surface.

### 4.3 Complete selected subtrees

`cards_of` does not run a predicate on descendants after their root has been
accepted. Consequently opening a selected Role still returns all of its direct
children, Skills and Tools, preserving ADR 004's complete-sibling-set rule.

The only special case is a `uses` edge crossing roots. Containment determines
the selected subtree; `uses` does not. A cross-root target receives a card only
when its target root is also selected. Contexture must not automatically add the
target root because that would let an implementation detail widen a caller's
surface. `contexture inspect --roots ...` should report such omitted edges so an
application author can decide whether the caller needs both roots.

### 4.4 Execution and graph introspection

`ExecutionAPI` receives the same `RootSelection` and rejects an out-of-surface
ref before `Index.tool`. Both read-only and writing doors use the same check.

During an allowed Tool call, `current_graph()` should expose a root-selected
read facade rather than the concrete global Index. Otherwise a graph
introspection Tool under an allowed root could enumerate excluded roots. This
does not attempt to constrain arbitrary application Channels or the business
data returned by the Tool; those remain application responsibilities.

## 5. Request selector

### 5.1 Canonical HTTP form

The first transport adapter reads one header:

```http
Contexture-Roots: team,work
```

Rules:

- missing header means all roots, preserving existing applications and clients;
- an empty header is invalid rather than an alias for all roots;
- values are comma-separated exact root refs, trimmed and deduplicated;
- an unavailable or misspelled root rejects the request without listing roots
  outside the effective surface;
- header length and item count are bounded before parsing;
- root names remain case-sensitive, like Contexture refs;
- the normalized selection is computed independently on every request.

The MCP Python SDK exposes HTTP headers through the injected request `Context`.
That parameter is absent from the published Tool input schema, so the model
cannot change the configured root set while calling a gateway Tool. Hermes
already supports static headers on a remote MCP declaration, making this usable
without an upstream Hermes hook.

Do not put `roots` into the four gateway Tool schemas. Repeating it in every
model-authored call is easy to omit, lets the model widen it, and turns one
request surface into an informal conversation convention. Do not use a query
string in the first release: it is more likely to enter access logs and cache
keys and is less explicit beside the existing Authorization configuration.

For a stdio server, the equivalent is a fixed selection passed while the
server Surface is built. A later MCP `_meta` adapter can carry the same
`RootSelection` for clients that need transport-neutral dynamic selection; it
must not create a second selection model.

### 5.2 Optional authoritative ceiling

A caller-supplied header is an attenuation request, not authenticated authority.
Contexture should allow an application to supply a generic ceiling resolver:

```python
RootCeiling = Callable[[Principal | None], RootSelection]

effective = requested.intersect(root_ceiling(principal))
```

Contexture does not define permission names, token claims or tenant rules. OC
App may map a service credential claim such as `contexture_roots` to the
ceiling. Intersection is the only composition operation: neither a header nor a
resolver may add a root removed by the other.

For One Creator, adding a ceiling to Hermes service credentials is recommended
before deleting the hooks. It makes the boundary survive use of the same token
outside the configured Hermes process. Human credentials can continue to
resolve to all roots under OC's existing shared-team access model while Tools
enforce their own capability permissions.

## 6. Every entrance must use the effective selection

Root selection is a Surface property only if every door agrees:

| Entrance | Required behavior |
| --- | --- |
| server instructions / `server/discover` | Build routing text from selected roots only. |
| `contexture_discover` | Return all and only selected model-root cards, in declaration order. |
| `contexture_open` | Refuse a ref outside the selection before Index lookup. |
| `contexture_invoke_read_only` / `contexture_invoke` | Apply the same root check before resolving or calling a Tool. |
| Prompt list/get and `goto` | Publish, complete and open only entries whose `opens` root is selected. |
| Resource list/read | Publish and read only entries whose `opens` root is selected. |
| lookup errors | Never enumerate excluded roots as recovery suggestions. |
| telemetry | Record the normalized selection or a stable digest as request context; never use it to alter later requests. |

The MCP gateway Tool list itself remains the fixed two- or four-tool Contexture
surface. Root selection changes gateway results, not which framework gateway
names exist.

### 6.1 Server instructions

The current `MCPServer.instructions` value is built once and contains the global
roster. A request-selected server cannot keep that value without leaking roots.

When request selection is enabled:

1. the static fallback instructions are root-neutral and explain
   `contexture_discover` without naming application roots;
2. the MCP 2026-07-28 `server/discover` result is built from the request's
   selected Disclosure and may carry the filtered roster;
3. a handshake-era client receives the root-neutral fallback, then obtains its
   roots through filtered `contexture_discover`.

An unselected server keeps the current static roster byte-for-byte. This limits
the context-cost change to applications that opt into dynamic root surfaces.

### 6.2 Cache behavior

Any protocol response that varies by `Contexture-Roots` must use a private or
request cache scope. A shared cache key that ignores the selection would leak a
prior caller's roster. Fixed `tools/list` can retain its existing cache policy.

## 7. Server composition

The server layer owns header parsing because it imports the MCP SDK. It turns
request facts into the core `RootSelection`, then constructs lightweight API
views over the one compiled Index:

```text
MCP request
  -> parse configured header
  -> authenticate Principal
  -> requested selection intersect application ceiling
  -> Disclosure.select(effective)
  -> DisclosureAPI + ExecutionAPI for that view
  -> selected gateway / Prompt / Resource result
```

The Index, bindings, node objects, Channels lifecycle and telemetry collector
are shared. The selection is immutable request data and is never stored on the
server, connection, Index or node. Concurrent requests with different headers
therefore cannot affect one another, and a later request is not influenced by
which branch an earlier request opened.

Suggested code placement:

```text
contexture/core/model/root_selection.py    RootSelection, selected graph facade
contexture/core/model/disclosure.py        selected roots and root-safe resolution
contexture/core/model/system_api.py        selected disclosure and execution refusals
contexture/core/model/runtime.py           bind selected graph during invocation

contexture/server/root_selector.py         header parser and optional ceiling adapter
contexture/server/surface/tools.py         request Context -> selected APIs
contexture/server/surface/prompts.py       selected list/get/completion
contexture/server/surface/resources.py     selected list/read
contexture/server/instructions.py          root-neutral fallback + selected roster
contexture/server/server.py                opt-in selector wiring
```

A public construction shape should keep selection separate from transport
address options:

```python
compiled.server(
    root_selector=HeaderRootSelector(),
    root_ceiling=oc_root_ceiling,       # optional application policy
)
```

`ContextureOptions` continues to describe how and where the server is served.
The selection adapter belongs to the Surface/Server topology, because it
changes what a request can reach.

## 8. Selector language

Version one accepts exact root refs only. Raw regular expressions and exclude
rules should not ship initially.

Exact allowlists fit this model better:

- OC currently has fifteen roots and each Hermes surface names at most seven;
- an exact list is trivial to review in YAML and in a token claim;
- a typo fails instead of silently matching nothing;
- a future root is not automatically granted to an old service;
- matching has no regex complexity, anchoring ambiguity or denial-of-service
  budget;
- a root ref already is Contexture's canonical identifier.

If real applications later have too many roots to enumerate, add a separate
`patterns` selector using anchored shell-style globs and resolve it immediately
against the compiled root snapshot. Do not change the meaning of `roots`, and
do not add deny-only semantics: `exclude=account` would silently expose every
new root added in the future.

## 9. One Creator migration

### 9.1 Target configuration

| Instance | `Contexture-Roots` | Gateway tools after migration |
| --- | --- | --- |
| Conversation | header omitted | discover, open, invoke_read_only, invoke |
| Team | `team` | discover, open, invoke_read_only, invoke |
| Cron | `team,work` | discover, open, invoke_read_only, invoke |
| Information | `information,team,goal,project,work,notebook,memory` | discover, open, invoke_read_only, invoke |

The scoped instances can use normal discovery again. Their prompts may still
start from a known root when that saves a call; the fixed ref is an optimization
rather than the security boundary.

### 9.2 Rollout sequence

1. Implement and release Contexture root selection with conformance tests.
2. Upgrade OC App and enable `HeaderRootSelector` on its existing MCP Surface.
3. Add root ceilings for dedicated Hermes credentials in OC's token verifier or
   RootCeiling adapter.
4. Add `protocol: stateless`, `Contexture-Roots` and
   `contexture_discover` to Team, Cron and Information profiles.
5. Keep each pre-tool hook for one canary release and assert that Contexture
   itself refuses the same out-of-root probes.
6. Remove the three hook files, hook registration, service profile validators
   and duplicated hook tests once the server-side boundary is proven.
7. Keep Tool-level OC permission checks unchanged.

Do not migrate by constructing three new OC Contexture applications or three
copies of ApplicationServices. The purpose of this feature is one authority and
one compiled runtime with several request views.

## 10. Verification matrix

### Contexture kernel

- omitted selection reproduces current golden payloads and errors;
- selected discovery includes exactly the requested roots in Index order;
- opening a selected root exposes its complete immediate sibling set;
- a direct ref under an excluded root is refused by open and both invoke doors;
- `Disclosure.unrestricted()` restores Prompt access without restoring an
  excluded root;
- a cross-root `uses` card is absent unless both roots are selected;
- a Tool's `current_graph()` cannot find an excluded root;
- intersecting selections is monotonic, commutative and idempotent;
- two concurrent selections over one Index never contaminate each other.

### MCP Surface

- request Context is absent from published gateway schemas;
- selected server instructions and `server/discover` name no excluded root;
- Prompt list/get/completion and Resource list/read cannot bypass the root set;
- unknown and outside-surface errors do not enumerate hidden roots;
- request-varying responses are not shared-cacheable;
- stdio fixed selection and HTTP header selection produce equivalent payloads;
- unselected Runtime and Disclosure-only application goldens remain unchanged.

### One Creator

- each of the four instance rows above passes discovery, open and invocation
  probes for every allowed root;
- the same probes fail for every other OC root before the Tool body runs;
- Team can discover `team` and no other root;
- Cron can discover `team` and `work` and no other root;
- Information can discover exactly its seven roots;
- Conversation continues to receive the full surface;
- dedicated token ceilings prevent a caller from widening its configured
  header;
- removing Hermes hooks does not change the accepted/refused matrix.

## 11. Compatibility and release

This is additive when no selector is configured:

- `Contexture(...)`, Manager, Index and declaration syntax do not change;
- missing request selection means all roots;
- the four gateway names and unselected schemas remain unchanged;
- existing stdio and HTTP servers retain their current instructions and
  payloads;
- business authorization remains in Tool code.

Enable request selection explicitly on the OC MCP server for the first release.
Ship it as the next minor Contexture version. The feature is ready to become a
default HTTP capability only after multiple clients prove that custom headers,
modern `server/discover` and cache behavior are carried consistently.
