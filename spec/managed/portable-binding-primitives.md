# Candidate portable binding primitives

This is bounded data-behavior evidence for Q04/M14, not an accepted managed ABI,
permission, installed-worker attestation, or arbitrary native-code portability.
The [shared corpus](referencevectors/portable-binding-primitives.json) is a new
candidate asset; no accepted static fixture or golden is changed.

## Explicit subset

Raw arguments are a closed object with optional `count` (integer or string) and
`label` (string). Missing count defaults to 2; missing label defaults to
`"default"`. Explicit null is not absence. Empty labels and count zero survive.
JSON Schema integers include integral binary64 numbers such as `2.0` and
`-0.0`; decoders normalize them to native integer values without accepting
fractions or boolean-as-number coercion.
String counts must be ASCII canonical decimals of at most two digits, optionally
negative; `-0`, signs on positive numbers, leading zeroes, whitespace, fractions,
exponents and Unicode digits are refused. Decoded count must be an integer in
the closed interval 0..9. Defaults and coercion belong to captured native decoder
code, not JSON Schema. Schema validation runs before decoder dispatch.

The read-only handler returns count, label and the actor from the supplied
trusted request context. It checks the captured resource. Each observation
records exactly which stages ran; schema/decoder/validator refusal must never
dispatch the handler. Raw input and returned JSON are detached. The 27 cases
include accepted defaults/boundaries/Unicode, and rejection at every stage.

## Native execution and limits

Python executes the existing runtime schema preflight and
`PythonManagedCallAdapter` on a registry-resolved binding. TypeScript and Go use
their captured `BindingRegistration.call` / `Call` pipelines, which include the
same raw-schema preflight. The three ABI names and binding/artifact identities
remain native; their contract digests are not claimed equal.

The [differential runner](../../scripts/verify_managed_binding_primitives.py)
executes all three native tests with explicit repository roots and a fresh
`--output` directory. The native tests emit their own observations when
`CONTEXTURE_BINDING_OBSERVATIONS` names an output file. The differential runner
compares canonical observation bytes, verifies candidate fixture snapshots are
byte-identical, and captures source, test, dependency-lock and raw-output hashes.
This finite corpus proves only this explicit subset, not the completeness of
every JSON Schema keyword or arbitrary native default/coercion behavior. Normative
review acceptance and production adoption remain separate.
