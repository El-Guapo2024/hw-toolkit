# control_hub_v1

Multi-actuator control hub for a teaching car / robot platform.

## Scope

- 4× DC motors (6V geared, ~500 mA stall)
- 4× hobby servos (5V class: SG90 / MG90 / MG996R)
- 2× steppers (NEMA17 via TMC2209)
- 9-DoF IMU
- VL53L0X ToF distance
- TCRT5000 ×5 line array (on-PCB)
- AS5600 ×N encoders behind TCA9548A I²C mux
- WS2812B RGB status
- Wi-Fi + BLE via ESP32-S3-WROOM-1-N16R8

## Power

- 3S Li-ion (11.1 V nom, 12.6 V max, ~9 V cutoff)
- 6 A glass fuse (TH + holder)
- Rails: 6 V buck (motors) · 5 V buck (servos+logic) · 3V3 LDO (MCU+sensors)

## Pre-committed parts (templated)

| subsystem | part |
|-----------|------|
| mcu | ESP32-S3-WROOM-1-N16R8 |
| stepper_driver | TMC2209 ×2 |
| encoder_mux | TCA9548A + AS5600 ×N |
| tof | VL53L0X breakout |
| line_array | TCRT5000 ×5 on PCB |
| status_led | WS2812B |
| fuse | 6 A TH glass + holder |

## To research (haiku swarm)

1. buck_6v (motor rail)
2. buck_5v (servo + logic rail)
3. ldo_3v3 (MCU + sensor rail)
4. dc_driver (×4 channels)
5. servo_header (×4 channels, no driver IC needed — just header + decoupling)
6. imu_9dof
7. power_switch (reverse-polarity + soft on/off)
