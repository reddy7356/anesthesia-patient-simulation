# anesthesia-patient-simulation

An AI **patient** voice agent for the anesthesia simulation mannequin.
A resident or attending speaks naturally to the mannequin before induction;
the mannequin listens, remembers, and answers as that patient would.

This is a **separate application** from the ABA mock oral examination system.
Claude plays the *patient*, not the examiner. There is no scoring, no topic
advancement, and no phaser.

---

## Repository protection

`~/perplexity-mock-oral` is a **protected reference specimen**.
Baseline commit: `73e0377194ee7a7e28fca07e9262a31b84b75295` (clean tree).

* It may be read and copied from.
* It is **never** edited, refactored, reformatted, or imported at runtime.
* Every line this project needs from it has been **copied in**, not shared.
* Write allowlist for this phase: **this directory only.**

If an implementation ever appears to require a change inside
`perplexity-mock-oral`, development stops and the question goes to the human
developer. See `docs/ARCHITECTURE_INVENTORY.md`.

---

## Runtime flow

```
clinician speech
   -> LiveKit audio
   -> Groq STT (whisper-large-v3-turbo)
   -> completed clinician turn
   -> patient runtime (scenario + patient state + conversation memory)
   -> Claude  ->  <utterance> + <state>
   -> <state> stripped, never spoken
   -> ElevenLabs TTS (scenario voice)
   -> LiveKit audio -> mannequin speaker
```

## Layout

```
anesthesia-patient-simulation/
  app/
    livekit_agent.py       worker entrypoint, AgentSession wiring   [copied]
    patient_agent.py       Agent subclass, llm_node override        [adapted]
    patient_runtime.py     turn loop, memory, state update          [new]
    utterance_stream.py    <utterance> streamer, tag-leak guard     [copied]
    turn_log.py            per-turn structured log record           [new]
  voice/
    stt.py                 Groq STT factory                         [copied]
    tts.py                 ElevenLabs TTS factory + scenario voice  [copied+]
  llm/
    claude_client.py       async Anthropic client + one-turn call   [copied]
  patient/
    patient_state.py       structured internal state                [new]
    patient_prompt.py      patient system-prompt assembler          [new]
    scenario.py            scenario package loader                  [new]
  mannequin/
    interface.py           abstract mannequin I/O boundary          [new]
    livekit_room.py        default: sim-room mic + speaker          [new]
  scenarios/
    case_001/
      manifest.md          scenario id, title, entry point
      stem.md              Layer 1 - clinical situation
      patient_profile.md   Layer 2 - who the patient is
      patient_knowledge.md Layer 3 - knowledge boundary
      dialogue_map.md      Layer 4 - conversational domains
      behavior.md          Layer 5 - emotion and response style
      voice.json           ElevenLabs voice for this patient
  tests/                   regression suite (spec section 29)
  logs/                    per-run transcripts
  run_patient_sim.sh       launcher (forces .env over shell exports)
```

One scenario = one self-contained folder. Adding a case never means editing
core code.

## Setup

```bash
cd ~/projects/anesthesia-patient-simulation
rm -rf .venv                 # remove the stub folder first
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env         # then fill in the keys
```

## Test it by typing first (no sim room needed)

```bash
.venv/bin/python -m tools.dryrun case_001
```

Type a clinician line, read the patient's reply. `/state` shows the hidden
state. This is where humanization is validated cheaply — answer length,
memory, paraphrase consistency, the knowledge boundary. Only book the sim room
once it reads right here.

## Run in the simulation room

```bash
./run_patient_sim.sh case_001            # LiveKit room (dev)
./run_patient_sim.sh case_001 console    # laptop mic/speaker, no LiveKit
```

The patient does **not** speak first. A person on the table waits to be
spoken to.

## Tests

```bash
python3 tests/test_contract.py            # offline: contract, memory, tag safety
python3 tests/test_protected_baseline.py  # asserts mock oral is untouched
.venv/bin/python -m tests.test_patient_behavior   # live: needs ANTHROPIC_API_KEY
```

## Debrief (optional, offline only)

```bash
.venv/bin/python -m observer.checklist logs/turns_case_001_*.jsonl
```

Reads a finished turn log and ticks off introduction, identity confirmation,
addressing concerns, explaining the anesthetic and the oxygen. It never runs
during an encounter and can never influence what the patient says.

## Adding a case

Copy `scenarios/case_001/` to `scenarios/case_002/` and rewrite the five layer
files plus `voice.json`. No core file changes. That isolation is the whole
point — it is the fix for the cross-stem leakage that bit the mock oral system.
