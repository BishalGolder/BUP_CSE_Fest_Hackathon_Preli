$env:GRIDWISE_LLM_PROVIDER = "stub"
$env:GRIDWISE_ALLOW_OFFLINE = "1"
$py = "C:\Program Files\Python313\python.exe"
$root = "F:\BUP_CSE_Fest_Hackathon_Preli"
$code = @"
import json,sys
sys.path.insert(0, r'F:\BUP_CSE_Fest_Hackathon_Preli')
from app.llm_interpreter import interpret_notes
d = json.load(open(r'F:\BUP_CSE_Fest_Hackathon_Preli\BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json', encoding='utf-8'))
c = [x for x in d['cases'] if x['id'] == 'SAMPLE-03'][0]
print('--- SAMPLE-03 notes ---')
print(c['input']['operator_notes'])
print('--- SAMPLE-03 battery ---')
print(c['input']['battery'])
print('--- SAMPLE-03 stub directives ---')
print(json.dumps(interpret_notes(c['input']['operator_notes'], battery=c['input']['battery']), indent=2))
"@
& $py -c $code
