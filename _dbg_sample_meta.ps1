$py = "C:\Program Files\Python313\python.exe"
$code = @"
import json
d = json.load(open(r'F:\BUP_CSE_Fest_Hackathon_Preli\BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json', encoding='utf-8'))
for cid in ('SAMPLE-03','SAMPLE-07','SAMPLE-10'):
    c = [x for x in d['cases'] if x['id'] == cid][0]
    print('===', cid, '===')
    print('notes:', c['input']['operator_notes'])
    print('battery.capacity_kwh:', c['input']['battery'].get('capacity_kwh'))
    print('expected references:', {k: c.get(k) for k in c if k != 'input'})
"@
& $py -c $code
