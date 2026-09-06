# spec

[简体中文概览](README.zh-CN.md) · [Project README](../README.md)

What every implementation of Contexture has to reproduce, stated as files
rather than Python implementation details.

Prose is not enough to keep three implementations saying the same sentence to
an agent: a port that quietly drops the recovery half of a failure message
still starts, still answers, and no test goes red. So the shared half is
captured as bytes.

```
fixtures/   language-neutral declaration inputs for every implementation
golden/     the exact payload and the exact sentence the bundled demo produces
model.md    the language-neutral public Application/Role/Skill/Tool model
conformance.md  behavioral rules every language binding must reproduce
bindings.md language-specific authoring notes; explicitly non-normative
```

`golden/` is produced from `contexture.demo`, which is the reference
declaration — three roles, one of them a coordinator, six tools, two skills,
two documents and one command. It covers every branch an agent can reach:

| file | what it pins |
| --- | --- |
| `instructions.txt` | what a host reads before it calls anything |
| `tools.json` | the four entry points, their descriptions and their hints |
| `prompts.json` | the declared commands, plus `goto` |
| `resources.json` | the published addresses |
| `discover.json` | the roots, as cards |
| `open.json` | every node in the forest, opened |
| `refusals.json` | all five lookup failures and both wrong-door refusals |
| `commands.json` | what a person reads when they run a command |
| `reads.json` | what a host gets from a resource read |
| `completions.json` | what a person is offered while typing a ref |

Regenerate deliberately, and review the diff — these bytes are the contract:

```
.venv/bin/python tests/golden.py --update
```

`fixtures/reference-application.json` is the smallest language-neutral runtime
declaration. The other fixtures pin Prompt-only roots, request-selected root
views, disclosure-only compilation, and explicit REST publication. A port maps
them to its own classes, structs, or schema objects, then checks behavior
against the conformance rules and golden surface. Fixtures describe Tool inputs
but not Tool bodies: business execution is binding-specific; the public model
and protocol behavior are not.

The Python package is currently the only shipped binding. The
[TypeScript](https://github.com/CarterShi01/contexture-mcp-typescript) and
[Go](https://github.com/CarterShi01/contexture-mcp-go) repositories are guarded
scaffolds, not installable implementations. The PHP row in `bindings.md`
remains compatibility guidance only.
