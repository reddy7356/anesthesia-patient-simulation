"""The boundary between the AI patient engine and physical hardware.

    AI Patient Engine
          |
    MannequinInterface     <- this file
          |
    speaker / microphone / sensors / future hardware

Nothing above this line imports LiveKit, Groq, ElevenLabs or a vendor SDK.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class MannequinInterface(ABC):
    """What the patient engine is allowed to ask of a mannequin."""

    name: str = "abstract"

    @abstractmethod
    async def connect(self) -> None:
        """Bring the audio path up."""

    @abstractmethod
    async def disconnect(self) -> None:
        """Tear the audio path down."""

    @property
    @abstractmethod
    def supports_barge_in(self) -> bool:
        """True if the clinician can interrupt speech already playing."""

    async def set_physiology(self, **signals) -> None:
        """Optional hook for vendor mannequins that accept vitals or motion.

        Deliberately a no-op by default: v1 is voice only, and the patient's
        spoken behaviour must never depend on hardware that may be absent.
        """
        return None
