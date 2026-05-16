# Control Hub — Live Design View

```mermaid
flowchart TD
  BAT[3S Li-ion<br/>11.1V nom]:::done --> FUSE[Fuse 6A TH]:::done
  FUSE --> SW[Power Switch]:::tbd
  SW --> BUCK6[Buck 6V<br/>TPS54620 6A]:::done
  SW --> BUCK5[Buck 5V<br/>servos+logic]:::tbd
  BUCK5 --> LDO3[LDO 3V3<br/>MCU+sensors]:::tbd

  LDO3 --> MCU[ESP32-S3-WROOM-1<br/>N16R8 wifi+BT]:::done

  BUCK6 --> DC[DC Driver<br/>4× 6V 500mA]:::tbd
  BUCK5 --> SRV[Servo Header<br/>4× hobby 5V]:::tbd
  BUCK5 --> STP[TMC2209 ×2<br/>NEMA17]:::done

  MCU -- I²C --> MUX[TCA9548A<br/>I²C mux]:::done
  MUX --> ENC[AS5600 ×4<br/>encoders]:::done
  MUX --> IMU[9-DoF IMU<br/>BNO055?]:::tbd
  MUX --> TOF[VL53L0X<br/>breakout]:::done
  MCU -- PWM --> SRV
  MCU -- STEP/DIR --> STP
  MCU -- PWM/DIR --> DC
  MCU -- ADC --> LINE[TCRT5000 ×5<br/>line array]:::done
  MCU -- 1-wire --> LED[WS2812B<br/>status]:::done

  classDef tbd fill:#fee,stroke:#c33,stroke-dasharray:4
  classDef done fill:#dfd,stroke:#363
  classDef wip fill:#ffd,stroke:#aa3
```

**Legend:** `done` = part picked · `wip` = researching · `tbd` = needs research

## Subsystems to research (haiku swarm)

| # | subsystem | status |
|---|-----------|--------|
| 1 | buck_6v (motors) | **done — TPS54620RHLR** |
| 2 | buck_5v (servos+logic) | tbd |
| 3 | ldo_3v3 (MCU+sensors) | tbd |
| 4 | dc_driver (×4) | tbd |
| 5 | servo_header (×4) | tbd |
| 6 | imu_9dof | tbd |
| 7 | power_switch | tbd |

Templated / pre-committed: ESP32-S3, TMC2209, AS5600+TCA9548A, VL53L0X, TCRT5000, WS2812B, 6A glass fuse.
