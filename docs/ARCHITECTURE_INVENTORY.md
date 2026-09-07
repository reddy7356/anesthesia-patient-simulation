# Architecture Inventory — Task 19

**Protected reference system:** `~/perplexity-mock-oral`
**PROTECTED_MOCK_ORAL_BASELINE:** `73e0377194ee7a7e28fca07e9262a31b84b75295`
**Working tree at baseline:** clean (`git status --porcelain` empty)
**Access mode:** READ / COPY ONLY. Zero writes to that repository.

The mock-oral voice pipeline is concentrated in a single file,
`orchestrator/livekit_agent.py` (1,185 lines). The exam-specific machinery
(scoring, phaser, topic advancement) lives in separate modules and is
**not needed** by the patient simulator.

## Reusable vs. exam-only

| Required capability | Mock-oral source | Action |
|---|---|---|
| LiveKit session + worker entrypoint | `orchestrator/livekit_agent.py` → `entrypoint()` L1106–1141; `cli.run_app(WorkerOptions(...))` L1184 | **COPY** |
| Groq STT | same file, `AgentSession(stt=groq.STT(model="whisper-large-v3-turbo"))` L1122–1125 | **COPY verbatim** |
| ElevenLabs TTS | same file, `elevenlabs.TTS(voice_id=VOICE_ID)` L1130–1133 + `VOICE_ID` L104 | **COPY**, extend with per-scenario voice |
| Claude API client | `anthropic.AsyncAnthropic()` L307; `_call_claude()` L897–910 | **COPY** |
| Turn detection / turn-taking | `silero.VAD.load()` L1134 + `llm_node()` override L353–392 + `_last_user_text()` L1090–1100 | **COPY verbatim** |
| LiveKit placeholder-LLM gate | `PLACEHOLDER_LLM_MODEL` L68 + `llm=groq.LLM(...)` L1126–1129 | **COPY verbatim — non-obvious.** Without a dummy `llm=` on the session, LiveKit 1.6 never calls `llm_node` and the agent silently never speaks. |
| Interruption / barge-in | LiveKit `AgentSession` defaults + yielding chunks from `llm_node` rather than `session.say()` | **COPY pattern** |
| Streaming TTS + tag-leak safety | `_UtteranceStreamer` L208–296 | **COPY verbatim.** Already solves "`<state>` must never reach TTS" for the streaming path, including tags split across deltas. |
| TTS sanitization (batch path) | `orchestrator/state.py` → `strip_tags_for_tts()` L379–386 | **COPY** (6 lines) |
| Environment loading | `WORKSPACE` + `load_dotenv()` L46–54; `_require_env()` L1142–1150 | **COPY**, rename options to `PSIM_*` |
| File logging per run | `_setup_file_logging()` L1155–1183 | **COPY**, extend with the per-turn log record (spec §30) |
| Launcher hardening | `run_exam_v2.sh` — forces LiveKit creds from the project `.env` because python-dotenv will not override an already-exported shell variable | **COPY the technique** into `run_patient_sim.sh`. This is the fix for the Aug 31 stale-credential incident; the same trap applies here. |
| Scenario package loader | `orchestrator/manifest.py`, `orchestrator/factory.py` (449 + 120 lines, exam-shaped) | **CONCEPT ONLY → CREATE NEW.** Reuse the "one case = one self-contained folder + manifest" idea; do not import exam manifest fields. |
| Patient state model | none | **CREATE NEW** (`patient/patient_state.py`) |
| Patient system prompt | none — `examiner_system_prompt.md` is protected and examiner-shaped | **CREATE NEW** (`patient/patient_prompt.py` + scenario markdown) |
| Patient runtime / turn loop | `orchestrator/runtime.py` is exam logic | **CREATE NEW** (`app/patient_runtime.py`) |
| Scoring, must-knows, hard fails | `orchestrator/runtime.py`, `state.py` | **DO NOT COPY** — out of scope (spec §9) |
| Topic advance, phaser, watchdogs | `orchestrator/runtime.py`, `injection.py`, `topic_parser.py` | **DO NOT COPY** — out of scope (spec §10) |

## What this means in practice

Roughly **300 lines** of the 5,217-line mock-oral orchestrator are reusable.
Everything else the patient simulator needs is new. Nothing is imported from
`perplexity-mock-oral` at runtime — the code is copied in, so the protected
system can be moved, renamed, or deleted without affecting this project.

## Two non-obvious traps carried over

1. **Placeholder LLM.** `AgentSession(llm=None)` makes LiveKit skip reply
   generation entirely. The dummy Groq LLM must stay even though it is never
   invoked.
2. **Shell exports beat `.env`.** `python-dotenv` does not override variables
   already exported in the shell. The launcher must read the project `.env`
   and pass the values explicitly via `env`, or a stale `LIVEKIT_URL` sends the
   patient to the wrong LiveKit project.
