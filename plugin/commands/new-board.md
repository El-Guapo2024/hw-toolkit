---
description: Scaffold a new hw_toolkit board — ask the user-only forks, author, render, ERC-gate
argument-hint: [board-name]
---

You are scaffolding a new `hw_toolkit` board named `$1` (default `board` if
empty). Follow the **ee-design** skill for the full author flow and the
go/assume/ask conversation policy. This command is the kickoff — be a
conversation, not a wizard.

## 1. Gather the user-only forks — ONE AT A TIME

Ask these **one question per turn, wait for the answer before the next** (don't
front-load an interview, don't ask the obvious). Skip any the user already
stated. These are the genuine forks; everything else you ASSUME and narrate.

1. **What does the board do?** (one line — the load drives everything)
2. **MCU / brain?** (or "none / analog")
3. **Power: input source + rails?** (e.g. "USB-C 5V in → 3V3") — topology (buck
   vs LDO) you propose, don't ask, unless current draw makes it a real fork.
4. **Interfaces / connectors?** (USB, I2C, CAN, headers…)

ASSUME without asking: jellybean passive values, decoupling caps, refdes,
default footprints, render/ERC. State them as you go ("100n decoupling on each
rail — standard").

## 2. Author — load-first, ONE subsystem at a time

Pick actuators/sensors/MCU BEFORE sizing power rails (current draw sets the rail
spec). Then, per subsystem, collapse to a single write→render→ERC:

```python
import hw_toolkit as hw
board = hw.Board("$1")
# ... module() / resistor() / capacitor() / power() / gnd() / nets ...
board.write_kicad()      # real symbols, ELK orthogonal layout (no fallback)
srv = board.serve_live() # MVP canvas — open its URL in VS Code Simple Browser
board.show()             # inline SVG every step — the render IS the report
board.check_erc()        # gate: ERC_REAL_SYMBOL_CODES (all-real) / ERC_BASELINE_CODES
```

Run this in the LIVE notebook kernel via `mcp__ide__executeCode` — the `board`
the user sees is the `board` you mutate.

## 3. Hard rules (don't skip)

- **Show the render every step** — never hand back only a file/zip.
- **Real KiCad symbols first**; placeholder only as last resort.
- **ELK orthogonal layout is mandatory** — if it raises, fix the env, don't add
  a fallback.
- **Errors are feedback** — typed exceptions (`ERCViolation`, …) → fix the
  design → one auto-retry. Never swallow, never dead-end.
- **Announce file paths before editing** so the user can follow in VS Code panes.

## 4. Hand off

After each subsystem: one-line result + rationale, then
"**did X · next is Y · proceed?**" and wait for ack. Ramp autonomy as proposals
get accepted. When the first subsystem is up and ERC-clean, optionally invoke the
**board-verifier** agent for an adversarial second pass.
