import os
import json
from typing import List
from groq import Groq
from dotenv import load_dotenv
from app.schemas import DirectiveInterpretation, StructuredAdjustment

load_dotenv()

client = Groq(api_key=os.getenv("GROQ_API_KEY"))

SYSTEM_PROMPT = """You are an expert energy grid parser. Analyze operator notes for a 24-hour campus schedule (hours 0 to 23) and return structured directives.

Return a JSON object with a single key "directives" containing a list of directive objects.

For each note, construct an object with:
- "note": The original note text.
- "applies": boolean (true if directive modifies today's plan, false for no_op).
- "directive_type": One of ["solar_reduction", "no_charge_window", "minimum_battery_reserve", "no_discharge_window", "max_grid_window", "no_op"].
- "structured_adjustment": null if applies=false, else an object containing applicable keys:
  - "hours": list of integer hours (0 to 23).
  - "factor": float (for solar_reduction: fraction of available solar remaining, e.g., 80% reduction -> 0.2 factor).
  - "minimum_energy_kwh": float (for minimum_battery_reserve).
  - "max_grid_kwh": float (for max_grid_window).

Directive-Specific Rules:

- For solar_reduction: express "factor" as the remaining fraction of solar. An 80% reduction -> factor = 0.2. A 50% reduction -> factor = 0.5.

- For minimum_battery_reserve: Express minimum_energy_kwh as an absolute kWh value. If a percentage is specified (e.g., "maintain 50% reserve"), calculate percentage * capacity_kwh if capacity is known, or return the decimal ratio (0.5).

- For max_grid_window: express "max_grid_kwh" as an absolute kWh cap on grid import during those hours.

- For no_charge_window / no_discharge_window: only "hours" is required; no numeric adjustment field.

Time Window Mapping Rules:
- Windows are start-inclusive and end-exclusive.
- "12 PM to 2 PM" -> hours: [12, 13]
- "1 PM to 3 PM" -> hours: [13, 14]
- "2 AM to 5 AM" -> hours: [2, 3, 4]
- "6 PM to 9 PM" -> hours: [18, 19, 20]

If a note is unrelated to operations or describes a future/past day, set applies=false, directive_type="no_op", structured_adjustment=null.
"""

def parse_operator_notes(notes: List[str]) -> List[DirectiveInterpretation]:
    if not notes:
        return []

    user_prompt = "Parse the following operator notes:\n" + "\n".join([f"- {note}" for note in notes])

    response = client.chat.completions.create(
        model="openai/gpt-oss-120b",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt}
        ],
        response_format={"type": "json_object"}
    )

    content = response.choices[0].message.content
    parsed_json = json.loads(content)
    directives_raw = parsed_json.get("directives", [])

    interpretations = []
    for item in directives_raw:
        adj_data = item.get("structured_adjustment")
        adjustment = StructuredAdjustment(**adj_data) if adj_data else None

        directive = DirectiveInterpretation(
            note=item.get("note", ""),
            applies=item.get("applies", False),
            directive_type=item.get("directive_type", "no_op"),
            structured_adjustment=adjustment
        )
        interpretations.append(directive)

    return interpretations