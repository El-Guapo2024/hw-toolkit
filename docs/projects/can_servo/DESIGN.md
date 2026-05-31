# DESIGN — can_servo

CAN-connected servo controller node. Accepts 12V, converts to 3.3V logic,
drives an STM32G431CBUx MCU, reads a magnetic rotary encoder via SPI, and
bridges the MCU to a CAN bus via a TCAN330G transceiver.

---

## Architecture

```
12V header (J_VIN)
  └─ TPS54302 buck (SOT-23-6, 10uH / 22uF) ─> 3.3V rail
         └─ STM32G431CBUx (UFQFPN-48)
               ├─ SPI1 (PB3/PB4/PB5/PA4) ─> AS5047D encoder (TSSOP-14)
               └─ FDCAN1 (PA11/PA12) ─> TCAN330G (SOIC-8)
                                              └─ CANH/CANL ─> J_CAN (2-pin)
         SWD header (J_SWD, 3-pin) ─> MCU PA13/PA14
```

---

## Subsystems

| Ref    | MPN                | Package       | lib_id resolved                     | Role                    |
|--------|--------------------|---------------|--------------------------------------|-------------------------|
| J_VIN  | Conn_01x02         | PinHeader 1×2 | Connector_Generic:Conn_01x02         | 12V power input         |
| U1     | TPS54302           | SOT-23-6      | Regulator_Switching:TPS54302         | 12V→3.3V buck           |
| U8     | STM32G431CBUx      | UFQFPN-48     | MCU_ST_STM32G4:STM32G431CBUx         | MCU                     |
| U9     | AS5047D            | TSSOP-14      | Sensor_Magnetic:AS5047D              | Magnetic encoder (SPI)  |
| U10    | TCAN330G           | SOIC-8        | Interface_CAN_LIN:TCAN330G           | CAN transceiver         |
| J_CAN  | Conn_01x02         | PinHeader 1×2 | Connector_Generic:Conn_01x02         | CAN bus output          |
| J_SWD  | Conn_01x03         | PinHeader 1×3 | Connector_Generic:Conn_01x03         | SWD debug               |
| C1–C5  | 100nF/4.7uF        | 0402/0805     | Device:C (real)                      | Decoupling              |
| R1     | 10k                | 0402          | Device:R (real)                      | TCAN330G SHDN pull-up   |
| Buck passives | L 10uH, Cin 10uF, Cout 22uF, Cboot 100nF, Rtop 31.6k, Rbot 10k | — | Device:L / Device:C / Device:R | Buck support |

---

## Pin assignments (MCU)

| MCU Pin | Function       | Net          |
|---------|----------------|--------------|
| PA4     | SPI1_NSS (GPIO CS) | spi_cs   |
| PB3     | SPI1_SCK       | spi_sck      |
| PB4     | SPI1_MISO      | spi_miso     |
| PB5     | SPI1_MOSI      | spi_mosi     |
| PA11    | FDCAN1_RX      | can_rx       |
| PA12    | FDCAN1_TX      | can_tx       |
| PA13    | SWDIO          | swd_swdio    |
| PA14    | SWDCLK         | swd_swdclk   |

---

## Power

- Input: 12V via J_VIN (2-pin header)
- Buck (TPS54302): Vin=12V, Vout=3.3V, L=10uH, Cin=10uF, Cout=22uF, Cboot=100nF
- Feedback: Rtop=31.6k / Rbot=10k → Vout = 0.9 × (1 + 31.6k/10k) ≈ 3.3V
- 3.3V rail serves: MCU VDD/VDDA/VBAT, TCAN330G VCC, AS5047D VDD, all decoupling

---

## ERC

- ERC code set used: `hw.ERC_REAL_SYMBOL_CODES`
- All ICs (TPS54302, STM32G431CBUx, AS5047D, TCAN330G) and connectors resolved
  to real KiCad library symbols — no hwagent placeholders.
- Passives resolve to Device:R / Device:C / Device:L.
- ERC passed clean (no real violations) on first execution after connector symbols fixed.

---

## Files

```
docs/projects/can_servo/
├── can_servo.ipynb           source notebook
├── can_servo.executed.ipynb  executed notebook (all cells clean)
├── DESIGN.md                 this file
└── can_servo.zip             KiCad project (sch + pro + sym-lib-table)
    └── can_servo/            unpacked mirror
```
