# power_brick — Design Document

## Spec

Dual-rail 12V input power supply brick:
- **Input**: 12V via 2-pin screw/barrel connector (J_IN)
- **Rail 1**: 5V @ 2A (Buck #1 — TPS54302)
- **Rail 2**: 3.3V @ 1A (Buck #2 — TPS54302)
- **Outputs**: Two 2-pin pin headers (J_5V, J_3V3)

## Parts

| Ref | MPN | Symbol | Role |
|-----|-----|--------|------|
| J_IN | Conn_01x02 | Connector_Generic:Conn_01x02 | 12V input header |
| U_BUCK_5V | TPS54302 | Regulator_Switching:TPS54302 | 5V buck controller |
| U_BUCK_3V3 | TPS54302 | Regulator_Switching:TPS54302 | 3.3V buck controller |
| J_5V | Conn_01x02 | Connector_Generic:Conn_01x02 | 5V output header |
| J_3V3 | Conn_01x02 | Connector_Generic:Conn_01x02 | 3.3V output header |

Each buck includes auto-wired passives via `hw_toolkit.parts.Buck`:
- Input cap: 10uF / 0805
- Output cap: 22uF / 0805
- Inductor: 10uH / 1210
- Bootstrap cap: 100nF / 0402
- Feedback divider: Rtop + Rbot / 0603

## Feedback Divider Sizing

TPS54302 Vref = 0.8V; Vout = 0.8 × (1 + Rtop/Rbot)

- **5V rail**: Rtop=52.3k, Rbot=10k → Vout = 0.8 × 6.23 = **4.984V**
- **3.3V rail**: Rtop=31.6k, Rbot=10k → Vout = 0.8 × 4.16 = **3.328V**

Both bucks use `tie_enable=True` (EN tied to VIN) — always-on operation.

## Power Rail Architecture

The Buck factory creates per-instance VIN nets (`buck_5v_vin`, `buck_3v3_vin`).
Both bucks share the 12V input by merging all VIN-side pins of buck_3v3 into
`buck_5v_vin` alongside `j_in.Pin_1`. The GND net is shared across all parts.

## ERC Result

**Gated on `hw.ERC_REAL_SYMBOL_CODES`** (tighter set — all-real-symbol board).

All ICs and passives resolved to real KiCad library symbols:
- `Regulator_Switching:TPS54302` — both buck controllers
- `Device:R`, `Device:C`, `Device:L` — all passives
- `Connector_Generic:Conn_01x02` — all connectors

No synthesized placeholders were used. ERC passed on the first iteration.

## Files

| File | Description |
|------|-------------|
| `power_brick.ipynb` | Source notebook |
| `power_brick.executed.ipynb` | Executed notebook (all cells clean) |
| `power_brick.zip` | KiCad project archive (sch + pro + lib tables) |
| `power_brick/` | Unpacked KiCad project directory |
