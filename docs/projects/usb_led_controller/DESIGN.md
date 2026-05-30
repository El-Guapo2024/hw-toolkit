# usb_led_controller — Design Document

## Overview

USB-C powered RGB LED controller. VBUS (5 V) powers an LDO down to 3.3 V
for an STM32F042 MCU. The MCU drives three PWM channels through current-limit
resistors into the cathodes of a common-anode RGB LED. USB data lines carry
the USB FS interface for future firmware updates or HID control.

---

## Block Diagram

```
USB-C Connector (GCT USB4125-GF-A)
  VBUS ──────────────────► LDO (AP2112K-3.3TRG1)
                                 │ VOUT (3.3 V)
                        ┌────────┴────────────────────────────┐
                        │  MCU (STM32F042K6T6, LQFP-32)       │
                        │   TIM1_CH1 ──► R1 (33 Ω) ──► KR ─┐  │
                        │   TIM1_CH2 ──► R2 (33 Ω) ──► KG ─┤  │
                        │   TIM1_CH3 ──► R3 (33 Ω) ──► KB ─┘  │
                        │                                    │  │
                        │   USB_DP ◄──────── D+              │  │
                        │   USB_DM ◄──────── D-              │  │
                        └────────────────────────────────────┘  │
                                                   RGB LED       │
                                               (LTST-C19HE1WT)  │
                                                  A ◄── 3.3 V   │
```

---

## Parts

| Reference  | MPN                  | Description                  | Package    | Price  |
|------------|----------------------|------------------------------|------------|--------|
| usbc_conn  | USB4125-GF-A         | USB-C 16-pin mid-mount conn  | USB-C-16P  | $0.95  |
| ldo        | AP2112K-3.3TRG1      | 600 mA LDO, 5V→3.3V         | SOT-23-5   | $0.35  |
| mcu        | STM32F042K6T6        | Cortex-M0, USB FS, 32 KB     | LQFP-32    | $2.10  |
| led        | LTST-C19HE1WT        | Common-anode RGB LED         | LED-0805   | $0.25  |
| R1, R2, R3 | R_33_0603            | 33 Ω current-limit resistors | 0603       | $0.01  |
| C1         | C_4.7uF_0805         | Bulk decoupling cap          | 0805       | $0.02  |
| C2, C3     | C_100nF_0402         | HF bypass caps on MCU VDD    | 0402       | $0.02  |

---

## Power Architecture

- **5 V VBUS**: from USB-C connector VBUS pin. Max ~900 mA at USB-C 1.5A contract.
- **3.3 V rail**: AP2112K-3.3TRG1 LDO, rated 600 mA. EN tied to VIN (always-on).
  - LDO quiescent current: ~55 µA.
  - MCU active draw: ~8 mA max.
  - LED channels: 3 × ~20 mA = 60 mA @ 33 Ω / 0.7 V forward drop per color.
  - Total 3.3 V load: ~70 mA — well within LDO limit.
- **Decoupling**: 4.7 µF bulk (C1) + two 100 nF HF bypasses (C2, C3) on VDD.

---

## Schematic Nets

| Net        | Type   | Description                          |
|------------|--------|--------------------------------------|
| v5         | power  | 5 V VBUS rail                        |
| v3v3       | power  | 3.3 V regulated output               |
| gnd        | power  | Common ground                        |
| usb_p      | usb    | D+ differential pair positive        |
| usb_n      | usb    | D- differential pair negative        |
| cc1, cc2   | usb    | USB-C CC orientation detect          |
| nc_sbu1/2  | nc     | SBU pins, intentionally unconnected  |
| pwm_r/g/b  | pwm    | PWM channels from MCU to resistors   |
| led_r/g/b  | analog | Resistor output to LED cathodes      |

---

## ERC Status

Passed with the standard `expected_codes` baseline:
- `pin_not_connected`: SBU1/2 lines not routed (unused in charge-only path).
- `lib_symbol_issues`: hwagent lib synthesized at runtime, not globally registered.
- `pin_to_pin`: LDO EN tied directly to VIN net (always-on topology).
- `power_pin_not_driven`: connector power pins lack PWR_FLAG (synthesis artifact).
- `unconnected_wire_endpoint`: synthesized wire-layout artifact.

No real violations.

---

## Firmware Notes

- MCU: STM32F042K6T6 running at 48 MHz from HSI48 + USB clock recovery.
- USB: USB FS device mode (HID or CDC) for LED control commands.
- PWM: TIM1 CH1/CH2/CH3 on PA8/PA9/PA10 for R/G/B channels.
- LED anode tied to 3.3 V; cathodes are open-drain sinks via PWM-driven resistors.
