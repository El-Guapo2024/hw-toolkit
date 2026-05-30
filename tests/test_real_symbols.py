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
def _bundle_with(mpn: str, lib_id: str | None):
    # An mpn not in the resolver catalog + lib_id=None exercises the
    # synthesis fallback; an explicit lib_id or a catalogued mpn does not.
    b = hw.Board("t")
    b.module(id="u1", category="custom_ic", mpn=mpn,
             package="SOT-23-6", lib_id=lib_id)
    b.module(id="u2", category="mcu", mpn="WIDGET-MCU-1", package="LQFP-32")
    n = b.signal("link", protocol="gpio")
    n += "u1.VIN", "u2.PA0"
    return b.bundle


def test_explicit_lib_id_emits_add_symbol_op() -> None:
    plan = plan_schematic(
        _bundle_with("WIDGET-9000", "Regulator_Switching:TPS54302"),
        "/tmp/x.kicad_sch",
    )
    u1_ops = [o for o in plan.ops
              if isinstance(o, (AddSymbol, AddCustomIC)) and o.ref == "U1"]
    assert len(u1_ops) == 1
    assert isinstance(u1_ops[0], AddSymbol)
    assert u1_ops[0].lib_id == "Regulator_Switching:TPS54302"
    assert u1_ops[0].value == "WIDGET-9000"


def test_uncatalogued_part_still_synthesizes() -> None:
    plan = plan_schematic(_bundle_with("WIDGET-9000", None), "/tmp/x.kicad_sch")
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


# ----------------------------------------------------------- resolver
def test_resolver_passives_to_device_symbols() -> None:
    from hw_toolkit.kicad.resolve import resolve_kicad_part
    assert resolve_kicad_part("R_10k_0603", "resistor", "0603") == (
        "Device:R", "Resistor_SMD:R_0603_1608Metric")
    assert resolve_kicad_part("C_1uF_0805", "capacitor", "0805") == (
        "Device:C", "Capacitor_SMD:C_0805_2012Metric")
    lib_id, _ = resolve_kicad_part("L_10uH_0805", "inductor", "0805")
    assert lib_id == "Device:L"


def test_resolver_catalogued_ic() -> None:
    from hw_toolkit.kicad.resolve import resolve_kicad_part
    lib_id, fp = resolve_kicad_part("TPS54302", "buck_converter", "SOT-23-6")
    assert lib_id == "Regulator_Switching:TPS54302"
    assert fp == "Package_TO_SOT_SMD:SOT-23-6"


def test_resolver_unknown_ic_returns_none() -> None:
    from hw_toolkit.kicad.resolve import resolve_kicad_part
    assert resolve_kicad_part("WIDGET-9000", "mcu", "QFN-32") == (None, None)


def test_resolver_rejects_bad_catalog_entry() -> None:
    # _symbol_exists must validate the SYMBOL, not just the lib file.
    from hw_toolkit.kicad.resolve import _symbol_exists
    assert _symbol_exists("Device:R") is True
    assert _symbol_exists("Regulator_Switching:TPS54331D") is False  # file ok, symbol absent


def test_board_module_auto_resolves() -> None:
    b = hw.Board("t")
    r = b.resistor("R1", "10k")
    assert r.lib_id == "Device:R"
    u = b.module(id="u1", category="buck_converter", mpn="TPS54302", package="SOT-23-6")
    assert u.lib_id == "Regulator_Switching:TPS54302"
    ghost = b.module(id="u2", category="mcu", mpn="WIDGET-9000", package="QFN-32")
    assert ghost.lib_id is None


# ----------------------------------------------------------- ERC win
def test_all_real_board_needs_no_synthesis_suppressions() -> None:
    # A board whose parts all resolved to real symbols emits neither
    # lib_symbol_issues nor footprint_link_issues, so the tighter
    # ERC_REAL_SYMBOL_CODES gate suffices.
    b = hw.Board("t_allreal")
    b.module(id="u1", category="buck_converter", mpn="TPS54302", package="SOT-23-6")
    b.capacitor("C1", "10uF", package="0805")
    b.capacitor("C2", "22uF", package="0805")
    vin = b.power("vin", 12.0); vin += "u1.VIN", "c1.1"
    gnd = b.gnd(); gnd += "u1.GND", "c1.2", "c2.2"
    en = b.signal("en_t", protocol="gpio"); en += "u1.EN", "u1.VIN"
    boot = b.signal("bt", protocol="gpio"); boot += "u1.BOOT", "u1.SW"
    fb = b.signal("fbn", protocol="gpio"); fb += "u1.FB", "c2.1"
    assert "lib_symbol_issues" not in hw.ERC_REAL_SYMBOL_CODES
    assert "footprint_link_issues" not in hw.ERC_REAL_SYMBOL_CODES
    b.check_erc(expected_codes=hw.ERC_REAL_SYMBOL_CODES)
