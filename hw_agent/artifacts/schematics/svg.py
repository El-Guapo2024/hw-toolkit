"""Schemdraw-based schematic renderer for the project model.

Renders real-looking schematics from the Project model using schemdraw.
Saves both an SVG (text — grep for label positions, stroke widths) and
a PNG (raster — what the VLM actually sees). Each renderer returns a
`{"svg": Path, "png": Path}` dict so callers can pick.
"""

from __future__ import annotations

from pathlib import Path
from typing import TypedDict

import schemdraw
import schemdraw.elements as elm

from hw_agent.artifacts.project_state.paths import ensure_project


class RenderPaths(TypedDict):
    svg: Path
    png: Path


def _save(d: schemdraw.Drawing, project_name: str, filename: str) -> RenderPaths:
    path = ensure_project(project_name) / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    d.save(str(path))
    _inject_white_bg(path)
    return {"svg": path, "png": _to_png(path)}


def _inject_white_bg(svg_path: Path) -> None:
    """Ensure SVG has a white background. Idempotent."""
    if not svg_path.exists():
        return
    txt = svg_path.read_text()
    if 'fill="white"' in txt:
        return  # already done
    # Try standard schemdraw SVG format
    if 'version="1.1">' in txt:
        txt = txt.replace(
            'version="1.1">',
            'version="1.1" style="background: white;">\n'
            '<rect width="100%" height="100%" fill="white"/>',
            1,
        )
    elif '<svg ' in txt:
        # Fallback: inject after first <svg ...>
        idx = txt.index('>', txt.index('<svg '))
        txt = txt[:idx + 1] + '\n<rect width="100%" height="100%" fill="white"/>' + txt[idx + 1:]
    svg_path.write_text(txt)


def _to_png(svg_path: Path) -> Path:
    """Rasterize SVG → PNG sibling file. Falls back to the SVG path if
    cairosvg is unavailable (caller can detect: returned path != .with_suffix('.png'))."""
    try:
        import cairosvg
    except ImportError:
        return svg_path
    png_path = svg_path.with_suffix(".png")
    cairosvg.svg2png(url=str(svg_path), write_to=str(png_path), scale=2)
    return png_path


# ─── Buck converter ─────────────────────────────────────────────────────────

