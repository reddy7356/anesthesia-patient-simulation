"""Review a finished turn log for the failure modes that matter.

    .venv/bin/python -m tools.review_log logs/turns_case_001_*.jsonl
    .venv/bin/python -m tools.review_log --transcript logs/turns_case_002_*.jsonl

Offline: reads the log only, makes no API calls, and can never influence a
live encounter. This is the diagnostic complement to observer.checklist --
the checklist scores the CLINICIAN's communication, this one audits the
PATIENT's realism.

What it flags, and why each matters:

  state leak        Any <state> text reaching the spoken utterance. The one
                    unambiguous bug: internal bookkeeping must never be said
                    out loud.
  clinician voice   The patient using vocabulary a patient would not have
                    ("preoxygenation", "aspiration", "NPO"). Teaches
                    residents that patients speak in jargon.
  examiner voice    Praise, grading or coaching ("that's correct", "you
                    should"). The patient is not an examiner -- this is the
                    exact failure the project exists to avoid.
  long answer       Over the soft word ceiling. Real patients are brief.
  induction narrated  "I'm feeling sleepy", "everything's going dark". People
                    do not narrate their own induction.
  talking asleep    Speech after the fade should have silenced them. The
                    resident must be able to read depth from the patient.
  verbatim repeat   The same utterance twice, or a repeated patient question.
                    Memory failure.
  contract miss     A reply that needed salvage or a retry.
"""

from __future__ import annotations

import glob
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SOFT_MAX_WORDS = 45

CLINICIAN_WORDS = (
    "preoxygenation", "denitrogenation", "induction agent", "aspiration",
    "npo status", "asa class", "rapid sequence", "cricoid", "laryngoscope",
    "endotracheal", "propofol", "sevoflurane", "rocuronium", "suxamethonium",
    "ejection fraction", "creatinine", "functional residual capacity",
)

EXAMINER_PHRASES = (
    "that's correct", "that is correct", "good answer", "well done",
    "excellent", "you should", "the correct management", "let me ask you",
    "next question", "that's right", "you are right", "well explained",
)

NARRATION = (
    "i'm feeling sleepy", "im feeling sleepy", "feeling sleepy",
    "going dark", "getting sleepy", "drifting off", "i'm going under",
    "everything is fading", "goodbye", "thanks doc",
)

ASLEEP_MARKERS = ("asleep", "unconscious", "out")


def words(s: str) -> int:
    return len(s.split())


