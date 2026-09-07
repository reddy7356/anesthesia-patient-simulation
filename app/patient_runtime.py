"""The patient turn loop.

Written new. This is where the mock-oral system had its exam orchestrator
(topic advancement, probes, phaser, scoring). None of that exists here. A
patient encounter has no agenda: the clinician speaks, the patient answers,
and that is the whole machine.

One Claude call per clinician turn. No second call for state, no separate
"reflection" pass, no critic. Every extra call is latency the resident hears
as an unreal pause (spec section 21).
"""

from __future__ import annotations

import logging
import time
from typing import AsyncIterator

from app.config import SOFT_MAX_WORDS, STREAM_TTS
from app.turn_log import TurnLog
from app.utterance_stream import UtteranceStreamer
from llm.claude_client import ClaudePatient
from patient.patient_prompt import build_system_prompt
from patient.patient_state import PatientState, parse_state_block, strip_tags_for_tts
from patient.scenario import Scenario

logger = logging.getLogger("patient-sim.runtime")

# The conversation the model sees. Trimmed to the recent past: a pre-induction
# encounter is 1-3 minutes, and durable memory lives in PatientState rather
# than in a growing transcript.
MAX_HISTORY_MESSAGES = 24


class PatientRuntime:
    def __init__(self, scenario: Scenario, turn_log: TurnLog) -> None:
        self.scenario = scenario
        self.state = PatientState()
        self.state.apply(scenario.initial_state)
        self.claude = ClaudePatient()
        self.turn_log = turn_log
        self.history: list[dict] = []
        self.stream_enabled = STREAM_TTS
        # Text input (tools.dryrun) is never a half-heard fragment, so the
        # silence exception is switched off there. Voice keeps it.
        self.allow_silence = True
        self._was_silent_last_turn = False

    # ---------------------------------------------------------------- helpers

    def _trim(self) -> None:
        if len(self.history) > MAX_HISTORY_MESSAGES:
            self.history = self.history[-MAX_HISTORY_MESSAGES:]

    def _open_turn(self, clinician_text: str | None) -> tuple[str, dict]:
        """Queue the clinician turn and build this turn's system prompt."""
        self.state.turn += 1
        before = self.state.snapshot()
        content = (clinician_text or "").strip()
        if not content:
            # on_enter: the clinician has not spoken yet.
            content = (
                "[The anesthesiologist has just walked up to the table and is "
                "looking at you. They have not said anything yet.]"
            )
        # Keep the transcript strictly alternating. If the patient said nothing
        # last turn, no assistant message was appended -- appending another user
        # message would leave two in a row, which reads to the model as a queue
        # of unanswered questions and produced a one-turn answer lag.
        if self.history and self.history[-1]["role"] == "user":
            self.history[-1]["content"] += "\n" + content
        else:
            self.history.append({"role": "user", "content": content})
        self._trim()
        prompt = build_system_prompt(
            self.scenario, self.state,
            allow_silence=self.allow_silence,
            was_silent_last_turn=self._was_silent_last_turn,
        )
        return prompt, before

    def _close_turn(
        self,
        raw: str,
        spoken: str,
        before: dict,
        clinician_text: str | None,
        llm_ms: float,
        total_ms: float,
        interrupted: bool = False,
    ) -> None:
        """Apply state, extend history, log. Never raises into the audio path."""
        delta = parse_state_block(raw)
        if delta is None and raw:
            logger.warning("No parseable <state> block this turn; state carried forward")
        self.state.apply(delta)

        self._was_silent_last_turn = not spoken
        if spoken:
            self.history.append({"role": "assistant", "content": spoken})
            self._trim()
            words = len(spoken.split())
            if words > SOFT_MAX_WORDS:
                # Not truncated -- clipping a patient mid-sentence is worse.
                # Logged so prompt drift is visible in the run record.
                logger.warning("LONG PATIENT TURN: %d words (soft max %d): %s",
                               words, SOFT_MAX_WORDS, spoken[:200])
        else:
            logger.info("PATIENT: (silent -- incomplete clinician turn or empty utterance)")

        self.turn_log.record(
            turn=self.state.turn,
            clinician_turn=clinician_text,
            patient_utterance=spoken,
            state_before=before,
            state_after=self.state.snapshot(),
            llm_latency_ms=round(llm_ms, 1),
            total_latency_ms=round(total_ms, 1),
            interrupted=interrupted,
            streaming=self.stream_enabled,
        )

    # ------------------------------------------------------------------ turns

    async def respond(self, clinician_text: str | None) -> str:
        """Batch path: the full reply is generated, then spoken."""
        t0 = time.monotonic()
        system_prompt, before = self._open_turn(clinician_text)
        logger.info("CLINICIAN: %s", (clinician_text or "(opening)")[:200])

        t_llm = time.monotonic()
        raw = await self.claude.complete(system_prompt, self.history)
        llm_ms = (time.monotonic() - t_llm) * 1000.0

        spoken = strip_tags_for_tts(raw)
        if not spoken and "<utterance>" not in raw.lower():
            # Contract violation: the model answered as prose. Never read raw
            # output aloud -- it may contain the state block.
            logger.error("CONTRACT VIOLATION: no <utterance> tag; suppressing turn")
        logger.info("PATIENT: %s", spoken or "(silence)")

        self._close_turn(raw, spoken, before, clinician_text, llm_ms,
                         (time.monotonic() - t0) * 1000.0)
        return spoken

    async def respond_stream(self, clinician_text: str | None) -> AsyncIterator[str]:
        """Streaming path: speech starts on the first completed sentence."""
        t0 = time.monotonic()
        system_prompt, before = self._open_turn(clinician_text)
        logger.info("CLINICIAN: %s", (clinician_text or "(opening)")[:200])

        streamer = UtteranceStreamer()
        raw_parts: list[str] = []
        first_chunk_ms: float | None = None

        async for delta in self.claude.stream(system_prompt, self.history):
            raw_parts.append(delta)
            chunk = streamer.feed(delta)
            if chunk:
                if first_chunk_ms is None:
                    first_chunk_ms = (time.monotonic() - t0) * 1000.0
                    logger.info("TTFB (first spoken chunk): %.0f ms", first_chunk_ms)
                yield chunk

        tail = streamer.flush()
        if tail:
            yield tail

        raw = "".join(raw_parts)
        spoken = streamer.spoken_text()
        logger.info("PATIENT: %s", spoken or "(silence)")

        self._close_turn(raw, spoken, before, clinician_text,
                         first_chunk_ms if first_chunk_ms is not None else 0.0,
                         (time.monotonic() - t0) * 1000.0)
