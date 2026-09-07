"""Default mannequin: the simulation room's microphone and speaker via LiveKit.

The room IS the mannequin in v1 -- the speaker sits in the manikin's head. A
vendor mannequin later implements the same MannequinInterface and the patient
engine does not change.
"""

from __future__ import annotations

import logging

from mannequin.interface import MannequinInterface

logger = logging.getLogger("patient-sim.mannequin")


class LiveKitRoomMannequin(MannequinInterface):
    name = "livekit-room"

    def __init__(self, ctx) -> None:
        self._ctx = ctx

    async def connect(self) -> None:
        await self._ctx.connect()
        logger.info("Mannequin audio path up (%s)", self.name)

    async def disconnect(self) -> None:
        logger.info("Mannequin audio path closing (%s)", self.name)

    @property
    def supports_barge_in(self) -> bool:
        return True
