"""Scenario package loader.

One case = one self-contained folder. Adding a case never means touching core
code -- the lesson from the mock-oral TOF/OB cross-contamination.

  scenarios/<id>/
      manifest.md            title + which layer files to load
      stem.md                Layer 1  true clinical situation
      patient_profile.md     Layer 2  who this person is
      patient_knowledge.md   Layer 3  knowledge boundary
      dialogue_map.md        Layer 4  conversational domains (not a script)
      behavior.md            Layer 5  emotion + response style
      voice.json             ElevenLabs voice for this patient

The concept is borrowed from mock-oral's manifest/factory pattern, but none of
its exam fields (topics, must-knows, scoring) exist here.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("patient-sim.scenario")

LAYER_FILES = {
    "stem": "stem.md",
    "profile": "patient_profile.md",
    "knowledge": "patient_knowledge.md",
    "dialogue_map": "dialogue_map.md",
    "behavior": "behavior.md",
}

REQUIRED_LAYERS = ("stem", "profile", "knowledge", "behavior")


@dataclass
class Scenario:
    scenario_id: str
    root: Path
    title: str
    layers: dict[str, str]
    voice: dict
    initial_state: dict

    def layer(self, name: str) -> str:
        return self.layers.get(name, "").strip()


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8") if p.is_file() else ""


def load_scenario(scenario_id: str, scenarios_dir: Path) -> Scenario:
    root = scenarios_dir / scenario_id
    if not root.is_dir():
        available = sorted(d.name for d in scenarios_dir.glob("*") if d.is_dir())
        raise FileNotFoundError(
            f"No scenario package '{scenario_id}' in {scenarios_dir}. "
            f"Available: {', '.join(available) or '(none)'}"
        )

    layers = {name: _read(root / fn) for name, fn in LAYER_FILES.items()}
    missing = [n for n in REQUIRED_LAYERS if not layers.get(n, "").strip()]
    if missing:
        raise ValueError(
            f"Scenario '{scenario_id}' is missing required layer file(s): "
            + ", ".join(LAYER_FILES[m] for m in missing)
        )

    manifest = _read(root / "manifest.md")
    m = re.search(r"^\*\*Title:\*\*\s*(.+)$", manifest, re.MULTILINE)
    title = m.group(1).strip() if m else scenario_id

    voice: dict = {}
    vp = root / "voice.json"
    if vp.is_file():
        try:
            voice = json.loads(vp.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            logger.warning("voice.json in %s is invalid (%s); using defaults", scenario_id, e)

    initial_state: dict = {}
    sp = root / "state.json"
    if sp.is_file():
        try:
            initial_state = json.loads(sp.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            logger.warning("state.json in %s is invalid (%s); ignoring", scenario_id, e)

    logger.info(
        "SCENARIO: id=%s title=%r layers=%s voice_id=%s",
        scenario_id, title,
        {k: len(v) for k, v in layers.items() if v},
        voice.get("voice_id", "(default)"),
    )
    return Scenario(
        scenario_id=scenario_id,
        root=root,
        title=title,
        layers=layers,
        voice=voice,
        initial_state=initial_state,
    )
