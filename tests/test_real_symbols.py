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


def test_resolver_ic_by_exact_symbol_name() -> None:
    # The KiCad symbol name IS the MPN for most stock parts — exact index hit.
    from hw_toolkit.kicad.resolve import resolve_kicad_part
    lib_id, fp = resolve_kicad_part("TPS54302", "buck_converter", "SOT-23-6")
    assert lib_id == "Regulator_Switching:TPS54302"
    assert fp == "Package_TO_SOT_SMD:SOT-23-6"


def test_resolver_normalizes_st_packing_suffix() -> None:
    # Distributor MPN STM32F042K6T6 -> KiCad symbol STM32F042K6Tx.
    from hw_toolkit.kicad.resolve import resolve_kicad_part
    lib_id, _ = resolve_kicad_part("STM32F042K6T6", "mcu", "LQFP-32")
    assert lib_id == "MCU_ST_STM32F0:STM32F042K6Tx"


def test_resolver_alias() -> None:
    from hw_toolkit.kicad.resolve import resolve_kicad_part
    lib_id, _ = resolve_kicad_part("TCAN330GD", "interface", "SOIC-8")
    assert lib_id == "Interface_CAN_LIN:TCAN330G"


def test_resolver_unknown_ic_returns_none() -> None:
    from hw_toolkit.kicad.resolve import resolve_kicad_part
    assert resolve_kicad_part("WIDGET-9000", "mcu", "QFN-32") == (None, None)


def test_index_validates_symbol_not_just_file() -> None:
    # find_symbol_lib_id must match the SYMBOL, not the library file:
    # TPS54331D's file (Regulator_Switching) exists but the symbol doesn't.
    from hw_toolkit.kicad import lib
    assert lib.find_symbol_lib_id("TPS54302") == "Regulator_Switching:TPS54302"
    assert lib.find_symbol_lib_id("TPS54331D") is None
    assert lib.find_symbol_lib_id("Device:R") is None  # name only, not lib_id
    assert lib._symbol_index()["R"] == "Device:R"


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


def test_symbol_index_covers_thousands_of_parts() -> None:
    # The index scans the installed libs from cold (unlike kicad_sch_api's
    # cache-only search), so it resolves any stock symbol with no catalog.
    from hw_toolkit.kicad import lib
    idx = lib._symbol_index()
    assert len(idx) > 1000
    assert idx["TPS54302"] == "Regulator_Switching:TPS54302"


def test_aliases_resolve_to_installed_symbols() -> None:
    # Every alias target must be a real installed symbol.
    from hw_toolkit.kicad import lib
    from hw_toolkit.kicad.resolve import _ALIASES
    bad = [f"{k} -> {v}" for k, v in _ALIASES.items()
           if lib.find_symbol_lib_id(v) is None]
    assert not bad, f"aliases pointing at missing symbols: {bad}"
