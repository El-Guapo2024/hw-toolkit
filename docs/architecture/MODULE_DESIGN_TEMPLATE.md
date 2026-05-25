# Module Design — `<module/path/here>`

> **How to use this template:** copy to `docs/architecture/modules/<name>.md`. Fill every section. Sections that genuinely don't apply: write "n/a — reason." Don't delete headings — a missing heading hides intent.

---

## Purpose

One paragraph: what this module exists to do. Stick to the *job*, not the *implementation*. If you can't write this in 3 sentences, the module is too big — split it.

## Public contract

What the rest of the repo can rely on. List the surface area:

- **Functions / classes / tools exposed.** Names + 1-line purpose.
- **Input shapes.** Pydantic models, file schemas, CLI args.
- **Output shapes.** What callers receive.
- **Side effects.** Files written, hooks fired, state mutated.

If callers should care about it, it goes here. Anything not listed is *internal* and can change without notice.

## Internal layout

The files inside this module and how they relate. Tree + 1-line per file:

```
<module>/
├── __init__.py        re-exports the public contract
├── foo.py             does X
├── bar.py             does Y
└── tests/             golden values
```

Plus a 1-paragraph note on data flow within the module if it's non-obvious.

## Dependencies

**Imports from:**
- which other internal modules (e.g. `hw_agent.core`, `ee.result`)
- which external libs (e.g. `pydantic`, `lcapy`)

**Imported by:**
- which modules depend on this one

**Forbidden imports** (hexagonal / layering rules):
- e.g. "must not import from `hw_agent.scripts.*` or `mcp_server.*`"
- enforced by CI grep where applicable

## Configuration

How the module gets configured: env vars, config files, runtime args. If none, write "n/a — pure functions."

## Lifecycle / state

If stateful: what state, where stored, when written, when read. If pure: write "stateless — all inputs explicit."

## Failure modes

How this module fails and what the caller sees. Be honest about the messy parts:

- What happens if external dep is missing?
- What happens on malformed input?
- What error types are raised; which are caller-fault vs internal?

## Performance characteristics

If the module has known cost — write it down.

- Typical call latency
- Memory profile
- Caching policy

If "fast and cheap, no surprises" — write that.

## Testing

How this module is verified.

- Unit test location (`<module>/tests/`)
- Golden values used (where, why those numbers)
- Integration tests that cover this module

## Open questions / known limitations

The honest list of "not figured out yet" + "things this can't currently do."

## Related

Links to:
- Upstream design rationale (`../investigations/<...>.md`)
- Sibling modules this collaborates with
- Implementation phase in `gradual-implementation-plan.md`
- Tickets/issues if any

## Status

One line:
- `planned` | `in-progress` | `mature` | `deprecating` | `frozen`
- Last meaningful update date.
- Owner (person responsible).
