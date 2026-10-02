import numpy as np
from scipy.optimize import linprog
from typing import List
from app.schemas import (
    OptimizationRequest,
    DirectiveInterpretation,
    HourlyPlanItem,
    OptimizationResponse
)

def solve_energy_optimization(
    request: OptimizationRequest,
    directives: List[DirectiveInterpretation]
) -> OptimizationResponse:
    n_hours = len(request.hours)
    battery = request.battery

    # 1. Initialize hourly modifier arrays based on default bounds
    solar_factors = [1.0] * n_hours
    no_charge_hours = set()
    no_discharge_hours = set()
    min_reserve_kwh = [0.0] * n_hours
    max_grid_kwh = [float('inf')] * n_hours

    # 2. Apply parsed directives to modifier arrays
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
            for h in hours:
                if 0 <= h < n_hours:
                    min_reserve_kwh[h] = max(min_reserve_kwh[h], adj.minimum_energy_kwh)

        elif directive.directive_type == "max_grid_window" and adj.max_grid_kwh is not None:
            for h in hours:
                if 0 <= h < n_hours:
                    max_grid_kwh[h] = min(max_grid_kwh[h], adj.max_grid_kwh)

    # 3. Setup Decision Variables for 24 hours (5 variables per hour = 120 total)
    # Indices per hour h:
    # 5*h + 0 -> grid_kwh (g)
    # 5*h + 1 -> solar_used_kwh (s)
    # 5*h + 2 -> battery_charge_kwh (c)
    # 5*h + 3 -> battery_discharge_kwh (d)
    # 5*h + 4 -> battery_energy_kwh (e)

    c_obj = np.zeros(5 * n_hours)
    bounds = []

    for h in range(n_hours):
        hour_data = request.hours[h]

        # Objective coefficient: Grid cost in BDT/kWh
        c_obj[5 * h + 0] = hour_data.grid_tariff_bdt_per_kwh

        # Decision variable bounds
        g_max = max_grid_kwh[h] if max_grid_kwh[h] != float('inf') else None
        s_max = max(0.0, hour_data.solar_kwh * solar_factors[h])
        c_max = 0.0 if h in no_charge_hours else battery.max_charge_kw
        d_max = 0.0 if h in no_discharge_hours else battery.max_discharge_kw
        e_min = min_reserve_kwh[h]
        e_max = battery.capacity_kwh

        bounds.extend([
            (0, g_max),    # g_h
            (0, s_max),    # s_h
            (0, c_max),    # c_h
            (0, d_max),    # d_h
            (e_min, e_max)  # e_h
        ])

    # 4. Equality Constraints: A_eq * x = b_eq
    # - Energy Balance per hour: g_h + s_h + d_h - c_h = demand_h
    # - Battery Energy Balance per hour: e_h - e_{h-1} - eff*c_h + (1/eff)*d_h = 0
    # - End-of-day Neutrality: e_{23} = initial_energy

    num_eq = 2 * n_hours + 1
    A_eq = np.zeros((num_eq, 5 * n_hours))
    b_eq = np.zeros(num_eq)

    eff = battery.efficiency if battery.efficiency > 0 else 1.0

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

    # End-of-day Neutrality: e_23 = initial_energy
    eq_idx_end = 2 * n_hours
    A_eq[eq_idx_end, 5 * (n_hours - 1) + 4] = 1.0
    b_eq[eq_idx_end] = battery.initial_energy_kwh

    # 5. Solve via SciPy HiGHS
    res = linprog(c=c_obj, A_eq=A_eq, b_eq=b_eq, bounds=bounds, method='highs')

    if not res.success:
        raise ValueError(f"Linear programming optimization failed: {res.message}")

    # 6. Extract solution vectors into response plan
    hourly_plan = []
    total_cost = 0.0

    for h in range(n_hours):
        g = float(res.x[5 * h + 0])
        s = float(res.x[5 * h + 1])
        c = float(res.x[5 * h + 2])
        d = float(res.x[5 * h + 3])
        e = float(res.x[5 * h + 4])
        cost = g * request.hours[h].grid_tariff_bdt_per_kwh

        total_cost += cost
        hourly_plan.append(
            HourlyPlanItem(
                hour=h,
                grid_kwh=round(g, 4),
                solar_used_kwh=round(s, 4),
                battery_charge_kwh=round(c, 4),
                battery_discharge_kwh=round(d, 4),
                battery_energy_kwh=round(e, 4),
                hourly_cost_bdt=round(cost, 4)
            )
        )

    return OptimizationResponse(
        scenario_id=request.scenario_id,
        directive_interpretation=directives,
        hourly_plan=hourly_plan,
        total_cost_bdt=round(total_cost, 4)
    )