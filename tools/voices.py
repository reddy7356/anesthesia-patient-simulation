"""Find, audition and assign the ElevenLabs voice for a patient scenario.

    python -m tools.voices list                     # your voices, male first
    python -m tools.voices audition <id> [<id>...]  # hear Ray say his own lines
    python -m tools.voices set case_001 <id>        # write it into voice.json

A voice belongs to a patient, not to the application, so `set` only ever
touches scenarios/<id>/voice.json. Choosing a voice for one case can never
change another.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import httpx

from app.config import SCENARIOS_DIR, WORKSPACE, require_env

API = "https://api.elevenlabs.io/v1"
OUT = WORKSPACE / "logs" / "auditions"

# Real lines from a real encounter. A voice that carries the correction, the
# flat factual answer, the flinch and the fear is the right voice; one that
# only sounds good reading a neutral sentence is not.
AUDITION_LINES = [
    ("01_correction", "It's Alvarez, actually. Ray's fine. I'm okay, I guess."),
    ("02_factual",    "Had some soup around eight last night. Little sip of water this morning with my blood pressure pill."),
    ("03_flinch",     "Okay — just, give me a second before you put it on."),
    ("04_fear",       "Am I gonna be sick when I wake up? I was last time."),
    ("05_going_under", "Mm. Okay..."),
]


def _key() -> str:
    require_env()
    return os.environ["ELEVENLABS_API_KEY"]


def _labels(v: dict) -> str:
    lab = v.get("labels") or {}
    bits = [lab.get(k) for k in ("gender", "age", "accent", "use_case", "description")]
    return ", ".join(b for b in bits if b)


def cmd_list() -> int:
    r = httpx.get(f"{API}/voices", headers={"xi-api-key": _key()}, timeout=30)
    r.raise_for_status()
    voices = r.json().get("voices", [])

    def rank(v: dict) -> tuple:
        lab = (v.get("labels") or {})
        g = (lab.get("gender") or "").lower()
        a = (lab.get("age") or "").lower()
        # Ray is 58. Male and older sorts first; everything else still listed.
        return (0 if g.startswith("m") else 1,
                0 if ("old" in a or "middle" in a or "mature" in a) else 1,
                v.get("name", ""))

    print(f"\n  {len(voices)} voices in your library "
          "(male / older first — Ray is a 58-year-old delivery driver)\n")
    for v in sorted(voices, key=rank):
        print(f"  {v.get('voice_id')}  {v.get('name','?'):<22} {_labels(v)}")
    print("\n  Audition two or three:")
    print("    python -m tools.voices audition <id> <id>\n")
    return 0


def cmd_audition(ids: list[str]) -> int:
    key = _key()
    model = os.environ.get("PSIM_TTS_MODEL", "eleven_flash_v2_5")
    OUT.mkdir(parents=True, exist_ok=True)
    made: list[Path] = []

    for vid in ids:
        try:
            meta = httpx.get(f"{API}/voices/{vid}", headers={"xi-api-key": key}, timeout=30)
            meta.raise_for_status()
            name = meta.json().get("name", vid[:8])
        except Exception:
            name = vid[:8]
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)
        print(f"\n  {name}  ({vid})")

        for tag, text in AUDITION_LINES:
            dest = OUT / f"{safe}__{tag}.mp3"
            try:
                resp = httpx.post(
                    f"{API}/text-to-speech/{vid}",
                    headers={"xi-api-key": key, "content-type": "application/json"},
                    json={"text": text, "model_id": model,
                          "voice_settings": {"stability": 0.55,
                                             "similarity_boost": 0.75}},
                    timeout=60,
                )
                resp.raise_for_status()
            except httpx.HTTPStatusError as e:
                print(f"    FAILED {tag}: {e.response.status_code} "
                      f"{e.response.text[:160]}")
                continue
            dest.write_bytes(resp.content)
            made.append(dest)
            print(f"    {tag:<16} {text[:58]}")

    if made:
        print(f"\n  {len(made)} clips in {OUT}")
        print("  Play them all in order:\n")
        print(f"    for f in {OUT}/*.mp3; do echo \"$f\"; afplay \"$f\"; done\n")
        print("  Listen for: does he sound 58, tired, and slightly wary?")
        print("  A voice that sounds like a narrator is the wrong voice.\n")
    return 0 if made else 1


def cmd_set(scenario_id: str, voice_id: str) -> int:
    path = SCENARIOS_DIR / scenario_id / "voice.json"
    if not path.parent.is_dir():
        print(f"no scenario '{scenario_id}'", file=sys.stderr)
        return 2
    cfg = {}
    if path.is_file():
        try:
            cfg = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    old = cfg.get("voice_id")
    cfg["voice_id"] = voice_id
    try:
        meta = httpx.get(f"{API}/voices/{voice_id}",
                         headers={"xi-api-key": _key()}, timeout=30)
        meta.raise_for_status()
        j = meta.json()
        cfg["_voice_name"] = j.get("name")
        lab = j.get("labels") or {}
        if lab.get("gender"):
            cfg["gender"] = lab["gender"]
    except Exception as e:
        print(f"  (could not read voice metadata: {e})")
    path.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    print(f"\n  {scenario_id}: voice {old} -> {voice_id} "
          f"({cfg.get('_voice_name','?')})")
    print(f"  written to {path}")
    print("  Only this scenario changed.\n")
    return 0


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__); return 2
    cmd = argv[1]
    if cmd == "list":
        return cmd_list()
    if cmd == "audition" and len(argv) > 2:
        return cmd_audition(argv[2:])
    if cmd == "set" and len(argv) == 4:
        return cmd_set(argv[2], argv[3])
    print(__doc__); return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
