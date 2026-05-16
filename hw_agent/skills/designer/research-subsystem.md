Search JLCPCB for candidate parts, verify each against the subsystem's requirements, and commit the winner with full actuals — atomically. This is the **research** phase for a single subsystem; the **investigation** report (separate skill) reads the result and writes the markdown.

## Execution context — this skill always runs in a subagent

The orchestrator (`full-board-design.md`) dispatches this skill via an `Agent` tool call. You are running in a subagent's isolated context. Your final user-facing message becomes the report the orchestrator reads and narrates to the engineer.

This means:
- All MCP work (jlc_search, mouser_search, digikey_search, jlc_get_part, verify_candidate, subsystem_choose_part, etc.) happens **inside this subagent's context**, not in the main orchestrator's. Search results, JSON dumps, and verify_candidate outputs stay here — they don't bubble up.
- Your **final reply** must be a structured ≤300-word report (format below). It's the only thing the orchestrator sees.
- Be aggressive with parallelism (Round 1, Round 2, Round 3 each in one batched message). The whole research run should fit in ≤6 sequential rounds.

## Arguments

`$ARGUMENTS` is `<project_slug> <subsystem_name> <category>`. All three required.
Example: `/research-subsystem robocar_hub_v2 buck_5v buck_converter`.

The subsystem must already exist (created via `subsystem_add` with its requirements) — this skill researches and commits a **part** for it. Use the orchestrator (`full-board-design.md`) to set up requirements first, or call `subsystem_add` manually.

## DISCIPLINE (NON-NEGOTIABLE)

1. **NO part names from memory.** Every candidate must originate from `jlc_search`, `mouser_search`, `digikey_search`, `sensor_recommend`, or a distributor cross-reference (`digikey_get_part` / `mouser_get_part`). Memory-pulled MPNs bias the alternatives table — even when "verifying" a memorized part with a search, you've narrowed the candidate pool before seeing what's actually stocked.
2. **`mcp__designer-mcp__verify_candidate` is the ground truth.** Run every candidate through verify_candidate against the subsystem's requirements. The verifier's PASS/FAIL/MISSING result is what determines the rejection reason in the investigation report.
3. **Use `subsystem_choose_part(actuals={...})`** atomically. Never commit a part without populated actuals — read `q_load(component_type=<category>).actuals_schema` for exact field names + units before filling.
4. **Parallelize.** Round 1 fires `q_load` + `q_searches` + parallel distributor searches across JLC, Mouser, and DigiKey in ONE tool-call message. Round 2 fires `jlc_get_part` for the top 4–6 LCSCs in ONE message. Round 3 fires `verify_candidate` for each in ONE message. Total: ≤6 sequential rounds (1, 2, 3, 4, 4.5, 5).
5. **Sanity-bound checks WILL reject implausible values.** LDO `iout_max` is bounded ≤5 A in actuals; buck `iout_max` ≤30 A; motor_driver `iout_per_ch` ≤50 A. If you hit those bounds, you passed mA where Amps were expected — convert before retrying.
6. **JLC search is preferred for *commit selection*** (turnkey assembly when stocked), but Mouser and DigiKey searches must run alongside it in Round 1. The cheapest READY part wins regardless of distributor — JLC's only structural advantage is JLC-PCB-A turnkey-board assembly cost. Do not run JLC-only searches.
7. **Verify lifecycle before commit (conditional).** If the chosen winner came ONLY from `jlc_search` (no parallel Mouser/DigiKey hit in Round 1), run `digikey_get_part(<MPN>)` OR `mouser_get_part(<MPN>)` to verify lifecycle. If the winner already has a lifecycle field from Round 1 (Mouser/DigiKey already returned it), skip the explicit cross-reference. If lifecycle is `Obsolete` / `EOL` / `NRND` / `Not Recommended for New Designs`, do NOT commit — go back to Round 4 and pick a different winner. JLC stock count alone does not catch EOL parts (vendors burn through inventory before delisting).

## Workflow

