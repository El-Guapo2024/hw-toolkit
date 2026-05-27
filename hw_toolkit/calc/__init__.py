"""`hw.calc` — engineering math wrappers.

Each class wraps a stateless calc module (`hw_toolkit.calc._buck_math` etc.)
behind a typed object. Construct with operating-point inputs, call
methods, get typed result dataclasses back.

    >>> import hw_toolkit as hw
    >>> b = hw.calc.Buck(vin=24, vout=6, iout=5)
    >>> b.inductor().inductor_uh
    >>> b.thermal(rdson_mohm=80, theta_ja=40)

LDO, MotorDriver, Thermal, TLine to follow the same pattern as needed.
"""
from hw_toolkit.calc.buck import (
    Buck,
    InductorResult,
    OutputCapResult,
    ThermalResult,
)

__all__ = ["Buck", "InductorResult", "OutputCapResult", "ThermalResult"]
