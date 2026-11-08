# Managed runtime: a live, versioned context tree

[简体中文](managed-runtime.zh-CN.md) · [Documentation](../README.md) · [Hosting](hosting.md)

A static Contexture application is compiled once and served frozen. The managed
runtime is for trees that keep changing **while agents use them**: a new Skill,
a renamed Role, a retired Tool, a reorganized project area. It keeps four things
on one auditable version chain:

| Concept | What it is |
| --- | --- |
| **Definition** | The accepted graph at a revision: Roles, Skills, Tools, relations, publications. Changed only by transactions. |
| **History** | Every accepted revision, replayable from the journal alone; per-node history, stable-ID diffs, reverts. |
| **Release** | An explicit request to serve a revision. Committing a change never silently changes what agents get. |
| **Generation** | The immutable compiled snapshot actually serving. Requests pin one generation for their whole lifetime. |

Status: 2.0.0. The managed specification in [`spec/managed`](../../spec/managed/)
is accepted as the v1 baseline with this Python implementation as reference;
TypeScript/Go ports follow its port obligations.

## Run the example

```bash
uv run python examples/managed_dynamic_tree.py
```

[`examples/managed_dynamic_tree.py`](../../examples/managed_dynamic_tree.py)
walks the whole loop: seed → publish → add a Skill and a new Tool while a
request is open → publish (the open request keeps its generation) → node
history and diff → revert → upgrade a Tool binding in one transaction → reopen.

## Compose a host

`ManagedGraphHost` is the supported composition root:

```python
from contexture.core.graph.bindings import AppDependencyContract, RegistryGeneration
from contexture.server.managed_host import (
    CompilerProfile, ManagedGraphHost, SingleActorPolicy, python_tool, tool_binding,
)

async def search(arguments, context, resource):
    return {"hits": [...]}

SEARCH = python_tool("search", search, version="1", artifact="my-app:search@1",
                     input_schema={"type": "object", "properties": {"query": {"type": "string"}}})
registry = RegistryGeneration(build="my-app-1", registrations=[SEARCH])

async with ManagedGraphHost.open("/var/lib/my-app/graph", graph_id="assistant",
                                 policy=SingleActorPolicy(), registry=registry,
                                 profile=CompilerProfile(AppDependencyContract("my-app", "1"))) as host:
    if not host.initialized:
        host.bootstrap(seed_definition)      # revision 1
        await host.publish()                 # serve it
    else:
        await host.wait_ready()              # resume the durable desired release
```

* **Store**: a private directory on local ext4/xfs/btrfs (strict durability),
  outside any git checkout. One process owns it (an OS lock on `writer.lock`).
  Never delete `writer.lock` while a process runs: the running owner stops
  writing as soon as its lock file is removed or replaced.
* **Registry**: every deployed executable binding (code you ship). Graph edits
  can only *reference* registered bindings; new code is a deployment, not an edit.
  Execution is attested per exact binding contract, not per node, so a Tool node
  created at runtime against a deployed binding runs without a restart.
* **Policy**: `HostPolicy` makes every governance decision (identity, admission,
  approvals, authority fence, per-request authority). `SingleActorPolicy` allows
  everything for one local actor: **development only**.

## Change the tree

```python
receipt = host.change(
    {"op": "create_node", "node": {"node_id": "summarize", "kind": "skill", "name": "summarize",
     "description": "Summarize", "instructions": "Summarize the hits.", "mutable": True}},
    {"op": "attach_node", "node_id": "summarize",
     "target": {"parent_id": "skills", "slot": "skills", "position": 0}},
    reason="add a Skill")
```

A transaction is atomic: all commands apply or none do, and only the final graph
is validated (a transient detach is fine). It carries the expected base revision
(stale bases get `revision_conflict`) and an idempotency key (a retry returns the
original receipt; the same key with a different payload is `idempotency_conflict`).
Use `host.preview(...)` to inspect `definition_diff`, `impact` and
`protected_dependents` before committing.

