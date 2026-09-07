"""Groq speech-to-text. Copied from mock-oral livekit_agent.py L1122-1125.

Same model, same plugin version. Groq is non-negotiable (spec section 31).
"""

from __future__ import annotations

import os

from livekit.plugins import groq


def build_stt():
    return groq.STT(
        model=os.environ.get("PSIM_STT_MODEL", "whisper-large-v3-turbo"),
        api_key=os.environ["GROQ_API_KEY"],
    )


def build_placeholder_llm(model: str):
    """The dummy LLM that keeps LiveKit's reply gate open. Never invoked.

    See app/config.PLACEHOLDER_LLM_MODEL. DO NOT REMOVE.
    """
    return groq.LLM(model=model, api_key=os.environ["GROQ_API_KEY"])