def render_buck_schematic(
    project=None,
    comp_name: str = "buck",
    *,
    vin_label: str = "VBAT\n7.4V",
    vout_label: str = "5V Rail",
    part: str = "SY8205FCC",
    cin: str = "2×10µF",
    cout: str = "2×22µF",
    inductor: str = "3.3µH",
    r1: str = "73.2kΩ",
    r2: str = "10kΩ",
    vref: str = "Vfb=0.6V",
    cboot: str = "100nF",
    output: str = "buck_schematic.svg",
) -> RenderPaths:
    """Render a buck converter with external passives, properly spaced."""
    with schemdraw.Drawing(show=False, transparent=False) as d:
        d.config(fontsize=12, font="sans-serif", inches_per_unit=0.6)

        # ── Input ────────────────────────────────────────────────────
        d += elm.Dot(open=True).label(vin_label, loc="left")
        d += elm.Line().right().length(2)
        cin_node = d.here

        # Input cap branch (down)
        d += elm.Capacitor().at(cin_node).down().length(3).label(cin, loc="right")
        d += elm.Ground()

        # Continue right to IC
        d += elm.Line().at(cin_node).right().length(2)
        vin_ic = d.here

        # ── IC Block ─────────────────────────────────────────────────
        ic = elm.Ic(
            pins=[
                elm.IcPin(name="VIN", side="left", pin="1"),
                elm.IcPin(name="EN", side="left", pin="2"),
                elm.IcPin(name="BOOT", side="top", pin="3"),
                elm.IcPin(name="SW", side="right", pin="4"),
                elm.IcPin(name="FB", side="right", pin="5"),
                elm.IcPin(name="GND", side="bot", pin="6"),
            ],
            size=(5, 4),
            plblsize=11,
            botlabel=part,
            leadlen=1,
        ).at(vin_ic).anchor("inL1")
        d += ic

        # IC ground
        d += elm.Line().at(ic.inB1).down().length(1)
        d += elm.Ground()

        # ── Boot cap: BOOT pin → cap → SW pin ───────────────────────
        d += elm.Line().at(ic.inT1).up().length(1.5)
        boot_top = d.here
        d += elm.Capacitor().right().length(3).label(cboot, loc="top")
        boot_right = d.here
        d += elm.Line().down().toy(ic.inR1)
        d += elm.Line().left().tox(ic.inR1)

        # ── SW → Inductor → Vout ────────────────────────────────────
        d += elm.Line().at(ic.inR1).right().length(2)
        d += elm.Inductor2().right().length(4).label(inductor, loc="top")
        vout_node = d.here
        d += elm.Dot()

        # Branch RIGHT: output rail label
        d += elm.Line().at(vout_node).right().length(5)
        d += elm.Dot(open=True).label(vout_label, loc="right")

        # Branch DOWN (left): output cap
        d += elm.Capacitor().at(vout_node).down().length(4).label(cout, loc="right")
        d += elm.Ground()

        # Branch DOWN (right): feedback divider — offset 3 units right
        fb_top = (vout_node[0] + 3, vout_node[1])
        d += elm.Line().at(vout_node).right().length(3)
        d += elm.Dot()
        d += elm.Resistor().down().length(3).label(r1, loc="right")
        fb_mid = d.here
        d += elm.Dot()
        d += elm.Resistor().down().length(3).label(r2, loc="right")
        d += elm.Ground()

        # FB wire: midpoint → left → down to IC FB pin level → left to IC
        fb_pin_y = ic.inR2[1]
        d += elm.Line().at(fb_mid).left().length(2)
        fb_wire = d.here
        d += elm.Line().up().toy(ic.inR2)
        d += elm.Line().left().tox(ic.inR2)

        # Vref label at FB midpoint — offset left to avoid overlapping R2 label
        vref_pos = (fb_mid[0] - 2, fb_mid[1])
        d += elm.Label().at(vref_pos).label(vref, loc="left", fontsize=10)

    proj_name = project if isinstance(project, str) else (project.name if project else "default")
    return _save(d, proj_name, output)


# ─── LDO ────────────────────────────────────────────────────────────────────

def render_ldo_schematic(
    project=None,
    *,
    vin_label: str = "5V Rail",
    vout_label: str = "3.3V Rail",
    part: str = "LDO",
    cin: str = "1µF",
    cout: str = "1µF",
    output: str = "ldo_schematic.svg",
) -> RenderPaths:
    with schemdraw.Drawing(show=False, transparent=False) as d:
        d.config(fontsize=12, font="sans-serif", inches_per_unit=0.6)

        d += elm.Dot(open=True).label(vin_label, loc="left")
        d += elm.Line().right().length(2)
        cin_node = d.here

        d += elm.Capacitor().at(cin_node).down().length(3).label(cin, loc="right")
        d += elm.Ground()

        d += elm.Line().at(cin_node).right().length(1.5)

        ic = elm.Ic(
            pins=[
                elm.IcPin(name="VIN", side="left", pin="1"),
                elm.IcPin(name="EN", side="left", pin="2"),
                elm.IcPin(name="VOUT", side="right", pin="3"),
                elm.IcPin(name="GND", side="bot", pin="4"),
            ],
            size=(4, 3),
            plblsize=11,
            botlabel=part,
            leadlen=1,
        ).anchor("inL1")
        d += ic

        d += elm.Line().at(ic.inB1).down().length(1)
        d += elm.Ground()

        d += elm.Line().at(ic.inR1).right().length(2)
        cout_node = d.here

        d += elm.Capacitor().at(cout_node).down().length(3).label(cout, loc="right")
        d += elm.Ground()

        d += elm.Line().at(cout_node).right().length(2)
        d += elm.Dot(open=True).label(vout_label, loc="right")

    proj_name = project if isinstance(project, str) else (project.name if project else "default")
    return _save(d, proj_name, output)


# ─── Voltage divider ────────────────────────────────────────────────────────

