"""The LiveKit Agent that IS the patient.

Adapted from mock-oral ExaminerAgent (orchestrator/livekit_agent.py L299-392).
The voice plumbing -- the llm_node override, _last_user_text, yielding chunks
so barge-in works -- is copied. The behaviour inside is entirely different:
this agent has no exam to run and does not speak first.
"""

from __future__ import annotations

import logging
from typing import AsyncIterable, Optional

from livekit.agents import Agent
from livekit.agents.llm import ChatContext

from app.patient_runtime import PatientRuntime

logger = logging.getLogger("patient-sim.agent")


def _last_user_text(chat_ctx: ChatContext) -> str:
    """Most recent clinician message out of the chat context.

    Copied verbatim from mock-oral livekit_agent.py L1090-1100.
    """
    for item in reversed(list(chat_ctx.items)):
        if getattr(item, "role", None) != "user":
            continue
        text = getattr(item, "text_content", None)
        if text:
            return text.strip()
    return ""


class PatientAgent(Agent):
    """STT turns arrive at llm_node; we answer as the patient and stream only
    the spoken utterance to TTS.
    """

    def __init__(self, rt: PatientRuntime) -> None:
        # `instructions` is unused -- PatientRuntime supplies the real system
        # prompt per turn. Placeholder for the framework, as in mock oral.
        super().__init__(
            instructions="A patient on the operating room table before induction."
        )
        self.rt = rt

    async def on_enter(self) -> None:
        """The patient does NOT speak first.

        This is the deliberate inversion of the mock-oral examiner, which fires
        an opener on entry. A patient lying on the table waits to be spoken to.
        Speaking first would break the illusion in the first two seconds.
        """
        logger.info(
            "Patient ready: scenario=%s title=%r -- waiting for the clinician to speak",
            self.rt.scenario.scenario_id, self.rt.scenario.title,
        )

    async def llm_node(
        self,
        chat_ctx: ChatContext,
        tools: list,
        model_settings,
    ) -> AsyncIterable[str]:
        clinician_text: Optional[str] = _last_user_text(chat_ctx)
        if not clinician_text:
            # Nothing intelligible arrived. Stay silent rather than reacting.
            logger.info("Empty clinician turn -- staying silent")
            return

        if self.rt.stream_enabled:
            async for chunk in self.rt.respond_stream(clinician_text):
                yield chunk
        else:
            spoken = await self.rt.respond(clinician_text)
            if spoken:
                yield spoken
