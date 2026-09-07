"""Per-turn structured log record (spec section 30).

Written as one JSON line per turn next to the run log so a session can be
replayed, diffed, or fed to the offline observer without parsing prose.
Secrets are never logged -- only text that was spoken or heard.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

logger = logging.getLogger("patient-sim.turnlog")


class TurnLog:
    def __init__(self, log_dir: Path, scenario_id: str) -> None:
        self.path: Path | None = None
        self.scenario_id = scenario_id
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            self.path = log_dir / f"turns_{scenario_id}_{int(time.time())}.jsonl"
        except Exception as e:
            logger.warning("Turn log unavailable: %s", e)

    def record(self, **fields) -> None:
        rec = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "scenario_id": self.scenario_id,
            "agent_mode": "patient_simulation",
            **fields,
        }
        logger.debug("TURN %s", json.dumps(rec, ensure_ascii=False))
        if not self.path:
            return
        try:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.warning("Could not append turn record: %s", e)
