"""LLM-driven interpreter for operator notes.

The LLM is mandatory: it must translate free-text operator notes into one
structured directive per note. We use JSON mode / structured output where the
provider supports it. Output is *always* re-validated deterministically by
``app.guardrails`` before it ever reaches the optimizer.

Supported providers:
  * ``openai``    -> OpenAI Chat Completions with ``response_format={"type":"json_object"}``
  * ``anthropic`` -> Anthropic Messages API with a JSON tool use schema
  * ``google``    -> Google Generative AI with ``generation_config.response_schema``
  * ``stub``      -> Deterministic offline rule-based fallback (clearly labeled)

Every provider returns a ``list[dict]`` with one entry per note, in note_index
order. Each entry has the shape::

    {
      "note_index": int,
      "applies": bool,
      "directive_type": "solar_reduction" | "minimum_battery_reserve"
                      | "no_charge_window" | "no_discharge_window"
                      | "max_grid_window" | "no_op",
      "structured_adjustment": object | None,
      "explanation": str,
    }

If the LLM provider fails after all retries, we raise ``LLMError`` so the API
can return a controlled HTTP 500. We never invent directives.
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from .config import settings

log = logging.getLogger("gridwise.llm")


class LLMError(RuntimeError):
    """Raised when the LLM provider cannot produce a usable response."""


# ---------- Prompt ----------

SYSTEM_PROMPT = (
    "You are the GridWise directive interpreter for a campus microgrid. "
    "Translate each operator note into exactly ONE structured directive. "
    "Use the six supported directive types only: solar_reduction, "
    "minimum_battery_reserve, no_charge_window, no_discharge_window, "
    "max_grid_window, no_op. "
    "If a note does not affect today's 24-hour energy schedule, return "
    "directive_type = 'no_op', applies = false, structured_adjustment = null. "
    "Time windows are start-inclusive and end-exclusive, whole-hour. "
    "Example: '1 PM to 3 PM' maps to hours [13, 14] (do not include 15). "
    "solar_reduction.factor is the usable fraction remaining. "
    "An 80% reduction means factor = 0.2 (only 20% remains usable). "
    "minimum_battery_reserve REQUIRES both 'hours' and 'minimum_energy_kwh'. "
    "minimum_energy_kwh is an ABSOLUTE kWh number (JSON number type, not a "
    "string). When the note specifies a percentage like '50% of battery "
    "capacity', multiply by the battery's capacity_kwh shown later in this "
    "prompt and emit the resulting kWh as a JSON number. Example: 50% of "
    "200 kWh capacity -> {\"hours\":[18,19,20],\"minimum_energy_kwh\":100}. "
    "Never emit strings like \"50%\" or \"90 kWh\". "
    "max_grid_window REQUIRES both 'hours' and 'max_grid_kwh' (a JSON "
    "number, not a string). no_charge_window and no_discharge_window "
    "require only 'hours'. solar_reduction requires both 'hours' and "
    "'factor' (a JSON number in [0,1]). "
    "Return ONLY a JSON object (no prose, no markdown). The JSON object "
    "MUST have a top-level 'directives' array with exactly one entry per "
    "note, in note_index order. Each entry MUST have these five keys: "
    "note_index (int), applies (bool), directive_type (one of the six "
    "above), structured_adjustment (object with 'hours' plus the "
    "type-specific number field, or null for no_op), and explanation "
    "(non-empty string). "
    "Example response: "
    "{\"directives\":[{\"note_index\":0,\"applies\":true,"
    "\"directive_type\":\"minimum_battery_reserve\","
    "\"structured_adjustment\":{\"hours\":[18,19,20],\"minimum_energy_kwh\":100},"
    "\"explanation\":\"Maintain at least 100 kWh from 6 PM to 9 PM.\"}]}"
)

# JSON schema used for OpenAI ``response_format`` and equivalent constraints.
INTERPRETATION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["directives"],
    "properties": {
        "directives": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "note_index",
                    "applies",
                    "directive_type",
                    "structured_adjustment",
                    "explanation",
                ],
                "properties": {
                    "note_index": {"type": "integer", "minimum": 0},
                    "applies": {"type": "boolean"},
                    "directive_type": {
                        "type": "string",
                        "enum": [
                            "solar_reduction",
                            "minimum_battery_reserve",
                            "no_charge_window",
                            "no_discharge_window",
                            "max_grid_window",
                            "no_op",
                        ],
                    },
                    "structured_adjustment": {
                        "oneOf": [
                            {"type": "null"},
                            {
                                "type": "object",
                                "properties": {
                                    "hours": {
                                        "type": "array",
                                        "items": {"type": "integer", "minimum": 0, "maximum": 23},
                                        "minItems": 1,
                                    },
                                    "factor": {"type": "number", "minimum": 0, "maximum": 1},
                                    "minimum_energy_kwh": {"type": "number", "minimum": 0},
                                    "max_grid_kwh": {"type": "number", "minimum": 0},
                                },
                                "required": ["hours"],
                                "additionalProperties": False,
                            },
                        ]
                    },
                    "explanation": {"type": "string", "minLength": 1},
                },
            },
        }
    },
}


def _build_user_prompt(notes: List[str]) -> str:
    body = "\n".join(f"[{i}] {n}" for i, n in enumerate(notes))
    return (
        "Interpret the following operator notes. Return a JSON object whose "
        "'directives' array has exactly one entry per note, in note_index order "
        "(0, 1, 2, ...). Do not skip notes. Do not add extra notes.\n\n"
        "Notes:\n" + body
    )


# ---------- Public entry point ----------

def interpret_notes(
    notes: List[str],
    battery: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Interpret ``notes`` via the configured LLM provider.

    Returns a list of dicts (one per note, in order) suitable for the
    deterministic guardrails validator.

    ``battery`` is an optional dict (or Pydantic model with the right
    fields) carrying ``capacity_kwh`` and ``minimum_energy_kwh``. It is
    forwarded to the stub so percentage-based reserves
    ("at least 50% of the battery capacity") can be resolved into an
    absolute ``minimum_energy_kwh`` value.

    Raises ``LLMError`` when no provider can return a usable response.
    """
    if not notes:
        raise LLMError("operator_notes is empty")

    provider = settings.llm_provider
    log.info("LLM interpret via provider=%s model=%s notes=%d",
             provider, settings.llm_model, len(notes))

    if provider == "stub":
        return _stub_interpret(notes, battery)

    factory: Optional[Callable[[], Callable[[List[str]], str]]] = {
        "openai": _openai_factory,
        "anthropic": _anthropic_factory,
        "google": _google_factory,
        "groq": _groq_factory,
    }.get(provider)
    if factory is None:
        raise LLMError(f"Unknown LLM provider '{provider}'")

    if not settings.llm_api_key:
        if settings.allow_offline:
            log.warning("LLM_API_KEY missing; falling back to stub interpreter.")
            return _stub_interpret(notes, battery)
        raise LLMError("LLM_API_KEY (or provider-specific key, e.g. GROQ_API_KEY) is required for non-stub providers")

    call = factory()
    user_prompt = _build_user_prompt(notes)

    last_err: Optional[Exception] = None
    for attempt in range(1, settings.llm_max_retries + 1):
        try:
            raw = call(user_prompt)
            parsed = _extract_json(raw)
            directives = parsed.get("directives")
            if not isinstance(directives, list):
                raise LLMError("LLM response missing 'directives' array")
            if len(directives) != len(notes):
                raise LLMError(
                    f"LLM returned {len(directives)} directives but {len(notes)} notes were provided"
                )
            return directives
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            log.warning("LLM attempt %d/%d failed: %s",
                        attempt, settings.llm_max_retries, exc)
            # If the error is a 429 / OTPM / TPM rate-limit, respect the
            # provider's "Please try again in Xs" hint (capped at 65s).
            # Otherwise use a small exponential backoff.
            wait_s = _retry_after_seconds(str(exc))
            if wait_s is None:
                wait_s = min(2 ** attempt * 0.5, 4.0)
            else:
                wait_s = min(wait_s + 1.0, 65.0)
            time.sleep(wait_s)
    raise LLMError(f"LLM provider failed after {settings.llm_max_retries} retries: {last_err}")