def render_voltage_divider(
    project=None,
    *,
    vin_label: str = "VBAT",
    r1: str = "18kΩ",
    r2: str = "10kΩ",
    cfilt: str = "100nF",
    adc_label: str = "To MCU ADC",
    output: str = "voltage_divider_schematic.svg",
) -> RenderPaths:
    with schemdraw.Drawing(show=False, transparent=False) as d:
        d.config(fontsize=12, font="sans-serif", inches_per_unit=0.6)

        d += elm.Dot(open=True).label(vin_label, loc="left")
        d += elm.Line().right().length(2)
        d += elm.Resistor().down().length(3).label(r1, loc="left")
        tap = d.here
        d += elm.Dot()
        d += elm.Resistor().down().length(3).label(r2, loc="left")
        d += elm.Ground()

        # ADC tap going right
        d += elm.Line().at(tap).right().length(3)
        d += elm.Dot(open=True).label(adc_label, loc="right")

        # Filter cap (offset slightly right from tap)
        d += elm.Line().at(tap).right().length(1)
        d += elm.Capacitor().down().length(3).label(cfilt, loc="right")
        d += elm.Ground()

    proj_name = project if isinstance(project, str) else (project.name if project else "default")
    return _save(d, proj_name, output)


# ─── MCU pin map ────────────────────────────────────────────────────────────

def render_mcu_schematic(
    project,
    comp_name: str = "mcu",
    output: str = "mcu_schematic.svg",
) -> RenderPaths:
    """Render MCU as an IC block with functional pin labels and external targets.

    Groups pins by subsystem for visual clarity. Left side = inputs/interfaces,
    right side = outputs/control signals.
    """
    comp = project.components.get(comp_name)
    if not comp:
        raise KeyError(f"Component '{comp_name}' not in project")

    # Group pins by function for logical ordering
    groups = {
        "i2c": [], "spi": [], "uart": [], "usb": [], "adc": [],
        "motor_pwm": [], "motor_dir": [], "motor_en": [],
        "stepper": [], "other_out": [], "other": [],
    }

    for pin in comp.pins:
        st = pin.signal_type.lower()
        fn = pin.function.lower()
        if "i2c" in st:
            groups["i2c"].append(pin)
        elif "spi" in st:
            groups["spi"].append(pin)
        elif "uart" in st:
            groups["uart"].append(pin)
        elif "usb" in st:
            groups["usb"].append(pin)
        elif "adc" in st:
            groups["adc"].append(pin)
        elif "ledc" in st or "motor" in fn and "pwm" in fn.lower():
            groups["motor_pwm"].append(pin)
        elif "motor" in fn and "dir" in fn.lower():
            groups["motor_dir"].append(pin)
        elif "motor" in fn and "en" in fn.lower():
            groups["motor_en"].append(pin)
        elif "stepper" in fn.lower() or "step" in fn.lower():
            groups["stepper"].append(pin)
        elif st in ("gpio_out", "gpio"):
            groups["other_out"].append(pin)
        else:
            groups["other"].append(pin)

    # Left side: interfaces (I2C, SPI, UART, USB, ADC)
    left_pins = groups["i2c"] + groups["spi"] + groups["uart"] + groups["usb"] + groups["adc"]
    # Right side: control outputs (motors, stepper, LEDs, proto)
    right_pins = groups["motor_pwm"] + groups["motor_dir"] + groups["motor_en"] + groups["stepper"] + groups["other_out"] + groups["other"]

    # Group pins into functional blocks for a clean block diagram
    # Instead of 33 individual pins, show ~14 grouped functional ports
    pin_groups = {
        "Motor PWM ×6": {"side": "right", "gpios": "GPIO4-15"},
        "Motor EN ×3": {"side": "right", "gpios": "GPIO0,45,46"},
        "Stepper S/D/E": {"side": "right", "gpios": "GPIO16-18"},
        "Neopixel": {"side": "right", "gpios": "GPIO48"},
        "Proto ×1": {"side": "right", "gpios": "GPIO47"},
        "I2C0 (IMU)": {"side": "left", "gpios": "GPIO1,2"},
        "I2C1 (Servo+Q)": {"side": "left", "gpios": "GPIO38,39"},
        "SPI": {"side": "left", "gpios": "GPIO40-43"},
        "UART": {"side": "left", "gpios": "GPIO44,21"},
        "USB OTG": {"side": "left", "gpios": "GPIO19,20"},
        "Battery ADC": {"side": "left", "gpios": "GPIO3"},
    }

    # Build from pin_groups if we have project data, otherwise use defaults
    left_groups = [(k, v["gpios"]) for k, v in pin_groups.items() if v["side"] == "left"]
    right_groups = [(k, v["gpios"]) for k, v in pin_groups.items() if v["side"] == "right"]

    ic_pins = []
    for name, gpios in left_groups:
        ic_pins.append(elm.IcPin(name=f"{name}", side="left", pin=""))
    for name, gpios in right_groups:
        ic_pins.append(elm.IcPin(name=f"{name}", side="right", pin=""))

    ic_h = max(len(left_groups), len(right_groups)) * 1.2 + 1
    ic_w = 8

    with schemdraw.Drawing(show=False, transparent=False) as d:
        d.config(fontsize=11, font="sans-serif", inches_per_unit=0.6)

        ic = elm.Ic(
            pins=ic_pins,
            size=(ic_w, ic_h),
            plblsize=10,
            botlabel=comp.part,
            toplabel=f"{comp.specs.get('core', 'MCU')}",
            leadlen=1.5,
        )
        d += ic

        # External labels using positional anchors (inL1, inL2, ... inR1, inR2, ...)
        left_targets = [
            "IMU (0x68)",
            "PWM IC + QWIIC ports",
            "Expansion Header",
            "Expansion Header",
            "USB-C Connector",
            "Bat Divider (18k/10k)",
        ]
        for i, target in enumerate(left_targets):
            anchor = f"inL{i+1}"
            if hasattr(ic, anchor):
                pos = getattr(ic, anchor)
                d += elm.Line().at(pos).left().length(2)
                d += elm.Dot(open=True).label(target, loc="left", fontsize=9)

        right_targets = [
            "3× Dual H-Bridge ICs",
            "Driver EN pins",
            "Stepper Driver IC",
            "WS2812 RGB LEDs",
            "Proto Breakout",
        ]
        for i, target in enumerate(right_targets):
            anchor = f"inR{i+1}"
            if hasattr(ic, anchor):
                pos = getattr(ic, anchor)
                d += elm.Line().at(pos).right().length(2)
                d += elm.Dot(open=True).label(target, loc="right", fontsize=9)

    return _save(d, project.name, output)


