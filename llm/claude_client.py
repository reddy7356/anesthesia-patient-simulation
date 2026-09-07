"""Claude -- the patient's reasoning. Copied from mock-oral _call_claude
(orchestrator/livekit_agent.py L897-910), plus a streaming variant.

Claude is non-negotiable here (spec section 31). No sampling parameters and no
extended thinking: the output is a short structured contract and latency is
the whole point.
"""

from __future__ import annotations

import logging

import anthropic

from app.config import MAX_TOKENS, MODEL

logger = logging.getLogger("patient-sim.claude")

# Spoken when the API fails. A real patient does pause; this is far better
# than silence or an error tone in the simulation room.
FALLBACK = "<utterance>Sorry... what was that?</utterance>"


class ClaudePatient:
    def __init__(self) -> None:
        self.client = anthropic.AsyncAnthropic()

    async def complete(self, system_prompt: str, history: list[dict]) -> str:
        """One full turn, returned when complete."""
        try:
            resp = await self.client.messages.create(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                system=system_prompt,
                messages=history,
            )
        except anthropic.APIStatusError as e:
            logger.error("Anthropic error %s: %s", e.status_code, e)
            return FALLBACK
        except Exception as e:  # network, timeout -- the room must not hang
            logger.error("Anthropic call failed: %s", e)
            return FALLBACK
        return "".join(b.text for b in resp.content if b.type == "text")

    async def stream(self, system_prompt: str, history: list[dict]):
        """Yield text deltas. The patient starts speaking on the first sentence."""
        try:
            async with self.client.messages.stream(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                system=system_prompt,
                messages=history,
            ) as s:
                async for delta in s.text_stream:
                    yield delta
        except anthropic.APIStatusError as e:
            logger.error("Anthropic stream error %s: %s", e.status_code, e)
            yield FALLBACK
        except Exception as e:
            logger.error("Anthropic stream failed: %s", e)
            yield FALLBACK
