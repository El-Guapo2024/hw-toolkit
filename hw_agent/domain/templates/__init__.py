"""Component templates — one Pydantic Subsystem class per component category.

Adding a new component type:
  1. Copy an existing template (e.g. `ldo.py`) and rename.
  2. Define `<Type>Requirements`, `<Type>Actuals`, `<Type>Subsystem` Pydantic classes.
  3. Add it to `SUBSYSTEM_REGISTRY` below so MCP tools (verify_candidate, q_load, …) can find it.
"""

from __future__ import annotations

from hw_agent.core.subsystem import ElectronicSubsystem
from hw_agent.domain.templates.ldo import LdoSubsystem
from hw_agent.domain.templates.buck import BuckSubsystem
from hw_agent.domain.templates.mcu_ble import McuBleSubsystem
from hw_agent.domain.templates.motor_driver import MotorDriverSubsystem
from hw_agent.domain.templates.imu import ImuSubsystem
from hw_agent.domain.templates.pwm_servo_driver import PwmServoDriverSubsystem
from hw_agent.domain.templates.stepper_driver import StepperDriverSubsystem


SUBSYSTEM_REGISTRY: dict[str, type[ElectronicSubsystem]] = {
    "ldo": LdoSubsystem,
    "buck_converter": BuckSubsystem,
    "mcu_ble": McuBleSubsystem,
    "motor_driver": MotorDriverSubsystem,
    "imu": ImuSubsystem,
    "pwm_servo_driver": PwmServoDriverSubsystem,
    "stepper_driver": StepperDriverSubsystem,
}


def get_subsystem_class(category: str) -> type[ElectronicSubsystem] | None:
    """Look up a subsystem class by its category key. Returns None if unknown."""
    return SUBSYSTEM_REGISTRY.get(category)


def list_categories() -> list[str]:
    return list(SUBSYSTEM_REGISTRY.keys())
