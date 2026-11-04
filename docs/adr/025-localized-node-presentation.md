# ADR 025: Localized presentation is selected per server surface

- Status: Accepted
- Date: 2026-11-04
- Supersedes: the “per-node titles are later work” boundary in ADR 024

## Context

ADR 024 separated stable protocol identity from human-facing MCP server and
gateway titles. Applications also need product-specific wording for the Role,
Skill, and Tool cards returned inside Contexture payloads, and they may serve
that wording in more than one language.

The ordinary MCP server initialization contract does not provide Contexture a
portable locale negotiation field. Some individual Host extensions do expose
a locale, but making the core depend on one Host would make behavior
non-deterministic and couple application declarations to an adapter.

Node `name` and canonical `ref` are protocol and graph identity. Node
`description` is routing copy read by the model. Role and Skill `instructions`
are executable semantic contracts: translating those independently can alter
obligations and therefore is not a presentation concern.

## Decision

Contexture provides three SDK-independent immutable declarations:

- `NodePresentation(title, description=None)` supplies a display title and an
  optional localized routing description for one canonical node ref;
- `Presentation(..., nodes={...})` describes one complete presentation of a
  server surface; and
- `PresentationCatalog(default_locale, locales={...})` groups presentations by
  normalized BCP-47-style locale tags.

An application selects a locale explicitly when it builds a server. Resolution
tries the normalized exact tag, then progressively less-specific parent tags,
then the declared default. Thus `en-US` can resolve `en`, while an unsupported
locale has deterministic fallback behavior. A plain `Presentation` remains a
language-neutral, source-compatible option.

The selected node presentation is applied to ROUTE cards, INSPECT cards,
ACTIVE child and `uses` cards, and the server root roster. It adds `title` and
may replace the rendered `description`. It never changes `kind`, `name`, `ref`,
input schema, read-only classification, invocation target, or authored
instructions.

Compilation validates the node refs in every catalog locale, not only the
selected locale. This catches stale translation entries before a less common
language reaches production. Locale keys and presentation mappings are frozen
after validation.

One server instance has one resolved locale. A deployment that needs multiple
languages concurrently exposes separate server instances or endpoints. This
keeps MCP initialization metadata, Tool titles, instructions, and disclosed
cards internally consistent for the lifetime of a session.

## Consequences

- Business modules own their vocabulary in an application-level language pack;
  the framework owns selection, fallback, validation, and rendering.
- Machine calls and stored refs remain stable across languages.
- Adding a language is data-only unless an application needs a new locale
  selection source.
- The default locale is explicit rather than inferred from process or machine
  locale, so deployments are reproducible.
- Contexture does not claim that a Host will render standard MCP titles. A Host
  that ignores them may still show its configured connection key and Tool
  machine name.
- Localizing semantic instructions, business results, validation errors, or
  arbitrary Tool output remains a separate application concern.

## Rejected alternatives

- **Rename `name` or `ref` per language.** This would break calls, saved refs,
  telemetry, selection, and cross-language continuity.
- **Detect the process locale implicitly.** The server would vary by machine
  and tests could not prove which language a session receives.
- **Switch locale per Tool call.** Initialization metadata and earlier cards
  would disagree with later responses, and ordinary MCP has no portable locale
  input for every call.
- **Translate instructions through `NodePresentation`.** Instructions define
  behavior, not decoration; an unreviewed translation could weaken or change a
  required procedure.
- **Put OC vocabulary in Contexture defaults.** Product copy belongs to the
  product; Contexture remains reusable and supplies only the mechanism.

