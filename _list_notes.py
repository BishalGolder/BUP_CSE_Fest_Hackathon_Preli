import json
with open('BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json') as f:
    data = json.load(f)
for c in data['cases']:
    print(f'== {c["id"]} ==')
    for n in c['input']['operator_notes']:
        print(f'  NOTE: {n}')
