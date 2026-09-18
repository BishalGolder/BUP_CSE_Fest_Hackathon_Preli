"""FastAPI application: GET /health and POST /optimize-energy."""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from .config import settings
from .guardrails import GuardrailError, validate_directives
from .llm_interpreter import LLMError, interpret_notes
from .models import ScenarioRequest
from .optimizer import OptimizerError, optimize
from .validator import ResponseValidationError, build_and_validate_response


logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
)
log = logging.getLogger("gridwise.api")


app = FastAPI(
    title="GridWise",
    version="1.0.0",
    description="GridWise — LLM-assisted 24-hour microgrid optimizer",
)


@app.exception_handler(RequestValidationError)
async def _pydantic_validation_handler(_: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=400,
        content={
            "error": "malformed_request",
            "message": "Request body did not match the expected schema.",
            "details": exc.errors(),
        },
    )


@app.exception_handler(ValidationError)
async def _pydantic_handler(_: Request, exc: ValidationError):
    return JSONResponse(
        status_code=400,
        content={
            "error": "malformed_request",
            "message": "Request body did not match the expected schema.",
            "details": json.loads(exc.json()),
        },
    )


@app.exception_handler(GuardrailError)
async def _guardrail_handler(_: Request, exc: GuardrailError):
    return JSONResponse(
        status_code=400,
        content={"error": "invalid_directive", "message": str(exc)},
    )


@app.exception_handler(LLMError)
async def _llm_handler(_: Request, exc: LLMError):
    log.error("LLM error: %s", exc)
    return JSONResponse(
        status_code=500,
        content={"error": "llm_unavailable", "message": "LLM provider failed after retries."},
    )


@app.exception_handler(OptimizerError)
async def _opt_handler(_: Request, exc: OptimizerError):
    log.error("Optimizer error: %s", exc)
    return JSONResponse(
        status_code=422,
        content={"error": "infeasible_scenario", "message": str(exc)},
    )


@app.exception_handler(ResponseValidationError)
async def _resp_handler(_: Request, exc: ResponseValidationError):
    log.error("Response validation error: %s", exc)
    return JSONResponse(
        status_code=500,
        content={"error": "internal_consistency", "message": "Plan failed final validation."},
    )


@app.exception_handler(Exception)
async def _fallback_handler(_: Request, exc: Exception):
    log.exception("Unhandled error: %s", exc)
    return JSONResponse(
        status_code=500,
        content={"error": "internal_error", "message": "Unexpected server error."},
    )


@app.get("/health")
async def health() -> Dict[str, str]:
    return {"status": "ok"}


@app.post("/optimize-energy")
async def optimize_energy(request: Request) -> Dict[str, Any]:
    raw = await request.body()
    try:
        body = json.loads(raw or b"{}")
    except Exception:
        raise HTTPException(status_code=400, detail={"error": "invalid_json"})

    try:
        scenario = ScenarioRequest.model_validate(body)
    except ValidationError:
        raise

    directives_raw = interpret_notes(scenario.operator_notes, battery=scenario.battery)
    directives = validate_directives(directives_raw, scenario.operator_notes)

    opt = optimize(scenario, directives)
    summary = _build_summary(scenario, directives, opt)
    response = build_and_validate_response(scenario, directives, opt, summary)
    return response


def _build_summary(scenario: ScenarioRequest, directives, opt) -> str:
    applied = [d for d in directives if d.get("applies")]
    if not applied:
        return (
            f"No directives applied for scenario {scenario.scenario_id}; "
            f"minimized total grid cost to {opt.total_cost_bdt:.0f} BDT "
            f"({opt.total_grid_kwh:.0f} kWh, peak {opt.peak_grid_kwh:.0f} kWh) "
            f"while restoring initial battery level."
        )
    parts = []
    for d in applied:
        dt = d["directive_type"]
        hs = d.get("structured_adjustment", {}).get("hours", [])
        parts.append(f"{dt}(hours={hs})")
    return (
        f"Applied {len(applied)} directive(s) [{', '.join(parts)}] for scenario "
        f"{scenario.scenario_id}; minimized total grid cost to {opt.total_cost_bdt:.0f} BDT "
        f"({opt.total_grid_kwh:.0f} kWh, peak {opt.peak_grid_kwh:.0f} kWh) "
        f"while restoring initial battery level."
    )
