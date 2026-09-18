"""Configuration loaded from environment variables.

All sensitive values (LLM API keys) are read from env. The service will not
start in production if a required secret is missing; in offline mode the
service runs with a deterministic stub interpreter that is clearly labeled.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List, Optional

from dotenv import load_dotenv

load_dotenv()


def _csv(value: str) -> List[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


@dataclass
class Settings:
    # Provider selection: "openai" | "anthropic" | "google" | "stub"
    llm_provider: str = os.getenv("GRIDWISE_LLM_PROVIDER", "stub").lower()
    llm_api_key: Optional[str] = os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
    llm_model: str = os.getenv("GRIDWISE_LLM_MODEL", "gpt-4o-mini")
    llm_base_url: Optional[str] = os.getenv("GRIDWISE_LLM_BASE_URL")
    llm_timeout_s: float = float(os.getenv("GRIDWISE_LLM_TIMEOUT", "30"))
    llm_max_retries: int = int(os.getenv("GRIDWISE_LLM_MAX_RETRIES", "3"))

    # Optimization
    solver_time_limit_s: int = int(os.getenv("GRIDWISE_SOLVER_TIME_LIMIT", "15"))
    solver_mip_gap: float = float(os.getenv("GRIDWISE_SOLVER_MIP_GAP", "0.001"))
    solver_threads: int = int(os.getenv("GRIDWISE_SOLVER_THREADS", "1"))

    # API
    log_level: str = os.getenv("GRIDWISE_LOG_LEVEL", "INFO")
    allow_offline: bool = os.getenv("GRIDWISE_ALLOW_OFFLINE", "1") == "1"

    # Equivalence tolerance (per spec: 0.01 kWh / 0.01 BDT)
    equivalence_tol: float = float(os.getenv("GRIDWISE_TOL", "0.01"))

    notes: List[str] = field(default_factory=list)

    @property
    def has_real_llm(self) -> bool:
        return self.llm_provider != "stub" and bool(self.llm_api_key)

    def describe(self) -> dict:
        # Never return raw secrets.
        return {
            "llm_provider": self.llm_provider,
            "llm_model": self.llm_model,
            "has_real_llm": self.has_real_llm,
            "log_level": self.log_level,
            "solver_time_limit_s": self.solver_time_limit_s,
            "equivalence_tol": self.equivalence_tol,
        }


settings = Settings()
