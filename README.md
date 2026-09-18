<div align="center">

# GridWise

### Natural-language energy dispatch optimizer

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110%2B-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![PuLP](https://img.shields.io/badge/PuLP-2.7%2B-orange)](https://github.com/coin-or/pulp)
[![GHCR Image](https://img.shields.io/badge/ghcr.io-bishalgolder%2Fgridwise%3Av1-2496ED?logo=docker&logoColor=white)](https://ghcr.io/bishalgolder/gridwise)
[![Public Cases](https://img.shields.io/badge/public%20cases-10%2F10-brightgreen)]()
[![License](https://img.shields.io/badge/license-MIT-lightgrey)](LICENSE)

Turn operator notes like *"cut solar to 60% from 11 AM to 2 PM and keep the
battery above 30% overnight"* into a verifiable 24-hour energy plan.

</div>

---

## TL;DR

A FastAPI service that interprets free-text operating notes into a small set
of typed directives, runs a deterministic PuLP MILP over those directives,
and returns a 24-hour plan plus per-hour totals. The published container
image is on GHCR — pull, set `GROQ_API_KEY`, run.

```bash
docker pull ghcr.io/bishalgolder/gridwise:v1
# sha256: d8c34dd429623f90145de222ee84f90326cafb06f438748ac067bab031942a5d
# size:    311 MB

docker run --rm -p 8000:8000 \
  -e GROQ_API_KEY="$GROQ_API_KEY" \
  ghcr.io/bishalgolder/gridwise:v1
```

Then:

```bash
curl -s http://localhost:8000/health
curl -s -X POST http://localhost:8000/optimize -H 'content-type: application/json' \
  -d @sample_request.json
```

---

## Table of Contents

1. [Problem](#problem)
2. [Architecture](#architecture)
3. [Tech Stack](#tech-stack)
4. [Quick Start](#quick-start)
5. [HTTP API](#http-api)
6. [Sample Request & Response](#sample-request--response)
7. [Directive Types](#directive-types)
8. [LLM Role & Provider Matrix](#llm-role--provider-matrix)
9. [Configuration](#configuration)
10. [Testing & Verification](#testing--verification)
11. [Project Structure](#project-structure)
12. [Security](#security)
13. [Limitations & Future Work](#limitations--future-work)
14. [License](#license)

---

## Problem

Industrial energy sites run on 24-hour plans with constraints that change
daily — battery minimums, charge / discharge windows, peak-shaving, partial
solar curtailment, and so on. Typing those constraints into a solver UI by
hand is slow and error-prone; expressing them as plain English is fast and
ambiguous.

GridWise sits in the middle:

- **Input:** site context (demand, solar, battery specs, prices) plus a
  free-text operator note.
- **Output:** a JSON 24-hour dispatch plan (battery charge, grid import,
  solar use, per-hour totals, summary totals) with a deterministic
  re-validation pass at the end.

The LLM is used **only** to translate notes into a small typed schema. The
optimization itself is fully deterministic — PuLP over CBC, ~15s timebox,
energy balance, battery SOC continuity, charge XOR discharge, end-of-day
neutrality, and any directive-derived window constraints.

---

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

Three things to notice in the flow above:

1. **LLM is sandboxed.** It only emits directives; everything numeric is
   re-checked by `guardrails.py`.
2. **The solver is the source of truth.** Even if the LLM hallucinates a
   bad directive, guardrails clamp it and the optimizer still returns a
   feasible plan.
3. **The response is re-validated.** `validator.py` re-runs energy balance
   and totals consistency on the produced schedule before it leaves the
   service.

---

## Tech Stack

| Layer            | Choice                                            |
|------------------|---------------------------------------------------|
| Web              | FastAPI 0.110+, uvicorn                           |
| Validation       | Pydantic 2.5+ (`extra="forbid"`)                  |
| Solver           | PuLP 2.7+ with bundled CBC                         |
| LLM transport    | `httpx` (sync, timeout, retry-aware)              |
| Tests            | Python `unittest` + a replay harness over JSON    |
| Container        | `python:3.11-slim`, non-root `gridwise` user      |
| Registry         | `ghcr.io/bishalgolder/gridwise:v1`                |

---

## Quick Start

### Option A — Local Python (development)

```bash
git clone https://github.com/bishalgolder/gridwise.git
cd gridwise
python -m venv .venv && source .venv/bin/activate    # or .venv\Scripts\activate on Windows
pip install -r requirements.txt
cp .env.example .env                                  # then edit .env
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### Option B — Docker Compose (recommended)

```bash
# .env must contain GROQ_API_KEY=...
docker compose up --build
```

`docker-compose.yml` mounts nothing, exposes port 8000, and reads the API
key from your shell environment (no secrets baked into the image).

### Option C — Pull the published image

```bash
docker pull ghcr.io/bishalgolder/gridwise:v1
docker run --rm -p 8000:8000 \
  -e GROQ_API_KEY="$GROQ_API_KEY" \
  ghcr.io/bishalgolder/gridwise:v1
```

Verify:

```bash
curl -s http://localhost:8000/health
# {"status":"ok"}
```

Interactive docs:

- Swagger UI → <http://localhost:8000/docs>
- ReDoc      → <http://localhost:8000/redoc>

---

## HTTP API

### `GET /health`

Liveness probe used by the container's `HEALTHCHECK`.

```json
{"status": "ok"}
```

### `POST /optimize`

**Request body** (`OptimizeRequest`):

| Field            | Type                          | Notes                              |
|------------------|-------------------------------|------------------------------------|
| `site_context`   | `SiteContext`                 | demand, solar, battery, prices     |
| `operator_notes` | `string`                      | free-text operating instructions   |

**Response body** (`OptimizeResponse`):

| Field           | Type                       | Notes                                |
|-----------------|----------------------------|--------------------------------------|
| `directives`    | `list[Directive]`          | parsed + guardrailed                 |
| `schedule`      | `list[HourlyPlan]` (24)    | per-hour battery / grid / solar      |
| `totals`        | `PlanTotals`               | aggregate sums                       |
| `solver_status` | `string`                   | `optimal`, `feasible`, `infeasible…` |

Errors use a uniform envelope:

```json
{"error": {"code": "validation_error", "message": "...",
           "details": {"loc": ["body", "operator_notes"]}}}
```

---

## Sample Request & Response

**Request** (truncated):

```json
{
  "site_context": {
    "site_id": "demo-1",
    "demand_kw":   [80, 75, 70, 68, 70, 85, 120, 150, 160, 155, 150, 145,
                    140, 135, 130, 135, 150, 170, 200, 220, 210, 180, 130, 100],
    "solar_kw":    [ 0,  0,  0,  0,  0,  5,  20,  60, 110, 150, 180, 190,
                    195, 180, 150, 100,  50,  15,   0,   0,   0,   0,   0,   0],
    "battery":     {"capacity_kwh": 400, "initial_soc_kwh": 200,
                     "max_charge_kw": 100, "max_discharge_kw": 100,
                     "round_trip_eff": 0.90},
    "prices":      {"import_per_kwh": [...], "export_per_kwh": [...]}
  },
  "operator_notes": "Cut solar to 60% from 11 AM to 2 PM, "
                     "and keep battery above 30% overnight."
}
```

**Response** (abridged):

```json
{
  "directives": [
    {"type": "solar_reduction",
     "params": {"percent": 60, "hours": [11, 12, 13, 14]}},
    {"type": "minimum_battery_reserve",
     "params": {"hours": [0, 1, 2, 3, 4, 5, 22, 23], "min_soc_percent": 30}}
  ],
  "schedule": [
    {"hour":  0, "battery_kw":  -40.0, "grid_kw": 120.0, "solar_kw":   0.0},
    {"hour":  1, "battery_kw":  -35.0, "grid_kw": 110.0, "solar_kw":   0.0},
    "...",
    {"hour": 23, "battery_kw":  -20.0, "grid_kw": 120.0, "solar_kw":   0.0}
  ],
  "totals": {
    "grid_import_kwh":    3120.5,
    "grid_export_kwh":    145.0,
    "solar_used_kwh":     1505.0,
    "peak_grid_kw":       220.0,
    "battery_throughput": 480.0
  },
  "solver_status": "optimal"
}
```

---

## Directive Types

The LLM is constrained to emit **only** these six types. Anything else is
rejected by `guardrails.py`.

| Type                          | Params                                                                                  | Semantics                                            |
|-------------------------------|------------------------------------------------------------------------------------------|------------------------------------------------------|
| `solar_reduction`             | `percent` (0–100), `hours` ([0..23])                                                     | Cap solar output at `percent%` during listed hours.  |
| `minimum_battery_reserve`     | `hours`, `min_soc_percent` (0–100)                                                       | SOC must stay ≥ `min_soc_percent%` during those hrs. |
| `no_charge_window`            | `hours`                                                                                  | Battery cannot charge in listed hours.               |
| `no_discharge_window`         | `hours`                                                                                  | Battery cannot discharge in listed hours.            |
| `max_grid_window`             | `hours`, `max_kw`                                                                        | Grid import ≤ `max_kw` during listed hours.          |
| `no_op`                       | _(none)_                                                                                  | Acknowledges a note that needs no constraint.        |

**Hour semantics:** hours are integers in `[0, 23]` representing the
**start** of an hour slot. *"6 PM to 9 PM"* → `[18, 19, 20]`.

---

## LLM Role & Provider Matrix

The LLM is strictly a **translator** — it converts a free-text note into
the directive list above. It never sees the solver state.

| Provider   | Env var(s)                          | Default model                  | Notes                              |
|------------|-------------------------------------|--------------------------------|------------------------------------|
| `groq`     | `GROQ_API_KEY`                      | `qwen/qwen3.8-27b`              | Default provider. Low latency.     |
| `openai`   | `OPENAI_API_KEY`                    | `gpt-4o-mini`                  | Standard OpenAI chat.              |
| `anthropic`| `ANTHROPIC_API_KEY`                 | `claude-3-5-sonnet-latest`     | Anthropic Messages API.            |
| `google`   | `GOOGLE_API_KEY`                    | `gemini-1.5-flash`             | Generative Language API.           |
| `stub`     | _(none)_                            | _(deterministic)_              | No network; for tests & CI.        |

The interpreter requests `response_format={"type": "json_object"}` with
`temperature ≤ 0.2`, applies deterministic coercion (string → number, list
expansion for *"11 to 14"*), and runs a 429-aware exponential backoff
before falling back to `stub` if all retries are exhausted.

**Swap providers without changing code:**

```env
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...
LLM_MODEL=claude-3-5-sonnet-latest
```

---

## Configuration

All configuration is read from environment variables (Pydantic Settings in
`app/config.py`). `.env.example` ships sane defaults.

| Variable                  | Default                | Meaning                                      |
|---------------------------|------------------------|----------------------------------------------|
| `LLM_PROVIDER`            | `groq`                 | `groq` / `openai` / `anthropic` / `google` / `stub` |
| `LLM_MODEL`               | `qwen/qwen3.8-27b`     | Model id for the chosen provider             |
| `GROQ_API_KEY`            | _(unset)_              | Required when provider = `groq`             |
| `OPENAI_API_KEY`          | _(unset)_              | Required when provider = `openai`           |
| `ANTHROPIC_API_KEY`       | _(unset)_              | Required when provider = `anthropic`        |
| `GOOGLE_API_KEY`          | _(unset)_              | Required when provider = `google`           |
| `LLM_TIMEOUT_S`           | `20`                   | Per-call timeout                             |
| `LLM_MAX_RETRIES`         | `2`                    | 429-aware backoff retries                    |
| `SOLVER_TIME_LIMIT_S`     | `15`                   | Hard cap on PuLP solve                       |
| `LOG_LEVEL`               | `INFO`                 | Standard log levels                          |
| `ENABLE_DETERMINISTIC_LOG`| `0`                    | 1 = pin seeded RNG for solver jitter         |
| `MAX_NOTE_CHARS`          | `2000`                 | Hard cap on `operator_notes` length         |

---

## Testing & Verification

The replay harness in `tests/replay_public_cases.py` runs every case in
`BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json` end-to-end against the
real `OptimizeRequest` shape:

```bash
python -m unittest tests.replay_public_cases -v
```

| Suite                               | Result                |
|-------------------------------------|-----------------------|
| Stub provider (offline)             | **10 / 10 pass**      |
| Groq `qwen/qwen3.8-27b` (live)      | **10 / 10 pass**      |
| `docker pull ghcr.io/.../gridwise:v1` | exit `0`, 311 MB    |
| `GET /health` in container          | `200 OK` within 1 s   |

The harness asserts:

- HTTP `200` from `POST /optimize`
- `solver_status` ∈ `{optimal, feasible}`
- `len(schedule) == 24`
- `validator.py` re-run reports zero violations
- `totals` equal the per-hour sum to within `1e-6`

---

## Project Structure

```
app/
  main.py             FastAPI app, exception handlers
  config.py           Pydantic Settings (env-driven)
  models.py           Request / response schemas
  llm_interpreter.py  LLM providers + SYSTEM_PROMPT
  guardrails.py       Deterministic directive validation
  optimizer.py        PuLP MILP model + solve
  validator.py        Final response re-check

tests/
  replay_public_cases.py   10-case regression harness

BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json
Dockerfile, docker-compose.yml, requirements.txt
.env.example
docs/
  DOCKER.md           Image build / push / pin guide
RUN_AND_MANUAL_TESTING.txt   11-section manual walkthrough
```

---

## Security

- `.env` is gitignored — **never commit it**.
- API keys are read only from environment variables
  (`GROQ_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`).
- The Dockerfile does **not** bake any secrets into the image; `docker run`
  / `docker compose` inject them at start time.
- The container runs as a non-root user (`gridwise`).
- If a key is ever shared in a chat / log / screenshot, **rotate it
  immediately** from the provider console.

---

## Limitations & Future Work

- Single battery, single site, 24-hour horizon (the optimization slot is
  fixed at 1h).
- No multi-day SOC continuity or lookahead beyond 24 h.
- LLM is the only non-deterministic step; if all retries fail, the service
  falls back to `stub` and emits `no_op` directives — a noisy but safe
  degradation.
- No persistent storage; every request is stateless.
- Planned: multi-site / multi-battery, rolling-horizon planning, persistent
  site profiles, and a thin Web UI on top of `/optimize`.

---

## License

MIT — see [`LICENSE`](LICENSE).
