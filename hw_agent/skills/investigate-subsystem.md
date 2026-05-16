Write a per-subsystem investigation report capturing **why this specific part won**, **what alternatives were considered**, and **what tradeoffs were accepted** — all grounded in real JLCPCB parts and `verify_candidate` results, not LLM training memory. Run AFTER `subsystem_choose_part` has committed a part.

## Execution context — this skill always runs in a subagent

The orchestrator (`full-board-design.md`) dispatches this skill via an `Agent` tool call. You are running in a subagent's isolated context. Your final user-facing message becomes the confirmation the orchestrator reads.

This skill writes a *file*. The orchestrator doesn't need to see the file's content — it just needs to know (a) the file was written, (b) any noteworthy findings during write-up. Keep your reply short.

## Arguments

`$ARGUMENTS` is `<project_slug> <subsystem_name>`. Both required.
Example: `/investigate-subsystem robocar_hub buck_5v`.

## Output Path

Write to **`docs/projects/<project>/components/<category>/<name>/investigation.md`** — alongside `schematic.svg` / `schematic.png`. Get the category from `subsystems/<name>.json`'s `category` field. Replace any prior `investigation.md` — investigations reflect current state, not append-only history.

## Hard rules (non-negotiable)

1. **NO PART NAMES from memory.** The "Alternatives considered" table must come from `jlc_search`, `sensor_recommend`, or distributor cross-reference (`digikey_get_part` / `mouser_get_part`) — never from training data. Memory-pulled MPNs (TI/Nordic/Bosch/Broadcom etc.) may not even be stocked or may be EOL; the discipline catches that.
2. **`verify_candidate` is the ground truth for rejection reasons.** Don't write "rejected because thermal margin tight" from intuition — call `mcp__designer-mcp__verify_candidate(project, name, actuals={...})` on the alternative and quote the FAIL/MISSING result. If verify says it passes, the rejection reason is something else (cost, package, stock) and that has to be the stated reason. Note: `verify_candidate` checks technical specs against requirements. It does NOT verify lifecycle (Active/Obsolete) — that's `digikey_get_part`/`mouser_get_part`'s job, performed by `research-subsystem` before commit.
3. **Prefer the orchestrated path: read state, don't re-search.** When `decisions[-1].rejected` is populated (the normal case — `research-subsystem` ran first and persisted verbatim `verify_candidate` results), the alternatives table is built directly from that list. Only when `decisions[-1].rejected` is missing or empty (standalone invocation) should you fall back to issuing `jlc_search` + `jlc_get_part` + `verify_candidate` calls — and in that fallback, fire them all in one message.
4. **Ground numbers in the actuals.** "Why this part won" must reference values from `subsystems/<name>.json`'s `actuals` block (already populated via the atomic `subsystem_choose_part(actuals=...)`), not numbers you remember from a datasheet.

## Workflow

This skill is normally invoked AFTER `research-subsystem` has already searched JLCPCB, run `verify_candidate` on each candidate, and committed the winner via `subsystem_choose_part(rejected=[...])`. The verbatim `verify_candidate` result strings live on disk in `decisions[-1].rejected`. The orchestrated path is therefore READ-ONLY — no re-searching.

**Round 1 (parallel, ONE message) — READ-ONLY state load:**
- `mcp__designer-mcp__subsystem_status(project, name)` — category, ready, checks
- `mcp__designer-mcp__q_load(component_type=<category>)` — `actuals_schema` + units (still needed for the Requirements-vs-actuals table)
- `Read("docs/projects/<project>/subsystems/<name>.json")` — chosen part + `decisions[-1].rejected` (with the verbatim verify strings)

