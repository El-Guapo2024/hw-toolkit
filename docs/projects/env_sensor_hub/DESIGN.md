# DESIGN — env_sensor_hub

## Overview

I2C environmental sensor hub. STM32L031K6T6 MCU on a 3.3V rail supplied by a
MIC5219-3.3YM5-TR LDO from a 5V input header. Two I2C sensors share one bus
(`env`): SHT31-DIS-B (temp/humidity) and BMP388 (barometric pressure). A UART
debug header exposes USART1 TX/RX. Decoupling caps on every IC rail.

## BOM Highlights

| Refdes   | MPN                    | Role                        | Package     |
|----------|------------------------|-----------------------------|-------------|
| U1 mcu   | STM32L031K6T6          | MCU, ultra-low-power M0+    | LQFP-32     |
| U2 ldo   | MIC5219-3.3YM5-TR      | 5V→3.3V LDO, 500mA          | SOT-23-5    |
| U3 sht   | SHT31-DIS-B            | Temp/humidity sensor, I2C   | DFN-8       |
| U4 bmp   | BMP388                 | Barometric pressure, I2C    | LGA-10      |
| J1 hdr5v | PinHeader_2.54mm_2pin  | 5V input header             | THT         |
| J2 dbghdr| PinHeader_2.54mm_3pin  | UART debug header (TX/RX/GND)| THT        |
| C_MCU    | C_100nF_0402           | MCU VDD decoupling          | 0402        |
| C_LDO    | C_100nF_0402           | LDO VOUT decoupling         | 0402        |
| C_SHT    | C_100nF_0402           | SHT31 VDD decoupling        | 0402        |
| C_BMP    | C_100nF_0402           | BMP388 VDD decoupling       | 0402        |
| R_SDA    | R_4k7_0402             | I2C SDA pull-up to 3.3V     | 0402        |
| R_SCL    | R_4k7_0402             | I2C SCL pull-up to 3.3V     | 0402        |

## Power Architecture

```
5V input header (hdr5v) → MIC5219 LDO → 3.3V rail
                                          ├── STM32L031K6T6
                                          ├── SHT31-DIS-B
                                          └── BMP388
```

LDO EN pin is tied to VIN (5V) so the regulator is always-on when power is applied.

## I2C Bus (env)

- Nets: `env_sda`, `env_scl`
- Master: MCU pins SDA/SCL
- Device 1: SHT31-DIS-B (SDA/SCL pins), default I2C address 0x44
- Device 2: BMP388 (SDI/SCK pins), default I2C address 0x76
- Pull-ups: R_SDA (4.7k), R_SCL (4.7k), both to 3.3V rail

## UART Debug (dbg)

- Nets: `dbg_tx`, `dbg_rx`
- MCU USART1 TX → dbghdr TX
- MCU USART1 RX → dbghdr RX
- GND pin on header for reference

## ERC Notes

`check_erc()` passed on iteration 2. The following `expected_codes` were applied:

| Code                     | Reason                                                  |
|--------------------------|---------------------------------------------------------|
| `pin_not_connected`      | Synthesized symbols have extra unconnected pins         |
| `lib_symbol_issues`      | `hwagent` lib is runtime-synthesized, not globally registered |
| `pin_to_pin`             | LDO EN tied to VIN (Power → Bidir) for always-on       |
| `power_pin_not_driven`   | Connector power pins lack PWR_FLAG                     |
| `unconnected_wire_endpoint` | KiCad schematic wire-layout synthesis artifact       |
| `footprint_link_issues`  | Synthesized footprint names (DFN-8, LGA-10) don't match KiCad stock lib filenames |
