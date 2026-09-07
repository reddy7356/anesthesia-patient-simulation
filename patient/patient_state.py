"""The patient's internal state -- and the hard boundary that keeps it silent.

Three concepts are kept deliberately separate (spec section 6):

  TRUE CASE STATE          scenario/stem.md      - immutable, may be hidden
  WHAT THE PATIENT KNOWS   patient_knowledge.md  - immutable, the ceiling
  WHAT HAS BEEN SAID       PatientState          - mutable, this class

Only the third changes during an encounter. Immutable facts are never copied
into PatientState -- if they were, the model could quietly rewrite the case.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict

_UTTERANCE_RE = re.compile(r"<utterance>(.*?)</utterance>", re.DOTALL | re.IGNORECASE)
_STATE_RE = re.compile(r"<state>(.*?)</state>", re.DOTALL | re.IGNORECASE)
_ANY_TAG_RE = re.compile(r"<[^>]*>?")


def strip_tags_for_tts(raw: str) -> str:
    """Return only the inside of <utterance>; everything else is hidden.

    Adapted from mock-oral orchestrator/state.py L379-386.

    A missing <utterance> returns "" -- the runtime treats that as a bad turn
    rather than reading raw XML aloud to the clinician. This is the last line
    of defence for the non-negotiable "<state> is never sent to TTS".
    """
    m = _UTTERANCE_RE.search(raw)
    if not m:
        return ""
    text = m.group(1)
    # Belt and braces: no stray markup ever reaches ElevenLabs, even if the
    # model nests something unexpected inside the utterance.
    return _ANY_TAG_RE.sub("", text).strip()


def salvage_untagged(raw: str) -> str:
    """Last-resort rescue when the model replies in prose with no contract.

    Only fires when the response contains NO angle bracket and NO brace at all,
    so it cannot possibly be a state block, a partial tag, or JSON. In that case
    the whole response is plainly just something the patient said, and speaking
    it is far better than an unexplained silence in the simulation room.

    Anything with markup in it stays suppressed -- a leaked <state> must never
    reach TTS, which is the one failure this system cannot have.
    """
    s = raw.strip()
    if not s or "<" in s or ">" in s or "{" in s or "}" in s:
        return ""
    if len(s.split()) > 80:  # a wall of prose is a prompt failure, not a patient
        return ""
    return s


def parse_state_block(raw: str) -> dict | None:
    """Pull the JSON out of <state>...</state>. Returns None if absent/invalid.

    A malformed state block is survivable: the previous state simply persists
    for another turn. It must never abort the conversation.
    """
    m = _STATE_RE.search(raw)
    if not m:
        return None
    body = m.group(1).strip()
    if body.startswith("```"):
        body = re.sub(r"^```[a-zA-Z]*\n?|```$", "", body).strip()
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


@dataclass
class PatientState:
    """Mutable, per-encounter. Never spoken.

    Deliberately small: every field costs latency on the way in and on the way
    out. Anything the scenario already states does not belong here.
    """

    # --- where we are ---
    conversation_stage: str = "before_greeting"   # greeting|identity|concerns|explanation|preinduction
    induction_stage: str = "awake"                # awake|preoxygenation|induction
    oxygen_mask_status: str = "off"               # off|offered|on|refused

    # --- how the patient is ---
    anxiety: str = "baseline"                     # scenario sets the baseline word
    pain: str = "none"
    nausea: str = "none"

    # --- conversational memory (the anti-repetition machinery) ---
    information_already_disclosed: list[str] = field(default_factory=list)
    questions_patient_has_asked: list[str] = field(default_factory=list)
    clinician_explanations_received: list[str] = field(default_factory=list)
    misunderstandings: list[str] = field(default_factory=list)

    # --- bookkeeping ---
    turn: int = 0

    _LIST_FIELDS = (
        "information_already_disclosed",
        "questions_patient_has_asked",
        "clinician_explanations_received",
        "misunderstandings",
    )
    _SCALAR_FIELDS = (
        "conversation_stage",
        "induction_stage",
        "oxygen_mask_status",
        "anxiety",
        "pain",
        "nausea",
    )

    def apply(self, delta: dict | None) -> None:
        """Merge one <state> block.

        Lists are UNIONED, never replaced. If the model forgets to repeat an
        earlier disclosure the memory survives anyway -- forgetting is exactly
        the failure mode that makes a simulated patient repeat itself.
        """
        if not delta:
            return
        for k in self._SCALAR_FIELDS:
            v = delta.get(k)
            if isinstance(v, str) and v.strip():
                setattr(self, k, v.strip())
        for k in self._LIST_FIELDS:
            v = delta.get(k)
            if isinstance(v, str):
                v = [v]
            if isinstance(v, list):
                cur = getattr(self, k)
                for item in v:
                    if not isinstance(item, str):
                        continue
                    item = item.strip()
                    if item and item.lower() not in {c.lower() for c in cur}:
                        cur.append(item)

    def as_prompt_block(self) -> str:
        """Compact rendering injected into the system prompt each turn."""
        def lst(items: list[str]) -> str:
            return "; ".join(items) if items else "(nothing yet)"

        return (
            f"turn: {self.turn}\n"
            f"conversation_stage: {self.conversation_stage}\n"
            f"induction_stage: {self.induction_stage}\n"
            f"oxygen_mask_status: {self.oxygen_mask_status}\n"
            f"anxiety: {self.anxiety} | pain: {self.pain} | nausea: {self.nausea}\n"
            f"ALREADY TOLD THE CLINICIAN: {lst(self.information_already_disclosed)}\n"
            f"QUESTIONS I HAVE ALREADY ASKED: {lst(self.questions_patient_has_asked)}\n"
            f"THINGS THE CLINICIAN HAS EXPLAINED TO ME: "
            f"{lst(self.clinician_explanations_received)}\n"
            f"MY MISUNDERSTANDINGS: {lst(self.misunderstandings)}"
        )

    def snapshot(self) -> dict:
        return asdict(self)