# ─── Motor driver ───────────────────────────────────────────────────────────

def render_motor_driver_schematic(
    project=None,
    *,
    part: str = "DRV8833",
    vm_label: str = "5V",
    motor_a: str = "Motor A",
    motor_b: str = "Motor B",
    pwm_a: str = "PWM_A",
    dir_a: str = "DIR_A",
    pwm_b: str = "PWM_B",
    dir_b: str = "DIR_B",
    cin_hi: str = "100nF",
    cin_lo: str = "10µF",
    output: str = "motor_driver_schematic.svg",
) -> RenderPaths:
    """Render a dual H-bridge motor driver (DRV8833-style) with decoupling,
    nSLEEP tie-high, EEP tie-low, and labelled GPIO inputs + motor outputs.
    """
    with schemdraw.Drawing(show=False, transparent=False) as d:
        d.config(fontsize=10, unit=3, margin=1.5)

        # IC Block — VM at top, GND at bottom
        ic = d.add(elm.Ic(
            pins=[
                elm.IcPin(name="nSLEEP", pin="16", side="left", slot="6/6"),
                elm.IcPin(name="VM", pin="1,2", side="left", slot="5/6"),
                elm.IcPin(name="IN1", pin="7", side="left", slot="4/6"),
                elm.IcPin(name="IN2", pin="8", side="left", slot="3/6"),
                elm.IcPin(name="IN3", pin="10", side="left", slot="2/6"),
                elm.IcPin(name="IN4", pin="11", side="left", slot="1/6"),
                elm.IcPin(name="OUT1", pin="3", side="right", slot="6/6"),
                elm.IcPin(name="OUT2", pin="6", side="right", slot="5/6"),
                elm.IcPin(name="OUT3", pin="14", side="right", slot="3/6"),
                elm.IcPin(name="OUT4", pin="9", side="right", slot="2/6"),
                elm.IcPin(name="nFAULT", pin="13", side="right", slot="4/6"),
                elm.IcPin(name="GND", pin="4,5", side="bot", slot="1/2"),
                elm.IcPin(name="EEP", pin="15", side="bot", slot="2/2"),
            ],
            edgepadW=0.6, edgepadH=0.3, pinspacing=0.9, lofst=0.2,
        ).at((8, 0)).label(part, loc="top", ofst=0.2))

        # VM power path with decoupling caps
        rail_y = ic.VM[1]
        d.add(elm.Line().at((1.5, rail_y)).right(1))
        d.add(elm.Label().at((1.5, rail_y)).label(vm_label, loc="left"))

        cdot = d.add(elm.Dot())
        d.push()
        d.add(elm.Capacitor().down(2).label(cin_hi, loc="right"))
        d.add(elm.Ground())
        d.pop()

        d.add(elm.Line().right(1.2))
        cdot2 = d.add(elm.Dot())
        d.push()
        d.add(elm.Capacitor().down(2).label(cin_lo, loc="right"))
        d.add(elm.Ground())
        d.pop()

        d.add(elm.Line().at(cdot2.end).right().to(ic.VM))

        # nSLEEP: tie to VM
        d.add(elm.Line().at(ic.nSLEEP).left(1))
        nsleep_corner = d.add(elm.Dot())
        d.add(elm.Line().down().toy(ic.VM))
        d.add(elm.Dot())
        d.add(elm.Label().at(nsleep_corner.end).label("tied HIGH", loc="top", ofst=0.15))

        # Control inputs
        for pin_name, lbl in [("IN1", pwm_a), ("IN2", dir_a), ("IN3", pwm_b), ("IN4", dir_b)]:
            pin = getattr(ic, pin_name)
            d.add(elm.Line().at(pin).left(2.5))
            d.add(elm.Label().label(lbl, loc="left"))

        # Motor outputs
        for pin_name, lbl in [("OUT1", f"{motor_a} +"), ("OUT2", f"{motor_a} −"),
                               ("OUT3", f"{motor_b} +"), ("OUT4", f"{motor_b} −")]:
            pin = getattr(ic, pin_name)
            d.add(elm.Line().at(pin).right(2.5))
            d.add(elm.Label().label(lbl, loc="right"))

        # nFAULT
        d.add(elm.Line().at(ic.nFAULT).right(2.5))
        d.add(elm.Label().label("nFAULT", loc="right"))

        # GND + EEP
        d.add(elm.Line().at(ic.GND).down(1))
        d.add(elm.Ground())
        d.add(elm.Line().at(ic.EEP).down(1))
        d.add(elm.Ground())

    proj_name = project if isinstance(project, str) else (project.name if project else "default")
    return _save(d, proj_name, output)


