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

### Moving keys in without exposing them

`.env` is gitignored and never committed. To get credentials into it without
the values appearing in a terminal, a chat transcript, or shell history, use
the importer instead of editing the file by hand:

```bash
# one key at a time — input is hidden (getpass), nothing is echoed
.venv/bin/python scripts/import_env.py --set GROQ_API_KEY

# or bulk-import a KEY=VALUE file you dropped in the sandbox,
# then shred the source
.venv/bin/python scripts/import_env.py --from /mnt/aidrive/psim_env.txt
shred -u /mnt/aidrive/psim_env.txt

# confirm what is loaded — secrets shown only as length + sha256 prefix
.venv/bin/python scripts/import_env.py --check
```

The importer writes `.env` with mode `0600`, keeps existing comments and
unrecognized keys, ignores anything that is not a known `PSIM_*`/provider
variable, and prints secrets only as a masked fingerprint. Never paste a live
key into a chat message or a `git`-tracked file.

`--set` requires a real terminal. Where there is no tty it refuses rather than
falling back to clear-text input, so use `--from` in that case.

## Running on your own AWS host

To run the worker somewhere persistent with credentials in KMS-encrypted SSM
rather than a local file, see [`deploy/aws/README.md`](deploy/aws/README.md):

```bash
./deploy/aws/deploy.sh      psim us-east-1 YOUR_KEYPAIR   # one EC2 instance
./deploy/aws/put_secrets.sh psim us-east-1 .env           # keys -> encrypted SSM
```

Keys travel from your machine to AWS directly; nothing sensitive appears in the
CloudFormation template, in EC2 user-data, or in this repository. Note that
`console` mode cannot work on a headless host (no mic or speaker) — use `dev`
mode or `tools.dryrun` there.

## Test it by typing first (no sim room needed)

```bash
.venv/bin/python -m tools.dryrun case_001
```

### Typed path on a server (lightest possible install)

`tools.dryrun` imports neither `livekit` nor `sounddevice`, so the whole voice
stack is optional when you only want to type to the patient. On a headless
Linux box this is the quickest way in, and it needs no system audio library:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dryrun.txt   # 2 packages, not 80+
cp .env.example .env && chmod 600 .env             # then add ANTHROPIC_API_KEY
.venv/bin/python -m tools.dryrun case_001
```

Only `ANTHROPIC_API_KEY` is required for this; STT/TTS keys are unused until
you run voice. Install the full `requirements.txt` when you move to `dev` or
`console` mode.

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

## Authoring a new patient

Scenarios are **markdown + JSON**, not YAML. One case = one folder; adding a
case never means touching core code.

```bash
.venv/bin/python -m tools.new_case create case_003   # copy annotated template
# fill in the <<...>> markers
.venv/bin/python -m tools.new_case check  case_003   # validate before running
.venv/bin/python -m tools.dryrun          case_003
.venv/bin/python -m tools.new_case list              # all cases
```

`check` catches unfilled `<<PLACEHOLDER>>` markers, missing layer files,
invalid JSON, a deleted induction-fade section, and a `state.json` that says
the patient feels fine when the case reads as symptomatic.

### The eight files

| File | Layer | What goes in it |
|---|---|---|
| `manifest.md` | — | Title, setting, learning focus |
| `stem.md` | 1 | The body and scene, present tense; **exact** last-oral-intake times; true facts the patient does not know |
| `patient_profile.md` | 2 | Who they are; the actual words they use for their body and drugs |
| `patient_knowledge.md` | 3 | What they know, do **not** know, and wrongly believe |
| `dialogue_map.md` | 4 | Domains the encounter may touch — deliberately **not** a script |
| `behavior.md` | 5 | How they sound; response length; the induction fade |
| `state.json` | — | How they feel at turn zero |
| `voice.json` | — | ElevenLabs voice (voice modes only) |

`stem.md`, `patient_profile.md`, `patient_knowledge.md` and `behavior.md` are
required; the loader refuses a case without them.

### Why there is no question-and-answer list

A fixed Q&A script breaks the moment a resident phrases something differently,
and it makes the patient volunteer information no real patient would offer.
Instead, put the **facts** in layers 1–3 and the **delivery rules** in layer 5.
The model then improvises consistently, and stays inside the knowledge
boundary. `dialogue_map.md` lists the domains an encounter may touch and what
each answer depends on — never the answers themselves.

### The three things that make a patient feel real

1. **The knowledge boundary** (`patient_knowledge.md`). Patients know their
   symptoms and their story; they do not know their numbers, drug names, or
   physiology. A patient who supplies an ejection fraction teaches something
   false.
2. **Misconceptions** (`patient_knowledge.md`, "believes"). These give the
   resident something real to correct. In `case_002` the patient has vomited
   and therefore believes her stomach is empty.
3. **Withheld facts with a disclosure condition** (`stem.md`, "true facts").
   State *what makes it come out* — asked directly, asked kindly, asked
   privately, asked twice. This is what rewards good communication.

Keep the **"Going under"** section of `behavior.md` intact: the resident must
notice the patient going under from the patient, not from a monitor.
