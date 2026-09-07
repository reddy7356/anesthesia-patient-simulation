"""Offline regression tests — no API keys, no network, no LiveKit.

Covers spec section 29 items 1, 3 (mechanism), 7 (mechanism) and 9.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from patient.patient_state import (  # noqa: E402
    PatientState, parse_state_block, strip_tags_for_tts,
)
from app.utterance_stream import UtteranceStreamer  # noqa: E402

FAILURES: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        FAILURES.append(name)


RAW = (
    "<utterance>No, nothing new.</utterance>\n"
    '<state>{"conversation_stage": "concerns", '
    '"information_already_disclosed": ["nothing has changed since this morning"]}</state>'
)


def test_state_never_reaches_tts() -> None:
    print("\n[1/9] <state> is never sent to TTS")
    spoken = strip_tags_for_tts(RAW)
    check("utterance extracted", spoken == "No, nothing new.", repr(spoken))
    check("no state leakage", "state" not in spoken.lower() and "{" not in spoken, repr(spoken))
    check("no angle brackets", "<" not in spoken and ">" not in spoken, repr(spoken))

    # Missing contract -> silence, never raw model output.
    check("missing utterance yields empty", strip_tags_for_tts("Hello there.") == "")
    check(
        "state-only response yields empty",
        strip_tags_for_tts('<state>{"anxiety": "high"}</state>') == "",
    )
    # Nested markup is scrubbed even inside a valid utterance.
    check(
        "nested tags scrubbed",
        strip_tags_for_tts("<utterance>Okay<state>{}</state></utterance>") == "Okay{}",
        repr(strip_tags_for_tts("<utterance>Okay<state>{}</state></utterance>")),
    )


def test_streamer_never_leaks_split_tag() -> None:
    print("\n[2/9] streamed closing tag split across deltas never leaks")
    for chunk_size in (1, 2, 3, 5, 7, 11):
        s = UtteranceStreamer()
        out = ""
        for i in range(0, len(RAW), chunk_size):
            out += s.feed(RAW[i:i + chunk_size])
        out += s.flush()
        ok = "<" not in out and "state" not in out.lower() and "{" not in out
        check(f"chunk_size={chunk_size} clean", ok, repr(out))
        check(f"chunk_size={chunk_size} content", out.strip() == "No, nothing new.", repr(out))


def test_state_merge_is_cumulative() -> None:
    print("\n[3/9] conversational memory accumulates and never silently resets")
    st = PatientState()
    st.apply({"information_already_disclosed": ["nothing since 8pm"], "anxiety": "mild"})
    st.apply({"information_already_disclosed": ["took blood pressure pill"]})
    check("first disclosure kept", "nothing since 8pm" in st.information_already_disclosed)
    check("second disclosure added", "took blood pressure pill" in st.information_already_disclosed)
    check("no duplicates on repeat", (
        st.apply({"information_already_disclosed": ["Nothing Since 8pm"]}) is None
        and len(st.information_already_disclosed) == 2
    ), st.information_already_disclosed)
    st.apply({})
    check("empty delta preserves memory", len(st.information_already_disclosed) == 2)
    st.apply(None)
    check("None delta preserves memory", len(st.information_already_disclosed) == 2)
    check("scalar updated", st.anxiety == "mild")


def test_malformed_state_is_survivable() -> None:
    print("\n[4/9] a malformed <state> block never aborts the encounter")
    check("no state block -> None", parse_state_block("<utterance>Okay.</utterance>") is None)
    check("bad json -> None", parse_state_block("<state>{not json}</state>") is None)
    check("fenced json parsed", parse_state_block(
        '<state>```json\n{"anxiety": "high"}\n```</state>') == {"anxiety": "high"})
    st = PatientState()
    st.apply({"information_already_disclosed": ["a"]})
    st.apply(parse_state_block("<state>{broken</state>"))
    check("state carried forward after bad block", st.information_already_disclosed == ["a"])


def test_empty_utterance_means_silence() -> None:
    print("\n[5/9] an empty <utterance> yields silence, not noise")
    raw = '<utterance></utterance>\n<state>{"conversation_stage": "greeting"}</state>'
    check("empty utterance -> empty string", strip_tags_for_tts(raw) == "")
    s = UtteranceStreamer()
    out = "".join(s.feed(c) for c in raw) + s.flush()
    check("streamed empty utterance -> nothing spoken", out.strip() == "", repr(out))


def test_prompt_contains_no_examiner_language() -> None:
    print("\n[6/9] the patient prompt contains no examiner behaviour")
    from patient.patient_prompt import BASE_RULES
    banned = ("score", "grade the", "must-know", "hard fail", "probe", "candidate",
              "advance to topic", "examiner")
    low = BASE_RULES.lower()
    for word in banned:
        # "grade" appears only inside the prohibition "Do not grade".
        check(f"no examiner concept: {word!r}",
              word not in low or f"do not {word}" in low or "never" in low)
    check("forbids grading", "do not grade" in low)
    check("forbids teaching", "teacher" in low or "teach" in low)
    check("forbids speaking state", "never speak anything from the <state> block" in low)


def test_scenario_loads_and_is_isolated() -> None:
    print("\n[7/9] scenario package loads with all five layers")
    from patient.scenario import load_scenario
    sc = load_scenario("case_001", ROOT / "scenarios")
    for layer in ("stem", "profile", "knowledge", "behavior", "dialogue_map"):
        check(f"layer present: {layer}", len(sc.layer(layer)) > 100, f"{len(sc.layer(layer))} chars")
    check("title parsed from manifest", "Alvarez" in sc.title, sc.title)
    check("voice is scenario-scoped", bool(sc.voice.get("voice_id")))
    check("initial state loaded", sc.initial_state.get("anxiety") == "mild")


def test_prompt_assembles_with_state() -> None:
    print("\n[8/9] system prompt carries scenario + live memory")
    from patient.patient_prompt import build_system_prompt
    from patient.scenario import load_scenario
    sc = load_scenario("case_001", ROOT / "scenarios")
    st = PatientState()
    st.apply({"information_already_disclosed": ["nothing since 8pm last night"]})
    p = build_system_prompt(sc, st)
    check("knowledge boundary included", "ceiling of what you know" in p)
    check("memory injected", "nothing since 8pm last night" in p)
    check("behaviour layer included", "Baseline emotion" in p)
    check("hidden true-case facts present for the engine", "two cigarettes" in p.lower())


def test_salvage_never_speaks_markup() -> None:
    print("\n[10/12] untagged-prose salvage never leaks a state block")
    from patient.patient_state import salvage_untagged
    check("plain prose salvaged",
          salvage_untagged("Had some soup around eight last night. That's it.")
          == "Had some soup around eight last night. That's it.")
    for bad in ('<state>{"anxiety": "high"}</state>',
                'Okay. <state>{"pain": "none"}',
                '{"conversation_stage": "greeting"}',
                'Okay.</utterance>',
                '<utterance>Okay.'):
        check(f"suppressed: {bad[:28]!r}", salvage_untagged(bad) == "")
    check("empty stays empty", salvage_untagged("   ") == "")
    check("wall of prose suppressed", salvage_untagged("word " * 100) == "")


def test_history_keeps_the_contract() -> None:
    print("\n[11/12] assistant history is stored TAGGED, not bare")
    src = (ROOT / "app" / "patient_runtime.py").read_text()
    check("assistant history carries BOTH blocks",
          'if "<utterance>" in low and "<state>" in low:' in src
          and 'content = raw.strip()[:1500]' in src)
    check("fallback history still well-formed",
          '<state>{{}}</state>' in src)
    check("turns are serialized before a new one opens",
          src.count("await self._settle_pending()") == 2)
    check("close_turn logs its own turn number",
          'turn=before.get("turn", self.state.turn)' in src)
    # This wait is dead air in front of the clinician's next answer.
    import re as _re
    m = _re.search(r'PSIM_SETTLE_TIMEOUT", "([0-9.]+)"', src)
    check("settle timeout is a latency budget (<=0.5s)",
          bool(m) and float(m.group(1)) <= 0.5, m.group(1) if m else "absent")
    llm = (ROOT / "llm" / "claude_client.py").read_text()
    # This model returns HTTP 400 for a prefilled request. Guard the regression.
    check("no assistant prefill sent",
          '{"role": "assistant", "content": PREFILL}' not in llm)
    check("prefill failure documented", "does not support" in llm)
    check("retry nudge available", "RETRY_NUDGE" in llm)
    rt = (ROOT / "app" / "patient_runtime.py").read_text()
    check("retry used on contract miss", "RETRY_NUDGE" in rt)


def test_plugins_imported_on_main_thread() -> None:
    """LiveKit plugins self-register at import. A deferred import inside a
    function runs on a job worker thread and raises
    'Plugins must be registered on the main thread' -- crashing every run."""
    print("\n[12/13] livekit plugin imports are at module level")
    import ast as _ast
    offenders = []
    for py in (ROOT / "app").rglob("*.py"):
        tree = _ast.parse(py.read_text(encoding="utf-8"))
        for node in _ast.walk(tree):
            if not isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
                continue
            for sub in _ast.walk(node):
                mod = ""
                if isinstance(sub, _ast.ImportFrom):
                    mod = sub.module or ""
                elif isinstance(sub, _ast.Import):
                    mod = ",".join(a.name for a in sub.names)
                if "livekit.plugins" in mod or mod.startswith(("voice.", "voice")):
                    offenders.append(f"{py.name}:{node.name}() imports {mod}")
    check("no plugin import inside a function", not offenders, "; ".join(offenders))
    src = (ROOT / "app" / "livekit_agent.py").read_text()
    check("stt imported at module level", "\nfrom voice.stt import" in src)
    check("tts imported at module level", "\nfrom voice.tts import" in src)


def test_no_runtime_import_from_protected_repo() -> None:
    print("\n[13/13] nothing imports the protected mock-oral system")
    offenders = []
    for py in ROOT.rglob("*.py"):
        if ".venv" in py.parts:
            continue
        text = py.read_text(encoding="utf-8")
        for line in text.splitlines():
            s = line.strip()
            if s.startswith(("import ", "from ")) and (
                "orchestrator" in s or "perplexity" in s or "mock_oral" in s
            ):
                offenders.append(f"{py.relative_to(ROOT)}: {s}")
    check("no imports from mock-oral", not offenders, "; ".join(offenders))


def main() -> int:
    print("=" * 62)
    print("  PATIENT SIMULATION — offline contract tests")
    print("=" * 62)
    for fn in (
        test_state_never_reaches_tts,
        test_streamer_never_leaks_split_tag,
        test_state_merge_is_cumulative,
        test_malformed_state_is_survivable,
        test_empty_utterance_means_silence,
        test_prompt_contains_no_examiner_language,
        test_scenario_loads_and_is_isolated,
        test_prompt_assembles_with_state,
        test_salvage_never_speaks_markup,
        test_history_keeps_the_contract,
        test_plugins_imported_on_main_thread,
        test_no_runtime_import_from_protected_repo,
    ):
        fn()
    print("\n" + "=" * 62)
    if FAILURES:
        print(f"  {len(FAILURES)} FAILED: {', '.join(FAILURES)}")
        return 1
    print("  ALL OFFLINE CONTRACT TESTS PASSED")
    print("=" * 62)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
