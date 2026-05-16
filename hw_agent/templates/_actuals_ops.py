"""Generic ops over Pydantic *Actuals models.

Pure data models stay pure — these are module-level helpers used by the
`Part` builder and any caller that wants to inspect/merge actuals.
"""

from __future__ import annotations

from typing import Iterable, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


def missing_fields(model: BaseModel) -> list[str]:
    """Field names whose value is None."""
    return [f for f, v in model.model_dump().items() if v is None]


def merge(*models: T) -> T:
    """Left-biased fill: first non-None value wins per field.

    All inputs must be instances of the same Pydantic class.
    """
    if not models:
        raise ValueError("merge() requires at least one model")
    cls = type(models[0])
    if any(type(m) is not cls for m in models[1:]):
        raise TypeError("merge() requires same-class models")
    out: dict = {}
    for m in models:
        for f, v in m.model_dump().items():
            if out.get(f) is None and v is not None:
                out[f] = v
    return cls(**out)


def fill_dict(model: T, fills: dict) -> T:
    """Return new model with `fills` filling only None fields. Does not overwrite."""
    cls = type(model)
    current = model.model_dump()
    for f, v in fills.items():
        if f in current and current[f] is None and v is not None:
            current[f] = v
    return cls(**current)