**Round 1 (parallel, ONE message):**
```
mcp__designer-mcp__q_load(component_type=<category>)
mcp__designer-mcp__q_searches(component_type=<category>, answers=<requirements>)   # curated query strings
mcp__pcbparts__jlc_search(query=<from q_searches>, sort_by="stock")
mcp__pcbparts__jlc_search(query=<from q_searches>, sort_by="price")
mcp__distributor-mcp__mouser_search(query=<same string from q_searches>, in_stock_only=true)
mcp__distributor-mcp__digikey_search(query=<same string from q_searches>, in_stock_only=true)
```

Use the SAME query string from `q_searches` across all three distributor searches. The result pool now spans 3 distributors (JLC + Mouser + DigiKey) → broader, less biased candidate set than a JLC-only round.

If the category measures a physical quantity (IMU, ambient light, ToF, mic, IMU/accel/gyro, environmental sensors), also include `mcp__pcbparts__sensor_recommend(measure=..., protocol=..., type=...)` in the Round 1 batch. The returned MPNs become additional `jlc_search(query=<MPN>)` candidates in Round 2. Skip `sensor_recommend` for non-sensor categories (buck, LDO, MCU, motor_driver, stepper, servo) — the 3-distributor search is sufficient there.

**Round 2 (parallel, ONE message):** for each of the top 4–6 candidates from Round 1:
```
mcp__pcbparts__jlc_get_part(lcsc=<C-id>)
```

**Round 3 (parallel, ONE message):** map each candidate's spec attributes into the `actuals_schema` field shape (use the unit metadata to convert mA→A, mΩ→mΩ etc.) and call:
```
mcp__designer-mcp__verify_candidate(project=<proj>, name=<sub>, actuals={…candidate's actuals…})
```

**Round 4 — pick the winner:** sort by *passes-all-hard-checks first, then min cost*. Cheapest READY part wins. If multiple READY at similar cost, pick the one with highest stock + smallest package (board area matters).

**Design-headroom principle (always apply):** an exact-rating part is a bad pick even when it passes `verify_candidate` with `iout_margin=1.0`. Real-world peaks, datasheet rating optimism, capacitor ESR rise over life, and thermal derating all eat at the headroom you didn't leave. Prefer the next size up when it costs within ~30% of the exact-rating part. A 6A buck at $0.23 beats a 3A buck at $0.20 every time — the 15% cost penalty buys 2× thermal margin, longer life, and quieter operation. This applies to: current rating (buck/LDO/motor driver Iout), voltage rating (Vin transient margin, cap derating to 50% of Vdc), thermal headroom (θJA × Pdiss leaves ≥30°C below Tjmax), and stock count (prefer ≥1k stock over 100 for re-orderability). The `iout_margin=1.2` default exists for this exact reason — don't override to 1.0 to make a marginal part pass.

**Round 4.5 — Lifecycle cross-reference (CONDITIONAL):** now that Round 1 searched all 3 distributors, the chosen winner often already has lifecycle visible from its Round-1 Mouser/DigiKey hit. Decision tree:

- **If the winner appeared in `mouser_search` OR `digikey_search` Round-1 results AND that result included a lifecycle field:** SKIP this round. Lifecycle is already known.
- **If the winner came ONLY from `jlc_search` (no parallel Mouser/DigiKey hit in Round 1):** run `digikey_get_part(product_number=<MPN>)` OR `mouser_get_part(part_number=<MPN>)` (one call is enough; only fire both if the first returns no result).

```
mcp__pcbparts__digikey_get_part(product_number=<MPN>)   # only if needed per above
mcp__pcbparts__mouser_get_part(part_number=<MPN>)       # only if needed per above
```

Read the `lifecycle` field. Acceptable: "Active", "RoHS Compliant", "Production". Unacceptable: "Obsolete", "End of Life", "EOL", "NRND", "Not Recommended for New Designs", "Last Time Buy".
If unacceptable, drop the winner and re-pick from Round 4. If unacceptable on one distributor but Active on the other, accept with an `accepted_warnings` entry naming the EOL distributor.
Do NOT call `digikey_get_part` / `mouser_get_part` on rejected alternatives — daily quota.

