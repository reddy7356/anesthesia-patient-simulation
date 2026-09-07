"""Worker entrypoint for the patient simulation.

Adapted from mock-oral livekit_agent.py entrypoint() L1106-1141 and the
`cli.run_app` tail L1184. The session wiring is copied deliberately
component-for-component: Groq STT, silero VAD turn detection, the placeholder
Groq LLM that keeps LiveKit's reply gate open, ElevenLabs TTS.

Run:
    .venv/bin/python -m app.livekit_agent dev        (simulation room)
    .venv/bin/python -m app.livekit_agent console    (laptop test)

Prefer ./run_patient_sim.sh -- it forces credentials from this project's .env.
"""

from __future__ import annotations

import logging

from livekit.agents import AgentSession, JobContext, WorkerOptions, cli
from livekit.plugins import silero

from app.config import (
    LOG_DIR,
    PLACEHOLDER_LLM_MODEL,
    SCENARIO_ID,
    SCENARIOS_DIR,
    STREAM_TTS,
    WORKER_PORT,
    require_env,
    setup_file_logging,
)
from app.patient_agent import PatientAgent
from app.patient_runtime import PatientRuntime
from app.turn_log import TurnLog
from mannequin.livekit_room import LiveKitRoomMannequin
from patient.scenario import load_scenario

logger = logging.getLogger("patient-sim")


async def entrypoint(ctx: JobContext) -> None:
    require_env()
    setup_file_logging()

    # Load the scenario BEFORE connecting: a bad case id should fail on the
    # console, not after a resident is already standing at the table.
    scenario = load_scenario(SCENARIO_ID, SCENARIOS_DIR)
    logger.info(
        "PATIENT SIM: scenario=%s title=%r streaming=%s",
        scenario.scenario_id, scenario.title, STREAM_TTS,
    )
    print(f"\n  Patient: {scenario.title}  [{scenario.scenario_id}]")
    print("  Verify this is the case you intend to run before speaking.\n")

    mannequin = LiveKitRoomMannequin(ctx)
    await mannequin.connect()

    runtime = PatientRuntime(scenario, TurnLog(LOG_DIR, scenario.scenario_id))

    # Imported late so a missing GROQ/ELEVENLABS key fails in require_env()
    # with a readable message rather than a KeyError at import time.
    from voice.stt import build_placeholder_llm, build_stt
    from voice.tts import build_tts

    session = AgentSession(
        stt=build_stt(),
        # Never invoked -- PatientAgent.llm_node owns the LLM step. Without
        # some llm= on the session LiveKit transcribes the clinician and then
        # returns without ever calling llm_node. DO NOT REMOVE.
        llm=build_placeholder_llm(PLACEHOLDER_LLM_MODEL),
        tts=build_tts(scenario.voice),
        vad=silero.VAD.load(),
    )

    await session.start(agent=PatientAgent(runtime), room=ctx.room)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint, port=WORKER_PORT))
