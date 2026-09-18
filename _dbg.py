import json, sys
sys.path.insert(0, r"c:\Users\USER\Desktop\BUP Preli 404 Brain Not Found")
from app.config import settings
from app.guardrails import validate_directives
from app.llm_interpreter import interpret_notes
from app.optimizer import optimize
from app.models import ScenarioRequest

with open(r"c:\Users\USER\Desktop\BUP Preli 404 Brain Not Found\BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json") as f:
    data = json.load(f)
case = data["cases"][0]
scenario = ScenarioRequest.model_validate(case["input"])
directives = validate_directives(interpret_notes(case["input"]["operator_notes"]), case["input"]["operator_notes"])
opt = optimize(scenario, directives)
print("ours cost:", opt.total_cost_bdt, "grid:", opt.total_grid_kwh, "peak:", opt.peak_grid_kwh)
print("expected cost:", case["expected_output"]["total_cost_bdt"], "grid:", case["expected_output"]["total_grid_kwh"], "peak:", case["expected_output"]["peak_grid_kwh"])
print("gap:", opt.total_cost_bdt - case["expected_output"]["total_cost_bdt"])
print("status:", opt.status)