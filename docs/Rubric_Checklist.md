# GridWise — Rubric Alignment Checklist

> **Purpose.** This file maps every rubric item in the **Participant Guide &
> Evaluation Rubric** (Section 02 / 06 / 07) and the **Problem Statement** to a
> concrete artefact in this repository. It is intended for both the team
> (pre-submission self-audit) and judges (one-page proof of completeness).

Legend: ✅ implemented & verified · ⚠ implemented, needs team-side action ·
🔵 required submission artefact (not committed to repo, e.g. video)

---

## A. Required deliverables (Rubric §02, 100 pts)

| Item | Where it lives in this repo | Status |
|------|------------------------------|--------|
| `GET /health` readiness endpoint | `app/main.py` — returns `{"status":"ok"}` | ✅ |
| `POST /optimize-energy` main endpoint | `app/main.py` — accepts the exact schema | ✅ |
| Exactly one `directive_interpretation` per note, in `note_index` order | `app/llm_interpreter.py` + `app/guardrails.py` | ✅ |
| `hourly_plan` (24 entries) after applying valid directives | `app/optimizer.py` + `app/validator.py` | ✅ |
| `directive_interpretation` schema / order / types | `app/models.py:DirectiveInterpretation` | ✅ |
| `hourly_plan` schema & top-level response fields | `app/models.py:HourPlan`, `ScenarioResponse` | ✅ |
| Source repo (private→public) | GitHub repo (post-deadline) | ⚠ team action |
| Self-contained `README.md` | `README.md` | ✅ |
| **Docker fallback image** (pullable, port `8000`, `0.0.0.0`, no baked-in secrets) | `Dockerfile` + `docker-compose.yml` + `docs/Docker_Fallback.md` | ⚠ push to Docker Hub / GHCR |
| **3-minute solution video** | MP4 link in submission package | 🔵 required at submission |
| **Working public endpoint URL** | Hosted service URL in submission package | ⚠ deploy to a reachable host |
| **GitHub repo (private during, public after)** | GitHub repo | ⚠ team action |

---

## B. Scoring categories (Rubric §06, 7 × sub-score)

| # | Category | Points | How we earn them | Status |
|---|----------|--------|-------------------|--------|
| 1 | LLM Directive Interpretation | 25 | Real LLM provider (Groq default), `groq`/`openai`/`anthropic`/`google` factories, `response_format=json_object` / tool-use / schema, paraphrase robustness via prompt+guardrails | ✅ code; ⚠ needs live key |
| 2 | Directive Application & Constraint Correctness | 25 | MILP enforces energy balance, battery bounds, rate limits, end-of-day neutrality, solar_reduction, minimum_battery_reserve, no_charge_window, no_discharge_window, max_grid_window | ✅ |
| 3 | Optimization Quality | 10 | PuLP + CBC, exact MILP, peak tie-break as a sub-1e-4 secondary weight | ✅ |
| 4 | API Contract & Schema | 10 | Pydantic models match the Problem Statement schemas, exception handlers map malformed → 400 / infeasible → 422 / controlled → 500 | ✅ |
| 5 | Performance & Reliability | 10 | p95 ≤ 5 s on sample cases, `LLMError` retries with backoff, no secret leakage in errors | ✅ stub; ⚠ live LLM p95 |
| 6 | Deployment & Docker Fallback | 10 | `Dockerfile` (python:3.11-slim, non-root, `HEALTHCHECK`, port 8000, `0.0.0.0`), `docker-compose.yml`, `docs/Docker_Fallback.md` | ⚠ push image |
| 7 | Documentation & Local Reproducibility | 10 | `README.md` (quickstart, env-var names, model/provider, solver, curl examples, public-sample test), `docs/GridWise_Guide.md`, `docs/Docker_Fallback.md` | ✅ |

---

## C. Pre-submit final checklist (Rubric §11, copy-paste)

- [ ] `GET /health` reachable externally → `{"status":"ok"}`.
- [ ] `POST /optimize-energy` reachable externally; accepts 1–3 operator notes.
- [ ] Every operator note yields exactly one `directive_interpretation` in
      `note_index` order; `no_op` uses `applies=false, structured_adjustment=null`.
- [ ] LLM output passes guardrails: hours are unique 0–23 ascending; numerics
      are valid; `applies=true` for every non-`no_op`.
- [ ] `hourly_plan` obeys organizer-ground-truth directives, energy balance,
      effective-solar cap, battery bounds, rate limits, end-of-day neutrality.
- [ ] `total_grid_kwh`, `total_cost_bdt`, `peak_grid_kwh` match a manual
      recompute of `hourly_plan` within 0.01 kWh / 0.01 BDT.
- [ ] `README.md` is self-contained; env-var names, model/provider, solver,
      run command, `/health` curl, public-sample test, known limitations, and
      **no committed secrets** are present.
- [ ] Repo was created after question reveal, kept private during the event,
      made public after the deadline; submitted endpoint stays reachable for
      evaluation; fallback / video links remain accessible.
- [ ] Docker fallback image is pullable with the documented command; the
      container reaches `/health`; the documented port is exposed; **no
      baked-in secrets**.
- [ ] 3-minute video is accessible, ≤ 3:00, and explains problem, architecture,
      solution approach, LLM → guardrails → optimizer pipeline, and how
      organizers run/test the submission.

---

## D. Critical violations to avoid (Rubric §09)

The system must **not**:

- omit the LLM from the operator-note interpretation path,
- mark an applicable directive as `no_op`,
- return a plan that ignores an applicable ground-truth directive,
- break energy balance or demand-supply,
- violate battery bounds / rate limits,
- overuse effective solar or emit negative kWh,
- break `no_charge_window` / `no_discharge_window` / `minimum_battery_reserve`
  / `max_grid_window`,
- end the day at a battery level ≠ initial,
- let reported totals diverge from a recompute of `hourly_plan`.

All of these are enforced by `app/optimizer.py` and re-checked in
`app/validator.py` before the response leaves the API.

---

## E. Submission package (Rubric §02 table)

| # | Item | Provided as |
|---|------|--------------|
| 1 | Working public endpoint URL | Submission form |
| 2 | GitHub repository (private→public) | GitHub link |
| 3 | README & configuration | `README.md`, `.env.example` |
| 4 | Docker fallback image | Registry URL + tag/digest + `docs/Docker_Fallback.md` |
| 5 | 3-minute architecture / solution video | MP4 link in submission form |
