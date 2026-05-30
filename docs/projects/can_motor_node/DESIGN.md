# DESIGN — can_motor_node

CAN-connected BLDC motor-driver node. Designed for field-oriented control (FOC)
of a 3-phase brushless motor from a 12V supply over a CAN FD bus.

## Architecture

```
12V Input Header ──┬──► AP63205WU-7 Buck ──► 3.3V Rail
                   │         (12V→3.3V, 600mA SOT-23-6)
                   │
                   └──► DRV8353RS Gate Driver (PVDD = 12V)
                              ▲
                   6×PWM ─────┤  TIM1 advanced timer
                   (INHA/B/C  │
                    INLA/B/C) │
                              │
STM32G431CBU6 MCU ────────────┤
(UFQFPN-48, 170 MHz,          │
 Cortex-M4 + FPU)             │
      │                       │
      │  SPI1 ────────────────┤  DRV8353RS (config, fault readback)
      │  SPI1 (shared) ───────┼──► AS5047P Encoder (14-bit position)
      │                       │
      │  FDCAN1 ──► TCAN330GD ──► CANH/CANL ──► 2-pin connector
      │             (CAN FD transceiver, 3.3V, SOT-23-5)
```

## Part Selection

| Ref | MPN | Role | Notes |
|-----|-----|------|-------|
| MCU | STM32G431CBU6 | Motor-control MCU | 170 MHz CM4, TIM1 6-PWM, FDCAN1, SPI1/2 |
| U1  | AP63205WU-7   | 3.3V buck regulator | 12V→3.3V, 600mA, SOT-23-6 |
| U2  | DRV8353RS     | 3-phase smart gate driver | SPI-configurable, PVDD up to 60V, VQFN-40 |
| U3  | AS5047P-TS_EK_AB | 14-bit magnetic encoder | SPI output, TSSOP-14 |
| U4  | TCAN330GD     | CAN FD transceiver | 5 Mbps, 3.3V logic, SOT-23-5 |
| J1  | 22-23-2021    | 12V input connector | 2-pin Molex THT |
| J2  | 691321100002  | CAN bus connector | 2-pin Wurth screw terminal |

## SPI Bus Sharing (§6.2)

DRV8353RS and AS5047P share SPI1 (MOSI/MISO/SCK on PA7/PA6/PA5).
- DRV CS: PA4 → `drv.nSCS` (uses `board.spi("spi1")` bundled cs net)
- Encoder CS: PB12 → `enc.CSN` (declared as separate `board.signal("enc_cs")`)

## PWM Mapping (TIM1 advanced timer, 6-channel)

| Signal | MCU Pin | DRV Pin | Phase |
|--------|---------|---------|-------|
| pwm_ah | PA8     | INHA    | A high-side |
| pwm_al | PB13    | INLA    | A low-side  |
| pwm_bh | PA9     | INHB    | B high-side |
| pwm_bl | PB0     | INLB    | B low-side  |
| pwm_ch | PA10    | INHC    | C high-side |
| pwm_cl | PB1     | INLC    | C low-side  |

## CAN Bus

MCU FDCAN1 (PB8=RX, PB9=TX) → TCAN330GD TXD/RXD → CANH/CANL → 2-pin connector.
120Ω termination resistor (R1) placed across CANH/CANL for end-node termination.

## Power Rails

- **12V**: Input connector → AP63205WU-7 VIN + DRV8353RS PVDD. Bulk 100µF (C1) + 100nF (C2) decoupling.
- **3.3V**: AP63205WU-7 VOUT → MCU/encoder/CAN-xcvr. 10µF bulk (C3) + 100nF×3 (C4-C6) decoupling.
- Regulator: 10µF input (C7), 22µF output (C8).
- DRV PVDD: 10µF (C9) + 100nF (C10).

## ERC Result

Passed on iteration 3 with baseline `expected_codes`:
- `pin_not_connected` — unused MCU GPIO pins
- `lib_symbol_issues` — hwagent runtime-synthesized library
- `pin_to_pin` — power-rail direct ties
- `power_pin_not_driven` — connector power pins without PWR_FLAG
- `unconnected_wire_endpoint` — synthesized wire-layout artifact
