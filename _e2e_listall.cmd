@echo off
set GROQ_API_KEY=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
"C:\Program Files\Python313\python.exe" -c "import httpx,os; key=os.environ['GROQ_API_KEY']; r=httpx.get('https://api.groq.com/openai/v1/models', headers={'Authorization':'Bearer '+key}, timeout=20); ids=[m['id'] for m in r.json().get('data',[])]; print('TOTAL',len(ids)); [print(i) for i in ids]; print('---'); print('qwen3-32b exact:', any(i=='qwen/qwen3-32b' for i in ids)); print('llama-3.1-8b-instant exact:', any(i=='llama-3.1-8b-instant' for i in ids)); print('qwen match:', [i for i in ids if 'qwen' in i.lower()]); print('llama-3.1 match:', [i for i in ids if 'llama-3.1' in i.lower()])"
echo EXITCODE=%ERRORLEVEL%