**Round 2 — assemble the alternatives table from persisted state:**
- Read `decisions[-1].rejected` from the JSON. Each entry already has `lcsc`, `mpn`, and `reason` (the verbatim `verify_candidate` result quoted by `research-subsystem`).
- Build one table row per entry: MPN, LCSC, the quoted verify string from `reason` (goes into the "Verify result" column), and the human-language rejection statement (composed from the verify string + any price/stock context already in the JSON's `chosen_part` block or the rationale).
- Do NOT call `jlc_search`, `jlc_get_part`, or `verify_candidate` here — that work was done by `research-subsystem`.

**Standalone fallback — ONLY if `decisions[-1].rejected` is empty/absent:**
This happens when the user invokes `/investigate-subsystem` without first running `/research-subsystem`. In that case, fall back to the legacy re-search behavior — fire ALL of these in one parallel message:
- `mcp__designer-mcp__q_searches(component_type=<category>, answers=<requirements>)` — curated queries
- `mcp__pcbparts__jlc_search(query=…, sort_by="stock")` — alternatives by stock
- `mcp__pcbparts__jlc_search(query=…, sort_by="price")` — alternatives by price
- `mcp__distributor-mcp__mouser_search(query=…, in_stock_only=true)` — full-text Mouser search using the same curated query (daily quota 1000)
- `mcp__distributor-mcp__digikey_search(query=…, in_stock_only=true)` — full-text DigiKey search using the same curated query (daily quota ~1000)
- `mcp__pcbparts__sensor_recommend(measure=…, protocol=…)` — when the subsystem category measures a physical quantity (IMU, sensors, etc.), broaden the candidate pool beyond JLC. Returned MPNs feed back into a follow-up `jlc_search` for cross-stocking, plus distributor cross-reference for the chosen winner.

Then in a second parallel message: `mcp__pcbparts__jlc_get_part(lcsc=…)` for each top candidate, map specs into the `actuals_schema` shape, and `mcp__designer-mcp__verify_candidate(project, name, actuals={…})` for each. The PASS/FAIL/MISSING result IS the rejection reason for the table.

**Lifecycle check (before writing the markdown):** for the *currently chosen* part on disk, call `mcp__pcbparts__digikey_get_part(mpn=…)` or `mcp__pcbparts__mouser_get_part(mpn=…)`. If the distributor reports EOL / Obsolete / NRND, surface this as a warning in the "Tradeoffs accepted" section of the markdown (named distributor + status + stock context).

**Round 3 — write the markdown:**
Format below. Identical regardless of which path populated the alternatives table.

## Markdown format (exact section order)

```markdown
# `<name>` — investigation

**Category:** `<category>` · **Status:** READY/BLOCKED · **Chosen part:** **<MPN>** (`<LCSC>`, <Mfr>) — $<price> @ <stock> stock

<one-sentence summary of what this subsystem does and what rail/bus it serves>

## Why this part won

<3–5 bullets of EE reasoning grounded in the actuals block. Each bullet must
reference a real spec — Vin range, RDS(on), thermal numbers, Iq, package size.
At least one bullet should COMPUTE something derived: Pdiss, Tj rise, ripple,
efficiency. The numbers must come from `subsystems/<name>.json`'s actuals,
not your memory of the datasheet. Lead the load-bearing fact in **bold**.>

## Alternatives considered

| Part | LCSC | Verify result | Reason rejected |
|------|------|---------------|-----------------|
| <MPN> | <C-id> | PASS / FAIL: <check> | <concrete reason — cost / package / stock / spec gap> |
| <MPN> | <C-id> | FAIL: Junction temperature | <Tj=137 °C at iout_max — actually fails the verifier> |
| <MPN> | <C-id> | PASS | <Acceptable but $4.20 vs target part's $0.95 — 4.4× cost not justified for hobby BOM> |

<Min 3 rows, all from JLCPCB jlc_search results, all run through verify_candidate.
The "Verify result" column is what the verifier returned — quote it; don't
paraphrase. The "Reason rejected" follows: if verify FAILed, that's the reason;
if it PASSed, the rejection is non-technical (cost / package / stock / library).>

## Tradeoffs accepted

- <one bullet per item in `decisions[-1].tradeoffs`>
- <one bullet per `decisions[-1].accepted_warnings`, explaining the soft check
  in plain language and why the engineering risk is acceptable>
- <If the chosen part is marked EOL / NRND / Obsolete on any distributor, that
  MUST appear here as a tradeoff with the source distributor named — e.g.
  "IMP34DT05 marked End of Life at Mouser — viable for current build (4254
  stock) but plan replacement before reorder.">

## Requirements vs actuals

| Field | Required | Actual | Unit |
|-------|----------|--------|------|
| `vin` | <req> | <actual.vin_min>–<actual.vin_max> | V |
| `iout` | <req> | <actual.iout_max> | A |
| ... | ... | ... | ... |

<Pull units from `q_load.actuals_schema[].unit`. Drop `allowed_packages`,
`iout_margin`, and similar machinery rows.>

## Schematic

![<name> schematic](./schematic.png)

<If no schematic was rendered for this category (mcu_ble, imu, pwm_servo_driver),
omit the image and write: "*Schematic not auto-generated for this category —
application-specific layout depends on connector + debug header choices.*">

---
*Investigation grounded in JLCPCB search + verify_candidate. Captured from `subsystems/<name>.json` decisions[-1] at <YYYY-MM-DD>.*
```

## Quality bar

A good investigation answers: **"if I'm reviewing this BOM in 6 months and don't remember why we picked this part, do I have enough here to either trust the choice or know what to revisit?"** If the answer is no, the report isn't done.

- ❌ "Standard low-Iq LDO" — vague.
- ✅ "AP2112K — **600 mA rated** (3× our 200 mA target), **55 µA Iq** keeps always-on dissipation at 0.18 mW, **250 mV dropout** at 200 mA gives 1.45 V headroom from 5 V — never the binding constraint. Tj rise computed from actuals `theta_ja=200 °C/W`: Pdiss = (5−3.3)×0.2 = 340 mW → Tj rise 68 °C → safe at 50 °C ambient."

## Output report (the message the orchestrator reads)

Your final user-facing reply MUST be ≤80 words in this shape:

```
**`<name>`** investigation written → `docs/projects/<project>/components/<category>/<name>/investigation.md` (<word_count> words)
- <one line summary of the chosen part's load-bearing fact>
- <Alternatives table: N rows>
- <Accepted warnings: list, or "none">
- Schematic embed: <yes (auto-rendered) | no (category-specific layout)>
```

Constraints:
- ≤80 words.
- Path is mandatory.
- Do NOT echo the investigation markdown back. The orchestrator doesn't need it; the engineer can `Read()` the file when they want.
- If you encountered an issue during write-up (e.g., decisions[-1].rejected was empty so you fell back to standalone search), surface it as a single line at the end.

## Anti-patterns

- ❌ Padding alternatives with parts you didn't actually search — `verify_candidate` will catch this if you try (you can't fake the actuals against a part that doesn't exist).
- ❌ "Rejected because price" without a concrete number from `jlc_get_part`.
- ❌ Empty "Tradeoffs accepted" — every part choice has at least one. If you can't name one, the search wasn't broad enough.
- ❌ Fabricating rejection reasons that don't quote the persisted `verify_candidate` string. In the orchestrated path `research-subsystem` already called `verify_candidate` on every alternative and wrote the verbatim result into `decisions[-1].rejected[].reason`. Quote that string in the "Verify result" column — don't paraphrase, don't substitute training-memory reasoning. (In the standalone fallback path, you must call `verify_candidate` yourself — pure-LLM rejection reasons are exactly the bias the JLCPCB-first rule is designed to kill.)
- ❌ Reporting an Obsolete or EOL part as a clean choice. The investigation must surface lifecycle status for the chosen part — readers reviewing the BOM in 6 months need to know if reorders are at risk.
- ❌ Echoing the investigation markdown back into the reply. The orchestrator doesn't need it; the engineer reads the file directly. Return only the confirmation.

## Rules summary

1. Multi-source search: `jlc_search` is primary (full-text + parametric), `sensor_recommend` for measurement-driven discovery, `digikey_get_part`/`mouser_get_part` for MPN cross-reference and lifecycle verification. No part names from memory.
2. `verify_candidate` is the ground truth for rejection reasons.
3. Orchestrated path is READ-ONLY: Round 1 loads state in one parallel message (`subsystem_status` + `q_load` + `Read`); Round 2 assembles the alternatives table from `decisions[-1].rejected` with no further tool calls. Only the standalone fallback (when `rejected` is empty) re-runs `jlc_search` / `jlc_get_part` / `verify_candidate` — and those still go in parallel batches.
4. Numbers come from the persisted actuals JSON, not training memory.
5. Output path is exactly `components/<category>/<name>/investigation.md`. Replace, don't append.
6. The "Alternatives considered" table needs ≥3 rows, each with a populated "Verify result".
7. Under ~400 words. Density > length.
