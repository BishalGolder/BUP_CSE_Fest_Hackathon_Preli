"""Deterministic guardrails for LLM directive output.

The LLM returns ``directive_interpretation`` entries; this module enforces
the rules in the canonical Problem Statement so a hallucinated or malformed
entry never reaches the optimizer.

Failure modes raise ``GuardrailError`` with a precise field path. The API
layer surfaces these as HTTP 400 (request validation) or HTTP 500 (the LLM
provider produced an unrecoverable response), depending on the rule.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List

ALLOWED_DIRECTIVE_TYPES = {
    "solar_reduction",
    "minimum_battery_reserve",
    "no_charge_window",
    "no_discharge_window",
    "max_grid_window",
    "no_op",
}


class GuardrailError(ValueError):
    pass


# ---------- Top-level entry ----------

def validate_directives(
    directives: List[Dict[str, Any]],
    operator_notes: List[str],
) -> List[Dict[str, Any]]:
    """Validate and normalize ``directives`` produced by the LLM.

    Returns a new list with the same length and order as ``operator_notes``.
    Raises ``GuardrailError`` on the first violation.
    """
    n = len(operator_notes)
    if len(directives) != n:
        raise GuardrailError(
            f"Expected exactly {n} directive entries (one per note); got {len(directives)}"
        )

    out: List[Dict[str, Any]] = []
    for i, d in enumerate(directives):
        out.append(_validate_one(d, operator_notes[i], i))
    return out


# ---------- Single-entry validation ----------

def _validate_one(d: Any, note: str, index: int) -> Dict[str, Any]:
    if not isinstance(d, dict):
        raise GuardrailError(f"directive[{index}] must be an object, got {type(d).__name__}")

    # note_index
    ni = d.get("note_index")
    if ni != index:
        raise GuardrailError(f"directive[{index}].note_index must equal {index}, got {ni}")

    # applies
    applies = d.get("applies")
    if not isinstance(applies, bool):
        raise GuardrailError(f"directive[{index}].applies must be a boolean")

    # directive_type
    dtype = d.get("directive_type")
    if dtype not in ALLOWED_DIRECTIVE_TYPES:
        raise GuardrailError(
            f"directive[{index}].directive_type '{dtype}' not in {sorted(ALLOWED_DIRECTIVE_TYPES)}"
        )

    # explanation
    explanation = d.get("explanation")
    if not isinstance(explanation, str) or not explanation.strip():
        raise GuardrailError(f"directive[{index}].explanation must be a non-empty string")

    # structured_adjustment: only checked when directive applies (non-no_op)
    sa = d.get("structured_adjustment")

    if dtype == "no_op":
        if applies is not False:
            raise GuardrailError(f"directive[{index}] is no_op; applies must be false")
        if sa is not None:
            raise GuardrailError(f"directive[{index}] is no_op; structured_adjustment must be null")
        return {
            "note_index": index,
            "applies": False,
            "directive_type": "no_op",
            "structured_adjustment": None,
            "explanation": explanation.strip(),
        }

    if applies is not True:
        raise GuardrailError(
            f"directive[{index}] is {dtype}; applies must be true (only no_op may be false)"
        )

    if not isinstance(sa, dict):
        raise GuardrailError(f"directive[{index}].structured_adjustment must be an object")

    hours = sa.get("hours")
    hours = _validate_hours(hours, index)

    result: Dict[str, Any] = {
        "note_index": index,
        "applies": True,
        "directive_type": dtype,
        "structured_adjustment": {"hours": hours},
        "explanation": explanation.strip(),
    }

    if dtype == "solar_reduction":
        factor = sa.get("factor")
        if isinstance(factor, str):
            s = factor.strip()
            if s and s.replace(".", "", 1).replace("-", "", 1).isdigit():
                factor = float(s)
            else:
                raise GuardrailError(
                    f"directive[{index}].factor must be a JSON number "
                    f"(not the string {factor!r}) in [0,1]."
                )
        if not isinstance(factor, (int, float)) or isinstance(factor, bool):
            raise GuardrailError(
                f"directive[{index}].structured_adjustment.factor must be a number, "
                f"got {type(factor).__name__}"
            )
        if not (0.0 <= float(factor) <= 1.0):
            raise GuardrailError(f"directive[{index}].factor must be in [0,1], got {factor}")
        result["structured_adjustment"]["factor"] = float(factor)

    elif dtype == "minimum_battery_reserve":
        mink = sa.get("minimum_energy_kwh")
        # Coerce numeric strings ("100", "100.5") to float, but reject
        # strings with non-numeric suffixes ("50%", "90 kWh").
        if isinstance(mink, str):
            s = mink.strip()
            if s and s.replace(".", "", 1).replace("-", "", 1).isdigit():
                mink = float(s)
            else:
                raise GuardrailError(
                    f"directive[{index}].minimum_energy_kwh must be a JSON number "
                    f"(not the string {mink!r}); if the note specifies a percentage, "
                    "convert to absolute kWh against the battery's capacity_kwh."
                )
        if not isinstance(mink, (int, float)) or isinstance(mink, bool):
            raise GuardrailError(
                f"directive[{index}].minimum_energy_kwh must be a number, got {type(mink).__name__}"
            )
        if float(mink) < 0:
            raise GuardrailError(f"directive[{index}].minimum_energy_kwh must be >= 0")
        result["structured_adjustment"]["minimum_energy_kwh"] = float(mink)

    elif dtype == "max_grid_window":
        cap = sa.get("max_grid_kwh")
        if isinstance(cap, str):
            s = cap.strip()
            if s and s.replace(".", "", 1).replace("-", "", 1).isdigit():
                cap = float(s)
            else:
                raise GuardrailError(
                    f"directive[{index}].max_grid_kwh must be a JSON number "
                    f"(not the string {cap!r}); convert any 'kWh' strings to a number."
                )
        if not isinstance(cap, (int, float)) or isinstance(cap, bool):
            raise GuardrailError(
                f"directive[{index}].max_grid_kwh must be a number, got {type(cap).__name__}"
            )
        if float(cap) < 0:
            raise GuardrailError(f"directive[{index}].max_grid_kwh must be >= 0")
        result["structured_adjustment"]["max_grid_kwh"] = float(cap)

    # no_charge_window / no_discharge_window: hours only, nothing else to check
    return result


# ---------- Helpers ----------

def _validate_hours(value: Any, index: int) -> List[int]:
    if not isinstance(value, list):
        raise GuardrailError(f"directive[{index}].structured_adjustment.hours must be an array")
    out: List[int] = []
    seen = set()
    for h in value:
        if not isinstance(h, int) or isinstance(h, bool):
            raise GuardrailError(f"directive[{index}].hours entries must be integers, got {h!r}")
        if h < 0 or h > 23:
            raise GuardrailError(f"directive[{index}].hours entries must be 0..23, got {h}")
        if h in seen:
            raise GuardrailError(f"directive[{index}].hours entries must be unique, duplicate {h}")
        seen.add(h)
        out.append(h)
    if not out:
        raise GuardrailError(f"directive[{index}].hours must be non-empty")
    out.sort()
    return out