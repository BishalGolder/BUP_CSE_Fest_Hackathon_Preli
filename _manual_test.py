"""
Easy manual tester for GridWise.

Usage:
    1) Make sure the API server is running in another terminal:
         uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
       (leave that terminal alone while you run this script)

    2) In a NEW terminal, run:
         python _manual_test.py            -> runs ALL 10 sample cases
         python _manual_test.py SAMPLE-03  -> runs just SAMPLE-03
         python _manual_test.py SAMPLE-05  -> runs just SAMPLE-05

The script:
  - reads BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json
  - POSTs each scenario.input to http://127.0.0.1:8000/optimize-energy
  - prints a friendly summary (cost / grid / peak + the directives)
  - compares against expected_output and reports PASS / FAIL

If anything goes wrong (server not running, network error, bad JSON)
the script prints the error in plain English.
"""
import json
import sys
import urllib.request
import urllib.error
from pathlib import Path

URL = "http://127.0.0.1:8000/optimize-energy"
CASES_FILE = Path("BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json")


def _hr(c: str = "-") -> None:
    print(c * 70)


def _post(payload: dict) -> dict:
    """Send payload to the API and return the parsed JSON response.
    Raises a friendly error if anything goes wrong."""
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        URL,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        text = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Server replied {e.code}: {text}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(
            f"Could not reach the API at {URL}. "
            f"Is uvicorn running? Detail: {e.reason}"
        ) from e


def _check(label: str, ours: float, theirs: float, tol: float) -> str:
    delta = abs(ours - theirs)
    return "OK " if delta <= tol else "FAIL"


def main(argv: list[str]) -> int:
    if not CASES_FILE.exists():
        print(f"ERROR: cannot find {CASES_FILE} in the current folder.")
        return 1

    with CASES_FILE.open("r", encoding="utf-8") as f:
        data = json.load(f)

    cases = data["cases"]
    if len(argv) >= 2:
        wanted = argv[1].upper()
        cases = [c for c in cases if c["id"].upper() == wanted]
        if not cases:
            print(f"ERROR: no case named {argv[1]!r}.")
            print(f"Available: {[c['id'] for c in data['cases']]}")
            return 1

    print(f"Running {len(cases)} scenario(s) against {URL}\n")

    overall_ok = 0
    overall_fail = 0

    for c in cases:
        case_id = c["id"]
        label = c.get("label", "")
        payload = c["input"]
        expected = c["expected_output"]

        _hr("=")
        print(f"{case_id}: {label}")
        _hr()
        print("Operator notes sent to the API:")
        for n in payload["operator_notes"]:
            print(f"  - {n}")

        try:
            body = _post(payload)
        except Exception as e:
            print(f"\nREQUEST FAILED: {e}")
            overall_fail += 1
            continue

        print("\nDirective interpretation (what the LLM/stub decided):")
        for d in body["directive_interpretation"]:
            print(f"  note {d['note_index']}: {d['directive_type']} "
                  f"applies={d['applies']} "
                  f"adjustment={d.get('structured_adjustment')}")

        print("\nHourly plan (24 rows):")
        print("  hr  grid    solar   action     batt_kw  energy_after")
        for row in body["hourly_plan"]:
            print(f"  {row['hour']:>2}  "
                  f"{row['grid_kwh']:>6.2f}  "
                  f"{row['solar_used_kwh']:>6.2f}  "
                  f"{row['battery_action']:>9}  "
                  f"{row['battery_kwh']:>6.2f}  "
                  f"{row['battery_energy_after_kwh']:>6.2f}")

        ours_cost = body["total_cost_bdt"]
        ours_grid = body["total_grid_kwh"]
        ours_peak = body["peak_grid_kwh"]
        exp_cost = expected["total_cost_bdt"]
        exp_grid = expected["total_grid_kwh"]
        exp_peak = expected["peak_grid_kwh"]

        print("\nTotals vs reference:")
        print(f"  cost: ours={ours_cost:9.2f}  expected={exp_cost:9.2f}  "
              f"({_check('cost', ours_cost, exp_cost, 0.5)})")
        print(f"  grid: ours={ours_grid:9.2f}  expected={exp_grid:9.2f}  "
              f"({_check('grid', ours_grid, exp_grid, 1.0)})")
        print(f"  peak: ours={ours_peak:9.2f}  expected={exp_peak:9.2f}  "
              f"({_check('peak', ours_peak, exp_peak, 1.0)})")

        ok = (
            abs(ours_cost - exp_cost) <= 0.5
            and abs(ours_grid - exp_grid) <= 1.0
            and abs(ours_peak - exp_peak) <= 1.0
        )
        if ok:
            overall_ok += 1
            print(f"\nResult: PASS")
        else:
            overall_fail += 1
            print(f"\nResult: FAIL (numbers don't match the reference)")
        print()

    _hr("=")
    print(f"Summary: {overall_ok} pass, {overall_fail} fail")
    return 0 if overall_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
