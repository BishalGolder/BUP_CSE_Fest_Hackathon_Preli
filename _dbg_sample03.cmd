@echo off
setlocal
set GRIDWISE_LLM_PROVIDER=groq
set GRIDWISE_LLM_MODEL=qwen/qwen3.8-27b
set GROQ_API_KEY=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
set GRIDWISE_ALLOW_OFFLINE=0
"C:\Program Files\Python313\python.exe" -c "import os, json, sys; sys.path.insert(0, r'F:\BUP_CSE_Fest_Hackathon_Preli'); base = r'F:\BUP_CSE_Fest_Hackathon_Preli'; files = [f for f in os.listdir(base) if f.lower() == 'bup_cse_fest_2026_preli_public_sample_cases.json']; assert files, 'cases file not found'; path = os.path.join(base, files[0]); from app.llm_interpreter import interpret_notes; d = json.load(open(path, encoding='utf-8')); c = [x for x in d['cases'] if x['id'] == 'SAMPLE-03'][0]; print(json.dumps(interpret_notes(c['input']['operator_notes'], battery=c['input']['battery']), indent=2))"
echo ---EXITCODE=%ERRORLEVEL%---
