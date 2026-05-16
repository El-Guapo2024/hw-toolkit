# Live Design View — control_hub_v1

## Pipeline

```mermaid
flowchart LR
  SPEC[/spec/]:::done --> DSGN[/designer/]:::next
  DSGN --> MATH[/designer-math/]:::stage
  MATH --> PCB[/pcb/]:::stage
  PCB --> RTR[/router/]:::stage
  RTR --> GTM[/gtm/]:::stage

  classDef stage fill:#e0f0ff,stroke:#247,stroke-width:2
  classDef done  fill:#dfd,stroke:#363,stroke-width:2
  classDef next  fill:#ffd,stroke:#aa3,stroke-width:3
```

| stage | purpose | output | status |
|---|---|---|---|
| `/spec` | load-first intake | `profile.md` + designer-mcp requirements | ✓ done |
| `/designer` | pick parts (Pass 1, no math) | filled subsystems + initial schematic | **next** |
| `/designer-math` | verify (Layer 1 closed-form + Layer 2 averaged model) | pass/fail flags + part-swap suggestions | scaffold |
| `/pcb` | placement, board outline, DRC | `.kicad_pcb` ready for routing | scaffold |
| `/router` | autoroute via freerouting/orthoroute | routed PCB, DRC clean | scaffold |
| `/gtm` | fab handoff | gerbers + drill + BOM CSV + CPL zip | scaffold |

## Architecture (post-/spec — loads locked, rails sized but unpicked)

```mermaid
flowchart TD
  BAT[3S Li-ion<br/>11.1V nom]:::done --> FUSE[Fuse 6A TH]:::done
  FUSE --> SW[Power Switch<br/>P-FET reverse-poly]:::tbd
  SW --> VBATR[VBAT rail<br/>11.1V]:::done
  VBATR --> BUCK6[Buck 6V<br/>>=5A]:::tbd
  VBATR --> BUCK5[Buck 5V<br/>>=6A]:::tbd
  VBATR --> STP[TMC2209-LA x2<br/>NEMA17]:::done
  BUCK5 --> LDO3[LDO 3V3<br/>>=700mA]:::tbd

  LDO3 --> MCU[ESP32-S3-WROOM-1<br/>N16R8 Wi-Fi+BLE]:::done

  BUCK6 --> DC[DC Driver x4ch<br/>DRV8833 / TB6612 TBD]:::tbd
  DC --> N20[N20 motors x4<br/>0.6A stall]:::done
  BUCK5 --> SRV[Servo Header x4<br/>MG90S class]:::done
  BUCK5 --> LINE[TCRT5000 x5<br/>line array]:::done

  MCU -- I2C --> MUX[TCA9548A<br/>I2C mux]:::done
  MUX --> ENC[AS5600 x4<br/>encoders, addr 0x36]:::done
  MCU -- I2C --> IMU[LSM6DSOX<br/>6-DoF, 0x6A]:::done
  MCU -- I2C --> TOF[VL53L0X<br/>ToF, 0x29]:::done
  MCU -- PWM --> SRV
  MCU -- STEP/DIR+UART --> STP
  MCU -- PWM/DIR --> DC
  MCU -- ADC --> LINE
  MCU -- 1-wire --> LED[WS2812B<br/>status]:::done
  VBATR -- divider --> MCU

  classDef tbd fill:#fee,stroke:#c33,stroke-dasharray:4
  classDef done fill:#dfd,stroke:#363
  classDef wip fill:#ffd,stroke:#aa3
```

**Legend:** `done` = locked MPN · `wip` = under research · `tbd` = no MPN, sized but unpicked

## Stage status

| stage | status | notes |
|---|---|---|
| /spec | ✓ **complete** | all loads + sensors + MCU MPN-locked. Rail tally computed. profile.md rewritten load-first. |
| /designer | **next** | pick buck_6v, buck_5v, ldo_3v3, dc_driver, power_switch. parts-finder + parts-specker agents wired. |
| /designer-math | blocked | runs after /designer Pass 1 picks. Layer 2 averaged-model verify. |
| /pcb | blocked | runs after schematic complete. Mechanical decisions deferred here from /spec. |
| /router | blocked | autoroute via router-mcp. |
| /gtm | blocked | gerbers + BOM CSV + CPL. |

## Hook + sub-agent infra (active)

- **Statusline:** `[hardware-agent] | stage=spec | project=control_hub_v1` (after Claude Code restart)
- **SessionStart hook:** injects doctrine bullets each session
- **UserPromptSubmit hook:** per-prompt stage reminder
- **PostToolUse hook:** auto-appends `subsystem_choose_part` calls to `BUILD_LOG.md` (warn-mode for stage mismatches)
- **Sub-agents:** `parts-finder` (haiku, DK/JLC/Mouser search), `parts-specker` (haiku, datasheet->actuals)
- **Deferred sub-agents:** `pcb-placer`, `router-runner`, `kicad-editor` (write at /pcb time)

## Doctrines (active)

1. **Load-first.** Pick actuators + sensors + MCU before sizing rails. Applied this stage.
2. **Pass 1 = pick + copy datasheet.** No math at selection. Datasheet typical-application BOM is canonical.
3. **Pass 2 = verify on averaged model.** python-control + SciPy. SPICE only for final ripple/EMI spot-check.
4. **Digi-Key primary.** DK > JLC > Mouser for stock + catalog (1-5 unit prototype, hand assembly).
5. **One-at-a-time narration.** Present each subsystem result conversationally, wait for ack.
6. **Announce workspace paths.** State file paths before editing.
