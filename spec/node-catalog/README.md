# Node Catalog extension corpus

This directory and `../golden/node-catalog.json` form the language-neutral
conformance corpus for `contexture.node-catalog/v1`. They are intentionally
separate from the immutable 0.16 MCP wire inventory in
`../porting/assets.json`: Node Catalog is a trusted application/developer read
model and changes no gateway payload.

`fixtures/reference-catalog.json` selects
`fixtures/reference-demo-application.json`, model view, and definition detail.
The selected input records the existing native `contexture-demo` author facts,
ordered members/uses and exact bound input schemas, including authored trailing
newlines. It is not the smaller `reference-operations` prototype in
`../fixtures/reference-application.json`: that application cannot produce this
12-node demo golden. Both old prototype input and the Catalog golden remain
unchanged. The Python producer verifies the selected input against the same
native declarations before producing the Catalog. A binding constructs
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
