from fastapi.testclient import TestClient
from app.main import app
import json

with open('BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json') as f:
    data = json.load(f)
client = TestClient(app)
ok = 0
fail = 0
for c in data['cases']:
    r = client.post('/optimize-energy', json=c['input'])
    if r.status_code != 200:
        print(f"{c['id']} HTTP {r.status_code}: {r.text[:150]}")
        fail += 1
        continue
    body = r.json()
    cost = body['total_cost_bdt']
    exp = c['expected_output']['total_cost_bdt']
    delta = abs(cost - exp)
    grid = body['total_grid_kwh']
    grid_exp = c['expected_output']['total_grid_kwh']
    grid_delta = abs(grid - grid_exp)
    peak = body['peak_grid_kwh']
    peak_exp = c['expected_output']['peak_grid_kwh']
    peak_delta = abs(peak - peak_exp)
    print(f"{c['id']:11s} cost={cost:9.2f}/{exp:9.2f} d={delta:6.2f} grid={grid:8.2f}/{grid_exp:8.2f} d={grid_delta:6.2f} peak={peak:6.2f}/{peak_exp:6.2f} d={peak_delta:5.2f}")
    if delta < 1.0 and grid_delta < 5.0 and peak_delta < 5.0:
        ok += 1
    else:
        fail += 1
print(f"Total: {ok} pass, {fail} fail")