Commands: `create_node`, `update_node`, `rename_node`, `attach_node`,
`detach_node`, `move_node`, `reorder_members`, `delete_nodes`
(`reject_references` or `explicit_cascade`), `restore_nodes`, `add_use` /
`remove_use`, `add_root` / `remove_root` (host `graph.roots` authority),
`put_publication` / `remove_publication`, `put_presentation` /
`remove_presentation`, and the scoped `replace_subtree` / `import` / `reconcile`.

### What may change: mutability

Each node declares `mutable` at creation (`"inherit"` by default, fixed forever):

* `mutable: false` is a **barrier**: the node and its whole subtree are frozen.
* A root left at `inherit` is **locked**. A locked node protects its own
  definition, display path, plane, publications/presentations, and the presence
  and relative order of its locked members.
* `mutable: true` opens an editable area. Mutable members of a locked parent can
  be renamed, re-described, re-instructed, reordered (not across a locked
  sibling), moved between non-barrier parents and deleted. A locked parent may
  gain new members only if they are `mutable: true`.
* When a mutable member, use or template target changes, the locked nodes whose
  agent-visible context changes are listed in `protected_dependents` and are part
  of admission `impact`: your policy decides, nothing is silently refused.

Refusals are `mutation_protected` with one structured diagnostic naming the rule
and node: `locked_deletion`, `creation_policy`, `barrier_move`, `locked_surface`,
`effective_mutability`, `barrier_create`, `locked_create`, `locked_order`.

## History, diff and revert

```python
host.node_history("summarize")       # revisions that changed this node, by stable ID
host.diff("3", "4").as_json()        # five groups: nodes, membership, uses, publications, locales
host.revert("3", node_ids=["summarize"], reason="undo rename")   # a NEW revision
```

Renames and moves keep the node ID, so history and diffs align across them.
A revert is an ordinary, admitted transaction judged against the current
protection rules; it never rewrites the past. For what *executes*, compare
compiled artifacts: `execution_diff(manifest_a, manifest_b)` (compiler, registry
and per-binding entries) and `surface_diff(plan_a, plan_b)` (effective
instructions, presentations, processes) in `contexture.core.graph.diff`.

## Export, import and merge

All three are framework operations on graph objects only (nodes, relations,
publications, presentations, binding *references*); no business data moves.
Imports and merges produce ordinary transactions: preview, admission,
mutability protection, CAS and idempotency all apply.

```python
bundle = host.export_bundle()                       # whole graph
part = host.export_bundle(node_ids=["skills"])      # containment subtrees
other.bootstrap_from(bundle.canonical_bytes())      # seed a new graph
other.import_bundle(part, target={"parent_id": "area"}, reason="add skills")
other.import_bundle(part, target={"parent_id": "area"}, reason="copy",
                    identity="remap", prefix="copy-")
result, receipt = host.merge(staged_bundle, base_revision="12", reason="merge staging")
if not result.clean:
    print([c.as_json() for c in result.conflicts])  # resolve explicitly and merge again
history = host.export_history()                      # all events from bootstrap
fresh_host.import_history(history.canonical_bytes()) # recreate graph + history in a new store
```

* **Bundles** (`contexture.graph-bundle/v1`) carry a self digest and their
  source (graph id, revision, definition digest). Subtree bundles list
  references that leave the exported scope as `external_refs`; on import each
  must map to an existing node (`external={...}`, default: the same ID).
* **Identity**: `identity="preserve"` (default) refuses any ID that is live or
  retained (tombstoned) in the target. `identity="remap"` renames by `remap`
  (explicit map) and/or `prefix` (also prefixing publication names) and
  rewrites every in-bundle reference.
* **Executables** are binding references; the target registry must vouch for
  them or the import is refused (`binding_contract_required`).
* **Three-way merge** aligns by stable ID (renames and moves keep identity):
  one-sided changes are taken, identical changes accepted, differing changes and
  delete/modify are conflicts. Ordered relations merge membership three-way and
  keep the side whose order changed (both changed differently: conflict). The
  merged graph must validate; otherwise a `structure` conflict. Resolve with
  `resolutions={"node:s1:description": "theirs" | "ours" | {"value": ...}}`.
  `base_revision` is the fork point in *this* graph's history (it defaults to
  the bundle's source revision when the bundle came from this graph).
