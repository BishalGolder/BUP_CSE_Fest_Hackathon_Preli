import json, sys
with open('BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json') as f:
    data = json.load(f)
target = sys.argv[1] if len(sys.argv) > 1 else None
for c in data['cases']:
    if target and c['id'] != target:
        continue
    print(f"=== {c['id']} ===")
    inp = c['input']
    print(f"battery: {inp['battery']}")
    print("hours:")
    for h in inp['hours']:
        print(f'  h={h["hour"]:2d} demand={h["demand_kwh"]:6.2f} solar={h["solar_kwh"]:6.2f} tariff={h["tariff_bdt_per_kwh"]:6.2f}')
    print()
