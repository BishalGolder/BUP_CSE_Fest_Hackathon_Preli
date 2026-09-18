@echo off
set GROQ_API_KEY=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
"C:\Program Files\Python313\python.exe" -c "import httpx, os; key=os.environ['GROQ_API_KEY']; r=httpx.get('https://api.groq.com/openai/v1/models', headers={'Authorization':'Bearer '+key}, timeout=20); print('HTTP',r.status_code); data=r.json(); print('first models:',[m['id'] for m in data.get('data',[])][:5]); print('has qwen/qwen3-32b:',any(m['id']=='qwen/qwen3-32b' for m in data.get('data',[]))); print('has llama-3.1-8b-instant:',any(m['id']=='llama-3.1-8b-instant' for m in data.get('data',[])))"
echo EXITCODE=%ERRORLEVEL%
