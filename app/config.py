"""Configuration + environment for the patient simulator.

Copied in structure from the protected mock-oral system
(orchestrator/livekit_agent.py L46-115, L1142-1183) and re-namespaced.

Every option here is PSIM_* on purpose. The mock-oral system uses ORCH_*;
keeping the namespaces disjoint means a stale `export ORCH_STEM=...` in a
shell can never steer a patient-simulation run, and vice versa.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

WORKSPACE = Path(os.environ.get("PSIM_WORKSPACE", Path(__file__).resolve().parent.parent))

# Load .env before reading any credential.
try:
    from dotenv import load_dotenv

    load_dotenv(WORKSPACE / ".env")
except Exception:  # python-dotenv optional; fall back to ambient env
    pass

logger = logging.getLogger("patient-sim")

# --- LLM -------------------------------------------------------------------
MODEL = os.environ.get("PSIM_MODEL", "claude-opus-4-8")

# A patient answer is a handful of words plus a small state block. 1500 tokens
# (the examiner's budget) is wasted latency here.
MAX_TOKENS = int(os.environ.get("PSIM_MAX_TOKENS", "400"))

# --- Scenario --------------------------------------------------------------
SCENARIO_ID = os.environ.get("PSIM_SCENARIO", "case_001")
SCENARIOS_DIR = Path(os.environ.get("PSIM_SCENARIOS_DIR", WORKSPACE / "scenarios"))

# --- Worker ----------------------------------------------------------------
# Deliberately NOT 8081 — that is the mock-oral worker port. Both systems can
# be running at once without fighting over the health-check socket.
WORKER_PORT = int(os.environ.get("PSIM_PORT", "8091"))

# LiveKit 1.6 skips reply generation entirely when AgentSession has no `llm`
# (agent_activity: `elif self.llm is None: return  # skip response`).
# PatientAgent.llm_node fully overrides the LLM step and never calls this
# instance -- it exists only to satisfy that gate so clinician turns reach
# llm_node. The model id is irrelevant since it is never invoked.
# DO NOT REMOVE. Carried over verbatim from the proven mock-oral system.
PLACEHOLDER_LLM_MODEL = os.environ.get("PSIM_PLACEHOLDER_LLM", "llama-3.3-70b-versatile")

# --- Voice -----------------------------------------------------------------
# Fallback only. scenarios/<id>/voice.json is authoritative for a given patient.
DEFAULT_VOICE_ID = os.environ.get("PSIM_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")

STREAM_TTS = os.environ.get("PSIM_STREAM_TTS", "1").strip() in ("1", "true", "True")

# --- Logging ---------------------------------------------------------------
LOG_DIR = Path(os.environ.get("PSIM_LOG_DIR", WORKSPACE / "logs"))

# --- Guards ----------------------------------------------------------------
# Soft ceiling. We never truncate a patient mid-sentence -- a clipped answer is
# worse than a long one -- but every breach is logged so prompt drift shows up
# in the run log instead of only in the room.
SOFT_MAX_WORDS = int(os.environ.get("PSIM_SOFT_MAX_WORDS", "45"))

REQUIRED_ENV = (
    "ANTHROPIC_API_KEY",
    "GROQ_API_KEY",
    "ELEVENLABS_API_KEY",
)


def require_env() -> None:
    missing = [k for k in REQUIRED_ENV if not os.environ.get(k)]
    if missing:
        raise RuntimeError(
            "Missing required environment variables: "
            + ", ".join(missing)
            + f"\nSet them in {WORKSPACE / '.env'} (copy .env.example)."
        )


_FILE_LOG_INSTALLED = False


def setup_file_logging() -> Path | None:
    """Per-run DEBUG file handler. Never rely on terminal scrollback."""
    global _FILE_LOG_INSTALLED
    if _FILE_LOG_INSTALLED:
        return None
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        log_path = LOG_DIR / f"patient_{int(time.time())}.log"
        fh = logging.FileHandler(log_path, mode="w", encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(
            logging.Formatter("%(asctime)s - %(levelname)s %(name)s - %(message)s")
        )
        root = logging.getLogger()
        if root.level == logging.NOTSET or root.level > logging.DEBUG:
            root.setLevel(logging.DEBUG)
        root.addHandler(fh)
        logger.info("File logging active -> %s", log_path)
        _FILE_LOG_INSTALLED = True
        return log_path
    except Exception as e:  # never let logging setup break a simulation
        logger.warning("Could not install file log handler: %s", e)
        return None
