Generate the project's system architecture artifacts: an editable Excalidraw block diagram + a rasterized PNG (so the agent can see it inline) + a README.md tying everything together. Run AFTER all subsystems are committed — the diagram reads from the subsystems on disk.

## Arguments

`$ARGUMENTS` is `<project_slug>`. Required.
Example: `/architecture-diagram robocar_hub_v2`.

## Output paths

Under `docs/projects/<project>/`:
- `architecture.excalidraw` — editable source (raw Excalidraw v2 JSON, opens in VS Code's Excalidraw extension)
- `architecture.excalidraw.md` — Obsidian-Excalidraw mirror (same JSON wrapped in markdown frontmatter)
- `architecture.png` — rasterized via PIL so the agent can read it inline (Claude can't render Excalidraw JSON visually)
- `README.md` — project overview with the embedded PNG, BOM table, links to per-component investigations

## Layout convention

Keep the layout consistent across projects so readers know where to look:

- **Title row** at top — project name + one-line spec summary
- **Power tree row** (y ≈ 200): battery → buck → 5 V rail label → LDO → 3.3 V rail label, left-to-right
- **Loads row** (y ≈ 460): 5 V loads on the left half, 3.3 V loads on the right half
- **Control overlay** (dashed arrows): MCU → drivers (PWM/DIR/STEP), MCU ↔ I²C peripherals
- **Legend** at bottom: power-flow vs control-signal arrow distinction; color key (orange = battery, blue = 5 V, yellow = 3.3 V, green = MCU/logic, pink = sensors); one-line status (READY count, BOM total, supply risk)

Box backgrounds:
- Battery: `#fff4e6` (orange)
- Buck: `#e7f5ff` (blue)
- LDO: `#fff9db` (yellow)
- Motor / stepper / servo drivers: `#ffe0b2` (peach)
- MCU: `#d3f9d8` (green)
- Sensors / IMU: `#fce4ec` (pink)

## Workflow

1. **Read state**:
   - `mcp__designer-mcp__project_status(project)` for the subsystem list + ready/fail counts
   - `mcp__designer-mcp__bom_summary(project)` for cost + supply-risk lines
   - For each subsystem, read `docs/projects/<project>/subsystems/<name>.json` to get the chosen part MPN, package, role labels.

2. **Build the Excalidraw JSON** with elements (rectangles, text, arrows). Required Excalidraw fields per element: `id`, `type`, `x`, `y`, `width`, `height`, `angle: 0`, `strokeColor: "#1e1e1e"`, `backgroundColor`, `fillStyle: "solid"`, `strokeWidth: 2`, `strokeStyle: "solid"|"dashed"`, `roughness: 1`, `opacity: 100`, `groupIds: []`, `frameId: null`, `roundness: {"type": 3}`, `seed`, `version: 1`, `versionNonce`, `isDeleted: false`, `boundElements: []`, `updated`, `link: null`, `locked: false`. For text: also `fontSize`, `fontFamily: 5` (Helvetica), `text`, `originalText`, `textAlign`, `verticalAlign`, `lineHeight: 1.25`, `baseline`. For arrows: `points: [[0,0], [dx,dy]]`, `endArrowhead: "arrow"`, `startArrowhead: null`, `elbowed: false`.

3. **Sanitize all text** to ASCII before rendering — no emoji, no em-dash (`—` → `-`), no middle dot if rendering with Helvetica (`·` → `•`). PIL's font glyph coverage is narrow on macOS Helvetica.

4. **Save the JSON** to `architecture.excalidraw` and ALSO embed it inside `architecture.excalidraw.md` with this frontmatter:
   ```
   ---
   excalidraw-plugin: parsed
   tags: [excalidraw]
   ---
   ==⚠ Switch to EXCALIDRAW VIEW in the MORE OPTIONS menu ⚠==
   
   # <project> — System Architecture
   
   <one-paragraph summary>
   
   %%
   # Drawing
   ```json
   <the Excalidraw JSON>
   ```
   %%
   ```

5. **Rasterize to PNG** by invoking the shared renderer at `hw_agent/scripts/render_excalidraw.py` — do not re-implement PIL drawing inline. From `Bash`:
   ```bash
   python /Users/juanantonioluera/ws/freight_flow_ai/hardware/hw_agent/scripts/render_excalidraw.py \
     docs/projects/<project>/architecture.excalidraw \
     docs/projects/<project>/architecture.png
   ```
   Or import it: `from hw_agent.scripts.render_excalidraw import render; render("…/architecture.excalidraw", "…/architecture.png")`.

   The renderer supports `rectangle`, `text`, and `arrow` elements (solid + dashed strokes, end + start arrowheads, rounded corners, fontFamily 5 mapped to Arial Unicode → Helvetica). It logs a warning to stderr and skips `ellipse`, `diamond`, `line`, `freedraw`, `image`, and `frame` — if you need those for a specific diagram, extend the script there rather than inline. The renderer does not crash on non-ASCII; missing glyphs render as `□`, which is why step 3's sanitization is upstream.

6. **Verify visually** — `Read()` the resulting PNG. Look for: emoji boxes (`□`), text overflow, missing arrows, unreadable labels. If any, fix and re-render before declaring done.

7. **Write README.md** at the project root with this structure:
   ```markdown
   # <Project Name>
   
   <one-paragraph description from project_profile.md>
   
   ## Architecture
   
   ![architecture](./architecture.png)
   
   Block diagram of the power tree and signal interconnect. Editable source: [`architecture.excalidraw`](./architecture.excalidraw) — open in VS Code's **Excalidraw** extension (right-click → Reopen Editor With → Excalidraw).
   
   ## Status
   
   **<N>/<total> subsystems READY** · <supply risk>% supply risk · <N> acknowledged warnings
   
   | Subsystem | Part (MPN) | LCSC | Mfr | Pkg | $/u | Stock | Status |
   |-----------|-----------|------|-----|-----|----:|------:|--------|
   | … | … | … | … | … | … | … | … |
   
   **Total: $<total>/board** (single quantity).
   
   ## Per-subsystem investigations
   
   - [`<name>`](./components/<cat>/<name>/investigation.md) — <one-line summary>
   - …
   
   ## Additional sensor pack (not modeled as subsystems)
   
   Read `docs/projects/<project>/sensor_pack.json` (a list of `{role, mpn, lcsc, manufacturer, package, price, stock, qty}` entries, persisted by the full-board-design orchestrator). If the file is missing or the list is empty, **omit this entire section** — do not leave a placeholder. Otherwise render it as a markdown table with these columns:
   
   `Role | MPN | LCSC | Mfr | Pkg | $/u | Qty | Subtotal`
   
   where `Subtotal = price * qty`. Include a totals row only if there are 2+ entries.
   
   ## Files
   
   <tree showing the project layout>
   ```

## Anti-patterns

- ❌ Building the diagram from `subsystems_list()` keys without reading the actual chosen_part / role inside each JSON — the labels look generic.
- ❌ Skipping the PNG rasterization — without it, only the human in VS Code can see the diagram; the agent can't audit its own output.
- ❌ Embedding emoji or unicode arrows in text labels — they'll render as `□` boxes in the PNG.
- ❌ Hardcoding x/y coordinates that don't grow with subsystem count — the convention layout above scales by counting loads-row entries; pre-compute box positions so 4 motor drivers fit as cleanly as 2.
- ❌ Forgetting the `architecture.excalidraw.md` Obsidian mirror — VS Code's official Excalidraw extension reads `.excalidraw`, but Obsidian users + some markdown previewers want the wrapped form.

## Done definition

All four artifacts present at the project root:
- `architecture.excalidraw` (raw JSON)
- `architecture.excalidraw.md` (Obsidian mirror)
- `architecture.png` (PIL-rendered, no `□` boxes, all labels readable)
- `README.md` (with embedded PNG + BOM table + per-component links)

Plus: agent has `Read()` the PNG once to confirm visual quality.
