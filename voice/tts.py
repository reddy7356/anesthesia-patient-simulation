"""ElevenLabs text-to-speech, with per-scenario voice.

Copied from mock-oral livekit_agent.py L1130-1133, extended so the voice
belongs to the patient rather than to the application. A 71-year-old man and a
28-year-old woman must not share a voice id, and changing one must never
change the other -- so the voice lives in scenarios/<id>/voice.json.
"""

from __future__ import annotations

import logging
import os

from livekit.plugins import elevenlabs

from app.config import DEFAULT_VOICE_ID

logger = logging.getLogger("patient-sim.tts")


def build_tts(voice: dict | None = None):
    voice = voice or {}
    voice_id = voice.get("voice_id") or DEFAULT_VOICE_ID
    model = voice.get("model") or os.environ.get("PSIM_TTS_MODEL", "eleven_flash_v2_5")

    kwargs: dict = {
        "voice_id": voice_id,
        "api_key": os.environ["ELEVENLABS_API_KEY"],
        "model": model,
    }

    settings = voice.get("voice_settings")
    if isinstance(settings, dict) and settings:
        try:
            kwargs["voice_settings"] = elevenlabs.VoiceSettings(**settings)
        except Exception as e:
            logger.warning("Ignoring voice_settings %s: %s", settings, e)

    logger.info("TTS: voice_id=%s model=%s", voice_id, model)
    try:
        return elevenlabs.TTS(**kwargs)
    except TypeError as e:
        # Plugin signature drift: fall back to the exact mock-oral call shape,
        # which is known to work with livekit-plugins-elevenlabs==1.6.0.
        logger.warning("elevenlabs.TTS(%s) rejected (%s); using minimal args", list(kwargs), e)
        return elevenlabs.TTS(voice_id=voice_id, api_key=os.environ["ELEVENLABS_API_KEY"])
