# Build Log

Newest first. Each entry: subsystem · decision · reason.

---

### 2026-05-15 · buck_6v → TPS54620RHLR
- 11.1V → 6V, 6A sync buck, VQFN-14-EP, JLC C263274 · 10,370 stock · $0.98
- Picked over TPS564201 (4A, $0.30) because 4×500mA stall + inrush margin justifies $0.68 for 6A headroom
- Picked over LMR51450 (36V Vin) — overkill for 12.6V max
- Open: fb divider (Vref 0.8V), inductor + Cout sizing pending Fsw=1.6MHz confirmation
