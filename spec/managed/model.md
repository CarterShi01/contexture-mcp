# Managed graph model — target v1 draft

Status: specified design draft, not an accepted release contract or a claim of implementation.
Source: detailed design D00–D25 and implementation plan dated 2026-11-05.
This document uses MUST/SHOULD to describe the target, not existing SDK capabilities.

## Authority and scope

This additive profile does not replace [the static model](../model.md), its
fixtures, or its golden bytes. D25 resolves earlier design summaries. Acceptance
requires reviewed English specifications, schemas and executable vectors pinned
to an accepted revision; that revision is not yet assigned. Candidate schemas and
raw/reference vectors are supplied in [schema contracts](schema-contracts.md).
They are not accepted; behavioral managed fixtures/golden remain pending.
See [protocol](protocol.md), [FileJournal](file-journal.md),
[conformance](conformance.md), [decisions](decisions.md) and [manifest](manifest.json).

The product target includes typed forests, all graph operations, transactions,
event history, diff/revert, default file persistence, hot release, leases,
pagination/watch, Catalog, management/CLI/debug, native Python/TS/Go and governed
OC adoption. A smaller mutation-region product is not an equivalent substitute.
Regions/scopes are optional authoring views. MCP is a transport, not graph owner.
Core is independent of SDKs, HTTP, filesystem adapters and OC business objects.
No database driver/service/broker is a default dependency.

The local Python `ManagedRequest.projection_snapshot()` API supplies trusted
Host projections with this request's immutable plan, actual revision/generation,
fresh verified context and current authorized model IDs. It reuses the normal
current-index authority and closed/cross-task/run guards; a retained old request
does not retain permission for removed or revoked IDs. The complete plan is
trusted custody, **not a filtered wire response**: consumers must filter by
`authorized_ids`, keep their explicit lifetime and continue domain authorization.
The snapshot grants no permission, execution or renewed lease. This additive
local API does not assign an accepted managed revision or publication protocol.

Out of scope: arbitrary source evaluation, hot native classes, dependency
installation, mandatory Wasm, cross-graph/global transactions, CRDTs, multiple
writers, replay of Tool effects, unbounded historical Sessions, full incremental
compilation, persistent maps and packed-log migration. These exclusions do not
remove ordinary CRUD, history, release or P0 performance obligations.

## Scoped complete-candidate replacement

Candidate M23, not accepted: scoped import/replace carries a complete normalized
GraphDefinition, inline or verified through the payload port, not a sparse patch.
The transaction implementation's endpoint algorithm is:

1. Require nonempty explicit anchor IDs, each present in at least one endpoint.
	Compute A and B as the containment-descendant closure of those anchors in the
	before and candidate graphs respectively, including each present anchor.
	Uses, templates and publications do not enlarge containment closure.
2. Reject existing identities crossing the boundary: `(B-A) intersect before IDs`
	and `(A-B) intersect candidate IDs` must both be empty. Explicitly enlarge
	anchors or use a separate ordered move/repair rather than smuggling a move.
3. Ordinary payloads preserve application_name. Always preserve schema,
	semantic_version and graph_id. Partial scope also preserves mode/extensions;
	only scope covering every node at both endpoints may change those fields.
4. Require the exact same outside ID set. For each outside node compare complete
	normalized JSON after removing only containment edges targeting its endpoint
	scope: omit scoped single-slot values and filter scoped ordered-slot entries.
	Preserve all other fields, uses, templates and outside member relative order.
	Boundary owners receive no general field/reference editing permission.
5. Filter roots by scoped node_id and publications by scoped opens_id, then
	require exact equality of the remaining ordered lists. A retained publication
	ID must not cross from an outside target to inside or vice versa.
6. For partial scope preserve default_locale. Filter scoped presentation entries
	from every existing catalog; preserve the resulting catalogs, including empty
	ones and relative order, exactly. New catalogs must be nonempty and contain
	only candidate-scoped entries. Complete scope may replace presentations.