**Round 5 — atomic commit:**
```
mcp__designer-mcp__subsystem_choose_part(
    project=<proj>, name=<sub>,
    lcsc=…, mpn=…, manufacturer=…, package=…, price=…, stock=…,
    actuals={…full populated actuals…},
    rationale="<2-3 sentences citing the actuals numbers and the verify_candidate PASS list>",
    rejected=[
        {"lcsc": …, "mpn": …, "reason": "<verify_candidate FAIL/MISSING result quoted>"},
        … (one entry per Round-2 candidate that didn't win)
    ],
    tradeoffs=[…],            # optional, only if accepting a soft warning
    accepted_warnings=[…],    # snake_case or display name — both work
)
```

The `rejected` list is what the investigate-subsystem skill reads later to populate the "Alternatives considered" table. Fill it carefully — the verify_candidate result string from Round 3 goes verbatim into the reason field.

**Round 6 — design-math cross-check (mandatory).** `verify_candidate` confirms the IC fits the spec. It does NOT compute the passives around it. Before declaring the subsystem fully READY, run the category-appropriate `calc_*` tools to cross-verify the design is feasible with standard-value parts:

- **buck_converter:** `calc_buck_inductor(vin, vout, iout_max, fsw_khz, ripple_pct)` → L_min + standard value. `calc_buck_output_cap(vout, iout_max, fsw_khz, vripple_pct, delta_il)` → Cout_min. `calc_feedback_resistors(vout, vref_v, r_low)` → R1/R2 from E96. `calc_thermal_gate(p_diss_w, theta_ja, t_amb_c)` → Tj headroom margin.
- **ldo:** `calc_ldo_thermal(vin, vout, iout_max, theta_ja, t_amb_c)` → confirms LDO dropout × Iout doesn't blow thermal budget (this is the LDO killer — 1.7V drop × 1A = 1.7W is borderline for any SOT-223 package).
- **other categories:** no calc_* tool — rely on verify_candidate alone.

If any calc returns a value that requires an out-of-stock/non-standard passive (e.g. 4.7 µH at Isat 12A — fine, available; 17.3 µH at Isat 12A — uncommon, force search for alt or adjust spec), flag in `accepted_warnings` or relax the requirement.

The calc results don't need to be stored in the subsystem JSON (they live in the schematic build phase), but the cross-check guarantees the picked IC isn't impossible to surround with reasonable passives. Skipping Round 6 has cost real prototypes: "the part passes verify but needs a 47µH inductor at 8A nobody stocks" is a real failure mode.

## What "good research" looks like

- ≥4 candidates verified before commit (not 1, not 10 — 4 is the sweet spot for cost/coverage).
- Each `rejected` entry has a CONCRETE reason: a verify_candidate FAIL reference, a stock number, or a price comparison. Not "too expensive" — "$4.20 vs target part's $0.95 (4.4× — not justified for hobby BOM)".
- Chosen part's actuals have ≥80% of fields populated (don't leave half the schema as None — fill from `jlc_get_part` attributes wherever possible).
- Search queries reflect the actual requirements (not a generic "buck converter") — use `q_searches` output.
- Round 1 searched all 3 distributors in parallel (JLC + Mouser + DigiKey). If a candidate appears at multiple sources, prefer it — multi-source resilience matters for the kit BOM.
- Lifecycle verified on at least one major distributor (Active/Production), either via the Round-1 Mouser/DigiKey hit or the Round-4.5 cross-reference.
- Round 6 design-math cross-check ran (calc_buck_inductor + calc_buck_output_cap + calc_feedback_resistors + calc_thermal_gate for buck; calc_ldo_thermal for LDO). Passive values are achievable with standard E12/E96 stocked parts.

## Output report (the message the orchestrator reads)

Your final user-facing reply MUST be a structured report ≤300 words in this exact shape:

