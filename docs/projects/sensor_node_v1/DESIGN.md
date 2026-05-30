# sensor_node_v1 — Design Document

## Overview

Battery-powered wireless environmental sensor node based on the ESP32-C3-MINI-1.
Measures temperature, humidity, pressure (BME280) and ambient light (VEML7700)
over I²C. Transmits via BLE. Charged from USB-C via MCP73831. Deep-sleep idle
current < 20 µA.

## Architecture

```
USB-C (charge only)
   │ VBUS
   ▼
MCP73831 (LiPo charger)
   │ VBAT (3.7V nom, 4.2V max)
   ▼
AP2112K-3.3 (LDO, 300mA, 55µA Iq)
   │ VDD 3.3V
   ├─► ESP32-C3-MINI-1 (MCU + BLE)
   ├─► BME280 (I²C: temp/hum/press)
   ├─► VEML7700 (I²C: ambient light)
   └─► Status LED + user button
```

## Parts (load-first selection)

| Role | Part | MPN | Package | Rationale |
|---|---|---|---|---|
| MCU/BLE | ESP32-C3-MINI-1-H4 | ESP32-C3-MINI-1-H4 | LCC-58 | Integrated BLE+WiFi, deep-sleep 5µA |
| Temp/Hum/Press | Bosch BME280 | BME280 | LGA-8 | I²C, 3.6µA sleep, ±0.5°C |
| Ambient light | Vishay VEML7700 | VEML7700-TT | ODFN-6 | I²C, 0–120 klux, 90dB dynamic range |
| LiPo charger | MCP73831T | MCP73831T-2ACI/OT | SOT-23-5 | 100–500mA prog, Isd=1µA |
| 3.3V LDO | AP2112K-3.3 | AP2112K-3.3TRG1 | SOT-25 | 600mA, 55µA Iq, PSRR 75dB |
| USB-C connector | GCT USB4085 | USB4085-GF-A | SMD | Charge-only, 5A rated, no data pins |
| Status LED | Wurth 150060GS75000 | 150060GS75000 | 0603 | Green 2.2V Vf, 20mA |
| User button | C&K PTS841 | PTS841 GKS M SMTR LFS | 4.6x3.0mm | 260°C reflow rated |

## Power Budget (sleep / active)

| Rail | Sleep | Active (BLE TX) |
|---|---|---|
| ESP32-C3 | 5 µA | 80 mA peak |
| BME280 | 0.1 µA | 3.6 µA |
| VEML7700 | 0.09 µA | 90 µA |
| LDO quiescent | 55 µA | 55 µA |
| **Total (3.3V rail)** | **~60 µA** | **~135 mA** |

LiPo 500mAh → ~8300h sleep / ~3.7h continuous BLE active.

## Net Architecture

| Net id | Voltage | Members |
|---|---|---|
| `vbat` | 3.7–4.2V | USB connector VBUS→charger IN, LDO IN |
| `v3v3` | 3.3V | LDO OUT, MCU VDD, BME VDD, VEML VDD |
| `gnd` | 0V | all grounds |
| `i2c_bus` | — | MCU IO4/IO5, BME SDI/SCK, VEML SDA/SCL |
| `led_gpio` | — | MCU IO6 → LED anode via 33Ω |
| `btn_gpio` | — | MCU IO7 ← button → GND |
| `chg_prog` | — | MCP73831 PROG pin → 2kΩ → GND (500mA prog) |

## I²C Pull-ups

4.7kΩ pull-ups on SCL and SDA at the MCU, referenced to 3.3V rail.
Single pull-up pair serves both BME280 + VEML7700 (bus capacitance ~15pF,
well within 400kHz NXP UM10204 Cb limit).

## ERC Notes

`expected_codes` baseline as per AGENT_GUIDE §6.1. Additional suppressed:
- `pin_not_connected` for MCP73831 STAT (open-drain, optional status indicator)
- USB-C SBU1/SBU2 unused (charge-only, no USB data path)

## Revision History

| Rev | Date | Note |
|---|---|---|
| A | 2026-05-28 | Initial design, hw_toolkit typed Iface API |