* **History bundles** (`contexture.history-bundle/v1`) are verified by pure
  replay. Import needs host `graph.bootstrap` authority, an approval over the
  bundle digest, and a graph that does not exist yet.
* **CLI**: `contexture managed` read commands `export`, `export-history`,
  `plan-import`, `plan-merge` (the plans return a transaction to `preview` and
  `commit`), and the write command `import-history`.

## Publish and hot swap

```python
await host.publish()                 # release the head and wait until served
async with host.runtime.request() as request:
    await request.discover()
    await request.invoke_read_only(request.reference("search"), {"query": "x"})
```

`publish` compiles the head against the registry, prepares resources outside the
owner lock, and swaps the local pointer atomically. A request opened before the
swap keeps its generation (handlers, schemas, Catalog) until it ends; the old
generation's resources close once, after its last request drains. A failed
preparation keeps the last known good generation; the cause is in
`host.releases.status().last_error`.

### Upgrading Tool code

Ship the new code as a new binding version next to the old one, then switch the
Tool in one transaction and publish:

```python
host.change({"op": "update_node", "node_id": "search",
             "set_fields": {"binding": tool_binding(SEARCH_V2)}, "unset_fields": []},
            reason="search v2")
await host.publish()
```

Registry evidence is checked only for new candidates; the journal replays without
any registry, so retiring v1 later never makes the store unreadable.

## Operations

* **Logging** (stdlib `logging`, no payloads or secrets): `contexture.managed.host`,
  `.owner` (writes stopped), `.journal` (recovery quarantine, lock loss),
  `.serving` (generation activated / preparation failed), `.release`
  (refused attempts with error code).
* **Errors that stop writes**: `commit_unknown` (outcome uncertain, e.g. disk
  error during publication) and `store_in_use` (another writer, removed lock).
  Close and reopen; recovery resolves the durable suffix. Never retry with a new
  idempotency key to "make it work".
* **Startup and snapshots**: every `snapshot_every` records (default 1000) and on
  a clean close the host writes an owner snapshot and a sealed journal index
  (`GraphOwner.checkpoint()`). On open, the journal checks the record sequence
  is contiguous, fully verifies the seal's anchor and every record after it,
  and takes the prefix hashes/receipt keys from the seal; the owner restores
  the snapshot and replays only the tail. Both are disposable hints, never
  authority: any mismatch means full verification and replay. Prefix record
  bodies are re-verified against the sealed hashes whenever they are loaded.
* **Bounded memory**: the journal keeps one hash (plus batch id and receipt
  key) per record and an LRU of recent bodies; each graph keeps the newest
  `history_window` revisions (default 256). Older revisions, full history,
  node history and reverts are rebuilt from the journal on demand and not
  retained; a watch cursor older than the window gets `resync_required`.
* **Offline store tools** (with the service stopped):
  `contexture store doctor --root STORE` verifies in place without modifying the
  store: layout and permissions, the full record chain and receipts, every
  referenced blob, and a pure replay of graph semantics (snapshots ignored).
  `--isolated` additionally restores a copy (needs ~2-3x the store size).
  `backup --output FILE` / `restore --input FILE --allow-write` produce and
  restore an isolated copy; `--max-store-mib` raises the backup size bound.
* **Management CLI**: `contexture managed --project DIR ...` drives a Host-provided
  management service; refusals include the stable code and, for protection
  failures, the rule diagnostics.

## Current limits

* One writer process per store; no multi-replica rollout or cross-graph atomicity.
* Startup after a seal is O(tail) plus a directory listing; without a seal (or
  with `trust_index=False`) every record is read and verified (about 1 ms per
  record on a small machine). Disk use grows with history (one file per
  record; no compaction yet). Backups are a single in-memory envelope bounded
  by `--max-store-mib`.
* Only `python-managed-call/v1` execution is built in; other ABIs need an
  explicit `ExecutionAdapter`.
