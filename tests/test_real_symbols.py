"""Real-KiCad-symbol resolution path.

When a SubsystemPick carries a `lib_id`, the planner places the actual
KiCad library symbol (AddSymbol → sch_ops.add_ic) instead of synthesizing
a placeholder into hwagent.kicad_sym. Parts without a lib_id still
synthesize, unchanged. These tests touch kicad-cli (write + ERC).
"""
from __future__ import annotations

import hw_toolkit as hw
from hw_toolkit.kicad import sch_ops
from hw_toolkit.kicad.planner import AddCustomIC, AddSymbol, plan_schematic


# ----------------------------------------------------------- planner branch
def _bundle_with(lib_id: str | None):
    b = hw.Board("t")
    b.module(id="u1", category="buck_converter", mpn="TPS54302",
             package="SOT-23-6", lib_id=lib_id)
    b.module(id="u2", category="mcu", mpn="STM32F042K6Tx", package="LQFP-32")
    n = b.signal("link", protocol="gpio")
    n += "u1.VIN", "u2.PA0"
    return b.bundle


def test_lib_id_emits_add_symbol_op() -> None:
    plan = plan_schematic(_bundle_with("Regulator_Switching:TPS54302"), "/tmp/x.kicad_sch")
    u1_ops = [o for o in plan.ops
              if isinstance(o, (AddSymbol, AddCustomIC)) and o.ref == "U1"]
    assert len(u1_ops) == 1
    assert isinstance(u1_ops[0], AddSymbol)
    assert u1_ops[0].lib_id == "Regulator_Switching:TPS54302"
    assert u1_ops[0].value == "TPS54302"


def test_no_lib_id_still_synthesizes() -> None:
    plan = plan_schematic(_bundle_with(None), "/tmp/x.kicad_sch")
    u1_ops = [o for o in plan.ops
              if isinstance(o, (AddSymbol, AddCustomIC)) and o.ref == "U1"]
    assert len(u1_ops) == 1
    assert isinstance(u1_ops[0], AddCustomIC)


# ----------------------------------------------------------- write + ERC
def test_real_symbols_written_without_synthesis() -> None:
    b = hw.Board("t_real")
    b.module(id="u1", category="buck_converter", mpn="TPS54302",
             package="SOT-23-6", lib_id="Regulator_Switching:TPS54302",
             footprint="Package_TO_SOT_SMD:SOT-23-6")
    b.module(id="c1", category="capacitor", mpn="C_10uF", package="0805",
             lib_id="Device:C", footprint="Capacitor_SMD:C_0805_2012Metric")
    vin = b.power("vin", 12.0); vin += "u1.VIN", "c1.1"
    gnd = b.gnd(); gnd += "u1.GND", "c1.2"
    en = b.signal("en_tie", protocol="gpio"); en += "u1.EN", "u1.VIN"
    boot = b.signal("boot_n", protocol="gpio"); boot += "u1.BOOT", "u1.SW"
    fb = b.signal("fb_n", protocol="gpio"); fb += "u1.FB", "c1.1"
    b.write_kicad(overwrite=True)
    txt = b.sch_path.read_text()
    assert "Regulator_Switching:TPS54302" in txt
    assert "Device:C" in txt
    assert "hwagent:" not in txt  # nothing synthesized
    # wires resolve against the real symbol's named pins
    assert {"GND", "SW", "VIN", "FB", "EN", "BOOT"} == {
        p["name"] for p in sch_ops.list_pins(b.sch_path, "U1")
    }


def test_real_symbol_board_passes_erc() -> None:
    b = hw.Board("t_real_erc")
    b.module(id="u1", category="buck_converter", mpn="TPS54302",
             package="SOT-23-6", lib_id="Regulator_Switching:TPS54302",
             footprint="Package_TO_SOT_SMD:SOT-23-6")
    b.module(id="c1", category="capacitor", mpn="C_10uF", package="0805",
             lib_id="Device:C", footprint="Capacitor_SMD:C_0805_2012Metric")
    vin = b.power("vin", 12.0); vin += "u1.VIN", "c1.1"
    gnd = b.gnd(); gnd += "u1.GND", "c1.2"
    en = b.signal("en_tie", protocol="gpio"); en += "u1.EN", "u1.VIN"
    boot = b.signal("boot_n", protocol="gpio"); boot += "u1.BOOT", "u1.SW"
    fb = b.signal("fb_n", protocol="gpio"); fb += "u1.FB", "c1.1"
    b.check_erc(expected_codes=hw.ERC_BASELINE_CODES)
