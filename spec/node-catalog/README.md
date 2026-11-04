# Node Catalog extension corpus

This directory and `../golden/node-catalog.json` form the language-neutral
conformance corpus for `contexture.node-catalog/v1`. They are intentionally
separate from the immutable 0.16 MCP wire inventory in
`../porting/assets.json`: Node Catalog is a trusted application/developer read
model and changes no gateway payload.

`fixtures/reference-catalog.json` selects the existing language-neutral
reference application, model view, and definition detail. A binding constructs
that application through its native declaration API and must produce
`../golden/node-catalog.json` semantically, including the canonical content
digest. Object key ordering and indentation are not normative; node and member
array ordering is normative.

`canonicalization-vectors.json` covers rules that ordinary ASCII fixtures can
miss. In particular, object keys are ordered by Unicode code point rather than
UTF-16 code unit, so JavaScript implementations must not use the default
string sort for non-BMP keys.

The normative local shape is `../node-catalog.schema.json`. Cross-node
invariants that JSON Schema cannot express—unique refs, exact roots/counts,
parent/member/ordinal agreement, and reciprocal dependencies—are normative in
`../model.md` and must be checked by the producer. Runtime and disclosure-only
mode behavior, surface projection, contributors, and request context isolation
require focused binding tests in addition to this corpus.
