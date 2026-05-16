"""Hardware design decision tree — code drives, AI presents.

A deterministic tree that walks through every hardware design decision.
Each node asks a question, validates the answer, and routes to the next node.
The tree covers every scenario and produces a complete system profile.

Usage (by LLM via MCP tool):
    from hw_agent.core.design_tree import DesignTree
    tree = DesignTree()
    q = tree.current()          # get current question
    tree.answer("vehicle_12v")  # answer it
    q = tree.current()          # get next question
    ...
    profile = tree.profile()    # get complete system profile

Usage (CLI):
    python -m hw_agent.core.design_tree  # interactive mode
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ─── Question Types ──────────────────────────────────────────────────────────

class QType(Enum):
    SINGLE = "single"       # pick one
    MULTI = "multi"         # pick many
    NUMBER = "number"       # numeric input
    TEXT = "text"           # free text
    CONFIRM = "confirm"     # yes/no


@dataclass
class Option:
    key: str
    label: str
    description: str = ""
    implies: dict[str, Any] = field(default_factory=dict)  # auto-set profile values
    enables: list[str] = field(default_factory=list)        # unlock follow-up questions
    disables: list[str] = field(default_factory=list)       # skip follow-up questions


@dataclass
class Question:
    id: str
    stage: str
    text: str
    qtype: QType
    options: list[Option] = field(default_factory=list)
    default: Any = None
    unit: str = ""
    depends_on: str | None = None       # only show if this question was answered
    depends_value: Any = None            # only show if depends_on == this value
    skip_if: list[str] = field(default_factory=list)  # skip if any of these flags set
    required: bool = True
    validate: str = ""                  # validation rule name


# ─── The Decision Tree ───────────────────────────────────────────────────────

STAGES = [
    "project",
    "power",
    "environment",
    "connectivity",
    "processing",
    "storage",
    "physical",
    "budget",
]

QUESTIONS: list[Question] = [

    # ── Stage: Project ───────────────────────────────────────────────────

    Question(
        id="project_name",
        stage="project",
        text="What's the project name?",
        qtype=QType.TEXT,
        default="my-hardware-project",
    ),
    Question(
        id="project_type",
        stage="project",
        text="What type of device?",
        qtype=QType.SINGLE,
        options=[
            Option("sensor_node", "Sensor / data logger", "Collects data, maybe sends it somewhere"),
            Option("controller", "Controller / actuator", "Controls motors, relays, valves, etc."),
            Option("gateway", "Gateway / bridge", "Translates between interfaces (CAN→WiFi, etc.)"),
            Option("tracker", "Tracker / telematics", "GPS location + data reporting",
                   enables=["need_gps", "need_cellular"]),
            Option("hmi", "HMI / display", "User interface with screen, buttons, etc."),
            Option("power_supply", "Power supply / converter", "Converts or manages power"),
            Option("custom", "Other (describe)", "Something else"),
        ],
    ),
    Question(
        id="project_desc",
        stage="project",
        text="Brief description — what does it do?",
        qtype=QType.TEXT,
    ),

    # ── Stage: Power ─────────────────────────────────────────────────────

    Question(
        id="power_source",
        stage="power",
        text="What powers this device?",
        qtype=QType.SINGLE,
        options=[
            Option("vehicle_12v", "Vehicle 12V", "Car/truck 12V battery (actual range 9-16V, transients to 40V)",
                   implies={"vin_nom": 12, "vin_min": 9, "vin_max": 16, "vin_transient": 40},
                   enables=["need_input_protection"]),
            Option("vehicle_24v", "Vehicle 24V", "Truck/bus 24V battery (actual range 18-32V, transients to 60V)",
                   implies={"vin_nom": 24, "vin_min": 18, "vin_max": 32, "vin_transient": 60},
                   enables=["need_input_protection"]),
            Option("vehicle_12_24", "Vehicle 12V or 24V", "Must handle both (9-32V, transients to 60V)",
                   implies={"vin_nom": 12, "vin_min": 9, "vin_max": 32, "vin_transient": 60},
                   enables=["need_input_protection"]),
            Option("usb_5v", "USB 5V", "USB powered (5V, 500mA or 3A with USB-C PD)",
                   implies={"vin_nom": 5, "vin_min": 4.5, "vin_max": 5.5, "vin_transient": 6}),
            Option("battery_lipo", "LiPo battery", "3.7V nominal (3.0-4.2V range)",
                   implies={"vin_nom": 3.7, "vin_min": 3.0, "vin_max": 4.2},
                   enables=["need_charger", "need_fuel_gauge"]),
            Option("battery_aa", "AA/AAA batteries", "1.5V per cell",
                   enables=["battery_cell_count"]),
            Option("poe", "Power over Ethernet", "48V from PoE switch",
                   implies={"vin_nom": 48, "vin_min": 36, "vin_max": 57},
                   enables=["need_poe_pd"]),
            Option("mains_ac", "Mains AC", "110/220V AC",
                   enables=["need_acdc_converter"]),
            Option("solar", "Solar panel", "Variable voltage",
                   enables=["need_mppt", "need_charger"]),
            Option("external_dc", "External DC supply", "Specific DC voltage",
                   enables=["custom_vin"]),
        ],
    ),
    Question(
        id="custom_vin",
        stage="power",
        text="What DC voltage range? (min-max in volts, e.g. '5-28')",
        qtype=QType.TEXT,
        depends_on="power_source",
        depends_value="external_dc",
    ),
    Question(
        id="battery_cell_count",
        stage="power",
        text="How many battery cells in series?",
        qtype=QType.NUMBER,
        default=2,
        unit="cells",
        depends_on="power_source",
        depends_value="battery_aa",
    ),
    Question(
        id="rails_needed",
        stage="power",
        text="What voltage rails do you need?",
        qtype=QType.MULTI,
        options=[
            Option("rail_5v", "5V", "USB, some sensors, cellular modules"),
            Option("rail_3v3", "3.3V", "Most MCUs, BLE, GPS, SPI flash"),
            Option("rail_1v8", "1.8V", "Some MCU cores, DDR memory"),
            Option("rail_1v2", "1.2V", "FPGA cores, DDR"),
            Option("rail_12v", "12V", "Relays, motors, CAN bus"),
            Option("rail_vbat", "VBAT backup", "RTC, SRAM retention"),
            Option("rail_auto", "Figure it out for me", "Choose based on components selected"),
        ],
    ),
    Question(
        id="sleep_mode",
        stage="power",
        text="Does the device need a low-power sleep mode?",
        qtype=QType.SINGLE,
        options=[
            Option("no_sleep", "No — always on", "Wall powered, no battery concern"),
            Option("light_sleep", "Light sleep", "MCU sleeps, peripherals stay powered, wake in <10ms"),
            Option("deep_sleep", "Deep sleep", "Everything off except RTC/wakeup, <10uA target",
                   implies={"sleep_target_ua": 10}),
            Option("shipping", "Shipping mode", "Near-zero power for months on shelf, <1uA",
                   implies={"sleep_target_ua": 1}),
        ],
    ),

    # ── Stage: Environment ───────────────────────────────────────────────

    Question(
        id="temp_range",
        stage="environment",
        text="Operating temperature range?",
        qtype=QType.SINGLE,
        options=[
            Option("consumer", "Consumer (0 to +70C)", "Indoor, climate controlled"),
            Option("industrial", "Industrial (-40 to +85C)", "Outdoor, factory, vehicle cabin"),
            Option("automotive", "Automotive (-40 to +125C)", "Under hood, harsh vehicle",
                   implies={"need_aec_q100": True}),
            Option("extended", "Extended (-40 to +105C)", "Between industrial and automotive"),
            Option("mil", "Military (-55 to +125C)", "Extreme environments"),
        ],
    ),
    Question(
        id="ip_rating",
        stage="environment",
        text="Ingress protection needed?",
        qtype=QType.SINGLE,
        options=[
            Option("none", "None — indoor/enclosed", "PCB inside a case"),
            Option("ip54", "IP54 — dust/splash", "Some dust and water exposure"),
            Option("ip67", "IP67 — submersible", "Full dust seal, brief submersion"),
            Option("ip69k", "IP69K — pressure wash", "High-pressure water jets"),
        ],
    ),
    Question(
        id="vibration",
        stage="environment",
        text="Vibration/shock environment?",
        qtype=QType.SINGLE,
        options=[
            Option("none", "None — stationary", "Desk, wall mount"),
            Option("light", "Light — handheld", "Dropped occasionally"),
            Option("moderate", "Moderate — vehicle mounted", "Road vibration, thermal cycling",
                   implies={"need_conformal_coat": True}),
            Option("heavy", "Heavy — engine/industrial", "Constant vibration, high shock",
                   implies={"need_conformal_coat": True, "need_mechanical_review": True}),
        ],
    ),
    Question(
        id="emc_cert",
        stage="environment",
        text="EMC/safety certifications needed?",
        qtype=QType.MULTI,
        required=False,
        options=[
            Option("fcc", "FCC Part 15", "Required for US sale with radio"),
            Option("ce", "CE marking", "Required for EU sale"),
            Option("eld_fmcsa", "FMCSA ELD", "Electronic logging device for US trucks"),
            Option("aec_q", "AEC-Q100/Q200", "Automotive component qualification"),
            Option("ul", "UL/CSA", "Safety listing"),
            Option("atex", "ATEX/IECEx", "Explosive atmosphere"),
            Option("none", "None / figure out later", "Prototype stage"),
        ],
    ),

    # ── Stage: Connectivity ──────────────────────────────────────────────

    Question(
        id="wired_interfaces",
        stage="connectivity",
        text="What wired interfaces?",
        qtype=QType.MULTI,
        options=[
            Option("can", "CAN bus", "Vehicle/industrial network, needs transceiver",
                   implies={"need_can_transceiver": True}),
            Option("can_fd", "CAN FD", "Faster CAN, needs FD-capable transceiver",
                   implies={"need_can_fd_transceiver": True}),
            Option("rs485", "RS-485 / Modbus", "Industrial serial bus, needs transceiver",
                   implies={"need_rs485_transceiver": True}),
            Option("rs232", "RS-232", "Legacy serial",
                   implies={"need_rs232_transceiver": True}),
            Option("j1708", "J1708/J1587", "Legacy truck diagnostic bus (RS-485 based)",
                   implies={"need_rs485_transceiver": True}),
            Option("usb_device", "USB device", "Connect to host PC/phone"),
            Option("usb_host", "USB host", "Connect USB peripherals"),
            Option("ethernet", "Ethernet", "Needs PHY chip",
                   implies={"need_eth_phy": True}),
            Option("i2c_ext", "External I2C", "Sensors, EEPROMs on external connector"),
            Option("spi_ext", "External SPI", "External SPI devices"),
            Option("gpio_ext", "GPIO / digital IO", "Relays, switches, LEDs"),
            Option("analog", "Analog inputs", "Voltage/current sensing, ADC"),
            Option("none", "None", "No wired external interfaces"),
        ],
    ),
    Question(
        id="wireless",
        stage="connectivity",
        text="What wireless connectivity?",
        qtype=QType.MULTI,
        options=[
            Option("ble", "Bluetooth Low Energy", "Short range, phone pairing, beacons",
                   implies={"need_ble_module": True}),
            Option("bt_classic", "Bluetooth Classic", "SPP, audio, legacy devices",
                   implies={"need_bt_module": True}),
            Option("wifi", "WiFi", "2.4/5GHz, cloud connectivity",
                   implies={"need_wifi_module": True}),
            Option("lte_m", "LTE-M / Cat-M1", "Low-power cellular IoT",
                   implies={"need_cellular_module": True, "cellular_type": "lte_m"}),
            Option("nb_iot", "NB-IoT", "Very low power, low data rate cellular",
                   implies={"need_cellular_module": True, "cellular_type": "nb_iot"}),
            Option("lte_cat1", "LTE Cat-1", "Full LTE, moderate data",
                   implies={"need_cellular_module": True, "cellular_type": "cat1"}),
            Option("lte_cat4", "LTE Cat-4", "High speed cellular",
                   implies={"need_cellular_module": True, "cellular_type": "cat4"}),
            Option("gps", "GPS/GNSS", "Position tracking",
                   implies={"need_gps_module": True}),
            Option("lora", "LoRa / LoRaWAN", "Long range, low power",
                   implies={"need_lora_module": True}),
            Option("zigbee", "Zigbee / Thread / Matter", "Mesh networking",
                   implies={"need_802154_radio": True}),
            Option("uwb", "Ultra-Wideband", "Precise ranging",
                   implies={"need_uwb_module": True}),
            Option("nfc", "NFC", "Tap to pair/configure",
                   implies={"need_nfc": True}),
            Option("none", "None", "No wireless"),
        ],
    ),
    Question(
        id="antenna_type",
        stage="connectivity",
        text="Antenna approach?",
        qtype=QType.SINGLE,
        skip_if=["no_wireless"],
        options=[
            Option("chip", "Chip antenna", "Small, easy, lower performance"),
            Option("pcb", "PCB trace antenna", "Free, tuning required"),
            Option("external", "External antenna (SMA/U.FL)", "Best performance, connector needed"),
            Option("mixed", "Mixed — some chip, some external", "Depends on radio"),
            Option("unsure", "Not sure yet", "Decide per radio"),
        ],
    ),

    # ── Stage: Processing ────────────────────────────────────────────────

    Question(
        id="processing_level",
        stage="processing",
        text="How much processing power?",
        qtype=QType.SINGLE,
        options=[
            Option("minimal", "Minimal", "Read sensors, send data. 8-bit or Cortex-M0 fine.",
                   implies={"mcu_class": "m0", "flash_min_kb": 64, "ram_min_kb": 16}),
            Option("moderate", "Moderate", "Protocol stacks (BLE, TCP), some logic. Cortex-M4.",
                   implies={"mcu_class": "m4", "flash_min_kb": 256, "ram_min_kb": 64}),
            Option("heavy", "Heavy", "Real-time control, DSP, crypto. Cortex-M7.",
                   implies={"mcu_class": "m7", "flash_min_kb": 1024, "ram_min_kb": 256}),
            Option("linux", "Linux-class", "Networking, UI, complex apps. MPU with Linux/RTOS.",
                   implies={"mcu_class": "mpu"}),
            Option("fpga", "FPGA needed", "Custom logic, high-speed IO.",
                   implies={"need_fpga": True}),
        ],
    ),
    Question(
        id="rtos",
        stage="processing",
        text="Operating system / RTOS?",
        qtype=QType.SINGLE,
        options=[
            Option("bare_metal", "Bare metal", "No OS, direct register access"),
            Option("freertos", "FreeRTOS", "Most common embedded RTOS"),
            Option("zephyr", "Zephyr", "Modern, good driver support, BLE stack"),
            Option("linux", "Embedded Linux", "Full Linux on MPU"),
            Option("unsure", "Not sure yet", "Decide based on complexity"),
        ],
    ),

    # ── Stage: Storage ───────────────────────────────────────────────────

    Question(
        id="storage_needs",
        stage="storage",
        text="Non-volatile storage needs?",
        qtype=QType.MULTI,
        options=[
            Option("config", "Config / calibration", "< 1KB, EEPROM or flash sector",
                   implies={"need_eeprom": True}),
            Option("logging", "Data logging", "Store sensor data when offline",
                   enables=["logging_size"]),
            Option("firmware", "OTA firmware updates", "Need space for 2 firmware images",
                   implies={"need_ext_flash": True}),
            Option("files", "File storage", "Large files, FAT filesystem",
                   implies={"need_sd_card": True}),
            Option("fram", "FRAM", "Fast byte-level writes, no wear",
                   implies={"need_fram": True}),
            Option("none", "Internal flash only", "MCU flash is enough"),
        ],
    ),
    Question(
        id="logging_size",
        stage="storage",
        text="How much logging storage?",
        qtype=QType.SINGLE,
        depends_on="storage_needs",
        depends_value="logging",
        options=[
            Option("small", "< 1MB", "Hours of sensor data, SPI flash",
                   implies={"ext_flash_mb": 1}),
            Option("medium", "1-16MB", "Days of data, SPI flash",
                   implies={"ext_flash_mb": 16}),
            Option("large", "16-128MB", "Weeks of data, QSPI or SD",
                   implies={"ext_flash_mb": 128}),
            Option("huge", "> 128MB", "Months of data, SD card required",
                   implies={"need_sd_card": True}),
        ],
    ),

    # ── Stage: Physical ──────────────────────────────────────────────────

    Question(
        id="board_size",
        stage="physical",
        text="Target board size?",
        qtype=QType.SINGLE,
        options=[
            Option("tiny", "Tiny (< 25x25mm)", "Wearable, coin-size"),
            Option("small", "Small (25-50mm)", "Credit card width"),
            Option("medium", "Medium (50-100mm)", "Typical embedded board"),
            Option("large", "Large (> 100mm)", "No real size constraint"),
            Option("custom", "Specific dimensions", "Has enclosure constraint"),
        ],
    ),
    Question(
        id="pcb_layers",
        stage="physical",
        text="PCB layer count?",
        qtype=QType.SINGLE,
        options=[
            Option("2layer", "2-layer", "Cheapest, OK for simple designs"),
            Option("4layer", "4-layer", "Standard for MCU boards, good EMC"),
            Option("6layer", "6-layer", "High-speed, many power planes"),
            Option("unsure", "Not sure yet", "4-layer is safe default"),
        ],
    ),

    # ── Stage: Budget ────────────────────────────────────────────────────

    Question(
        id="bom_target",
        stage="budget",
        text="Target BOM cost per unit?",
        qtype=QType.SINGLE,
        options=[
            Option("ultra_low", "< $5", "Very cost sensitive"),
            Option("low", "$5 - $15", "Cost conscious"),
            Option("moderate", "$15 - $50", "Feature-driven"),
            Option("high", "$50 - $200", "Performance-driven"),
            Option("any", "No target yet", "Prototype stage, optimize later"),
        ],
    ),
    Question(
        id="volume",
        stage="budget",
        text="Expected production volume?",
        qtype=QType.SINGLE,
        options=[
            Option("proto", "Prototype (1-5)", "Just proving the concept"),
            Option("small", "Small batch (5-100)", "Pilot run, beta users"),
            Option("medium", "Medium (100-1000)", "First production run"),
            Option("large", "Large (1000-10000)", "Full production"),
            Option("mass", "Mass (10000+)", "High volume"),
        ],
    ),
    Question(
        id="jlcpcb_assembly",
        stage="budget",
        text="Using JLCPCB assembly service?",
        qtype=QType.SINGLE,
        options=[
            Option("yes_basic", "Yes — prefer basic parts", "No assembly fee on basic parts",
                   implies={"prefer_basic_parts": True}),
            Option("yes_any", "Yes — any parts OK", "Extended parts OK, $3 per unique part"),
            Option("no", "No — hand assembly or other fab", "No JLCPCB constraint"),
            Option("unsure", "Not sure yet", "Default to JLCPCB for prototyping"),
        ],
    ),
]


# ─── Tree Engine ─────────────────────────────────────────────────────────────

class DesignTree:
    """Walks through the decision tree, collecting answers."""

    def __init__(self):
        self.answers: dict[str, Any] = {}
        self.flags: set[str] = set()
        self._profile_values: dict[str, Any] = {}
        self._idx = 0
        self._questions = list(QUESTIONS)

    def current(self) -> dict | None:
        """Get the current question as a dict. Returns None if tree is complete."""
        while self._idx < len(self._questions):
            q = self._questions[self._idx]

            # Check skip conditions
            if q.skip_if and any(f in self.flags for f in q.skip_if):
                self._idx += 1
                continue

            # Check dependency
            if q.depends_on:
                dep_answer = self.answers.get(q.depends_on)
                if dep_answer is None:
                    self._idx += 1
                    continue
                # For MULTI, check if value is in the list
                if isinstance(dep_answer, list):
                    if q.depends_value not in dep_answer:
                        self._idx += 1
                        continue
                elif dep_answer != q.depends_value:
                    self._idx += 1
                    continue

            return self._format_question(q)

        return None  # done

    def answer(self, value: Any) -> dict:
        """Answer the current question. Returns {ok, next_question, profile_updates}."""
        q = self._questions[self._idx]

        # Validate
        if q.qtype == QType.SINGLE:
            valid_keys = [o.key for o in q.options]
            if value not in valid_keys:
                return {"ok": False, "error": f"Pick one of: {valid_keys}"}
        elif q.qtype == QType.MULTI:
            if isinstance(value, str):
                value = [value]
            valid_keys = [o.key for o in q.options]
            for v in value:
                if v not in valid_keys:
                    return {"ok": False, "error": f"Invalid: {v}. Pick from: {valid_keys}"}

        # Store answer
        self.answers[q.id] = value

        # Process implications
        updates = {}
        selected = value if isinstance(value, list) else [value]
        for opt in q.options:
            if opt.key in selected:
                self._profile_values.update(opt.implies)
                updates.update(opt.implies)
                self.flags.update(opt.enables)
                # Handle disables
                for d in opt.disables:
                    self.flags.discard(d)

        # Check for "none" wireless
        if q.id == "wireless" and value == ["none"]:
            self.flags.add("no_wireless")

        self._idx += 1
        nxt = self.current()

        return {"ok": True, "profile_updates": updates, "next": nxt}

    def profile(self) -> dict:
        """Get the complete system profile derived from all answers."""
        p = dict(self._profile_values)
        p["answers"] = dict(self.answers)

        # Derive subsystems needed
        subsystems = []
        if self._profile_values.get("vin_transient", 0) > 6:
            subsystems.append({"type": "input_protection", "reason": "High voltage input needs TVS + reverse polarity"})

        vin = self._profile_values.get("vin_nom", 5)
        rails = self.answers.get("rails_needed", [])

        if "rail_5v" in rails and vin > 6:
            subsystems.append({"type": "buck_regulator", "vin": vin, "vout": 5.0, "reason": f"{vin}V → 5V step-down"})
        if "rail_3v3" in rails:
            if "rail_5v" in rails:
                subsystems.append({"type": "ldo", "vin": 5.0, "vout": 3.3, "reason": "5V → 3.3V LDO"})
            elif vin > 4:
                subsystems.append({"type": "buck_regulator", "vin": vin, "vout": 3.3, "reason": f"{vin}V → 3.3V"})
        if "rail_1v8" in rails:
            subsystems.append({"type": "ldo", "vin": 3.3, "vout": 1.8, "reason": "3.3V → 1.8V LDO"})

        for key, val in self._profile_values.items():
            if key.startswith("need_") and val:
                name = key[5:]  # strip "need_"
                subsystems.append({"type": name, "reason": f"Required by design choices"})

        p["subsystems"] = subsystems

        # Derive search queries
        p["search_queries"] = self._generate_searches(subsystems)

        return p

    def _generate_searches(self, subsystems: list[dict]) -> list[dict]:
        """Generate JLCPCB search queries from the subsystem list."""
        queries = []
        vin_max = self._profile_values.get("vin_transient") or self._profile_values.get("vin_max", 6)

        for sub in subsystems:
            t = sub["type"]
            if t == "buck_regulator":
                queries.append({
                    "subsystem": t,
                    "tool": "jlc_search",
                    "subcategory": "DC-DC Converters",
                    "spec_filters": [
                        {"name": "Voltage - Supply", "op": ">=", "value": f"{vin_max}V"},
                        {"name": "Iout", "op": ">=", "value": "3A"},
                        {"name": "Function", "op": "=", "value": "Buck"},
                    ],
                    "sort_by": "stock",
                })
            elif t == "ldo":
                vout = sub.get("vout", 3.3)
                queries.append({
                    "subsystem": t,
                    "tool": "jlc_search",
                    "query": f"{vout}V LDO",
                    "subcategory": "Linear Voltage Regulators(LDO)",
                    "sort_by": "stock",
                })
            elif t == "can_transceiver":
                queries.append({
                    "subsystem": t,
                    "tool": "jlc_search",
                    "query": "CAN transceiver 3.3V",
                    "subcategory": "CAN Transceivers",
                    "sort_by": "stock",
                })
            elif t == "gps_module":
                queries.append({
                    "subsystem": t,
                    "tool": "jlc_search",
                    "query": "GNSS GPS module",
                    "sort_by": "stock",
                })
            elif t == "cellular_module":
                cell_type = self._profile_values.get("cellular_type", "lte_m")
                queries.append({
                    "subsystem": t,
                    "tool": "jlc_search",
                    "query": f"{cell_type} module",
                    "sort_by": "stock",
                })
            elif t == "ble_module":
                queries.append({
                    "subsystem": t,
                    "tool": "jlc_search",
                    "query": "BLE 5 module",
                    "sort_by": "stock",
                })
            elif t == "fram":
                queries.append({
                    "subsystem": t,
                    "tool": "jlc_search",
                    "query": "FRAM SPI",
                    "sort_by": "stock",
                })
            elif t == "input_protection":
                queries.append({
                    "subsystem": t,
                    "tool": "jlc_search",
                    "query": f"TVS bidirectional {vin_max}V",
                    "subcategory": "TVS Diodes",
                    "sort_by": "stock",
                })

        return queries

    def progress(self) -> dict:
        """How far through the tree we are."""
        answered = len(self.answers)
        remaining = sum(1 for i in range(self._idx, len(self._questions))
                        if not self._questions[i].skip_if or
                        not any(f in self.flags for f in self._questions[i].skip_if))
        return {
            "answered": answered,
            "remaining": remaining,
            "stage": self._questions[self._idx].stage if self._idx < len(self._questions) else "done",
            "pct": round(answered / max(1, answered + remaining) * 100),
        }

    def _format_question(self, q: Question) -> dict:
        """Format a question for display."""
        d = {
            "id": q.id,
            "stage": q.stage,
            "text": q.text,
            "type": q.qtype.value,
            "required": q.required,
        }
        if q.options:
            d["options"] = [
                {"key": o.key, "label": o.label, "description": o.description}
                for o in q.options
            ]
        if q.default is not None:
            d["default"] = q.default
        if q.unit:
            d["unit"] = q.unit
        return d


# ─── CLI ─────────────────────────────────────────────────────────────────────

def main():
    import json

    tree = DesignTree()
    print("=== Hardware Design Decision Tree ===\n")

    while True:
        q = tree.current()
        if q is None:
            break

        prog = tree.progress()
        print(f"[{prog['stage'].upper()}] ({prog['pct']}%) {q['text']}")

        if q.get("options"):
            for i, opt in enumerate(q["options"]):
                desc = f" — {opt['description']}" if opt['description'] else ""
                print(f"  {i+1}. {opt['key']}: {opt['label']}{desc}")

        raw = input("\n> ").strip()
        if not raw and q.get("default"):
            raw = str(q["default"])

        # Handle multi-select (comma separated)
        if q["type"] == "multi":
            value = [v.strip() for v in raw.split(",")]
        else:
            value = raw

        result = tree.answer(value)
        if not result["ok"]:
            print(f"  Error: {result['error']}")
            continue

        if result.get("profile_updates"):
            print(f"  → Set: {result['profile_updates']}")
        print()

    print("\n=== SYSTEM PROFILE ===\n")
    print(json.dumps(tree.profile(), indent=2))


if __name__ == "__main__":
    main()
