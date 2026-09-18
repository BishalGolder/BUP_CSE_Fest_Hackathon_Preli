import json, sys
with open('BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json') as f:
    data = json.load(f)
target = sys.argv[1] if len(sys.argv) > 1 else None
for c in data['cases']:
    if target and c['id'] != target:
        continue
    print(f"=== {c['id']} ===")
    exp = c['expected_output']
    print('NOTES:')
    for n in c['input']['operator_notes']:
        print(f'  - {n}')
    print('EXPECTED DIRECTIVES:')
    for d in exp['directive_interpretation']:
        print(f'  - {d}')
    print(f'TOTALS: grid={exp["total_grid_kwh"]:.4f} cost={exp["total_cost_bdt"]:.4f} peak={exp["peak_grid_kwh"]:.4f}')
    print()
