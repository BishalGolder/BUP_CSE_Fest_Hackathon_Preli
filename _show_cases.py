import json, sys
with open('BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json') as f:
    data = json.load(f)
target = sys.argv[1] if len(sys.argv) > 1 else None
for c in data['cases']:
    if target and c['id'] != target:
        continue
    print(f"=== {c['id']} -- {c['label']} ===")
    print('NOTES:')
    for n in c['input']['operator_notes']:
        print(f'  - {n}')
    exp = c['expected_output']
    print('EXPECTED DIRECTIVES:')
    for d in exp['directive_interpretation']:
        print(f'  - {d}')
    print('HOURLY PLAN:')
    for row in exp['hourly_plan']:
        print(f'  h={row["hour"]:2d} grid={row["grid_kwh"]:7.2f} solar={row["solar_used_kwh"]:6.2f} act={row["battery_action"]:9s} bkw={row["battery_kwh"]:6.2f} after={row["battery_energy_after_kwh"]:7.2f}')
    print(f'TOTALS: grid={exp["total_grid_kwh"]:.4f} cost={exp["total_cost_bdt"]:.4f} peak={exp["peak_grid_kwh"]:.4f}')
    print(f'SUMMARY: {exp["plan_summary"]}')
    print()