def review(path: Path, show_transcript: bool = False) -> tuple[int, int]:
    turns = []
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            turns.append(json.loads(line))
        except json.JSONDecodeError:
            print(f"  WARN  line {i} is not valid JSON; skipped")

    if not turns:
        print(f"  (empty log: {path.name})")
        return 0, 0

    findings: list[tuple[str, int, str]] = []
    seen_utterances: Counter[str] = Counter()
    seen_questions: Counter[str] = Counter()

    for t in turns:
        n = t.get("turn", "?")
        said = (t.get("patient_utterance") or "").strip()
        raw = t.get("raw_response") or ""
        before = t.get("state_before") or {}
        after = t.get("state_after") or {}
        low = said.lower()

        # --- API failure (checked FIRST) ----------------------------------
        # llm_client.FALLBACK is spoken when the Anthropic call raises. It is
        # deliberately patient-like, so in a transcript it is indistinguishable
        # from a real line -- and being a fixed string it also looks like a
        # verbatim repeat. Classify it for what it is, and skip the remaining
        # checks so it cannot masquerade as a behaviour problem.
        if low.startswith("sorry... what was that") and not raw.strip():
            findings.append(("API call failed (fallback spoken)", n, "no raw response"))
            continue

        # --- the unambiguous bug -----------------------------------------
        if said and ("<state" in low or "</utterance" in low or "conversation_stage" in low):
            findings.append(("STATE LEAK", n, said[:90]))

        # --- voice errors -------------------------------------------------
        for w in CLINICIAN_WORDS:
            if w in low:
                findings.append(("clinician vocabulary", n, f"said {w!r}: {said[:70]}"))
        for p in EXAMINER_PHRASES:
            if p in low:
                findings.append(("examiner phrase", n, f"said {p!r}: {said[:70]}"))

        # --- brevity ------------------------------------------------------
        if words(said) > SOFT_MAX_WORDS:
            findings.append(("long answer", n, f"{words(said)} words: {said[:70]}"))

        # --- induction behaviour -----------------------------------------
        for phrase in NARRATION:
            if phrase in low:
                findings.append(("induction narrated", n, f"{phrase!r}: {said[:70]}"))
                break

        stage_before = str(before.get("induction_stage", "")).lower()
        if said and any(m in stage_before for m in ASLEEP_MARKERS):
            findings.append(
                ("talking while asleep", n, f"state was {stage_before!r}: {said[:70]}")
            )

        # --- memory -------------------------------------------------------
        # Skip interrupted turns. On the voice path a barge-in aborts the
        # patient mid-utterance and the turn is re-attempted, so the SAME
        # answer is logged two or three times under the same turn number.
        # That is barge-in working, not a memory failure -- an earlier version
        # of this tool reported those as verbatim repeats.
        if said and words(said) >= 4 and not t.get("interrupted"):
            key = re.sub(r"[^a-z ]", "", low).strip()
            seen_utterances[key] += 1
            if seen_utterances[key] == 2:
                findings.append(("verbatim repeat", n, said[:80]))
        # NOTE: questions_patient_has_asked is CUMULATIVE memory -- it carries
        # every question forward, by design, so that the prompt can forbid
        # re-asking. Counting its entries per turn therefore measures how long
        # a question has been remembered, NOT how often it was asked. An
        # earlier version of this tool did exactly that and reported "x17" for
        # a question asked once on turn 12 of a 28-turn log.
        #
        # A genuine re-ask is a new entry appearing in state_after that was
        # ALREADY present in state_before.
        before_qs = {
            re.sub(r"[^a-z ]", "", str(q).lower()).strip()
            for q in (before.get("questions_patient_has_asked") or [])
        }
        after_qs = [
            re.sub(r"[^a-z ]", "", str(q).lower()).strip()
            for q in (after.get("questions_patient_has_asked") or [])
        ]
        # Duplicates within the post-turn list are the real signal.
        for qk, c in Counter(after_qs).items():
            if qk and c > 1:
                seen_questions[qk] = max(seen_questions[qk], c)
        del before_qs

        # --- contract -----------------------------------------------------
        if raw and "<utterance>" not in raw.lower():
            findings.append(("contract miss (no tags)", n, raw[:70]))
        if t.get("stop_reason") == "max_tokens":
            findings.append(("truncated at max_tokens", n, said[:70]))

    # --- transcript ------------------------------------------------------
    if show_transcript:
        print(f"\n  --- transcript: {path.name} ---\n")
        for t in turns:
            c = (t.get("clinician_turn") or "").strip()
            p = (t.get("patient_utterance") or "").strip()
            if c:
                print(f"  DOCTOR  > {c}")
            print(f"  PATIENT > {p if p else '(silence)'}")
        print()

    # --- summary ---------------------------------------------------------
    spoken = [t for t in turns if (t.get("patient_utterance") or "").strip()]
    silent = len(turns) - len(spoken)
    lens = [words((t.get('patient_utterance') or '')) for t in spoken]
    avg = sum(lens) / len(lens) if lens else 0
    lat = [t.get("total_latency_ms") or 0 for t in turns if t.get("total_latency_ms")]

    print(f"\n  {path.name}")
    print(f"    turns {len(turns)}   spoke {len(spoken)}   silent {silent}"
          f"   avg {avg:.1f} words   longest {max(lens) if lens else 0}")
    if lat:
        lat_sorted = sorted(lat)
        p50 = lat_sorted[len(lat_sorted) // 2]
        print(f"    latency median {p50:.0f} ms   max {max(lat):.0f} ms")

    repeated_q = {q: c for q, c in seen_questions.items() if c > 1}
    if repeated_q:
        for q, c in list(repeated_q.items())[:3]:
            findings.append(("patient re-asked own question", "-", f"{q[:60]!r} x{c}"))

    if not findings:
        print("    no issues found\n")
        return len(turns), 0

    by_kind: dict[str, list] = {}
    for kind, n, detail in findings:
        by_kind.setdefault(kind, []).append((n, detail))

    print()
    for kind in sorted(by_kind, key=lambda k: -len(by_kind[k])):
        items = by_kind[kind]
        marker = "!!" if kind == "STATE LEAK" else "  "
        print(f"  {marker}  {kind}  ({len(items)})")
        for n, detail in items[:4]:
            print(f"         turn {n}: {detail}")
        if len(items) > 4:
            print(f"         ... and {len(items) - 4} more")
    print()
    return len(turns), len(findings)


def main(argv: list[str]) -> int:
    args = [a for a in argv[1:] if not a.startswith("-")]
    show = "--transcript" in argv or "-t" in argv

    if not args:
        print(__doc__)
        return 0

    paths: list[Path] = []
    for a in args:
        paths.extend(Path(p) for p in sorted(glob.glob(a)))
    paths = [p for p in paths if p.is_file()]

    if not paths:
        print(f"ERROR: no log files matched {args}", file=sys.stderr)
        return 2

    total_turns = total_findings = 0
    for p in paths:
        turns, n = review(p, show)
        total_turns += turns
        total_findings += n

    if len(paths) > 1:
        print(f"  ==> {len(paths)} logs, {total_turns} turns, {total_findings} findings\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