def _retry_after_seconds(err_str: str) -> Optional[float]:
    """Parse a provider's 'Please try again in Xs' / 'Retry-After' hint.

    Returns the seconds to wait, or None if no hint could be parsed.
    """
    import re as _re
    patterns = [
        _re.compile(r"[Rr]etry[- ][Aa]fter[: ]+([0-9]+(?:\.[0-9]+)?)"),
        _re.compile(r"try again in ([0-9]+(?:\.[0-9]+)?)s"),
    ]
    for p in patterns:
        m = p.search(err_str)
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                pass
    return None


# ---------- Stub (deterministic) ----------

_HOUR_PATTERNS = [
    (re.compile(r"from\s+(\d{1,2})\s*(am|pm)\s*(?:to|until|-)\s*(\d{1,2})\s*(am|pm)", re.I), True),
    (re.compile(r"between\s+(\d{1,2})\s*(am|pm)\s*(?:and|to)\s*(\d{1,2})\s*(am|pm)", re.I), True),
    (re.compile(r"\b(\d{1,2})\s*(am|pm)\b", re.I), False),
]


def _to_24h(h: int, meridiem: str) -> int:
    h = h % 12
    if meridiem.lower() == "pm":
        h += 12
    return h % 24


# "noon" -> 12, "midnight" -> 0. The regex accepts these in place of digits.
_SPECIAL = {"noon": 12, "midnight": 0}

