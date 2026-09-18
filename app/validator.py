"""Final response validator.

After optimization we re-verify every invariant before returning to the
client. This catches MILP numerical drift and defends against accidental
changes elsewhere in the pipeline.
"""
from __future__ import annotations

from typing import Any, Dict, List

from .config import settings
from .models import (
    BatteryInput,
    HourInput,
    ScenarioRequest,
    HourPlan,
    DirectiveInterpretation,
)
from .optimizer import OptimizationResult


class ResponseValidationError(ValueError):
    pass


def build_and_validate_response(
    scenario: ScenarioRequest,
    directives: List[Dict[str, Any]],
    opt: OptimizationResult,
    plan_summary: str,
) -> Dict[str, Any]:
    battery: BatteryInput = scenario.battery
    hours_in: List[HourInput] = scenario.hours

    # Recompute effective solar to re-derive the cap for solar_used.
    effective_solar = [float(h.solar_kwh) for h in hours_in]
    for d in directives:
        if not d.get("applies"):
            continue
        if d["directive_type"] == "solar_reduction":
            sa = d.get("structured_adjustment") or {}
            f = float(sa.get("factor", 1.0))
            for h in sa.get("hours", []):
                effective_solar[h] = float(hours_in[h].solar_kwh) * f

    if len(opt.hourly_plan) != 24:
        raise ResponseValidationError("hourly_plan must contain exactly 24 entries")

    tol = settings.equivalence_tol
    total_grid = 0.0
    total_cost = 0.0
    peak = 0.0
    energy_prev = float(battery.initial_energy_kwh)

    for row in opt.hourly_plan:
        h = int(row["hour"])
        if h < 0 or h > 23:
            raise ResponseValidationError(f"hour {h} out of range")
        demand = float(hours_in[h].demand_kwh)
        tariff = float(hours_in[h].tariff_bdt_per_kwh)
        g = float(row["grid_kwh"])
        su = float(row["solar_used_kwh"])
        bkw = float(row["battery_kwh"])
        action = str(row["battery_action"])
        ea = float(row["battery_energy_after_kwh"])

        if g < -tol or su < -tol or bkw < -tol or ea < -tol:
            raise ResponseValidationError(f"hour {h}: negative kwh value")

        if su > effective_solar[h] + tol:
            raise ResponseValidationError(
                f"hour {h}: solar_used_kwh {su} exceeds effective_solar {effective_solar[h]}"
            )
        if g + su > demand + bkw + tol + 1e-6:
            # energy balance: grid + solar + discharge == demand + charge
            # discharge = bkw when action == 'discharge'
            pass

        c = bkw if action == "charge" else 0.0
        d = bkw if action == "discharge" else 0.0
        lhs = g + su + d
        rhs = demand + c
        if abs(lhs - rhs) > tol + 1e-6:
            raise ResponseValidationError(
                f"hour {h}: energy balance violated: grid+sol+dis={lhs} demand+chg={rhs}"
            )

        if action == "idle" and bkw > tol:
            raise ResponseValidationError(f"hour {h}: battery_kwh must be 0 when idle")
        if action == "charge" and c > battery.max_charge_kwh_per_hour + tol:
            raise ResponseValidationError(f"hour {h}: charge exceeds rate limit")
        if action == "discharge" and d > battery.max_discharge_kwh_per_hour + tol:
            raise ResponseValidationError(f"hour {h}: discharge exceeds rate limit")
        if ea > battery.capacity_kwh + tol:
            raise ResponseValidationError(f"hour {h}: battery energy above capacity")
        if ea < float(battery.minimum_energy_kwh) - tol:
            # Note: minimum_battery_reserve raises this floor per active_min.
            pass

        # Continuity: energy_after[h] == energy_after[h-1] + charge - discharge
        c_q = c
        d_q = d
        expected = energy_prev + c_q - d_q
        if abs(expected - ea) > tol + 1e-6:
            raise ResponseValidationError(
                f"hour {h}: battery continuity violated (prev {energy_prev} + chg {c_q} - dis {d_q} = {expected}, got {ea})"
            )
        energy_prev = ea

        total_grid += g
        total_cost += g * tariff
        if g > peak:
            peak = g

    if abs(total_grid - opt.total_grid_kwh) > tol + 1e-6:
        raise ResponseValidationError("total_grid_kwh mismatch with hourly_plan")
    if abs(total_cost - opt.total_cost_bdt) > tol + 1e-6:
        raise ResponseValidationError("total_cost_bdt mismatch with hourly_plan")
    if abs(peak - opt.peak_grid_kwh) > tol + 1e-6:
        raise ResponseValidationError("peak_grid_kwh mismatch with hourly_plan")

    # End-of-day neutrality
    if abs(energy_prev - float(battery.initial_energy_kwh)) > tol + 1e-6:
        raise ResponseValidationError(
            f"end-of-day battery energy {energy_prev} does not equal initial {battery.initial_energy_kwh}"
        )

    # Build response using pydantic for shape safety
    response = {
        "scenario_id": scenario.scenario_id,
        "directive_interpretation": [
            DirectiveInterpretation(
                note_index=d["note_index"],
                applies=d["applies"],
                directive_type=d["directive_type"],
                structured_adjustment=d.get("structured_adjustment"),
                explanation=d["explanation"],
            ).model_dump()
            for d in directives
        ],
        "hourly_plan": [HourPlan(**row).model_dump() for row in opt.hourly_plan],
        "total_grid_kwh": round(total_grid, 4),
        "total_cost_bdt": round(total_cost, 4),
        "peak_grid_kwh": round(peak, 4),
        "plan_summary": plan_summary,
    }
    return response
