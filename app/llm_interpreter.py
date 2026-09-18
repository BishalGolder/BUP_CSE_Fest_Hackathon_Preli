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
    "Use the six supported directive types only. "
    "If a note does not affect today's 24-hour energy schedule, return "
    "directive_type = 'no_op', applies = false, structured_adjustment = null. "
    "Time windows are start-inclusive and end-exclusive, whole-hour. "
    "Example: '1 PM to 3 PM' maps to hours [13, 14] (do not include 15). "
    "solar_reduction.factor is the usable fraction remaining. "
    "An 80% reduction means factor = 0.2 (only 20% remains usable). "
    "Return JSON only, matching the required schema exactly."
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

def interpret_notes(notes: List[str]) -> List[Dict[str, Any]]:
    """Interpret ``notes`` via the configured LLM provider.

    Returns a list of dicts (one per note, in order) suitable for the
    deterministic guardrails validator.

    Raises ``LLMError`` when no provider can return a usable response.
    """
    if not notes:
        raise LLMError("operator_notes is empty")

    provider = settings.llm_provider
    log.info("LLM interpret via provider=%s model=%s notes=%d",
             provider, settings.llm_model, len(notes))

    if provider == "stub":
        return _stub_interpret(notes)

    factory: Optional[Callable[[], Callable[[List[str]], str]]] = {
        "openai": _openai_factory,
        "anthropic": _anthropic_factory,
        "google": _google_factory,
    }.get(provider)
    if factory is None:
        raise LLMError(f"Unknown LLM provider '{provider}'")

    if not settings.llm_api_key:
        if settings.allow_offline:
            log.warning("LLM_API_KEY missing; falling back to stub interpreter.")
            return _stub_interpret(notes)
        raise LLMError("LLM_API_KEY is required for non-stub providers")

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
            time.sleep(min(2 ** attempt * 0.5, 4.0))
    raise LLMError(f"LLM provider failed after {settings.llm_max_retries} retries: {last_err}")


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


def _stub_interpret(notes: List[str]) -> List[Dict[str, Any]]:
    """Heuristic, deterministic offline interpreter used only when no real LLM
    is configured. It is intentionally conservative: if no pattern matches,
    it returns ``no_op``. It is clearly marked so guardrails / judges can
    distinguish it from a real LLM interpretation."""
    out: List[Dict[str, Any]] = []
    for i, note in enumerate(notes):
        n = note.lower()
        win = _parse_window(n) or []

        # Solar reduction (wording variants)
        m_red = re.search(r"(\d{1,3})\s*%\s*(?:reduction|reduced|reduce|lower|less|drop|down)", n)
        m_pct = re.search(r"(\d{1,3})\s*%\s*(?:usable|available|of\s*the\s*forecast|of\s*forecast|of\s*the\s*expected|of\s*expected)", n)
        m_remain = re.search(r"(?:usable|only|leaves?|remaining|remain)\s*(\d{1,3})\s*%", n)
        if m_red and win:
            pct = max(0, min(100, int(m_red.group(1))))
            factor = round((100 - pct) / 100.0, 4)
            out.append({
                "note_index": i,
                "applies": True,
                "directive_type": "solar_reduction",
                "structured_adjustment": {"hours": sorted(set(win)), "factor": factor},
                "explanation": f"Stub interpreter: {pct}% solar reduction over {win}",
            })
            continue
        if m_pct and win:
            pct = max(0, min(100, int(m_pct.group(1))))
            factor = round(pct / 100.0, 4)
            out.append({
                "note_index": i,
                "applies": True,
                "directive_type": "solar_reduction",
                "structured_adjustment": {"hours": sorted(set(win)), "factor": factor},
                "explanation": f"Stub interpreter: only {pct}% solar usable over {win}",
            })
            continue
        if m_remain and win:
            pct = max(0, min(100, int(m_remain.group(1))))
            factor = round(pct / 100.0, 4)
            out.append({
                "note_index": i,
                "applies": True,
                "directive_type": "solar_reduction",
                "structured_adjustment": {"hours": sorted(set(win)), "factor": factor},
                "explanation": f"Stub interpreter: {pct}% of solar usable over {win}",
            })
            continue
        if ("solar" in n or "panel" in n or "inverter" in n) and win and re.search(r"(offline|down|lower|less|reduce|reduced|reduction|unavailable)", n):
            out.append({
                "note_index": i,
                "applies": True,
                "directive_type": "solar_reduction",
                "structured_adjustment": {"hours": sorted(set(win)), "factor": 0.0},
                "explanation": f"Stub interpreter: solar unavailable over {win}",
            })
            continue

        # Battery charging window
        if win and re.search(r"(no|neon)?\s*charge|isolated.*charger|charger.*isolat|maintenance.*charger|charger.*maintenance|can.?t charge|cannot charge|charging.*(offline|outage|isolated|down|disabled|unavailable|interrupted)", n):
            out.append({
                "note_index": i,
                "applies": True,
                "directive_type": "no_charge_window",
                "structured_adjustment": {"hours": sorted(set(win))},
                "explanation": f"Stub interpreter: no charge over {win}",
            })
            continue

        # Battery discharging window
        if win and re.search(r"(no|neon)?\s*discharge|relay test|relay testing|inverter.*test|discharging.*(offline|outage|down|disabled|unavailable|interrupted)", n):
            out.append({
                "note_index": i,
                "applies": True,
                "directive_type": "no_discharge_window",
                "structured_adjustment": {"hours": sorted(set(win))},
                "explanation": f"Stub interpreter: no discharge over {win}",
            })
            continue

        # Max grid window
        m_cap = re.search(r"(?:grid\s*(?:cap|limit|max|maximum)|no\s*more\s*than)\s*(\d+(?:\.\d+)?)\s*kwh", n)
        if m_cap and win:
            out.append({
                "note_index": i,
                "applies": True,
                "directive_type": "max_grid_window",
                "structured_adjustment": {"hours": sorted(set(win)), "max_grid_kwh": float(m_cap.group(1))},
                "explanation": f"Stub interpreter: grid cap {m_cap.group(1)} kWh over {win}",
            })
            continue

        # Minimum battery reserve
        m_min = re.search(r"(?:keep|reserve|minimum|hold|maintain)\s*(\d+(?:\.\d+)?)\s*kwh", n)
        if m_min and win:
            out.append({
                "note_index": i,
                "applies": True,
                "directive_type": "minimum_battery_reserve",
                "structured_adjustment": {"hours": sorted(set(win)), "minimum_energy_kwh": float(m_min.group(1))},
                "explanation": f"Stub interpreter: min reserve {m_min.group(1)} kWh over {win}",
            })
            continue

        out.append({
            "note_index": i,
            "applies": False,
            "directive_type": "no_op",
            "structured_adjustment": None,
            "explanation": "Stub interpreter: note does not affect today's energy schedule.",
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