# ─── Full system schematic ─────────────────────────────────────────────────

def render_system_schematic(
    project_slug: str = "robocar-hub",
    output: str = "system_schematic.svg",
) -> RenderPaths:
    """Render a full system-level schematic from design.yaml.

    Layout: power flows left→right at top, MCU center-left,
    motor drivers stacked on the right, I2C/bus labels on the left.
    """
    import yaml

    design_path = ensure_project(project_slug) / "design.yaml"
    if not design_path.exists():
        raise FileNotFoundError(f"No design.yaml at {design_path}")

    design = yaml.safe_load(design_path.read_text())
    components = design.get("components", {})
    power_source = design.get("power_source", {})

    with schemdraw.Drawing(show=False, transparent=False) as d:
        d.config(fontsize=12, unit=3.5, margin=3.5)

        # ════════════════════════════════════════════════════
        #  POWER PATH (top, left to right)
        # ════════════════════════════════════════════════════

        # Battery — start further right so label isn't clipped
        d.add(elm.BatteryCell().at((3, 0)).up().reverse())
        d.add(elm.Ground().at((3, 0)))
        d.add(elm.Label().at((1, 1)).label("7.4V\n2S LiPo", fontsize=11, halign="center"))
        d.add(elm.Dot().at((3, 2)))
        d.add(elm.Line().right(2))

        # Buck — simplified (system-level: just VIN→VOUT)
        buck_part = components.get("buck_converter", {}).get("part", "Buck")
        buck = d.add(elm.Ic(
            pins=[
                elm.IcPin(name="VIN", side="left", slot="1/1"),
                elm.IcPin(name="VOUT", side="right", slot="1/1"),
            ],
            edgepadW=0.6, edgepadH=0.5, pinspacing=1, lofst=0.2,
        ).anchor("VIN").label(buck_part, loc="top", ofst=0.2))

        d.add(elm.Line().at(buck.VOUT).right(1))
        d.add(elm.Inductor2().right(2.5).label("3.3µH", loc="top"))
        rail5v_x, rail5v_y = d.here
        d.add(elm.Dot())

        d.push()
        d.add(elm.Capacitor().down(2.5).label("22µF", loc="right"))
        d.add(elm.Ground())
        d.pop()

        # 5V rail
        d.add(elm.Line().right(20))
        d.add(elm.Label().label("5V", loc="right", fontsize=14))

        # ── LDO (horizontal, below 5V rail) ──
        ldo_tap_x = rail5v_x + 5
        ldo_y = rail5v_y - 4
        d.add(elm.Dot().at((ldo_tap_x, rail5v_y)))
        d.add(elm.Line().down().toy((0, ldo_y)))

        ldo_part = components.get("ldo", {}).get("part", "LDO")
        ldo = d.add(elm.Ic(
            pins=[
                elm.IcPin(name="IN", side="left", slot="1/1"),
                elm.IcPin(name="OUT", side="right", slot="1/1"),
            ],
            edgepadW=0.7, edgepadH=0.5, pinspacing=1, lofst=0.2,
        ).anchor("IN").label(ldo_part, loc="top", ofst=0.2))

        d.add(elm.Line().at(ldo.OUT).right(1))
        rail3v3_x, rail3v3_y = d.here
        d.add(elm.Dot())

        d.push()
        d.add(elm.Capacitor().down(2.5).label("1µF", loc="right"))
        d.add(elm.Ground())
        d.pop()

        d.add(elm.Line().right(13))
        d.add(elm.Label().label("3.3V", loc="right", fontsize=14))

        # ════════════════════════════════════════════════════
        #  MCU
        # ════════════════════════════════════════════════════
        mcu_part = components.get("mcu_ble", {}).get("part", "MCU")
        mcu_x = ldo_tap_x + 3
        mcu_y = rail3v3_y - 12

        mcu = d.add(elm.Ic(
            pins=[
                elm.IcPin(name="I2C0", side="left", slot="5/5"),
                elm.IcPin(name="I2C1", side="left", slot="4/5"),
                elm.IcPin(name="SPI", side="left", slot="3/5"),
                elm.IcPin(name="UART", side="left", slot="2/5"),
                elm.IcPin(name="USB", side="left", slot="1/5"),
                elm.IcPin(name="M1-2", side="right", slot="5/5"),
                elm.IcPin(name="M3-4", side="right", slot="4/5"),
                elm.IcPin(name="M5-6", side="right", slot="3/5"),
                elm.IcPin(name="STEP", side="right", slot="2/5"),
                elm.IcPin(name="ADC", side="right", slot="1/5"),
                elm.IcPin(name="", pin="", side="top", slot="1/1"),
                elm.IcPin(name="", pin="", side="bot", slot="1/1"),
            ],
            edgepadW=0.8, edgepadH=0.8, pinspacing=1.3, lofst=0.2,
        ).at((mcu_x, mcu_y)).label(mcu_part, loc="top", ofst=0.3))

        # VDD (top) and GND (bottom) — use positional anchors since pin names are empty
        d.add(elm.Line().at(mcu.inT1).up().toy((0, rail3v3_y)))
        d.add(elm.Dot())
        d.add(elm.Label().at(mcu.inT1).label("3V3", loc="left", ofst=0.3, fontsize=9))
        d.add(elm.Line().at(mcu.inB1).down(0.8))
        d.add(elm.Ground())

        # MCU left labels — use actual GPIO numbers from design.yaml when available
        i2c_buses = design.get("i2c_buses", {})
        bus0 = i2c_buses.get(0, {})
        bus1 = i2c_buses.get(1, {})
        bus0_label = f"I2C0 [{bus0.get('sda','')}/{bus0.get('scl','')}]" if bus0 else "I2C0"
        bus1_label = f"I2C1 [{bus1.get('sda','')}/{bus1.get('scl','')}]" if bus1 else "I2C1"

        # Build I2C device lists from design.yaml
        bus0_devs = ", ".join([d.get("name", "") for d in bus0.get("devices", [])])[:30]
        bus1_devs = ", ".join([d.get("name", "") for d in bus1.get("devices", [])])[:30]

        for attr, lbl in [
            ("I2C0", f"{bus0_label} → {bus0_devs or 'IMU'}"),
            ("I2C1", f"{bus1_label} → {bus1_devs or 'sensors'}"),
            ("SPI", "SPI → expansion header"),
            ("UART", "UART → debug header"),
            ("USB", "USB-C → host"),
        ]:
            d.add(elm.Line().at(getattr(mcu, attr)).left(4))
            d.add(elm.Label().label(lbl, loc="left", fontsize=9))

        # MCU right non-motor labels with GPIO assignments
        d.add(elm.Line().at(mcu.STEP).right(3.5))
        d.add(elm.Label().label("Stepper [GPIO10-13]", loc="right", fontsize=9))
        d.add(elm.Line().at(mcu.ADC).right(3.5))
        d.add(elm.Label().label("Battery ADC [GPIO4]", loc="right", fontsize=9))

        # ════════════════════════════════════════════════════
        #  MOTOR DRIVERS (qty from design.yaml — typically 4: 3 motors + 1 stepper)
        # ════════════════════════════════════════════════════
        motor_part = components.get("motor_driver", {}).get("part", "DRV8833")
        motor_qty = components.get("motor_driver", {}).get("qty_per_board", 3)
        # Get actual GPIO pin assignments from design.yaml
        pin_pool = design.get("pin_pool", {})
        breakdown = pin_pool.get("breakdown", {})
        has_stepper = "stepper_in1234" in breakdown and motor_qty >= 4
        drv_x = mcu_x + 15
        drv_spacing = 5

        # Show all motor drivers (cap at 4 for layout)
        n_drivers = min(motor_qty, 4)
        labels_for = ["Motor 1/2", "Motor 3/4", "Motor 5/6", "Stepper Coil A/B"]
        pwm_labels = ["GPIO4/5", "GPIO6/7", "GPIO8/9", "GPIO10-13"]

        for i in range(n_drivers):
            drv_y = mcu_y + 8 - i * drv_spacing
            is_stepper = (i == 3)

            drv = d.add(elm.Ic(
                pins=[
                    elm.IcPin(name="IN", side="left", slot="1/1"),
                    elm.IcPin(name="OUT A", side="right", slot="2/2"),
                    elm.IcPin(name="OUT B", side="right", slot="1/2"),
                    elm.IcPin(name="", pin="", side="top", slot="1/1"),
                    elm.IcPin(name="", pin="", side="bot", slot="1/1"),
                ],
                edgepadW=0.6, edgepadH=0.5, pinspacing=1.1, lofst=0.2,
            ).at((drv_x, drv_y)).label(
                f"{motor_part} #{i+1}" + (" (stepper)" if is_stepper else ""),
                loc="top", ofst=0.3, fontsize=10,
            ))

            # VM → 5V rail
            d.add(elm.Line().at(drv.inT1).up().toy((0, rail5v_y)))
            d.add(elm.Dot())
            d.add(elm.Label().at(drv.inT1).label("VM=5V", loc="left", ofst=0.2, fontsize=8))

            # GND
            d.add(elm.Line().at(drv.inB1).down(0.5))
            d.add(elm.Ground())

            # Motor outputs with labels
            out_a = getattr(drv, "OUT A")
            out_b = getattr(drv, "OUT B")
            d.add(elm.Line().at(out_a).right(2.5))
            d.add(elm.Label().label(labels_for[i].split("/")[0], loc="right", fontsize=10))
            d.add(elm.Line().at(out_b).right(2.5))
            d.add(elm.Label().label(labels_for[i].split("/")[-1], loc="right", fontsize=10))

            # Input label with GPIO pins
            d.add(elm.Line().at(drv.IN).left(2.5))
            d.add(elm.Label().label(pwm_labels[i], loc="left", fontsize=9))

    out_path = ensure_project(project_slug) / output
    d.save(str(out_path))
    _inject_white_bg(out_path)
    return {"svg": out_path, "png": _to_png(out_path)}
