"""MILP optimizer using PuLP.

After applying all *valid* directives, we minimize total grid cost
subject to:

  * exactly 24 hourly intervals h = 0..23
  * energy balance per hour:
        grid[h] + solar_used[h] + battery_discharge[h]
            == demand[h] + battery_charge[h]
  * solar_used[h] <= effective_solar[h]
        (effective_solar[h] = original_solar[h] * factor for solar_reduction
         hours, else original_solar[h])
  * battery energy in [active_min[h], capacity]
        (active_min[h] = max(battery.minimum_energy_kwh, directive_min) when
         a minimum_battery_reserve directive applies in hour h, else the base
         minimum)
  * battery charge / discharge per hour <= rate limits
  * battery_kwh == 0 when idle (enforced via binary selection)
  * battery_energy_after[23] == initial_energy_kwh  (end-of-day neutrality)
  * no_charge_window[h] forces charge[h] == 0
  * no_discharge_window[h] forces discharge[h] == 0
  * max_grid_window[h] forces grid[h] <= cap[h]
  * charge and discharge cannot happen simultaneously in the same hour
        (big-M via binary selection variables)
  * minimum_battery_reserve enforces a *lower* bound on energy_after; it does
    NOT force idle hours. The reserve is implemented via the active_min[h]
    floor.

Objective:
    minimize sum_h grid[h] * tariff[h]
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, List, Tuple

import pulp

from .config import settings
from .models import BatteryInput, HourInput, ScenarioRequest
from .guardrails import GuardrailError

log = logging.getLogger("gridwise.optimizer")


class OptimizerError(RuntimeError):
    pass


@dataclass
class OptimizationResult:
    hourly_plan: List[Dict[str, float]]
    total_grid_kwh: float
    total_cost_bdt: float
    peak_grid_kwh: float
    status: str


def optimize(
    scenario: ScenarioRequest,
    directives: List[Dict],
) -> OptimizationResult:
    hours_in: List[HourInput] = scenario.hours
    battery: BatteryInput = scenario.battery
    H = list(range(24))

    # ---- effective solar and per-hour constraints ----
    effective_solar = [float(h.solar_kwh) for h in hours_in]
    no_charge: set = set()
    no_discharge: set = set()
    max_grid_cap: Dict[int, float] = {}
    active_min: List[float] = [float(battery.minimum_energy_kwh) for _ in H]

    for d in directives:
        if not d.get("applies"):
            continue
        dtype = d["directive_type"]
        sa = d.get("structured_adjustment") or {}
        hs = sa.get("hours", [])
        if dtype == "solar_reduction":
            factor = float(sa.get("factor", 1.0))
            for h in hs:
                effective_solar[h] = float(hours_in[h].solar_kwh) * factor
        elif dtype == "no_charge_window":
            for h in hs:
                no_charge.add(h)
        elif dtype == "no_discharge_window":
            for h in hs:
                no_discharge.add(h)
        elif dtype == "max_grid_window":
            cap = float(sa.get("max_grid_kwh"))
            for h in hs:
                max_grid_cap[h] = min(max_grid_cap.get(h, float("inf")), cap)
        elif dtype == "minimum_battery_reserve":
            mn = float(sa.get("minimum_energy_kwh"))
            for h in hs:
                active_min[h] = max(active_min[h], mn)

    # If the active minimum ever exceeds capacity, the scenario is infeasible.
    for h in H:
        if active_min[h] > battery.capacity_kwh:
            raise OptimizerError(
                f"Active minimum battery reserve {active_min[h]} exceeds capacity "
                f"{battery.capacity_kwh} at hour {h}"
            )
        if battery.initial_energy_kwh < active_min[0] or battery.initial_energy_kwh > battery.capacity_kwh:
            raise OptimizerError(
                f"initial_energy_kwh {battery.initial_energy_kwh} violates active bounds "
                f"[{active_min[0]}, {battery.capacity_kwh}]"
            )

    tariff = [float(h.tariff_bdt_per_kwh) for h in hours_in]
    demand = [float(h.demand_kwh) for h in hours_in]

    prob = pulp.LpProblem("gridwise", pulp.LpMinimize)
    grid = [pulp.LpVariable(f"grid_{h}", lowBound=0) for h in H]
    solar_used = [pulp.LpVariable(f"solar_{h}", lowBound=0, upBound=effective_solar[h]) for h in H]
    charge = [pulp.LpVariable(f"charge_{h}", lowBound=0) for h in H]
    discharge = [pulp.LpVariable(f"discharge_{h}", lowBound=0) for h in H]
    batt = [pulp.LpVariable(f"batt_{h}", lowBound=0) for h in H]
    energy_after = [pulp.LpVariable(f"energy_after_{h}", lowBound=0) for h in H]
    is_charge = [pulp.LpVariable(f"is_charge_{h}", cat="Binary") for h in H]
    is_discharge = [pulp.LpVariable(f"is_discharge_{h}", cat="Binary") for h in H]
    peak = pulp.LpVariable("peak_grid_kwh", lowBound=0)

    cap = float(battery.capacity_kwh)
    mn = float(battery.minimum_energy_kwh)
    rate_c = float(battery.max_charge_kwh_per_hour)
    rate_d = float(battery.max_discharge_kwh_per_hour)
    init = float(battery.initial_energy_kwh)
    BIG_M = max(cap, max(demand), max(effective_solar), 1.0) * 2 + 1.0

    # Objective: minimize total cost (grid * tariff). A tiny secondary weight
    # on peak grid prefers lower-peak schedules among equally-cost solutions.
    # The peak weight is below the smallest BDT-per-kWh gap so it never
    # changes the primary cost minimum.
    peak_weight = min(tariff) * 1e-4
    prob += (
        pulp.lpSum(grid[h] * tariff[h] for h in H)
        + peak_weight * peak
    ), "objective"

    for h in H:
        # peak bound: every grid[h] <= peak
        prob += grid[h] <= peak, f"peak_{h}"
        # energy balance
        prob += (
            grid[h] + solar_used[h] + discharge[h]
            == demand[h] + charge[h]
        ), f"balance_{h}"
        # solar_used upper bound
        prob += solar_used[h] <= effective_solar[h], f"solar_cap_{h}"
        # battery rate limits and binary selection
        prob += charge[h] <= rate_c * is_charge[h], f"rate_c_{h}"
        prob += discharge[h] <= rate_d * is_discharge[h], f"rate_d_{h}"
        prob += is_charge[h] + is_discharge[h] <= 1, f"exclusive_{h}"
        # battery_kwh equals the active action quantity
        # (battery_kwh must be 0 when idle; charge/discharge already 0 when not selected)
        prob += batt[h] == charge[h] + discharge[h], f"battkw_{h}"
        # battery energy after
        if h == 0:
            prob += energy_after[h] == init + charge[h] - discharge[h], f"energy0_{h}"
        else:
            prob += (
                energy_after[h] == energy_after[h - 1] + charge[h] - discharge[h]
            ), f"energy_{h}"
        # active minimum reserve and capacity
        prob += energy_after[h] >= active_min[h], f"minres_{h}"
        prob += energy_after[h] <= cap, f"cap_{h}"
        # window constraints
        if h in no_charge:
            prob += charge[h] == 0, f"nocharge_{h}"
        if h in no_discharge:
            prob += discharge[h] == 0, f"nodischarge_{h}"
        if h in max_grid_cap:
            prob += grid[h] <= max_grid_cap[h], f"gridcap_{h}"

    # end-of-day neutrality: energy_after[23] == initial_energy_kwh
    prob += energy_after[23] == init, "eod_neutrality"

    solver = pulp.PULP_CBC_CMD(
        msg=False,
        timeLimit=settings.solver_time_limit_s,
        gapRel=settings.solver_mip_gap,
        threads=settings.solver_threads,
    )
    status = prob.solve(solver)
    status_name = pulp.LpStatus[status]
    if status_name not in ("Optimal",):
        raise OptimizerError(f"Solver status: {status_name}")

    plan: List[Dict[str, float]] = []
    total_grid = 0.0
    total_cost = 0.0
    peak_val = 0.0
    for h in H:
        g = float(pulp.value(grid[h]) or 0.0)
        su = float(pulp.value(solar_used[h]) or 0.0)
        c = float(pulp.value(charge[h]) or 0.0)
        d = float(pulp.value(discharge[h]) or 0.0)
        bkw = c + d
        if c > 1e-6 and d <= 1e-6:
            action = "charge"
        elif d > 1e-6 and c <= 1e-6:
            action = "discharge"
        else:
            action = "idle"
            c = 0.0
            d = 0.0
            bkw = 0.0
        ea = float(pulp.value(energy_after[h]) or 0.0)
        plan.append({
            "hour": h,
            "grid_kwh": round(g, 6),
            "solar_used_kwh": round(su, 6),
            "battery_action": action,
            "battery_kwh": round(bkw, 6),
            "battery_energy_after_kwh": round(ea, 6),
        })
        total_grid += g
        total_cost += g * tariff[h]
        if g > peak_val:
            peak_val = g

    return OptimizationResult(
        hourly_plan=plan,
        total_grid_kwh=round(total_grid, 6),
        total_cost_bdt=round(total_cost, 6),
        peak_grid_kwh=round(peak_val, 6),
        status=status_name,
    )
