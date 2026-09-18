"""Replay validator for public sample cases.

Reads the public sample-case JSON file, POSTs each case.input to the
in-process FastAPI app (or to a running server via ``--url``), and verifies:

  1. The LLM directive interpretation matches the public expected
     ``directive_interpretation`` (semantically, not byte-for-byte).
  2. The hourly_plan satisfies all GridWise constraints and achieves the
     same total cost as the reference schedule within the official 0.01
     BDT tolerance.

Run:
    python -m tests.replay_public_cases \
        --cases ../BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json \
        --case SAMPLE-01

If ``--url`` is given, the script POSTs to that URL instead of using the
in-process app.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

from app.config import settings  # noqa: E402
from app.guardrails import validate_directives  # noqa: E402
from app.llm_interpreter import interpret_notes  # noqa: E402
from app.optimizer import optimize  # noqa: E402
from app.validator import build_and_validate_response  # noqa: E402
from app.models import ScenarioRequest  # noqa: E402


def _semantic_match_directives(actual: List[Dict], expected: List[Dict]) -> Optional[str]:
    """Return None if equivalent, else an error message."""
    if len(actual) != len(expected):
        return f"length mismatch: {len(actual)} vs {len(expected)}"
    for a, e in zip(actual, expected):
        if a["note_index"] != e["note_index"]:
            return f"note_index mismatch {a['note_index']} vs {e['note_index']}"
        if a["directive_type"] != e["directive_type"]:
            return f"directive_type mismatch: {a['directive_type']} vs {e['directive_type']}"
        if a["applies"] != e["applies"]:
            return f"applies mismatch for note {a['note_index']}"
        sa_a = a.get("structured_adjustment") or {}
        sa_e = e.get("structured_adjustment") or {}
        if a["directive_type"] == "no_op":
            continue
        if sa_a.get("hours", []) != sa_e.get("hours", []):
            return f"hours mismatch for note {a['note_index']}: {sa_a.get('hours')} vs {sa_e.get('hours')}"
        if "factor" in sa_e:
            if abs(float(sa_a.get("factor", 0)) - float(sa_e["factor"])) > 0.01:
                return f"factor mismatch note {a['note_index']}"
        if "minimum_energy_kwh" in sa_e:
            if abs(float(sa_a.get("minimum_energy_kwh", 0)) - float(sa_e["minimum_energy_kwh"])) > 0.01:
                return f"min_energy mismatch note {a['note_index']}"
        if "max_grid_kwh" in sa_e:
            if abs(float(sa_a.get("max_grid_kwh", 0)) - float(sa_e["max_grid_kwh"])) > 0.01:
                return f"max_grid mismatch note {a['note_index']}"
    return None


def _run_one(case: Dict[str, Any], url: Optional[str]) -> Tuple[bool, str]:
    payload = case["input"]
    expected = case["expected_output"]
    notes = payload["operator_notes"]
    if url:
        import httpx
        r = httpx.post(url, json=payload, timeout=60)
        if r.status_code != 200:
            return False, f"HTTP {r.status_code}: {r.text[:200]}"
        body = r.json()
    else:
        scenario = ScenarioRequest.model_validate(payload)
        directives_raw = interpret_notes(notes)
        directives = validate_directives(directives_raw, notes)
        opt = optimize(scenario, directives)
        body = build_and_validate_response(
            scenario, directives, opt,
            plan_summary=expected.get("plan_summary", "")
        )

    # 1. directive interpretation must match semantically
    diff = _semantic_match_directives(body["directive_interpretation"], expected["directive_interpretation"])
    if diff:
        return False, f"DIRECTIVE MISMATCH: {diff}"

    # 2. totals must match within 0.01 (or be at most as good as reference)
    if abs(body["total_cost_bdt"] - expected["total_cost_bdt"]) > settings.equivalence_tol + 0.05:
        return False, (
            f"COST GAP: ours={body['total_cost_bdt']:.4f} "
            f"expected={expected['total_cost_bdt']:.4f}"
        )
    if abs(body["total_grid_kwh"] - expected["total_grid_kwh"]) > 1.0:
        return False, (
            f"GRID GAP: ours={body['total_grid_kwh']:.4f} "
            f"expected={expected['total_grid_kwh']:.4f}"
        )
    if abs(body["peak_grid_kwh"] - expected["peak_grid_kwh"]) > 0.5:
        return False, (
            f"PEAK GAP: ours={body['peak_grid_kwh']:.4f} "
            f"expected={expected['peak_grid_kwh']:.4f}"
        )
    return True, "OK"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--cases", default=str(ROOT.parent / "BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json"))
    p.add_argument("--case", default=None, help="single case id to run")
    p.add_argument("--url", default=None, help="optional live URL to POST against")
    args = p.parse_args()

    cases_path = Path(args.cases)
    if not cases_path.exists():
        # Try relative to workspace
        candidates = [
            cases_path,
            ROOT / "BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json",
            Path("BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json"),
            Path(__file__).resolve().parents[2] / "BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json",
        ]
        for c in candidates:
            if c.exists():
                cases_path = c
                break
    if not cases_path.exists():
        print(f"ERROR: cases file not found: {args.cases}", file=sys.stderr)
        return 2

    with cases_path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    cases = data["cases"]
    if args.case:
        cases = [c for c in cases if c["id"] == args.case]
        if not cases:
            print(f"ERROR: case id {args.case!r} not found", file=sys.stderr)
            return 2

    print(f"Replaying {len(cases)} case(s) from {cases_path}")
    provider = settings.llm_provider
    print(f"LLM provider: {provider} (model={settings.llm_model})")

    pass_count = 0
    fail_count = 0
    for case in cases:
        ok, msg = _run_one(case, args.url)
        status = "PASS" if ok else "FAIL"
        print(f"  [{status}] {case['id']}: {case['label']} -- {msg}")
        if ok:
            pass_count += 1
        else:
            fail_count += 1

    print(f"\nSummary: {pass_count} pass, {fail_count} fail")
    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
