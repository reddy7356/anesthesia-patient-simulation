"""Create and validate scenario packages.

    # start a new case from the annotated template
    .venv/bin/python -m tools.new_case create case_003

    # check a case is complete and loadable before you run it
    .venv/bin/python -m tools.new_case check case_003

    # list every case the launcher can see
    .venv/bin/python -m tools.new_case list

`check` is the useful one. It catches the mistakes that otherwise only show up
as a strange patient mid-encounter: an unfilled <<PLACEHOLDER>>, a missing
layer file, invalid JSON, or a state.json that says the patient feels fine
when the whole point of the case is that they do not.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCENARIOS = ROOT / "scenarios"
TEMPLATE = SCENARIOS / "_template"

LAYER_FILES = (
    "manifest.md",
    "stem.md",
    "patient_profile.md",
    "patient_knowledge.md",
    "dialogue_map.md",
    "behavior.md",
)
JSON_FILES = ("state.json", "voice.json")

# Files the loader refuses to run without (patient/scenario.py REQUIRED_LAYERS).
HARD_REQUIRED = ("stem.md", "patient_profile.md", "patient_knowledge.md", "behavior.md")

PLACEHOLDER = re.compile(r"<<.*?>>", re.DOTALL)
HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)


def _strip_comments(text: str) -> str:
    """Instructional <!-- --> blocks are guidance, not content."""
    return HTML_COMMENT.sub("", text)


def cmd_create(case_id: str) -> int:
    if not re.fullmatch(r"[a-z0-9_]+", case_id):
        print(f"ERROR: '{case_id}' -- use lowercase letters, digits and underscores.", file=sys.stderr)
        return 2
    if case_id.startswith("_"):
        print("ERROR: names starting with '_' are reserved (e.g. _template).", file=sys.stderr)
        return 2

    dest = SCENARIOS / case_id
    if dest.exists():
        print(f"ERROR: {dest} already exists. Pick another id or delete it first.", file=sys.stderr)
        return 2
    if not TEMPLATE.is_dir():
        print(f"ERROR: template not found at {TEMPLATE}", file=sys.stderr)
        return 2

    shutil.copytree(TEMPLATE, dest)

    # Pre-fill the scenario id so one fewer placeholder needs editing.
    mf = dest / "manifest.md"
    mf.write_text(
        mf.read_text(encoding="utf-8").replace("<<SCENARIO_ID>>", case_id),
        encoding="utf-8",
    )

    print(f"\n  Created {dest}\n")
    print("  Fill in these files, in this order:\n")
    for i, (fn, what) in enumerate([
        ("manifest.md", "title + what the resident should practise"),
        ("stem.md", "the body and the scene, present tense -- and the true facts"),
        ("patient_profile.md", "who this person is, and the words they use"),
        ("patient_knowledge.md", "what they know, do NOT know, and wrongly believe"),
        ("dialogue_map.md", "domains the encounter may touch (not a script)"),
        ("behavior.md", "how they sound; keep the 'Going under' section intact"),
        ("state.json", "how they feel at turn zero"),
        ("voice.json", "ElevenLabs voice -- only needed for voice modes"),
    ], 1):
        print(f"    {i}. {fn:<22} {what}")
    print(f"\n  Every <<...>> marker must be replaced. Then check it:\n")
    print(f"    .venv/bin/python -m tools.new_case check {case_id}")
    print(f"    .venv/bin/python -m tools.dryrun {case_id}\n")
    return 0


def cmd_check(case_id: str) -> int:
    root = SCENARIOS / case_id
    if not root.is_dir():
        print(f"ERROR: no scenario '{case_id}' in {SCENARIOS}", file=sys.stderr)
        return 2

    errors: list[str] = []
    warnings: list[str] = []

    print(f"\n  Checking {root}\n")

    # --- files present, placeholders replaced ---------------------------
    for fn in LAYER_FILES:
        p = root / fn
        if not p.is_file():
            (errors if fn in HARD_REQUIRED else warnings).append(f"{fn} is missing")
            print(f"  {'FAIL' if fn in HARD_REQUIRED else 'WARN'}  {fn:<22} missing")
            continue

        raw = p.read_text(encoding="utf-8")
        body = _strip_comments(raw).strip()
        left = PLACEHOLDER.findall(body)

        if not body:
            errors.append(f"{fn} is empty")
            print(f"  FAIL  {fn:<22} empty")
        elif left:
            errors.append(f"{fn} has {len(left)} unfilled placeholder(s)")
            first = " ".join(left[0].split())[:60]
            print(f"  FAIL  {fn:<22} {len(left)} unfilled <<...>>  e.g. {first}...")
        else:
            print(f"  ok    {fn:<22} {len(body):>5} chars")

    # --- JSON files parse ----------------------------------------------
    state: dict = {}
    for fn in JSON_FILES:
        p = root / fn
        if not p.is_file():
            warnings.append(f"{fn} missing (defaults will be used)")
            print(f"  WARN  {fn:<22} missing -- defaults will be used")
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if fn == "state.json":
                state = data
            print(f"  ok    {fn:<22} valid JSON")
        except json.JSONDecodeError as e:
            errors.append(f"{fn} is not valid JSON: {e}")
            print(f"  FAIL  {fn:<22} invalid JSON: {e}")

    # --- title ----------------------------------------------------------
    mf = root / "manifest.md"
    if mf.is_file():
        m = re.search(r"^\*\*Title:\*\*\s*(.+)$", mf.read_text(encoding="utf-8"), re.MULTILINE)
        if not m:
            warnings.append("manifest.md has no **Title:** line; the id will be shown instead")
            print("  WARN  manifest.md          no **Title:** line")
        else:
            print(f"  ok    title                 {m.group(1).strip()[:56]}")

    # --- the induction fade must survive editing ------------------------
    bh = root / "behavior.md"
    if bh.is_file():
        text = bh.read_text(encoding="utf-8").lower()
        if "going under" not in text:
            errors.append("behavior.md has lost its 'Going under' section")
            print("  FAIL  behavior.md           no 'Going under' section")
        elif "empty <utterance>" not in text:
            warnings.append("behavior.md 'Going under' may not specify an empty <utterance>")
            print("  WARN  behavior.md           'Going under' lacks the empty <utterance> rule")
        else:
            print("  ok    induction fade        present")

    # --- state.json sanity ---------------------------------------------
    # A sick patient whose state says nausea: none will not behave as intended.
    if state:
        sick_words = ("nausea", "vomit", "sick", "emergency", "pain")
        blob = " ".join(
            _strip_comments((root / f).read_text(encoding="utf-8")).lower()
            for f in ("stem.md", "manifest.md")
            if (root / f).is_file()
        )
        looks_sick = sum(w in blob for w in sick_words) >= 2
        if looks_sick:
            for field in ("nausea", "pain"):
                val = str(state.get(field, "")).strip().lower()
                if val in ("", "none", "no", "nil"):
                    warnings.append(
                        f"state.json {field}='{val or 'unset'}' but the case reads as a symptomatic patient"
                    )
                    print(f"  WARN  state.json            {field}='{val or 'unset'}' -- case looks symptomatic")

    # --- does the loader actually accept it? ---------------------------
    try:
        sys.path.insert(0, str(ROOT))
        from patient.scenario import load_scenario  # noqa: E402

        sc = load_scenario(case_id, SCENARIOS)
        print(f"  ok    loader                 accepted ({len(sc.layers)} layers)")
    except Exception as e:
        errors.append(f"loader rejected the scenario: {e}")
        print(f"  FAIL  loader                 {e}")

    # --- verdict --------------------------------------------------------
    print()
    for w in warnings:
        print(f"  warning: {w}")
    if errors:
        print(f"\n  {len(errors)} ERROR(S) -- fix these before running:\n")
        for e in errors:
            print(f"    - {e}")
        print()
        return 1

    print(f"  PASS -- scenario '{case_id}' is ready.\n")
    print(f"    .venv/bin/python -m tools.dryrun {case_id}\n")
    return 0


def cmd_list() -> int:
    if not SCENARIOS.is_dir():
        print(f"ERROR: {SCENARIOS} not found", file=sys.stderr)
        return 2
    print("\n  Scenarios:\n")
    found = False
    for d in sorted(SCENARIOS.iterdir()):
        if not d.is_dir() or d.name.startswith("_"):
            continue
        found = True
        title = d.name
        mf = d / "manifest.md"
        if mf.is_file():
            m = re.search(r"^\*\*Title:\*\*\s*(.+)$", mf.read_text(encoding="utf-8"), re.MULTILINE)
            if m:
                title = m.group(1).strip()
        print(f"    {d.name:<14} {title}")
    if not found:
        print("    (none)")
    print()
    return 0


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    cmd = argv[1]
    if cmd == "list":
        return cmd_list()
    if cmd in ("create", "check"):
        if len(argv) < 3:
            print(f"ERROR: {cmd} needs a scenario id, e.g. '{cmd} case_003'", file=sys.stderr)
            return 2
        return cmd_create(argv[2]) if cmd == "create" else cmd_check(argv[2])
    print(f"ERROR: unknown command '{cmd}'. Use create, check or list.", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
