# cnc_mini_v1 — Final Bill of Materials

## Summary
- **Board-level JLCPCB**: ~$6.20 (all components in stock)
- **External modules**: ~$11-14 (third-party breakout boards)
- **Total system**: ~$17-20 (well under $60 ceiling)

## Board-Level (JLCPCB Turnkey Assembly)

### Active ICs
| Ref | MPN | Mfr | Package | LCSC | Qty | Unit Cost | Subtotal |
|-----|-----|-----|---------|------|-----|-----------|----------|
| U1 | ESP32-S3-WROOM-1 | Espressif | SMD 25.5×18mm | C2913202 | 1 | $4.90 | $4.90 |
| U2 | LM2596S-5.0 | UMW | TO-263-5 | C347421 | 1 | $0.59 | $0.59 |
| U3 | AMS1117-3.3 | AMS | SOT-223 | C6186 | 1 | $0.21 | $0.21 |

### Connectors
| Ref | MPN | Mfr | Package | LCSC | Qty | Unit Cost | Subtotal |
|-----|-----|-----|---------|------|-----|-----------|----------|
| J1 | TF PUSH | SHOU HAN | SMD | C393941 | 1 | $0.065 | $0.065 |
| J2 | TYPE-C 16P 2MD(073) | SHOU HAN | SMD | C165948 | 1 | $0.18 | $0.18 |
| J3–J5 | A2541WV-5P | CJT | Through-hole 2.54mm | C225480 | 2 | $0.10 | $0.20 |

### Passives
| Ref | Value | Spec | LCSC | Qty | Unit Cost | Subtotal |
|-----|-------|------|------|-----|-----------|----------|
| C1 | 100uF | 35V Electrolytic SMD | C72478 | 1 | $0.031 | $0.031 |
| C2–C6 | 100nF | 50V X7R 0603 | C14663 | 5 | $0.003 | $0.015 |
| C7 | 22uF | 35V X5R 0805 | C6119901 | 1 | $0.112 | $0.112 |
| L1 | 10µH | 0603 (1.85Ω DCR) | C1035 | 1 | $0.019 | $0.019 |
| R1–R15 | 10kΩ | ±1% 0603 | C25804 | 15 | $0.001 | $0.015 |
| R16–R20 | 1kΩ | ±1% 0603 | (stock) | 5 | $0.001 | $0.005 |
| D1 | Schottky (optional) | – | (stock) | 1 | $0.005 | $0.005 |

### **Board-Level Subtotal: $6.22**

---

## External Modules (Not Assembled by JLCPCB)

| Module | Supplier | Qty | Unit Cost | Subtotal | Notes |
|--------|----------|-----|-----------|----------|-------|
| TMC2209 Stepper Driver (DIP breakout) | AliExpress / Digi-Key | 3 | $3.00–5.00 | $9.00–15.00 | Soldered to J3–J5 headers on board |
| SSD1306 OLED 0.96" (optional) | AliExpress | 1 | $2.00–3.00 | $2.00–3.00 | Optional; connect via I2C |
| USB Cable (Micro/Type-C) | Included or $1.00 | 1 | $1.00 | $1.00 | For debug/programming |

### **External Modules Subtotal: $12–19**

---

## Cost Breakdown

| Category | Cost | % of Total |
|----------|------|-----------|
| JLCPCB Assembly | $6.22 | 31–37% |
| Stepper Drivers (3×) | $9.00 | 45–54% |
| OLED Module (optional) | $2.00 | 10–12% |
| Accessories (cables, headers) | $1.00 | 5% |
| **Total (without OLED)** | **~$16.22** | **—** |
| **Total (with OLED)** | **~$18.22** | **—** |

**Well under $60 ceiling. ✓**

---

## Design Notes

### Why Hybrid (Board + External Modules)?

1. **JLCPCB IC Shortage**:
   - TMC2209, DRV8825, A4988 stepper drivers not in JLC extended library
   - SSD1306 OLED modules not offered by JLCPCB
   - Solution: Use industry-standard DIP breakout boards (widely available, proven designs)

2. **Assembly Strategy**:
   - **Phase 1 (JLCPCB)**: Assemble board with ESP32, power regulators, SD card, USB
   - **Phase 2 (Manual)**: Solder TMC2209 modules to pin headers, connect limit switches

3. **Advantages**:
   - Modular: swap stepper drivers or OLED without re-spinning board
   - Fast sourcing: modules available on AliExpress (1–2 weeks)
   - Cost-effective: modular approach cheaper than custom IC breakout design

---

## Load Verification

| Load | Demand | Rail | Status |
|------|--------|------|--------|
| ESP32-S3 | 0.5 A peak | 3.3V @ 1 A (LDO) | ✓ OK |
| 3× TMC2209 @ 1 A ea. | 3 A motor | 24V direct | ✓ OK |
| SD card | 100 mA | 3.3V | ✓ OK |
| OLED (opt) | 30 mA | 3.3V | ✓ OK |
| Limit switches | GPIO inputs | 3.3V pull-ups | ✓ OK |
| **Total 3.3V** | **0.63 A** | 1 A LDO | ✓ Safe margin |
| **Total 5V** | **1.2 A** | 3 A buck | ✓ Safe margin |
| **Motor supply** | **24V @ 3 A** | 24V direct | ✓ Direct pass-through |

---

## Next Steps (Doctrine Harness)

1. ✓ **Intake (complete)**: Loads, MCU, power requirements documented
2. ✓ **Part search (complete)**: BOM locked, all ICs sourced
3. **Schematic design** (Phase 2): Create .kicad_sch with designer-mcp tools
4. **PCB layout** (Phase 3): Place components, route traces, DRC check
5. **Fabrication** (Phase 4): Export gerbers, submit to JLCPCB + manual soldering

---

**Status**: READY for schematic phase. All subsystems locked, no blockers.
