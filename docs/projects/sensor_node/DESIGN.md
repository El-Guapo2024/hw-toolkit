# sensor_node — Design Document

## Overview

Low-power I2C environmental sensor node. Converts a 5V input to a regulated 3.3V rail
using a linear LDO, then drives an STM32L031K6Tx MCU and a SHT31-DIS temperature/humidity
sensor over a shared I2C bus. A 2-pin UART header exposes the MCU debug port.

## Architecture

```
5V Header → AP2112K-3.3 LDO → 3.3V Rail → STM32L031K6Tx (I2C master)
                                         → SHT31-DIS (I2C slave, addr 0x44)
                                         → UART debug header (TX/RX)
```

## Subsystems

### Power: AP2112K-3.3 LDO (U1)
- Package: SOT-23-5 (Regulator_Linear:AP2112K-3.3 — real KiCad symbol)
- VIN tied to 5V; EN pin tied directly to VIN for always-on operation
- Input decoupling: 10 µF (C1, 0805)
- Output decoupling: 10 µF + 100 nF (C2/C3, 0805/0603)
- NC pin (pin 4) routed through `board.nc()`

### MCU: STM32L031K6Tx (U5)
- Package: LQFP-32 (MCU_ST_STM32L0:STM32L031K6Tx — real KiCad symbol)
- Supply: 3.3V on VDD + VDDA; VSS to GND
- Decoupling: 4 × 100 nF + 1 × 1 µF (C4–C7, 0603)
- NRST: 100 nF filter cap to GND (C8); satisfies ERC `pin_not_driven`
- BOOT0: 10 kΩ pull-down to GND (R3) → boot from flash
- Unused GPIO pins left unconnected (covered by `pin_not_connected` suppression)

### Sensor: SHT31-DIS (U10)
- Package: DFN-8 (Sensor_Humidity:SHT31-DIS — real KiCad symbol)
- Footprint: Package_DFN_QFN:DFN-8-1EP_3x3mm_P0.65mm_EP1.55x2.4mm
- VDD to 3.3V; VSS to GND
- ADDR (pin 2) tied to GND via 0R (R4) → I2C address 0x44
- ~RESET (pin 6) pulled up to 3.3V via 10 kΩ (R5)
- R pin (7) and ALERT (3) no-connected via `board.nc()`

### I2C Bus (I2C1: PA9=SCL, PA10=SDA)
- 4.7 kΩ pull-up resistors to 3.3V on each line (R1, R2, 0603)
- Single master (MCU), single slave (SHT31, addr 0x44)

### UART Debug Header (J1, uart_hdr)
- 2-pin 2.54 mm header: Pin 1 = MCU PA2 (TX), Pin 2 = MCU PA3 (RX)
- USART2 peripheral

## ERC

ERC passed with `hw.ERC_BASELINE_CODES` on iteration 3.
- 0 real violations
- 30 expected violations (suppressed): `pin_not_connected` (unused MCU GPIO pins),
  `pin_to_pin`, `power_pin_not_driven`, `unconnected_wire_endpoint`,
  `lib_symbol_issues`, `footprint_link_issues` (synthesized connectors)

**Real symbol status:**
- STM32L031K6Tx → MCU_ST_STM32L0:STM32L031K6Tx ✓
- AP2112K-3.3 → Regulator_Linear:AP2112K-3.3 ✓
- SHT31-DIS → Sensor_Humidity:SHT31-DIS ✓
- Resistors/Capacitors → Device:R / Device:C ✓
- Connectors (hdr5v, uart_hdr) → synthesized (reason for BASELINE_CODES)

## BOM Summary

| Ref | MPN | Package | Qty |
|-----|-----|---------|-----|
| U5 | STM32L031K6Tx | LQFP-32 | 1 |
| U1 | AP2112K-3.3 | SOT-23-5 | 1 |
| U10 | SHT31-DIS | DFN-8 | 1 |
| C1, C2 | 10 µF | 0805 | 2 |
| C3–C8 | 100 nF / 1 µF | 0603 | 6 |
| R1, R2 | 4.7 kΩ | 0603 | 2 |
| R3, R5 | 10 kΩ | 0603 | 2 |
| R4 | 0R | 0603 | 1 |
| J (hdr5v) | 2-pin 2.54mm | TH | 1 |
| J (uart_hdr) | 2-pin 2.54mm | TH | 1 |

## Files

- `sensor_node.ipynb` — source notebook
- `sensor_node.executed.ipynb` — executed notebook with outputs
- `sensor_node.zip` — KiCad project (schematic + project files)
- `sensor_node/` — unpacked KiCad files