_MERIDIEM = r"(?:am|pm)"
_DIGIT = r"(\d{1,2})"
_SPECIAL_GROUP = r"(noon|midnight)"


def _parse_clock(text: str) -> Optional[Tuple[int, int]]:
    """Return (start_hour_24, end_hour_24) for the first matching window, or None."""
    sep = r"(?:\s*(?:to|until|and|-|–)\s*)"
    # special -> digit
    m = re.search(rf"\b(noon|midnight){sep}(\d{{1,2}})\s*(am|pm)\b", text, re.I)
    if m:
        return _SPECIAL[m.group(1).lower()], _to_24h(int(m.group(2)), m.group(3))
    # digit -> special
    m = re.search(rf"\b(\d{{1,2}})\s*(am|pm){sep}(noon|midnight)\b", text, re.I)
    if m:
        return _to_24h(int(m.group(1)), m.group(2)), _SPECIAL[m.group(3).lower()]
    # digit -> digit
    m = re.search(rf"\b(\d{{1,2}})\s*(am|pm){sep}(\d{{1,2}})\s*(am|pm)\b", text, re.I)
    if m:
        return _to_24h(int(m.group(1)), m.group(2)), _to_24h(int(m.group(3)), m.group(4))
    return None


def _parse_single(text: str) -> Optional[int]:
    m = re.search(rf"\b({_SPECIAL_GROUP})\b", text, re.I)
    if m:
        return _SPECIAL[m.group(1).lower()]
    m = re.search(rf"\b({_DIGIT})\s*{_MERIDIEM}\b", text, re.I)
    if m:
        return _to_24h(int(m.group(1)), m.group(2))
    return None


def _parse_window(text: str) -> Optional[List[int]]:
    pair = _parse_clock(text)
    if pair:
        a, b = pair
        if a == b:
            return [a]
        if b > a:
            return list(range(a, b))  # start-inclusive, end-exclusive
        return list(range(a, 24)) + list(range(0, b))
    single = _parse_single(text)
    if single is not None:
        return [single]
    return None


