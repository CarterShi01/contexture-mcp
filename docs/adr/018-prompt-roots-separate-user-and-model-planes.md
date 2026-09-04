# ADR 018 — Prompt roots separate the user and model planes

## Decision

`Contexture` accepts `prompt_roots=` alongside `roots=`. Both sets are compiled
into one Manager and one canonical Index. The ordinary roots form the
model-controlled forest. Prompt roots form a user-controlled forest that MCP
Prompts and host-side navigation may read, but the model gateway may neither
discover, open, nor invoke.

The server instructions roster follows the same filtered Disclosure used by
`contexture_discover`. Invocation checks the root again, so knowing or guessing
a hidden ref cannot bypass progressive disclosure. Host-controlled Prompt and
Resource rendering uses an unrestricted view of the same Index; no business
node is copied.

## Why

MCP Prompts are user-controlled while MCP Tools are model-controlled. Publishing
a command as both a Prompt and a discoverable Role gives the model a duplicate
entrance and changes who decides to run it. It also makes root discovery compete
with business capabilities that the agent actually needs to choose among.

A per-Prompt `model_may_open` flag is too narrow: it hides one opened procedure
but still exposes its root, siblings, and guessed Tools. Separate Indexes are
also wrong because a Prompt would then render a projection rather than the same
declaration inspected and validated everywhere else.

## Host compatibility

Hosts that present MCP Prompts can expose them directly as commands. A host that
does not present Prompts may add a human-triggered adapter, but that adapter must
call the Prompt plane. Contexture does not compensate by duplicating commands in
model discovery.

## Compatibility

Applications that use only `roots=` retain the prior surface. The new field is
optional. This boundary is released in 0.10.0 because it adds a public
Application capability without changing existing declarations.