All comparisons use managed canonical JSON equality, not object identity or
partial field comparison. Passing this boundary check does not bypass complete
candidate validation, identity retention, mutability, reference closure, budgets,
current admission, approval or base-revision CAS. No partial candidate is appended.

## Identity, counters and immutable state (D03–D04)

`graph_id` is persistent application identity explicitly bound to stable
`application_name`, not inferred from a title. `node_id` and `publication_id`
are opaque, graph-scoped identities; generated IDs may use UUIDv7. They MUST NOT
be reused for different logical objects. Rename/move preserves identity.
`display_path` is a revision-derived slash path, never durable identity.
Graph/node author IDs are Unicode scalar strings of 1..256 UTF-8 bytes without
normalization. A retained deletion preserves identity and creation policy.

| Identity | Exact scope |
| --- | --- |
| `revision` | Graph definition change counter; initial 0, bootstrap first yields 1 |
| `journal_seq` | Store-wide complete record counter; first record is 1, including receipts/releases |
| `release_epoch` | Graph+target intent counter; initial 0, rollback also increments |
| `generation_id` | Fresh UUID for every local build, associated with owner boot; not revision |
| `instance_id`, `boot_id` | Configured instance identity and fresh startup identity; observations carry both |
| `definition_digest` | Normalized definition content, excluding revision/time/actor |
| `manifest_digest` | Exact compiler/registry/binding/artifact/config execution identity |
| `catalog_digest` | Authorized view/detail/locale/scope content, not definition or manifest identity |

Persistent counters use canonical decimal JSON strings matching `0|[1-9][0-9]*`,
maximum 10^20−1. No signs, leading zeros, JSON numbers or wrapping. Only disk
names use 20-digit zero padding. Revision 0 is an unseeded management projection,
not a committed empty serving forest. Every committed definition has at least
one model-visible root; drafts may temporarily be empty.

