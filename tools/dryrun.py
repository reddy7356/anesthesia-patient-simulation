"""Talk to the patient by typing. No LiveKit, no microphone, no TTS.

    .venv/bin/python -m tools.dryrun case_001

This is where humanization is validated cheaply: answer length, memory,
paraphrase consistency, the knowledge boundary, and whether it sounds like a
person. Only after it reads right here is it worth booking the sim room.

Type a clinician line and press enter. Ctrl-D or 'quit' to end.
Type '/state' to inspect the hidden state (never spoken in a real run).
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys

from app.config import LOG_DIR, SCENARIOS_DIR, require_env, setup_file_logging
from app.patient_runtime import PatientRuntime
from app.turn_log import TurnLog
from patient.scenario import load_scenario


async def run(scenario_id: str) -> int:
    require_env()
    setup_file_logging()
    logging.getLogger().setLevel(logging.WARNING)  # keep the transcript readable

    scenario = load_scenario(scenario_id, SCENARIOS_DIR)
    rt = PatientRuntime(scenario, TurnLog(LOG_DIR, scenario.scenario_id))
    rt.stream_enabled = False   # batch path: one clean line per turn
    rt.allow_silence = False    # you press enter; nothing here is a fragment

    print(f"\n  {scenario.title}")
    print("  The patient is on the table. Speak first.")
    print("  ('/state' to inspect hidden state, 'quit' to end)\n")

    while True:
        try:
            line = input("  DOCTOR  > ").strip()
        except EOFError:
            print()
            break
        if not line:
            continue
        if line.lower() in ("quit", "exit"):
            break
        if line == "/state":
            print("  " + json.dumps(rt.state.snapshot(), indent=2).replace("\n", "\n  "))
            continue

        spoken = await rt.respond(line)
        print(f"  PATIENT > {spoken or '(silence)'}\n")

    print("\n  Encounter ended.")
    if rt.turn_log.path:
        print(f"  Turn log: {rt.turn_log.path}")
    return 0


if __name__ == "__main__":
    sid = sys.argv[1] if len(sys.argv) > 1 else "case_001"
    raise SystemExit(asyncio.run(run(sid)))