def _fraction_word_to_factor(text: str) -> Optional[float]:
    """Match textual fractions like 'half', 'a quarter', 'two thirds'."""
    if re.search(r"\bhalf\b|\babout half\b|\broughly half\b", text):
        return 0.5
    if re.search(r"\bquarter\b|\ba quarter\b", text):
        return 0.25
    if re.search(r"\bthird\b|\ba third\b|\bone third\b", text):
        return 1.0 / 3.0
    if re.search(r"\btwo thirds\b", text):
        return 2.0 / 3.0
    return None


def _stub_interpret(
    notes: List[str],
    battery: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Heuristic, deterministic offline interpreter used only when no real LLM
    is configured. It is intentionally conservative: if no pattern matches,
    it returns ``no_op``. It is clearly marked so guardrails / judges can
    distinguish it from a real LLM interpretation."""
    # Resolve capacity_kwh from the battery context (Pydantic model or dict).
    cap_kwh: Optional[float] = None
    if battery is not None:
        if hasattr(battery, "capacity_kwh"):
            cap_kwh = float(getattr(battery, "capacity_kwh"))
        elif isinstance(battery, dict):
            v = battery.get("capacity_kwh")
            if v is not None:
                cap_kwh = float(v)

    out: List[Dict[str, Any]] = []
    for i, note in enumerate(notes):
        n = note.lower()
        win = _parse_window(n) or []

        # ---- Solar reduction (wording variants) ----
        # Pattern A: "X% reduction" / "reduced by X%"
        m_red = re.search(
            r"(\d{1,3})\s*%\s*(?:reduction|reduced|reduce|lower|less|drop|down)",
            n,
        )
        # Pattern B: "X% usable/available/of the forecast"
        m_pct = re.search(
            r"(\d{1,3})\s*%\s*(?:usable|available|of\s*the\s*forecast|of\s*forecast|of\s*the\s*expected|of\s*expected)",
            n,
        )
        # Pattern C: "leaves/leaves about X%" / "only X%"
        m_remain = re.search(
            r"(?:usable|only|leaves?|leaves\s+about|leaves\s+roughly|remaining|remain)\s*(\d{1,3})\s*%",
            n,
        )
        # Pattern D: textual fraction ("half", "quarter", "third")
        frac = _fraction_word_to_factor(n)

        if win and any(kw in n for kw in ("solar", "panel", "panels", "inverter", "pv")):
            if m_red:
                pct = max(0, min(100, int(m_red.group(1))))
                factor = round((100 - pct) / 100.0, 4)
                out.append({
                    "note_index": i,
                    "applies": True,
                    "directive_type": "solar_reduction",
                    "structured_adjustment": {"hours": sorted(set(win)), "factor": factor},
                    "explanation": f"Stub: {pct}% solar reduction over {sorted(set(win))}.",
                })
                continue
            if m_pct:
                pct = max(0, min(100, int(m_pct.group(1))))
                factor = round(pct / 100.0, 4)
                out.append({
                    "note_index": i,
                    "applies": True,
                    "directive_type": "solar_reduction",
                    "structured_adjustment": {"hours": sorted(set(win)), "factor": factor},
                    "explanation": f"Stub: only {pct}% solar usable over {sorted(set(win))}.",
                })
                continue
            if m_remain:
                pct = max(0, min(100, int(m_remain.group(1))))
                factor = round(pct / 100.0, 4)
                out.append({
                    "note_index": i,
                    "applies": True,
                    "directive_type": "solar_reduction",
                    "structured_adjustment": {"hours": sorted(set(win)), "factor": factor},
                    "explanation": f"Stub: {pct}% of solar usable over {sorted(set(win))}.",
                })
                continue
            if frac is not None:
                factor = round(frac, 4)
                out.append({
                    "note_index": i,
                    "applies": True,
                    "directive_type": "solar_reduction",
                    "structured_adjustment": {"hours": sorted(set(win)), "factor": factor},
                    "explanation": (
                        f"Stub: usable solar reduced to "
                        f"{int(round(factor * 100))}% over {sorted(set(win))}."
                    ),
                })
                continue
            if re.search(r"(offline|down|lower|less|reduce|reduced|reduction|unavailable|outage|isolat)", n):
                out.append({
                    "note_index": i,
                    "applies": True,
                    "directive_type": "solar_reduction",
                    "structured_adjustment": {"hours": sorted(set(win)), "factor": 0.0},
                    "explanation": f"Stub: solar unavailable over {sorted(set(win))}.",
                })
                continue

        # ---- Minimum battery reserve (must come BEFORE no-charge/discharge
        # and max-grid checks so percentages and "remain" wordings match) ----
        # Pattern A: percentage of battery capacity
        #   "at least 50% of the battery capacity" /
        #   "keep 50% of the battery" / "50% of capacity" etc.
        m_pct_cap = re.search(
            r"(\d{1,3})\s*%\s*(?:of\s*the\s*)?(?:battery\s*)?(?:capacity|stored|charge|energy)",
            n,
        )
        # Pattern B: absolute kWh
        #   "keep|reserve|minimum|hold|maintain|remain|stay|require X kWh"
        m_min_kwh = re.search(
            r"(?:keep|reserve|minimum|hold|maintain|remain|stay|require|requires)\s*"
            r"(?:at\s*least\s*)?(\d+(?:\.\d+)?)\s*kwh",
            n,
        )
        if win and m_pct_cap and cap_kwh is not None:
            pct = max(0, min(100, int(m_pct_cap.group(1))))
            min_kwh = round(cap_kwh * pct / 100.0, 4)
            out.append({
                "note_index": i,
                "applies": True,
                "directive_type": "minimum_battery_reserve",
                "structured_adjustment": {"hours": sorted(set(win)), "minimum_energy_kwh": min_kwh},
                "explanation": (
                    f"Stub: {pct}% of {cap_kwh:g} kWh capacity = {min_kwh:g} kWh "
                    f"reserve over {sorted(set(win))}."
                ),
            })
            continue
        if win and m_min_kwh:
            min_kwh = float(m_min_kwh.group(1))
            out.append({
                "note_index": i,
                "applies": True,
                "directive_type": "minimum_battery_reserve",
                "structured_adjustment": {"hours": sorted(set(win)), "minimum_energy_kwh": min_kwh},
                "explanation": f"Stub: {min_kwh:g} kWh reserve over {sorted(set(win))}.",
            })
            continue

        # ---- Max grid window (kWh cap per hour) ----
        # Wordings: "must not exceed X kWh", "not exceed X kWh",
        # "no more than X kWh", "stay at or below X kWh",
        # "limit is X kWh", "grid cap/limit/max X kWh",
        # "transformer limit", "feeder ... limit", "substation ... constraint"
        m_cap = re.search(
            r"(?:grid\s*(?:cap|limit|max|maximum)\s*(?:is|of)?\s*|"
            r"limit\s*(?:is|of)?\s*|"
            r"(?:must|should|will|shall)\s*not\s*exceed\s*|"
            r"not\s*exceed\s*|"
            r"no\s*more\s*than\s*|"
            r"at\s*or\s*below\s*|"
            r"stay\s*at\s*or\s*below\s*|"
            r"cap\s*(?:of|at)?\s*)"
            r"(\d+(?:\.\d+)?)\s*kwh",
            n,
        )
        # Also: "grid import must not exceed 155 kWh" - the wording has the
        # "must not exceed" phrase followed by kWh without an intervening word.
        m_cap2 = re.search(
            r"(?:not\s*exceed|exceed|cap|limit|maximum|max)\s*(\d+(?:\.\d+)?)\s*kwh",
            n,
        )
        if win and m_cap:
            cap = float(m_cap.group(1))
            out.append({
                "note_index": i,
                "applies": True,
                "directive_type": "max_grid_window",
                "structured_adjustment": {"hours": sorted(set(win)), "max_grid_kwh": cap},
                "explanation": f"Stub: grid cap {cap:g} kWh over {sorted(set(win))}.",
            })
            continue
        if win and m_cap2:
            cap = float(m_cap2.group(1))
            out.append({
                "note_index": i,
                "applies": True,
                "directive_type": "max_grid_window",
                "structured_adjustment": {"hours": sorted(set(win)), "max_grid_kwh": cap},
                "explanation": f"Stub: grid cap {cap:g} kWh over {sorted(set(win))}.",
            })
            continue

        # ---- Battery discharging window (checked BEFORE charging because
        # the substring "no charge" matches inside "no discharge") ----
        is_discharge_phrase = (
            re.search(r"\bdischarg", n)
            or re.search(r"\brelay\s*test", n)
            or re.search(r"\bdischarging\s*(?:is\s*)?(?:disabled|offline|outage|down|unavailable|interrupted)", n)
        )
        if win and is_discharge_phrase:
            out.append({
                "note_index": i,
                "applies": True,
                "directive_type": "no_discharge_window",
                "structured_adjustment": {"hours": sorted(set(win))},
                "explanation": f"Stub: no discharge over {sorted(set(win))}.",
            })
            continue

        # ---- Battery charging window ----
        # Wordings: "no charge", "not charge", "charger isolated/disabled/offline",
        # "charging circuit unavailable", "can't charge", "battery charger will be isolated".
        # We require at least one explicit context phrase so that notes which
        # merely mention "charger" in passing don't trip the rule.
        has_charge_word = (
            re.search(r"\bcharg(?:e|ing)\b", n)
            or re.search(r"\bcharger\b", n)
        )
        is_charge_phrase = has_charge_word and (
            not re.search(r"\bdischarg", n)
        ) and (
            re.search(r"\bno\s+charge\b", n)
            or re.search(r"\bnot\s+charge\b", n)
            or re.search(r"\bcan.?t\s+charge\b", n)
            or re.search(r"\bcannot\s+charge\b", n)
            or re.search(r"\bcharger\s+(?:will\s+be|is|are|will|has\s+been|was)\s+(?:disabled|offline|outage|down|isolated|unavailable|interrupted|off)", n)
            or re.search(r"\bcharger\s+(?:disabled|offline|outage|down|isolated|unavailable|interrupted)", n)
            or re.search(r"\bcharging\s+(?:is|are|will\s+be|has\s+been|was)\s+(?:disabled|offline|outage|down|isolated|unavailable|interrupted|off)", n)
            or re.search(r"\bcharging\s+circuit\s+(?:is|will\s+be|will)\s+(?:unavailable|disabled|offline|outage|down|interrupted|isolated)", n)
            or re.search(r"\bcharging\s+circuit\b", n)
        )
        if win and is_charge_phrase:
            out.append({
                "note_index": i,
                "applies": True,
                "directive_type": "no_charge_window",
                "structured_adjustment": {"hours": sorted(set(win))},
                "explanation": f"Stub: no charge over {sorted(set(win))}.",
            })
            continue

        out.append({
            "note_index": i,
            "applies": False,
            "directive_type": "no_op",
            "structured_adjustment": None,
            "explanation": "Stub: note does not affect today's energy schedule.",
        })
    return out


# ---------- Provider factories ----------

def _openai_factory() -> Callable[[str], str]:
    import httpx

    api_key = settings.llm_api_key or ""
    base = settings.llm_base_url.rstrip("/") if settings.llm_base_url else "https://api.openai.com/v1"
    url = f"{base}/chat/completions"

    def _call(user_prompt: str) -> str:
        body = {
            "model": settings.llm_model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "gridwise_directives",
                    "schema": INTERPRETATION_SCHEMA,
                    "strict": True,
                },
            },
            "temperature": 0.0,
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        with httpx.Client(timeout=settings.llm_timeout_s) as client:
            r = client.post(url, headers=headers, json=body)
        if r.status_code >= 400:
            raise LLMError(f"OpenAI HTTP {r.status_code}: {r.text[:200]}")
        data = r.json()
        return data["choices"][0]["message"]["content"]

    return _call


def _anthropic_factory() -> Callable[[str], str]:
    import httpx

    api_key = settings.llm_api_key or ""
    base = settings.llm_base_url.rstrip("/") if settings.llm_base_url else "https://api.anthropic.com/v1"
    url = f"{base}/messages"

    tool = {
        "name": "emit_directives",
        "description": "Emit GridWise directive interpretations as JSON.",
        "input_schema": INTERPRETATION_SCHEMA,
    }

    def _call(user_prompt: str) -> str:
        body = {
            "model": settings.llm_model,
            "max_tokens": 2048,
            "system": SYSTEM_PROMPT,
            "tools": [tool],
            "tool_choice": {"type": "tool", "name": "emit_directives"},
            "messages": [{"role": "user", "content": user_prompt}],
            "temperature": 0.0,
        }
        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        with httpx.Client(timeout=settings.llm_timeout_s) as client:
            r = client.post(url, headers=headers, json=body)
        if r.status_code >= 400:
            raise LLMError(f"Anthropic HTTP {r.status_code}: {r.text[:200]}")
        data = r.json()
        for block in data.get("content", []):
            if block.get("type") == "tool_use" and block.get("name") == "emit_directives":
                return json.dumps(block["input"])
        raise LLMError("Anthropic response had no tool_use block")

    return _call


def _google_factory() -> Callable[[str], str]:
    import httpx

    api_key = settings.llm_api_key or ""
    base = settings.llm_base_url.rstrip("/") if settings.llm_base_url else "https://generativelanguage.googleapis.com/v1beta"
    url = f"{base}/models/{settings.llm_model}:generateContent"

    def _call(user_prompt: str) -> str:
        body = {
            "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
            "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseSchema": INTERPRETATION_SCHEMA,
                "temperature": 0.0,
            },
        }
        with httpx.Client(timeout=settings.llm_timeout_s) as client:
            r = client.post(url, params={"key": api_key}, json=body)
        if r.status_code >= 400:
            raise LLMError(f"Google HTTP {r.status_code}: {r.text[:200]}")
        data = r.json()
        parts = data["candidates"][0]["content"]["parts"]
        return "".join(p.get("text", "") for p in parts)

    return _call


def _groq_factory() -> Callable[[str], str]:
    """Groq Cloud provider (OpenAI-compatible Chat Completions API).

    Reads ``GROQ_API_KEY`` from the environment (falling back to the generic
    ``LLM_API_KEY`` set in ``Settings.groq_api_key``). The base URL defaults
    to Groq's public endpoint but can be overridden via ``GRIDWISE_LLM_BASE_URL``
    for proxies or self-hosted OpenAI-compatible gateways.

    Uses ``response_format={"type":"json_object"}`` (Groq's strict JSON mode),
    which requires the word "JSON" to appear in the system prompt — the
    strengthened ``SYSTEM_PROMPT`` already satisfies that.
    """
    import httpx

    api_key = settings.groq_api_key or settings.llm_api_key or ""
    base = (
        settings.llm_base_url.rstrip("/")
        if settings.llm_base_url
        else "https://api.groq.com/openai/v1"
    )
    url = f"{base}/chat/completions"

    def _call(user_prompt: str) -> str:
        body = {
            "model": settings.llm_model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.0,
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        with httpx.Client(timeout=settings.llm_timeout_s) as client:
            r = client.post(url, headers=headers, json=body)
        if r.status_code >= 400:
            raise LLMError(f"Groq HTTP {r.status_code}: {r.text[:300]}")
        data = r.json()
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as exc:
            raise LLMError(f"Groq response missing message content: {exc}")

    return _call


# ---------- JSON extraction helper ----------

def _extract_json(raw: str) -> Dict[str, Any]:
    raw = (raw or "").strip()
    try:
        return json.loads(raw)
    except Exception:
        pass
    # Look for the first balanced JSON object.
    start = raw.find("{")
    if start < 0:
        raise LLMError("LLM response did not contain JSON")
    depth = 0
    for i in range(start, len(raw)):
        ch = raw[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(raw[start:i + 1])
    raise LLMError("LLM response had unbalanced JSON braces")