"""Post-hoc communication checklist over a finished turn log.

Runs after the room is empty:  python -m observer.checklist logs/turns_*.jsonl

Intentionally crude keyword matching. It is a prompt for the debrief
conversation, not a score, and it is never shown to the patient engine.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

CHECKS = {
    "introduced themselves": ("i'm dr", "i am dr", "my name is", "one of the anesthes"),
    "confirmed patient identity": ("your name", "date of birth", "confirm your", "mr. alvarez", "mr alvarez"),
    "confirmed the procedure": ("gallbladder", "what surgery", "what procedure", "operation you"),
    "asked about interval change": ("anything changed", "anything new", "since this morning", "any new"),
    "invited questions or concerns": ("any questions", "any concerns", "anything you want to ask"),
    "responded to anxiety": ("nervous", "anxious", "scared", "it's normal", "that's normal", "understandable"),
    "explained the anesthetic": ("asleep", "put you to sleep", "anesthesia will", "you won't feel"),
    "explained oxygen administration": ("oxygen", "mask", "breathe normally", "some nice breaths"),
}


def review(path: Path) -> dict[str, bool]:
    said = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("clinician_turn"):
            said.append(rec["clinician_turn"].lower())
    blob = " || ".join(said)
    return {label: any(k in blob for k in keys) for label, keys in CHECKS.items()}


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: python -m observer.checklist <turns_*.jsonl>", file=sys.stderr)
        return 2
    p = Path(argv[1])
    if not p.is_file():
        print(f"no such turn log: {p}", file=sys.stderr)
        return 2
    print(f"\nCommunication checklist — {p.name}\n")
    for label, hit in review(p).items():
        print(f"  [{'x' if hit else ' '}] {label}")
    print("\nKeyword-based. Discussion prompt, not a score.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
