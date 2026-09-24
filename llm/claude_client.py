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

# NOTE: assistant prefill was tried here and is NOT available -- this model
# answers a prefilled request with HTTP 400 "This model does not support
# assistant message prefill. The conversation must end with a user message."
# The contract is held instead by (a) storing prior patient turns in their
# TAGGED form so the in-context examples reinforce it, and (b) salvaging an
# untagged reply rather than going silent. Do not reintroduce prefill without
# checking the model actually accepts it.

# Appended to the system prompt for ONE retry after a contract miss.
RETRY_NUDGE = (
    "\n\nYOUR LAST REPLY WAS REJECTED because it did not use the required "
    "format. Write the same thing again, this time starting with the literal "
    "characters <utterance> and closing with </utterance>, followed by the "
    "<state> block. Write nothing before the opening tag."
)


def _system_param(system: str | tuple[str, str]):
    """Build the `system` argument, marking a cache breakpoint when asked.

    Passed a plain string: sent as-is, uncached (the old behaviour).

    Passed (stable_prefix, per_turn_tail): sent as two text blocks with
    cache_control on the first. The prefix -- rules plus the five scenario
    layers -- is byte-identical on every turn, so after the first call it is
    served from cache at a tenth of the input price. Since the whole system
    prompt is resent every turn and is ~97% prefix, this is the single biggest
    lever on cost.

    Anthropic will not cache a block under ~1024 tokens; below that the
    breakpoint is simply ignored, so marking it is never harmful.
    """
    if isinstance(system, str):
        return system
    prefix, tail = system
    blocks = [{
        "type": "text",
        "text": prefix,
        "cache_control": {"type": "ephemeral"},
    }]
    if tail:
        blocks.append({"type": "text", "text": tail})
    return blocks


class ClaudePatient:
    def __init__(self) -> None:
        self.client = anthropic.AsyncAnthropic()
        self.last_stop_reason: str | None = None
        # Cumulative token accounting for the run, so the saving is visible
        # rather than assumed. Reported by tools.dryrun on exit.
        self.usage = {
            "calls": 0,
            "input": 0,
            "output": 0,
            "cache_write": 0,
            "cache_read": 0,
        }

    def _record(self, resp) -> None:
        u = getattr(resp, "usage", None)
        if u is None:
            return
        self.usage["calls"] += 1
        self.usage["input"] += getattr(u, "input_tokens", 0) or 0
        self.usage["output"] += getattr(u, "output_tokens", 0) or 0
        self.usage["cache_write"] += getattr(u, "cache_creation_input_tokens", 0) or 0
        self.usage["cache_read"] += getattr(u, "cache_read_input_tokens", 0) or 0

    def usage_report(self) -> str:
        u = self.usage
        if not u["calls"]:
            return "no API calls"
        # Cached reads bill at 0.1x input, cache writes at 1.25x.
        billed = u["input"] + u["cache_write"] * 1.25 + u["cache_read"] * 0.1
        uncached = u["input"] + u["cache_write"] + u["cache_read"]
        line = (
            f"{u['calls']} calls | in {u['input']} "
            f"cache_write {u['cache_write']} cache_read {u['cache_read']} "
            f"| out {u['output']}"
        )
        if u["cache_read"]:
            saved = 100 * (1 - billed / uncached) if uncached else 0
            line += (
                f"\n  input billed as ~{billed:,.0f} tokens vs {uncached:,} "
                f"uncached -- {saved:.0f}% less on input"
            )
        return line

    async def complete(self, system_prompt: str | tuple[str, str], history: list[dict]) -> str:
        """One full turn, returned when complete."""
        try:
            resp = await self.client.messages.create(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                system=_system_param(system_prompt),
                messages=history,
            )
        except anthropic.APIStatusError as e:
            logger.error("Anthropic error %s: %s", e.status_code, e)
            return FALLBACK
        except Exception as e:  # network, timeout -- the room must not hang
            logger.error("Anthropic call failed: %s", e)
            return FALLBACK
        self._record(resp)
        self.last_stop_reason = getattr(resp, "stop_reason", None)
        if self.last_stop_reason == "max_tokens":
            logger.warning("Response hit max_tokens (%s) -- output truncated", MAX_TOKENS)
        return "".join(b.text for b in resp.content if b.type == "text")

    async def stream(self, system_prompt: str | tuple[str, str], history: list[dict]):
        """Yield text deltas. The patient starts speaking on the first sentence."""
        try:
            async with self.client.messages.stream(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                system=_system_param(system_prompt),
                messages=history,
            ) as s:
                async for delta in s.text_stream:
                    yield delta
                try:
                    self._record(await s.get_final_message())
                except Exception:  # accounting must never break the room
                    pass
        except anthropic.APIStatusError as e:
            logger.error("Anthropic stream error %s: %s", e.status_code, e)
            yield FALLBACK
        except Exception as e:
            logger.error("Anthropic stream failed: %s", e)
            yield FALLBACK
