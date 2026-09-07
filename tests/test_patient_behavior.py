"""Live behavioural regression tests (spec section 29 items 2-8).

Needs ANTHROPIC_API_KEY and makes real Claude calls. Skips cleanly without one.

    .venv/bin/python -m tests.test_patient_behavior

These are behavioural assertions, so they are deliberately tolerant: they
check for the failure modes that make a simulated patient unusable, not for
exact wording.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FAILURES: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  -- {detail}" if not cond else ""))
    if not cond:
        FAILURES.append(name)


def fresh():
    from app.config import LOG_DIR, SCENARIOS_DIR
    from app.patient_runtime import PatientRuntime
    from app.turn_log import TurnLog
    from patient.scenario import load_scenario

    rt = PatientRuntime(load_scenario("case_001", SCENARIOS_DIR), TurnLog(LOG_DIR, "test"))
    rt.stream_enabled = False
    return rt


EXAMINER_WORDS = ("correct", "good answer", "well done", "excellent",
                  "you should", "the correct management", "let me ask you",
                  "next question", "that's right", "physiology")
CLINICAL_WORDS = ("preoxygenation", "induction agent", "aspiration prophylaxis",
                  "npo status", "mac ", "asa class", "denitrogenation")


async def main() -> int:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("SKIP  ANTHROPIC_API_KEY not set — live behavioural tests skipped")
        return 0

    print("=" * 62)
    print("  PATIENT SIMULATION — live behavioural tests")
    print("=" * 62)

    # --- short answers to simple questions (item 7) ---
    print("\n[1] simple questions get short answers")
    rt = fresh()
    a = await rt.respond("Good morning. I'm Dr. Chen, one of the anesthesiologists. How are you feeling?")
    print(f"      Q: how are you feeling?\n      A: {a}")
    check("greeting answer under 30 words", len(a.split()) < 30, f"{len(a.split())} words")
    b = await rt.respond("Are you having any pain right now?")
    print(f"      Q: any pain?\n      A: {b}")
    check("pain answer under 20 words", len(b.split()) < 20, f"{len(b.split())} words")
    check("no state leak in speech", "{" not in a + b and "<" not in a + b)

    # --- no examiner behaviour (item 2) ---
    print("\n[2] the patient never behaves like an examiner")
    blob = (a + " " + b).lower()
    for w in EXAMINER_WORDS:
        check(f"no examiner phrase {w!r}", w not in blob)
    for w in CLINICAL_WORDS:
        check(f"no clinician vocabulary {w!r}", w not in blob)

    # --- knowledge boundary (item 3) ---
    print("\n[3] the patient stays inside the knowledge boundary")
    c = await rt.respond("What was your blood pressure this morning?")
    print(f"      Q: BP this morning?\n      A: {c}")
    low = c.lower()
    hedged = any(h in low for h in ("don't know", "dont know", "not sure", "didn't say",
                                    "didn't tell", "couldn't tell", "no idea", "little up",
                                    "don't remember", "dont remember", "they might have"))
    check("does not invent a BP number", hedged or not any(ch.isdigit() for ch in c), c)

    d = await rt.respond("Can you explain why we give oxygen before anesthesia?")
    print(f"      Q: why do we give oxygen?\n      A: {d}")
    dlow = d.lower()
    check("does not lecture on physiology",
          not any(w in dlow for w in ("oxygen reserve", "denitrogen", "functional residual",
                                      "apnea", "saturation", "lungs fill with oxygen")), d)

    # --- memory / no unnecessary repetition (items 4, 5, 6) ---
    print("\n[4] memory: paraphrased repeat question does not restate verbatim")
    rt2 = fresh()
    e1 = await rt2.respond("When did you last have anything to eat or drink?")
    print(f"      Q1: when did you last eat?\n      A1: {e1}")
    e2 = await rt2.respond("And nothing at all to eat since then?")
    print(f"      Q2: nothing since then?\n      A2: {e2}")
    check("second answer is not a verbatim repeat",
          e1.strip().lower() != e2.strip().lower(), e2)
    check("second answer is shorter or equal", len(e2.split()) <= len(e1.split()) + 6,
          f"{len(e1.split())} -> {len(e2.split())}")
    check("fasting memory recorded",
          bool(rt2.state.information_already_disclosed), rt2.state.information_already_disclosed)

    print("\n[5] paraphrases of the same question give consistent facts")
    rt3 = fresh()
    p1 = await rt3.respond("Are you having any pain?")
    rt4 = fresh()
    p2 = await rt4.respond("Does anything hurt right now?")
    rt5 = fresh()
    p3 = await rt5.respond("Are you uncomfortable anywhere?")
    print(f"      {p1!r}\n      {p2!r}\n      {p3!r}")
    def denies(s: str) -> bool:
        s = s.lower()
        return any(n in s for n in ("no", "not really", "nothing", "i'm okay", "im okay", "fine"))
    check("all three paraphrases deny current pain",
          denies(p1) and denies(p2) and denies(p3), f"{p1} | {p2} | {p3}")

    print("\n[6] fragments produce silence, not a reply")
    rt6 = fresh()
    f1 = await rt6.respond("I'm going to")
    print(f"      fragment -> {f1!r}")
    check("fragment yields silence or a bare acknowledgement",
          len(f1.split()) <= 4, f1)

    print("\n[7] the mask moment reads like a person")
    rt7 = fresh()
    await rt7.respond("Good morning Mr. Alvarez, I'm Dr. Chen.")
    m = await rt7.respond("I'm going to put this oxygen mask over your face. Just take some nice normal breaths for me.")
    print(f"      A: {m}")
    check("mask reply under 25 words", len(m.split()) < 25, f"{len(m.split())} words")
    mlow = m.lower()
    check("no textbook oxygen explanation",
          not any(w in mlow for w in ("preoxygenation", "oxygen administration",
                                      "important component", "induction of general")), m)

    print("\n" + "=" * 62)
    if FAILURES:
        print(f"  {len(FAILURES)} FAILED: {', '.join(FAILURES)}")
        return 1
    print("  ALL LIVE BEHAVIOURAL TESTS PASSED")
    print("=" * 62)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
