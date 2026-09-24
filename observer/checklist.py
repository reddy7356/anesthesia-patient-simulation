"""Post-hoc communication checklist over a finished turn log.

    python -m observer.checklist logs/turns_case_002_*.jsonl

Runs after the room is empty. Intentionally crude keyword matching: it is a
prompt for the debrief conversation, not a score, and it is never shown to the
patient engine.

Two check sets. COMMON applies to every encounter. A scenario-specific set is
added on top, selected from the log's scenario_id -- an emergency full-stomach
case has to be assessed differently from an elective one, and grading the
former against "did you say gallbladder" is useless.

Checks marked SAFETY are the ones where missing the question can change what
drug is given or how the airway is managed. They are reported separately
because "you forgot to introduce yourself" and "you never asked about
pregnancy before a general anesthetic" are not the same class of miss.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# (label, keywords, is_safety)
COMMON = [
    ("introduced themselves",
     ("i'm dr", "i am dr", "my name is", "this is dr", "one of the anesthes",
      "from anesthes", "from anaesthes"), False),
    ("confirmed patient identity",
     ("your name", "date of birth", "confirm your", "who you are",
      "full name", "dob"), True),
    ("confirmed the procedure",
     ("what operation", "what surgery", "what procedure", "operation you",
      "what are you having", "which side"), True),
    ("asked about allergies",
     ("allerg", "react to any", "reaction to any"), True),
    ("invited questions or concerns",
     ("any questions", "any concerns", "anything you want to ask",
      "anything you'd like to ask", "anything else you want"), False),
    ("responded to anxiety",
     ("nervous", "anxious", "scared", "frightened", "it's normal",
      "that's normal", "understandable", "you're in good hands",
      "we'll look after you", "we will look after you"), False),
    ("explained the anesthetic",
     ("asleep", "put you to sleep", "anesthesia will", "anaesthetic will",
      "you won't feel", "you will not feel"), False),
    ("explained oxygen administration",
     ("oxygen", "mask", "breathe normally", "some nice breaths",
      "deep breaths"), False),
]

ELECTIVE = [
    ("asked about interval change",
     ("anything changed", "anything new", "since this morning", "any new",
      "since your assessment", "how have you been since"), False),
    ("asked about fasting",
     ("last eat", "last ate", "anything to eat", "last time you ate",
      "nil by mouth", "when did you eat", "last drink", "anything to drink"), True),
    ("asked about regular medications",
     ("medication", "tablets", "pills", "medicine", "take anything"), False),
]

# An emergency patient is a full stomach until proved otherwise, may be
# pregnant, and is in pain. These are the questions that change management.
EMERGENCY = [
    ("asked about LAST SOLID FOOD",
     ("last eat", "last ate", "anything to eat", "last time you ate",
      "when did you eat", "last meal", "eaten anything"), True),
    ("asked about LAST FLUID separately",
     ("anything to drink", "last drink", "had any water", "sips",
      "anything at all to drink", "any fluids", "drunk anything"), True),
    ("probed the fasting answer (did not accept the first reply)",
     ("anything at all", "even a sip", "are you sure", "nothing since",
      "how much did you", "anything else since", "even water"), True),
    ("asked about PREGNANCY",
     ("pregnan", "could you be", "any chance you", "last period",
      "periods"), True),
    ("asked about REFLUX / heartburn",
     ("reflux", "heartburn", "indigestion", "acid", "burning in your chest"), True),
    ("asked about the vomiting",
     ("vomit", "been sick", "throwing up", "thrown up", "nausea",
      "nauseous", "sick a few times"), False),
    ("asked about pain",
     ("pain", "hurt", "sore", "how bad is"), False),
    ("explained why fasting matters / aspiration risk",
     ("into your lungs", "in your lungs", "come up", "bring it up",
      "stomach contents", "aspirat", "empty stomach", "food in your stomach"), False),
    ("acknowledged a symptom the patient reported",
     ("sorry to hear", "i'm sorry", "that sounds", "i understand",
      "we'll give you something", "we will give you something",
      "medication for that", "something for the"), False),
]

SCENARIO_SETS = {
    "case_001": ELECTIVE,
    "case_002": EMERGENCY,
}

# Any scenario whose id or title suggests an emergency gets the emergency set.
EMERGENCY_HINTS = ("emergency", "appendic", "full stomach", "trauma", "urgent",
                   "obstruction", "caesarean", "cesarean")


def _load(path: Path) -> tuple[list[str], list[str], str]:
    clinician, patient, sid = [], [], ""
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        sid = rec.get("scenario_id") or sid
        if rec.get("clinician_turn"):
            clinician.append(str(rec["clinician_turn"]).lower())
        if rec.get("patient_utterance"):
            patient.append(str(rec["patient_utterance"]).lower())
    return clinician, patient, sid


def _pick_set(sid: str, path: Path) -> tuple[list, str]:
    if sid in SCENARIO_SETS:
        return SCENARIO_SETS[sid], sid
    blob = (sid + " " + path.name).lower()
    # Fall back on the scenario manifest title if it is available.
    manifest = Path(__file__).resolve().parent.parent / "scenarios" / sid / "manifest.md"
    if manifest.is_file():
        blob += " " + manifest.read_text(encoding="utf-8").lower()
    if any(h in blob for h in EMERGENCY_HINTS):
        return EMERGENCY, sid + " (emergency check set)"
    return ELECTIVE, sid + " (elective check set)"


def review(path: Path) -> int:
    clinician, patient, sid = _load(path)
    if not clinician:
        print(f"  no clinician turns found in {path.name}")
        return 2

    extra, label = _pick_set(sid, path)
    checks = COMMON + extra
    blob = " || ".join(clinician)

    print(f"\nCommunication checklist — {path.name}")
    print(f"Scenario: {label}   ({len(clinician)} clinician turns)\n")

    missed_safety = []
    for text, keys, safety in checks:
        hit = any(k in blob for k in keys)
        mark = "x" if hit else " "
        tag = "  [SAFETY]" if safety else ""
        print(f"  [{mark}] {text}{tag}")
        if safety and not hit:
            missed_safety.append(text)

    # --- things the PATIENT said that may deserve a second look ---------
    notes = []
    pblob = " || ".join(patient)
    if re.search(r"\bsorry,? what|what do you mean|what does that mean", pblob):
        notes.append("patient did not understand a word you used")
    if re.search(r"making me feel (a bit )?sick|feel like i'll be sick|going to be sick", pblob):
        notes.append("patient reported feeling sick during the encounter")
    if re.search(r"didn't really eat much|i didn't eat much|not much", pblob):
        notes.append("patient gave a vague fasting answer -- was it probed?")
    if re.search(r"i don't know|i couldn't tell you|nobody (said|told)", pblob):
        notes.append("patient could not answer something -- expected, or a gap?")
    if re.search(r"does that count|is that.*count", pblob):
        notes.append("patient offered information and asked if it mattered")

    if notes:
        print("\n  Worth revisiting:")
        for n in notes:
            print(f"    - {n}")

    if missed_safety:
        print(f"\n  !! {len(missed_safety)} SAFETY question(s) not asked:")
        for m in missed_safety:
            print(f"    - {m}")

    print("\nKeyword-based. A discussion prompt, not a score -- a question asked")
    print("in wording this tool does not recognise will read as missed.\n")
    return 0


def main(argv: list[str]) -> int:
    args = [a for a in argv[1:] if not a.startswith("-")]
    if not args:
        print("usage: python -m observer.checklist <turns_*.jsonl>", file=sys.stderr)
        return 2
    rc = 0
    for a in args:
        p = Path(a)
        if not p.is_file():
            print(f"no such turn log: {p}", file=sys.stderr)
            rc = 2
            continue
        rc = review(p) or rc
    return rc


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
