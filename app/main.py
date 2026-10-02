import numpy as np
from scipy.optimize import linprog
from typing import List
from fastapi import FastAPI, HTTPException

from app.schemas import (
    OptimizationRequest,
    TestCasePayload,
    DirectiveInterpretation,
    HourlyPlanItem,
    OptimizationResponse
)
from app.llm import parse_operator_notes


def solve_energy_optimization(
    request: OptimizationRequest,
    directives: List[DirectiveInterpretation]
) -> OptimizationResponse:
    n_hours = len(request.hours)
    battery = request.battery

    # 1. Initialize hourly modifier arrays
    solar_factors = [1.0] * n_hours
    no_charge_hours = set()
    no_discharge_hours = set()
    min_reserve_kwh = [battery.minimum_energy_kwh] * n_hours
    max_grid_kwh = [float('inf')] * n_hours

    # 2. Apply parsed directives
    for directive in directives:
        if not directive.applies or not directive.structured_adjustment:
            continue

        adj = directive.structured_adjustment
        hours = adj.hours if adj.hours is not None else list(range(n_hours))

        if directive.directive_type == "solar_reduction" and adj.factor is not None:
            for h in hours:
                if 0 <= h < n_hours:
                    solar_factors[h] = min(solar_factors[h], adj.factor)

        elif directive.directive_type == "no_charge_window":
            for h in hours:
                if 0 <= h < n_hours:
                    no_charge_hours.add(h)

        elif directive.directive_type == "no_discharge_window":
            for h in hours:
                if 0 <= h < n_hours:
                    no_discharge_hours.add(h)

        elif directive.directive_type == "minimum_battery_reserve" and adj.minimum_energy_kwh is not None:
            req_reserve = adj.minimum_energy_kwh

            # If LLM returned a ratio (e.g., 0.5 for 50%), convert to absolute kWh
            if 0.0 < req_reserve <= 1.0:
                req_reserve = req_reserve * battery.capacity_kwh
            # If LLM returned a percentage number > 1 (e.g., 50 for 50%), convert to absolute kWh
            elif 1.0 < req_reserve <= 100.0 and req_reserve > battery.capacity_kwh:
                req_reserve = (req_reserve / 100.0) * battery.capacity_kwh

            for h in hours:
                if 0 <= h < n_hours:
                    min_reserve_kwh[h] = max(min_reserve_kwh[h], req_reserve)

        elif directive.directive_type == "max_grid_window" and adj.max_grid_kwh is not None:
            for h in hours:
                if 0 <= h < n_hours:
                    max_grid_kwh[h] = min(max_grid_kwh[h], adj.max_grid_kwh)

    # 3. Setup Decision Variables
    c_obj = np.zeros(5 * n_hours)
    bounds = []

    for h in range(n_hours):
        hour_data = request.hours[h]

        c_obj[5 * h + 0] = hour_data.tariff_bdt_per_kwh

        g_max = max_grid_kwh[h] if max_grid_kwh[h] != float('inf') else None
        s_max = max(0.0, hour_data.solar_kwh * solar_factors[h])
        c_max = 0.0 if h in no_charge_hours else battery.max_charge_kwh_per_hour
        d_max = 0.0 if h in no_discharge_hours else battery.max_discharge_kwh_per_hour
        e_min = min_reserve_kwh[h]
        e_max = battery.capacity_kwh

        bounds.extend([
            (0, g_max),     # g_h
            (0, s_max),     # s_h
            (0, c_max),     # c_h
            (0, d_max),     # d_h
            (e_min, e_max)  # e_h
        ])

    # 4. Equality Constraints
    num_eq = 2 * n_hours + 1
    A_eq = np.zeros((num_eq, 5 * n_hours))
    b_eq = np.zeros(num_eq)

    eff = battery.efficiency if battery.efficiency and battery.efficiency > 0.01 else 1.0

    for h in range(n_hours):
        demand = request.hours[h].demand_kwh

        # Energy Balance equation
        eq_idx1 = 2 * h
        A_eq[eq_idx1, 5 * h + 0] = 1.0   # +g_h
        A_eq[eq_idx1, 5 * h + 1] = 1.0   # +s_h
        A_eq[eq_idx1, 5 * h + 2] = -1.0  # -c_h
        A_eq[eq_idx1, 5 * h + 3] = 1.0   # +d_h
        b_eq[eq_idx1] = demand

        # Battery SoC Dynamics equation
        eq_idx2 = 2 * h + 1
        A_eq[eq_idx2, 5 * h + 4] = 1.0          # +e_h
        A_eq[eq_idx2, 5 * h + 2] = -eff         # -eff * c_h
        A_eq[eq_idx2, 5 * h + 3] = 1.0 / eff     # +(1/eff) * d_h

        if h == 0:
            b_eq[eq_idx2] = battery.initial_energy_kwh
        else:
            A_eq[eq_idx2, 5 * (h - 1) + 4] = -1.0  # -e_{h-1}
            b_eq[eq_idx2] = 0.0

    # End-of-day Neutrality
    eq_idx_end = 2 * n_hours
    A_eq[eq_idx_end, 5 * (n_hours - 1) + 4] = 1.0
    b_eq[eq_idx_end] = battery.initial_energy_kwh

    # 5. Solve via SciPy HiGHS
    res = linprog(c=c_obj, A_eq=A_eq, b_eq=b_eq, bounds=bounds, method='highs')

    if not res.success:
        raise ValueError(f"Linear programming optimization failed: {res.message}")

    # 6. Build response plan with required judging structure
    hourly_plan = []
    total_cost = 0.0
    total_grid = 0.0
    peak_grid = 0.0

    for h in range(n_hours):
        g = max(0.0, float(res.x[5 * h + 0]))
        s = max(0.0, float(res.x[5 * h + 1]))
        c = max(0.0, float(res.x[5 * h + 2]))
        d = max(0.0, float(res.x[5 * h + 3]))
        e = max(0.0, float(res.x[5 * h + 4]))

        cost = g * request.hours[h].tariff_bdt_per_kwh
        total_cost += cost
        total_grid += g
        if g > peak_grid:
            peak_grid = g

        # Map charge/discharge into battery_action
        if c > 1e-3:
            action = "charge"
            bat_kwh = round(c, 4)
        elif d > 1e-3:
            action = "discharge"
            bat_kwh = round(d, 4)
        else:
            action = "idle"
            bat_kwh = 0.0

        hourly_plan.append(
            HourlyPlanItem(
                hour=h,
                grid_kwh=round(g, 4),
                solar_used_kwh=round(s, 4),
                battery_action=action,
                battery_kwh=bat_kwh,
                battery_energy_after_kwh=round(e, 4)
            )
        )

    # Ensure note_index is assigned for each directive
    for idx, d in enumerate(directives):
        d.note_index = idx

    return OptimizationResponse(
        scenario_id=request.scenario_id,
        directive_interpretation=directives,
        hourly_plan=hourly_plan,
        total_grid_kwh=round(total_grid, 4),
        total_cost_bdt=round(total_cost, 4),
        peak_grid_kwh=round(peak_grid, 4),
        plan_summary="Energy schedule optimized according to operator directives and grid tariff."
    )


app = FastAPI(
    title="GridWise LLM Energy Optimization API",
    version="1.0.0"
)

@app.get("/")
def read_root():
    return {"message": "GridWise Energy Optimization API is running."}

@app.get("/health")
def health_check():
    return {"status": "ok"}

@app.post("/optimize-energy", response_model=OptimizationResponse)
def optimize_energy(payload: TestCasePayload):
    try:
        request = payload.input
        directives = parse_operator_notes(request.operator_notes)
        response = solve_energy_optimization(request, directives)
        return response
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))