```
**Subsystem:** `<name>` (`<category>`) — <READY|BLOCKED>
**Chosen:** `<MPN>` (`<lcsc/dkpn>`, <Manufacturer>) — $<price> @ <stock> stock — <source: jlc|mouser|digikey>
**Lifecycle:** <Active | Obsolete | EOL | Last Time Buy | unknown>

**Key actuals:**
- <field>: <value> (<one-line gloss>)
- <field>: <value> (<one-line gloss>)
- <field>: <value> (<one-line gloss>)

**Alternatives rejected:**
- `<MPN-1>` ($<price>): <verify_candidate FAIL/MISSING quoted, OR concrete cost/stock reason>
- `<MPN-2>` ($<price>): <…>
- `<MPN-3>` ($<price>): <…>

**Accepted warnings:** <list, or "none">
**Tradeoffs:** <one line, or "none">
```

Constraints:
- ≤300 words total. Density over length.
- Lifecycle is mandatory — pull from the distributor cross-reference (Round 4.5).
- ≥3 rejected alternatives, each with a concrete reason.
- Accepted warnings list every entry from `decisions[-1].accepted_warnings`, written in plain English with the engineering reason.
- Do NOT include search-result tables, JSON dumps, or step-by-step narration. The orchestrator already knows the procedure; it wants the result.

## Anti-patterns

- ❌ Picking from training memory and *then* searching to "confirm" — the search tools are supposed to surface candidates, not validate predetermined answers.
- ❌ Calling `verify_candidate` only on the chosen part — every alternative needs verification too, otherwise the rejection reason is fabricated.
- ❌ Committing with empty `rejected=[]` — if you have no rejected alternatives, your search wasn't broad enough. Go back to Round 1 with broader queries.
- ❌ Treating sanity-bound rejections as bugs — they're catching real unit-confusion mistakes. Fix the actuals, not the bound.
- ❌ Overriding `iout_margin` to 1.0 just to make a marginal-rating part pass. The 1.2× default exists because datasheets are optimistic, transients exceed RMS, and lifetime derating is real. If the cheapest part fails margin, pick the next size up — see "Design-headroom principle" in Round 4.
- ❌ Skipping Round 2 (`jlc_get_part`) and feeding `verify_candidate` actuals from `jlc_search` summary alone — the search results don't include all spec attributes; full part data comes from `jlc_get_part`.
- ❌ **Dual-supply field confusion** — for parts with separate motor/logic supplies (TB6612FNG, DRV8833, DRV8871, A4988), populate `vm_*` (motor supply) and `vlogic_*` (control supply) from the correct datasheet table. Never copy Vcc/logic-supply numbers into VM fields. Symptom: motor driver fails Vin coverage check because actuals say VM=2.7-5.5V (logic spec) instead of VM=2.5-15V (motor spec). Same trap for any IC with split rails (gate drivers, level shifters, half-bridges) — read the **Absolute Maximum Ratings** and **Recommended Operating Conditions** tables carefully; each supply has its own row.
- ❌ Skipping lifecycle check — JLC may stock 50K of a part Broadcom obsoleted last year. EOL parts can't be re-ordered for the next build.
- ❌ Running JLC-only Round 1 with the excuse of "saving quota" — the daily quotas are 1000 calls/day at each of Mouser and DigiKey. A 9-subsystem design uses ~9 calls each. The quota is not the binding constraint; bias is. JLC-only searches narrow the candidate pool to whatever JLC happens to stock cheaply, which is exactly the bias we're trying to eliminate.
- ❌ Returning a long narrative ("First I searched JLC, then I called verify_candidate, then…"). The orchestrator already knows the procedure. Return the structured report only.
- ❌ Echoing search results back as part of your reply. They live on disk via `subsystem_choose_part(rejected=[...])` — the investigation skill reads them. Don't bloat the orchestrator's context with raw search dumps.

## Output

A committed subsystem with chosen_part + decisions[-1] populated. Status will be READY (or BLOCKED only with explicitly accepted soft warnings via `accepted_warnings`). The investigate-subsystem skill reads this state to write the markdown report.
