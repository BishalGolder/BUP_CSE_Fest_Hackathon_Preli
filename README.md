# GridWise — LLM-Assisted Microgrid Optimizer

> FastAPI service that translates free-text operator notes into structured
> energy directives and runs a 24-hour MILP optimization for a campus microgrid
> with solar PV, battery storage, and grid import.

## Problem

Campus microgrid operators leave daily notes ("panel cleaning noon-2 PM",
"keep at least 50% battery for emergency"). GridWise ingests 1–3 of those
notes, uses a real LLM to map each to exactly one structured directive,
then solves a linear program that schedules battery charge/discharge and
grid import to minimize total cost while honoring every constraint.

## Architecture

```
  ┌─────────────┐   operator_notes   ┌──────────────────┐
  │  HTTP POST  │ ─────────────────► │  LLM Interpreter │  (Groq / OpenAI / Anthropic / Google)
  │   /optimize │                    │  app/llm_interp. │   json_object + low temperature
  │   -energy   │                    └────────┬─────────┘
  └─────────────┘                             │ list[Directive]
                                              ▼
                                  ┌───────────────────────┐
                                  │ Deterministic         │
                                  │ Guardrails            │  app/guardrails.py
                                  │ - type / fields /     │
                                  │ - hours in [0,23] /   │
                                  │ - numeric coercion    │
                                  └────────┬──────────────┘
                                           │ clean list[Directive]
                                           ▼
                              ┌──────────────────────────┐
                              │ PuLP MILP optimizer      │  app/optimizer.py
                              │ - energy balance         │  CBC solver, ~15 s cap
                              │ - battery SOC continuity │
                              │ - charge XOR discharge   │
                              │ - end-of-day neutrality  │
                              │ - window constraints     │
                              └────────┬─────────────────┘
                                       │ 24-hour plan + totals
                                       ▼
                              ┌─────────────────────────┐
                              │ Response validator      │  app/validator.py
                              │ - re-runs energy balance│
                              │ - checks totals vs sum  │
                              │ - schema-correct dump   │
                              └────────┬────────────────┘
                                       │ JSON
                                       ▼
                                       HTTP 200
```

## Tech Stack

- **Python 3.10+**, **FastAPI 0.110+**, **Pydantic 2.5+**
- **PuLP 2.7+** with built-in **CBC** MILP solver (no extra install)
- **httpx** for provider HTTP calls (timeouts + retries)
- **python-dotenv** for env loading
- **uvicorn** ASGI server

## Prerequisites

- Python 3.10 or newer
- A real LLM API key from one of: **Groq** (recommended), OpenAI, Anthropic, Google
- Optional: Docker + Docker Compose

## Environment Variables

Copy `.env.example` to `.env` (gitignored) and fill in real values.

| Var                          | Default          | Notes                                                        |
|------------------------------|------------------|--------------------------------------------------------------|
| `GRIDWISE_LLM_PROVIDER`      | `groq`           | `groq` / `openai` / `anthropic` / `google` / `stub`          |
| `GRIDWISE_LLM_MODEL`         | `qwen/qwen3.8-27b` | Any model id accepted by your provider                    |
| `GRIDWISE_LLM_BASE_URL`      | _(blank)_        | Override only for proxies or self-hosted gateways            |
| `GROQ_API_KEY`               | _empty_          | Required when `GRIDWISE_LLM_PROVIDER=groq`                   |
| `LLM_API_KEY`                | _empty_          | Alias used by OpenAI / Anthropic / Google providers          |
| `GRIDWISE_LLM_TIMEOUT`       | `30`             | Per-call HTTP timeout (seconds)                              |
| `GRIDWISE_LLM_MAX_RETRIES`   | `3`              | 429-aware: parses "try again in Xs" hint, caps at 65 s       |
| `GRIDWISE_SOLVER_TIME_LIMIT` | `15`             | CBC wall-time cap per request                                |
| `GRIDWISE_SOLVER_MIP_GAP`    | `0.001`          | CBC optimality gap                                            |
| `GRIDWISE_SOLVER_THREADS`    | `1`              | PuLP thread count                                            |
| `GRIDWISE_ALLOW_OFFLINE`     | `0`              | Must be `0` for live judging (no stub fallback)              |
| `GRIDWISE_TOL`               | `0.01`           | kWh / BDT tolerance for the public-sample checker            |
| `GRIDWISE_LOG_LEVEL`         | `INFO`           | `DEBUG` / `INFO` / `WARNING` / `ERROR`                       |

## Quick Start

### Local (Python)

```bash
# 1. Install
pip install -r requirements.txt

# 2. Configure (use any real provider; Groq is fastest & cheapest)
cp .env.example .env
# edit .env and set GROQ_API_KEY=...

# 3. Run the API
PYTHONPATH=. uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Then open:
- `http://127.0.0.1:8000/health` → `{"status":"ok"}`
- `http://127.0.0.1:8000/docs` → interactive Swagger UI
- `http://127.0.0.1:8000/redoc` → ReDoc reference

### Docker

```bash
docker compose up --build
# API at http://127.0.0.1:8000
```

For full build / run / publish instructions (GHCR, Docker Hub, multi-arch,
digest pinning), see [`docs/DOCKER.md`](docs/DOCKER.md).

The single optimization endpoint is:

```http
POST /optimize-energy
Content-Type: application/json
```

### Tests