M18 candidate now gives this distinction an executable representation:
UnseededState has no GraphDefinition; its before digest hashes JSON null under
contexture.graph/v1. GraphSeeded plus trusted bootstrap provenance produces the
first nonempty definition at revision1. This is not review accepted; see
[closure contracts](schema-contracts.md#m17m21-closure-and-transactionfilejournal-integration).

`GraphDefinition` contains `schema=contexture.graph/v1`, `semantic_version`,
`graph_id`, `application_name`, `mode=runtime|disclosure_only`, `nodes`, ordered
`roots`, ordered publications, presentations and namespaced extensions. Nodes
serialize by node ID Unicode code-point order. Array declaration order is
semantic. Drafts are editable; accepted definitions are deeply immutable.
Unknown core fields are rejected, absent defaults normalized before hashing,
optional absent values omitted and allowed explicit null distinct from absence.

The local Python static lowerer accepts explicit `node_extensions`, keyed by
stable node ID, for inert namespaced JSON metadata. Unknown IDs, invalid extension
namespaces and non-JSON values are rejected. Values are detached into the immutable
definition and participate in its digest. The lowerer does not discover or invoke
Catalog contributors, infer handlers, or convert metadata into authorization.
Omitting the map preserves the existing empty extensions and static Catalog bytes.

The local Python authoring API accepts `mutable=True`, `mutable=False`, or
`mutable="inherit"` on Role, Skill, and Tool construction. The default is
`"inherit"`; this metadata does not change legacy disclosure cards or Catalog
bytes. Static lowering copies the declared policy into immutable NodeSpec
facts. Its trusted mutability map can resolve an inherited declaration, but
cannot override an explicit authored true/false policy. A false ancestor still
locks descendants even if a descendant declares true. Changing accepted
creation policy remains an approved migration, not an ordinary update command.

## Closed node kinds and containment (D04–D05, D25.1)

Every `NodeSpec` has node ID, closed `kind=role|skill|tool`, nonempty explicit name
and description, `mutable=inherit|true|false`, ordered `uses`, kind definition and
extensions. Names contain neither slash nor control characters. No class-name
inference. Future kinds require semantic/schema versioning.

Role/Skill MUST supply exactly one of literal `instructions:string` or
`instruction_template:{parts:[Text{text}|NodeRef{target_id,label?}]}`. A switch
removes the other field in the same transaction. Skill cannot contain nodes and
is followed by the model, not executed by the framework. Role variants are
`ordinary|pre_process|post_process`; all remain Roles.

| Role slot | Cardinality and accepted kind |
| --- | --- |
| `pre_process` | 0/1 Role with matching pre-process variant |
| `children` | Ordered Roles of any variant |
| `post_process` | 0/1 Role with matching post-process variant |
| `skills` | Ordered Skills |
| `tools` | Ordered Tools |

Flatten order is pre→children→post→skills→tools. Any Role variant can be a root
or ordinary child; only the matching dedicated slot creates designation.
Process Roles may contain their own process members without automatic recursive
execution. Singleton occupancy cannot implicitly replace an existing member.
Typed slots alone own containment; parent, ordinal and rendered ref are derived.
No duplicate member, shared instance, alias or multiple parent. Names are unique
across all five slots of one parent, not separately within each kind.

Each ordered root is `{node_id,plane:model|prompt}`; root names are unique across
kinds and planes. Preserve declaration order without kind regrouping. Every
nonroot belongs to exactly one Role slot; every node is reachable in an acyclic
forest. Plane derives from the root. `uses` targets exist, are unique/nonself and
may form cycles; they widen neither selection nor permission nor execution.

Runtime Tools require a binding descriptor; an optional `input_contract` is an
assertion against the registry-owned validator/schema/default/coercion/effect,
not a second editable execution contract. Disclosure-only Tools are structural
cards with no fabricated schema, effect or execution availability.

## Publications, planes and presentation (D05, D25.1/D25.4)

Prompt has publication ID/name/description/`opens_id`/`model_may_open` and is a
user-controlled entry into any declared target. Model open/invoke into a prompt
plane is always refused, even when `model_may_open=true`. In the model plane,
false reserves model open/inspect; it is not itself Tool invocation authority.
Resource has ID/name/URI/MIME/target and only targets an argument-free read-only
runtime Tool. Route has ID/method/path/target, explicitly allowlisted: GET/HEAD
read-only, writing methods writing Tools. No arbitrary-ref business REST door.
Public names, URIs and method+path pairs are unique in their respective domains.
Targets must exist; deletion cannot leave a dangling publication.

Disclosure-only mode rejects execution bindings, Channels, Resources and
executable Routes; Prompts and structural Tool cards remain allowed. Runtime and
disclosure-only instances do not share node instances, Indexes or lifecycle.
Channels are not writable GraphDefinition state. A deployment provider pins
`dependency_contract_id` plus build in the execution manifest; changes require a
new release, never a lookup into a mutable author's latest Channels object.

Pre/post contracts surround only their owner's ACTIVE instructions and identify
the actual returned view ref. They do not execute hooks, inherit an ancestor
process, or guarantee Agent compliance. ROUTE/INSPECT contain neither programs,
schemas nor process designations/contracts. Batch INSPECT is atomic and ordered.
Ordinary Role open retains complete immediate member groups; explicit managed
paging is a separate capability, not silent truncation.

Presentations use normalized locale catalogs and stable node IDs with explicit
default locale. Resolve exact locale, parent tags, then default; validate every
locale, including inactive ones. One Host generation uses one resolved locale.
Only human title/routing description changes, not IDs, name, instructions,
schema or effect. Static author paths lower to IDs, never reverse-engineer from
effective Catalog text. Gateway titles, Host transport and credentials are
deployment/surface configuration, not ordinary node edits. MCP has no assumed
portable locale negotiation field.

Typed NodeRefs participate in existence checks, reverse indexes, deletion/restore
closure and impact. Render only actual returned refs; labels are human copy.
Missing/invisible targets refuse the effective view without leaking identity.
All target presentations must be explicitly removed in a deleting transaction;
templates and publications must also be explicitly repaired. Restoration uses
the exact candidate, not an implicit recreation of all old translations.
Literal static paths retain static meaning. Managed migration inventories all
authored/generated prompts/bundles and manually classifies executable references
into typed parts or explanatory text. No regex rewrite or bare-path fallback;
unresolved inventory must be zero for G1, not a proof about arbitrary language.

## Static creation policy and current authority (D06, D25.2)

Declared mutability defaults to inherit and is fixed at creation. A root without
an enabling ancestor defaults false. Any explicit false on the path blocks all
descendants, including a later true; otherwise true enables inheritance. Store
the declaration, not just its effective value. Ordinary mutation cannot edit it.
Bootstrap/root creation requires separate host authority, not a self-grant.

Protection is judged on the final base/candidate diff, in this fixed order, with
one structured diagnostic `{code: <rule>, node_id}` under `mutation_protected`:

1. `locked_deletion`: the deletion closure contains an effectively locked node.
2. `creation_policy` (`identity_conflict`): kind, role variant or declared
   mutability changed.
3. `barrier_move`: a node leaves or enters a parent at/under `mutable=false`.
4. `locked_surface`: a locked node's OWN surface changed. That surface is its
   definition with only its locked members, its display path, parent/slot,
   plane, graph metadata, publications and presentations.
5. `effective_mutability`: an existing node's effective mutability flipped,
   except inside a subtree whose root membership changed (host-gated
   `graph.roots` bootstrap authority).
6. `barrier_create` / `locked_create`: a new node's parent is at/under a
   barrier, or the parent is locked and the new node is not effectively mutable.
7. `locked_order`: a locked member changed relative order or was crossed.

Consequently, mutable members of a locked (non-barrier) parent are fully
editable: rename, description/instructions, reorder among themselves, move
between non-barrier parents and delete; a locked parent may gain new mutable
members. A locked node's effective agent context may change through a mutable
direct member/use/template target (its card, definition or publications).
Such locked nodes are not refused; they are reported as `protected_dependents`
in the protection report and preview receipt and are part of admission
`impact`, so current admission/approval must cover them. This is one hop, not
transitive. Root add/remove/reorder additionally requires `graph.roots`;
link/unlink requires a separately permitted target reference.

Replace/restore/import/revert all pass the same base/candidate diff checks: no
same-ID recreation, kind switch or creation-policy reset. Ancestor edits that
change protected display path, plane, process/disclosure or actual execution
surface are rejected unless a trusted migration admits the exact change.
Normal generation-qualified ref rotation is not a protected author-path change.
No invented inheritance of every ancestor's instructions is assumed.

Admission receives verified actor, graph, operation, base/candidate digests,
complete impact, approval proof and authority revision. Actor/credentials are
context, never model arguments. Preview and commit both check authority;
changed authority requires revalidation or refusal. The linearization boundary
is the Host's successful current decision. External policy and file append are
not a distributed transaction; stronger revocation needs owner version/fencing.
Natural-language safety still requires business review/evaluation/handler checks.

## Binding and execution identity boundary (D13)

Descriptor fields are `kind=registered|remote`, binding ID/version, config,
secret handles, optional expected contract digest and portability. Trusted code
builds an immutable registry generation owning factory/handler/validator/schema,
effect, ABI/protocol, implementation artifact digest, allowed config and resource
sharing/cache policy. Invocation resolves pinned entries, never latest-by-key.
Unknown version/key, effect/schema mismatch or disallowed config refuses prepare.
Definition equality does not imply execution equality when compiler/registry
changes. ExecutionManifest covers compiler contract/build, registry, exact
entries, dependency contract and environment compatibility; no secret material.

Native nonportable bindings explicitly name language/version/factory and do not
promise closure serialization. Portable primitive decoder/default/coercion
semantics require a separately accepted, fixture-proven subset. Management HTTP,
MCP and native adapters share ports but do not define that subset by accident.
Pure compile creates immutable structural/selector/process/publication plans;
resource prepare may acquire resources but MUST NOT run business writes or start
background consumers. Disclosure-only compilation resolves no executable binding.
Deep freezing does not freeze external business state; handlers remain reentrant.