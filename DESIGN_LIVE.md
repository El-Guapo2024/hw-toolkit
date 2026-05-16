# Live Design View — control_hub_v1

## Pipeline

```mermaid
flowchart LR
  SPEC[/spec/]:::stage --> DSGN[/designer/]:::stage
  DSGN --> MATH[/designer-math/]:::stage
  MATH --> PCB[/pcb/]:::stage
  PCB --> RTR[/router/]:::stage
  RTR --> GTM[/gtm/]:::stage

  classDef stage fill:#e0f0ff,stroke:#247,stroke-width:2
```

| stage | purpose | output |
|---|---|---|
| `/spec` | load-first intake | `profile.md` + designer-mcp requirements |
| `/designer` | pick parts (Pass 1, no math) | filled subsystems + initial schematic |
| `/designer-math` | verify (Layer 1 closed-form + Layer 2 averaged model) | pass/fail flags + part-swap suggestions |
| `/pcb` | placement, board outline, DRC | `.kicad_pcb` ready for routing |
| `/router` | autoroute via freerouting/orthoroute | routed PCB, DRC clean |
| `/gtm` | fab handoff | gerbers + drill + BOM CSV + CPL zip |

## Current architecture

```mermaid
flowchart TD
  BAT[3S Li-ion<br/>11.1V nom]:::done --> FUSE[Fuse 6A TH]:::done
  FUSE --> SW[Power Switch]:::tbd
  SW --> BUCK6[Buck 6V<br/>motors]:::wip
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

**Legend:** `done` = template/locked · `wip` = picked but pending re-verify under load-first doctrine · `tbd` = needs spec lock

## Stage status

| stage | status | notes |
|---|---|---|
| /spec | **next** | run on control_hub_v1 to lock motor/servo/IMU MPNs first |
| /designer | blocked on /spec | buck_6v pick (TPS54620) will be re-checked once load MPNs lock |
| /designer-math | scaffold only | verify skeleton in `hw_agent/skills/designer-math/` |
| /pcb | scaffold only | |
| /router | scaffold only | |
| /gtm | scaffold only | |

## Doctrines (active)

1. **Load-first.** Pick actuators + sensors + MCU before sizing rails. Rails are derived.
2. **Pass 1 = pick + copy datasheet.** No math at selection time. Datasheet typical-application BOM is canonical.
3. **Pass 2 = verify on averaged model.** python-control + SciPy. SPICE only for final ripple/EMI spot-check.
4. **One-at-a-time narration.** Present each subsystem result conversationally, wait for ack.
