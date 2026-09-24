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

import asyncio
import logging
import os
import time
from typing import AsyncIterator

from app.config import SOFT_MAX_WORDS, STREAM_TTS
from app.turn_log import TurnLog
from app.utterance_stream import UtteranceStreamer
from llm.claude_client import FALLBACK, RETRY_NUDGE, ClaudePatient
from patient.patient_prompt import build_system_blocks
from patient.patient_state import (
    PatientState, parse_state_block, salvage_untagged, strip_tags_for_tts,
)
from patient.scenario import Scenario

logger = logging.getLogger("patient-sim.runtime")

# The conversation the model sees. Trimmed to the recent past: a pre-induction
# encounter is 1-3 minutes, and durable memory lives in PatientState rather
# than in a growing transcript.
MAX_HISTORY_MESSAGES = 24

# How long a new turn will wait for an interrupted turn's drain to finish.
# This is dead air the clinician hears, so it is a budget, not a guarantee.
SETTLE_TIMEOUT = float(os.environ.get("PSIM_SETTLE_TIMEOUT", "0.4"))


class PatientRuntime:
    def __init__(self, scenario: Scenario, turn_log: TurnLog) -> None:
        self.scenario = scenario
        self.state = PatientState()
        self.state.apply(scenario.initial_state)
        self.claude = ClaudePatient()
        self.turn_log = turn_log
        self.history: list[dict] = []
        self.last_raw: str = ""
        self.stream_enabled = STREAM_TTS
        # Text input (tools.dryrun) is never a half-heard fragment, so the
        # silence exception is switched off there. Voice keeps it.
        self.allow_silence = True
        self._was_silent_last_turn = False
        # Strong refs to in-flight drain tasks so they are not GC'd mid-flight.
        self._pending: set = set()

    # ---------------------------------------------------------------- helpers

    async def _settle_pending(self, timeout: float = SETTLE_TIMEOUT) -> None:
        """Let an interrupted turn's drain finish before opening the next one.

        On barge-in the drain task outlives the generator. If a new turn opens
        first, that drain closes into the WRONG turn -- duplicate turn numbers,
        an assistant message appended after the next clinician line, and stale
        state applied over fresh state.

        The timeout is deliberately SHORT and is a latency budget, not a
        correctness guarantee. This wait sits directly in front of the
        clinician's next answer: at 2.5s it produced a measured llm_ttft of
        3828ms -- four seconds of dead air after an interruption, which is
        exactly the failure this system exists to avoid. An interrupted turn's
        <state> block is worth a fraction of a second, never a stalled room.
        Losing it costs one turn of memory; the next turn re-reports what
        matters, because the lists are cumulative.
        """
        if not self._pending:
            return
        t0 = time.monotonic()
        _, still = await asyncio.wait(list(self._pending), timeout=timeout)
        for task in still:
            task.cancel()
        waited = (time.monotonic() - t0) * 1000.0
        if waited > 50:
            logger.info("settled previous turn in %.0f ms (%d cancelled)",
                        waited, len(still))
        self._pending.clear()

    def _trim(self) -> None:
        if len(self.history) > MAX_HISTORY_MESSAGES:
            self.history = self.history[-MAX_HISTORY_MESSAGES:]

    def _open_turn(self, clinician_text: str | None) -> tuple[str, dict]:
        """Queue the clinician turn and build this turn's system prompt."""
        self.state.turn += 1
        self.state.tick_induction()
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
        # Two blocks, not one string: the first is byte-identical every turn
        # and gets a prompt-cache breakpoint in the client. See
        # patient_prompt.build_system_blocks.
        prompt = build_system_blocks(
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
            # Store the FULL contract, both blocks. Storing bare text taught the
            # model its replies needed no tags; storing the utterance tag alone
            # then taught it the <state> block was optional, and the patient's
            # memory stopped updating on most turns. The in-context examples must
            # look exactly like what we want back.
            low = raw.lower()
            if "<utterance>" in low and "<state>" in low:
                content = raw.strip()[:1500]
            else:
                content = f"<utterance>{spoken}</utterance>\n<state>{{}}</state>"
            self.history.append({"role": "assistant", "content": content})
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
            turn=before.get("turn", self.state.turn),
            clinician_turn=clinician_text,
            patient_utterance=spoken,
            state_before=before,
            state_after=self.state.snapshot(),
            llm_latency_ms=round(llm_ms, 1),
            total_latency_ms=round(total_ms, 1),
            interrupted=interrupted,
            streaming=self.stream_enabled,
            stop_reason=self.claude.last_stop_reason,
            raw_response=(raw[:1200] if not spoken else None),
        )

    # ------------------------------------------------------------------ turns

    async def respond(self, clinician_text: str | None) -> str:
        """Batch path: the full reply is generated, then spoken."""
        await self._settle_pending()
        t0 = time.monotonic()
        system_prompt, before = self._open_turn(clinician_text)
        logger.info("CLINICIAN: %s", (clinician_text or "(opening)")[:200])

        t_llm = time.monotonic()
        raw = await self.claude.complete(system_prompt, self.history)
        llm_ms = (time.monotonic() - t_llm) * 1000.0

        spoken = strip_tags_for_tts(raw)
        self.last_raw = raw
        # A well-formed but EMPTY <utterance> is deliberate -- the patient is
        # asleep, or the turn was a fragment. That is not a contract failure and
        # must not trigger a retry.
        deliberate_silence = "<utterance>" in raw.lower()
        if not spoken and not deliberate_silence:
            spoken = salvage_untagged(raw)
            if spoken:
                logger.warning("SALVAGED untagged reply (no markup present): %s", spoken[:120])
        if not spoken and not deliberate_silence and raw and raw != FALLBACK:
            # Contract miss with markup in it -- unsafe to salvage, so ask once
            # more with an explicit correction. Costs latency only on failure.
            logger.warning("CONTRACT MISS -- retrying once with an explicit nudge")
            # Append the nudge to the VOLATILE tail so the cached prefix is
            # still matched on the retry.
            static, tail = system_prompt
            raw = await self.claude.complete((static, tail + RETRY_NUDGE), self.history)
            spoken = strip_tags_for_tts(raw) or salvage_untagged(raw)
            self.last_raw = raw
        if not spoken and deliberate_silence:
            logger.info("PATIENT: (deliberate silence -- asleep or fragment)")
        elif not spoken:
            # Otherwise silence IS a contract failure. Record exactly what came
            # back so it can be diagnosed from the log alone.
            logger.error(
                "EMPTY UTTERANCE  stop_reason=%s  has_open_tag=%s  len=%d\n"
                "  RAW>>> %s <<<",
                self.claude.last_stop_reason,
                "<utterance>" in raw.lower(), len(raw), raw[:800],
            )
        logger.info("PATIENT: %s", spoken or "(silence)")

        self._close_turn(raw, spoken, before, clinician_text, llm_ms,
                         (time.monotonic() - t0) * 1000.0)
        return spoken

    async def respond_stream(self, clinician_text: str | None) -> AsyncIterator[str]:
        """Streaming path: speech starts on the first completed sentence.

        The Anthropic stream is drained by a BACKGROUND task rather than by the
        consumer. LiveKit pulls this generator lazily and stops pulling once it
        has the audio it needs -- and on barge-in it closes the generator
        outright. Because <state> arrives *after* </utterance>, a consumer-driven
        loop silently lost the state block on most turns, and the patient's
        memory quietly stopped updating. The drain task always runs to
        completion and always closes the turn, whatever the consumer does.
        """
        await self._settle_pending()
        t0 = time.monotonic()
        system_prompt, before = self._open_turn(clinician_text)
        logger.info("CLINICIAN: %s", (clinician_text or "(opening)")[:200])

        streamer = UtteranceStreamer()
        raw_parts: list[str] = []
        queue: asyncio.Queue[str | None] = asyncio.Queue()
        first_ms: list[float] = []
        consumer_left = {"yes": False}

        async def drain() -> None:
            try:
                async for delta in self.claude.stream(system_prompt, self.history):
                    raw_parts.append(delta)
                    chunk = streamer.feed(delta)
                    if chunk:
                        if not first_ms:
                            first_ms.append((time.monotonic() - t0) * 1000.0)
                            logger.info("TTFB (first spoken chunk): %.0f ms", first_ms[0])
                        await queue.put(chunk)
                tail = streamer.flush()
                if tail:
                    await queue.put(tail)
            except Exception as e:
                logger.error("stream drain failed: %s", e)
            finally:
                await queue.put(None)
                raw = "".join(raw_parts)
                spoken = streamer.spoken_text()
                logger.info("PATIENT: %s", spoken or "(silence)")
                self._close_turn(
                    raw, spoken, before, clinician_text,
                    first_ms[0] if first_ms else 0.0,
                    (time.monotonic() - t0) * 1000.0,
                    interrupted=consumer_left["yes"],
                )

        task = asyncio.create_task(drain())
        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                yield item
        finally:
            if not task.done():
                # Barge-in, or LiveKit stopping early. Do NOT await here -- this
                # may run under GeneratorExit. The task holds its own references
                # and finishes on its own, applying <state> either way.
                consumer_left["yes"] = True
                self._pending.add(task)
                task.add_done_callback(self._pending.discard)
