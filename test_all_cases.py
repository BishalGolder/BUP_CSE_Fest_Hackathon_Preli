import json
import time
import urllib.request
import urllib.error

SAMPLE_FILE = "BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json"
API_URL = "http://127.0.0.1:8000/optimize-energy"
DELAY_SECONDS = 3  # Delay between requests to avoid Groq rate limits

def load_sample_cases(filepath):
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    if isinstance(data, list):
        return data
    
    if isinstance(data, dict):
        for key in ["sample_cases", "cases", "scenarios", "data"]:
            if key in data and isinstance(data[key], list):
                return data[key]
        if all(isinstance(v, dict) for v in data.values()):
            return list(data.values())
            
    return []

def run_batch_tests():
    try:
        cases = load_sample_cases(SAMPLE_FILE)
        if not cases:
            print(f"Error: Could not parse scenario objects from '{SAMPLE_FILE}'.")
            return
    except FileNotFoundError:
        print(f"Error: File '{SAMPLE_FILE}' not found in current directory.")
        return

    print(f"Found {len(cases)} test case(s) in sample file.\n" + "=" * 50)

    for idx, case in enumerate(cases, start=1):
        scenario_id = case.get("scenario_id") or case.get("input", {}).get("scenario_id", f"Case-{idx}")
        print(f"[{idx}/{len(cases)}] Testing Scenario: {scenario_id}...")

        data_bytes = json.dumps(case).encode('utf-8')
        req = urllib.request.Request(
            API_URL, 
            data=data_bytes, 
            headers={'Content-Type': 'application/json'}
        )

        try:
            start_time = time.time()
            with urllib.request.urlopen(req) as response:
                res_data = json.loads(response.read().decode('utf-8'))
                elapsed = time.time() - start_time
                
                print(f"   Status: 200 OK ({elapsed:.2f}s)")
                
                # Check for expected output comparisons
                actual_cost = res_data.get('total_cost_bdt')
                expected_output = case.get('expected_output', {})
                expected_cost = expected_output.get('total_cost_bdt')
                
                if expected_cost is not None:
                    diff = abs(actual_cost - expected_cost)
                    status_flag = "MATCH" if diff < 0.01 else f"MISMATCH (Diff: {diff:.2f} BDT)"
                    print(f"   Total Cost: {actual_cost} BDT (Expected: {expected_cost} BDT) -> {status_flag}")
                else:
                    print(f"   Total Cost: {actual_cost} BDT")

                print("   Parsed Directives:")
                for directive in res_data.get("directive_interpretation", []):
                    print(f"    - Type: {directive.get('directive_type')} | Applies: {directive.get('applies')}")
                    if directive.get('structured_adjustment'):
                        print(f"      Adjustment: {directive.get('structured_adjustment')}")
                print("-" * 50)

        except urllib.error.HTTPError as e:
            error_body = e.read().decode('utf-8')
            print(f"   HTTP Error {e.code}: {error_body}")
            print("-" * 50)
        except Exception as e:
            print(f"   Execution Error: {str(e)}")
            print("-" * 50)

        if idx < len(cases):
            time.sleep(DELAY_SECONDS)

if __name__ == "__main__":
    run_batch_tests()