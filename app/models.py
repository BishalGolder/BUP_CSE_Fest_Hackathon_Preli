"""Pydantic models for the GridWise API contract.

These models enforce the request and response schemas. The validator in
``app.validator`` performs the *semantic* checks that Pydantic cannot
express (e.g. energy balance). Pydantic here guarantees shape and basic
type correctness so we can return HTTP 400 on malformed input.
"""
from __future__ import annotations

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class DirectiveType(str, Enum):
    SOLAR_REDUCTION = "solar_reduction"
    MINIMUM_BATTERY_RESERVE = "minimum_battery_reserve"
    NO_CHARGE_WINDOW = "no_charge_window"
    NO_DISCHARGE_WINDOW = "no_discharge_window"
    MAX_GRID_WINDOW = "max_grid_window"
    NO_OP = "no_op"


class BatteryAction(str, Enum):
    CHARGE = "charge"
    DISCHARGE = "discharge"
    IDLE = "idle"


# ---------- Request models ----------

class HourInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hour: int = Field(..., ge=0, le=23)
    demand_kwh: float = Field(..., ge=0)
    solar_kwh: float = Field(..., ge=0)
    tariff_bdt_per_kwh: float = Field(..., ge=0)


class BatteryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    capacity_kwh: float = Field(..., gt=0)
    initial_energy_kwh: float = Field(..., ge=0)
    minimum_energy_kwh: float = Field(..., ge=0)
    max_charge_kwh_per_hour: float = Field(..., ge=0)
    max_discharge_kwh_per_hour: float = Field(..., ge=0)

    @field_validator("initial_energy_kwh")
    @classmethod
    def _initial_within_bounds(cls, v, info):
        cap = info.data.get("capacity_kwh")
        mn = info.data.get("minimum_energy_kwh")
        if cap is not None and mn is not None:
            if v < mn or v > cap:
                raise ValueError(
                    f"initial_energy_kwh ({v}) must be within [minimum_energy_kwh, capacity_kwh] = [{mn}, {cap}]"
                )
        return v


class ScenarioRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scenario_id: str = Field(..., min_length=1)
    operator_notes: List[str] = Field(..., min_length=1, max_length=10)
    hours: List[HourInput] = Field(..., min_length=24, max_length=24)
    battery: BatteryInput

    @field_validator("operator_notes")
    @classmethod
    def _notes_nonempty(cls, v):
        for i, note in enumerate(v):
            if not isinstance(note, str) or not note.strip():
                raise ValueError(f"operator_notes[{i}] must be a non-empty string")
        return v

    @field_validator("hours")
    @classmethod
    def _unique_hours_sorted(cls, v):
        hs = [h.hour for h in v]
        if hs != sorted(set(hs)):
            raise ValueError("hours must be 24 unique entries for hour 0..23 in ascending order")
        if hs != list(range(24)):
            raise ValueError("hours must contain exactly entries for 0..23")
        return v


# ---------- Response models ----------

class DirectiveInterpretation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note_index: int = Field(..., ge=0)
    applies: bool
    directive_type: DirectiveType
    structured_adjustment: Optional[dict] = None
    explanation: str = Field(..., min_length=1)


class HourPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hour: int = Field(..., ge=0, le=23)
    grid_kwh: float = Field(..., ge=0)
    solar_used_kwh: float = Field(..., ge=0)
    battery_action: BatteryAction
    battery_kwh: float = Field(..., ge=0)
    battery_energy_after_kwh: float = Field(..., ge=0)


class ScenarioResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scenario_id: str
    directive_interpretation: List[DirectiveInterpretation]
    hourly_plan: List[HourPlan] = Field(..., min_length=24, max_length=24)
    total_grid_kwh: float = Field(..., ge=0)
    total_cost_bdt: float = Field(..., ge=0)
    peak_grid_kwh: float = Field(..., ge=0)
    plan_summary: str = Field(..., min_length=1)
