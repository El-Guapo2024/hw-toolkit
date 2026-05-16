# cnc_mini_v1 — project profile

> stage: intake — fill in loads first (actuators, sensors, MCU), then rails.

## Inputs (load-first)
- power source: 24.0 V (bench supply)
- actuators: 3× NEMA17 stepper via TMC2209 drivers (Vmot=24V, IRMS=1A each)
- sensors: 6× limit switches (GPIO inputs, no power)
- MCU: ESP32-S3-WROOM-1 (Wi-Fi, peak 0.5A @ 3.3V)
- peripherals: SD card (3.3V, 100mA), OLED 0.96" (3.3V, 30mA)

## Constraints
- qty: 1 (bench controller)
- assembly: turnkey JLCPCB preferred
- BOM ceiling: $60.0
- enclosure / mech: desktop form factor (TBD)

## Decisions

### Design Architecture
- **Motherboard**: ESP32-S3 control + minimal on-board power (24V→5V→3.3V rails only)
- **Stepper drivers**: External breakout modules (TMC2209 or DRV8825 from AliExpress/Digi-Key)
  - Reason: JLCPCB lacks IC inventory for A4988, DRV8825, TMC2209
- **Display**: Optional SSD1306 0.96" OLED module (external, $2-3)
  - Can omit to stay under $60 BOM ceiling
- **I/O**: 6× GPIO headers for limit switches, 3× stepper motor connectors (terminal blocks), USB-C for debug
- **Ceiling achievement**: ~$18-22 board + $27-30 external modules = ~$50 total (under $60)

### Subsystem Table (READY)
| Subsystem | MPN | Cost | Status |
|-----------|-----|------|--------|
| MCU (ESP32-S3) | C2913202 | $4.90 | In stock ✓ |
| Buck (24V→5V, 3A) | C347421 | $0.59 | In stock ✓ |
| LDO (5V→3.3V, 1A) | C6186 | $0.21 | In stock ✓ |
| SD Card Socket | C393941 | $0.07 | In stock ✓ |
| USB Type-C | C165948 | $0.18 | In stock ✓ |
| Passives (caps, Rs, L) | Mixed | ~$0.15 | In stock ✓ |
| Pin Headers | C225480 | ~$0.10 | In stock ✓ |
| **Board subtotal** | | **~$6.20** | READY |
| **External modules** | | | |
| 3× TMC2209 Module | (AliExpress) | ~$9.00 | Third-party |
| 1× SSD1306 OLED (opt) | (AliExpress) | ~$2.00 | Optional |
| **Full system** | | **~$17-27** | Hybrid |

### Constraints Resolved
1. **Stepper driver IC shortage**: Use external breakout modules (industry standard for CNC hobbyists)
2. **OLED module shortage**: Omit from board-level BOM, use external SSD1306 or skip display
3. **BOM ceiling ($60)**: Achievable via hybrid approach (board-level ~$6 + modules ~$11 = $17 core, extras within ceiling)
4. **JLCPCB turnkey**: Not fully achievable; recommend **hybrid fabrication**:
   - JLCPCB: PCB assembly (ESP32, regulators, passives, connectors)
   - Manual: Solder external stepper modules + optional OLED via headers

_(subsystems/*.json files created via designer-mcp tools)_
