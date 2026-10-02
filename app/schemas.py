from typing import List, Optional, Dict, Any
from pydantic import BaseModel

# --- Input Schemas ---

class HourlyData(BaseModel):
    hour: int
    demand_kwh: float
    solar_kwh: float
    tariff_bdt_per_kwh: float

class BatteryConfig(BaseModel):
    capacity_kwh: float
    initial_energy_kwh: float
    minimum_energy_kwh: float = 0.0
    max_charge_kwh_per_hour: float
    max_discharge_kwh_per_hour: float
    efficiency: float = 1.0

class OptimizationRequest(BaseModel):
    scenario_id: str
    operator_notes: List[str] = []
    hours: List[HourlyData]
    battery: BatteryConfig

class TestCasePayload(BaseModel):
    id: Optional[str] = None
    label: Optional[str] = None
    input: OptimizationRequest
    expected_output: Optional[Dict[str, Any]] = None

# --- LLM & Directive Schemas ---

class StructuredAdjustment(BaseModel):
    hours: Optional[List[int]] = None
    factor: Optional[float] = None
    minimum_energy_kwh: Optional[float] = None
    max_grid_kwh: Optional[float] = None

class DirectiveInterpretation(BaseModel):
    note_index: int = 0
    note: Optional[str] = None
    applies: bool
    directive_type: str
    structured_adjustment: Optional[StructuredAdjustment] = None
    explanation: Optional[str] = ""

# --- Response Schemas Expected by Judge ---

class HourlyPlanItem(BaseModel):
    hour: int
    grid_kwh: float
    solar_used_kwh: float
    battery_action: str  # "idle", "charge", or "discharge"
    battery_kwh: float
    battery_energy_after_kwh: float

class OptimizationResponse(BaseModel):
    scenario_id: str
    directive_interpretation: List[DirectiveInterpretation]
    hourly_plan: List[HourlyPlanItem]
    total_grid_kwh: float
    total_cost_bdt: float
    peak_grid_kwh: float
    plan_summary: Optional[str] = ""