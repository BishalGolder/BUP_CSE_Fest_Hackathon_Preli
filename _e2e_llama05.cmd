@echo off
set GRIDWISE_LLM_PROVIDER=groq
set GRIDWISE_LLM_MODEL=llama-3.1-8b-instant
set GROQ_API_KEY=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
set GRIDWISE_ALLOW_OFFLINE=0
"C:\Program Files\Python313\python.exe" -m tests.replay_public_cases --case SAMPLE-05
echo EXITCODE=%ERRORLEVEL%