```bash
# Stub regression — deterministic, no API key needed
PYTHONPATH=. python -m tests.replay_public_cases

# Live LLM replay — set provider + key first
PYTHONPATH=. python -m tests.replay_public_cases
```

Expected output:

```
Summary: 10 pass, 0 fail
```

## Sample Request

```json
POST /optimize-energy
{
  "hours": [
    {"hour": 0,  "demand_kwh": 120, "solar_kwh": 0},
    {"hour": 12, "demand_kwh": 260, "solar_kwh": 180},
    ...
    {"hour": 23, "demand_kwh": 110, "solar_kwh": 0}
  ],
  "battery": {
    "capacity_kwh": 200,
    "initial_energy_kwh": 120,
    "minimum_energy_kwh": 40,
    "max_charge_kwh_per_hour": 50,
    "max_discharge_kwh_per_hour": 50
  },
  "operator_notes": [
    "Keep at least 50% of the battery capacity stored in the battery from 6 PM until 9 PM for emergency operations.",
    "Panel cleaning from 1 PM to 3 PM."
  ]
}
```

## Sample Response (truncated)

```json
{
  "directive_interpretation": [
    {
      "note_index": 0,
      "applies": true,
      "directive_type": "minimum_battery_reserve",
      "structured_adjustment": {"hours": [18,19,20], "minimum_energy_kwh": 100},
      "explanation": "Maintain at least 100 kWh from 6 PM to 9 PM."
    },
    ...
  ],
  "hourly_plan": [
    {"hour": 0,  "grid_kwh": 70,  "solar_used_kwh": 0,   "battery_kwh": 50, "cost_bdt": 1050, "action": "discharge"},
    ...
  ],
  "total_grid_kwh": 2430,
  "total_solar_kwh": 1820,
  "total_battery_kwh": 480,
  "total_cost_bdt": 35480,
  "peak_grid_kwh": 205
}
```

## The Six Supported Directive Types

| `directive_type`        | `structured_adjustment` shape                               |
|-------------------------|------------------------------------------------------------|
| `solar_reduction`       | `{"hours": [int,...], "factor": float in [0,1]}`            |
| `minimum_battery_reserve` | `{"hours": [int,...], "minimum_energy_kwh": float ≥ 0}`  |
| `no_charge_window`      | `{"hours": [int,...]}`                                     |
| `no_discharge_window`   | `{"hours": [int,...]}`                                     |
| `max_grid_window`       | `{"hours": [int,...], "max_grid_kwh": float ≥ 0}`          |
| `no_op`                 | `null` (note does not affect today's schedule)              |

Windows are **start-inclusive, end-exclusive** whole hours. `"6 PM to 9 PM"`
maps to `[18, 19, 20]`.

## LLM Role

The LLM is **mandatory** on the operator-note → directive path. The
repository does **not** ship a single hard-coded phrase matcher (that
would violate the rubric). The chosen provider's `json_object` mode is
used to force structured output, and every directive the LLM emits is
**deterministically re-validated** by `app/guardrails.py` before it
ever reaches the optimizer.

| Provider      | Function                | Endpoint                          |
|---------------|-------------------------|-----------------------------------|
| `groq`        | `_groq_factory`         | `https://api.groq.com/openai/v1`  |
| `openai`      | `_openai_factory`       | `https://api.openai.com/v1`       |
| `anthropic`   | `_anthropic_factory`    | `https://api.anthropic.com`       |
| `google`      | `_google_factory`       | `https://generativelanguage.googleapis.com` |
| `stub`        | `_stub_factory`         | offline, deterministic            |

429 / OTPM rate limits are handled by parsing the provider's
`"try again in Xs"` hint and waiting up to 65 s before retry.

## Security

- `.env` is gitignored — **never commit it**.
- API keys are read only from environment variables (`GROQ_API_KEY`,
  `LLM_API_KEY`).
- The Dockerfile does not bake any secrets into the image.
- If a key is ever shared in a chat / log / screenshot, **rotate it
  immediately** from the provider console.

## Verification

Against the `BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json` set of
ten cases:

- Stub interpreter (offline): `10 pass, 0 fail`.
- Real Groq `qwen/qwen3.8-27b`: `10 pass, 0 fail` (verified end-to-end
  with a live `GROQ_API_KEY`).

## Limitations

- LLM-driven interpretation is non-deterministic across runs (different
  phrasings may produce different exact orders/totals). The reference
  tolerance is `0.01` kWh / BDT.
- Pydantic `extra="forbid"` rejects unknown fields — clients must
  follow the request schema.
- Time zones: hours are interpreted as **local campus time** (0–23).
- The optimizer is offline-only (no real-time telemetry).
- Groq's free tier imposes tight output-token-per-minute limits; the
  retry loop handles this but pacing ≥ 5 s between calls reduces 429
  retries.

## Project Structure

```
app/
  main.py            FastAPI app, exception handlers
  config.py          Pydantic Settings (env-driven)
  models.py          Request/response schemas
  llm_interpreter.py LLM providers + SYSTEM_PROMPT
  guardrails.py      Deterministic directive validation
  optimizer.py       PuLP MILP model + solve
  validator.py       Final response re-check
tests/
  replay_public_cases.py   10-case regression harness
BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json
Dockerfile, docker-compose.yml, requirements.txt
.env.example
RUN_AND_MANUAL_TESTING.txt  // 11-section manual walkthrough
```
