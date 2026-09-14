#!/usr/bin/env python3
"""Securely import secrets into .env without ever echoing their values.

The only thing this prints is a MASKED fingerprint per key:
  name, character length, and a short SHA-256 digest prefix.
That is enough to confirm the right value landed, and useless to anyone
reading the transcript.

Usage
-----
    # from a file you uploaded / dropped anywhere in the sandbox
    python3 scripts/import_env.py --from /mnt/aidrive/psim_env.txt
    python3 scripts/import_env.py --from ~/uploads/.env

    # type/paste a single key without it appearing in shell history
    python3 scripts/import_env.py --set GROQ_API_KEY

    # just show what is currently loaded, masked
    python3 scripts/import_env.py --check

Merge semantics: keys present in the source overwrite those in .env;
keys absent from the source are left untouched. Comments and unknown
keys in the existing .env are preserved.
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import os
import re
import shutil
import stat
import sys
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"

# Keys this project understands. Anything else in the source is reported
# and skipped, so a stray line cannot silently become config.
KNOWN = {
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_BASE_URL",
    "GROQ_API_KEY",
    "ELEVENLABS_API_KEY",
    "LIVEKIT_URL",
    "LIVEKIT_API_KEY",
    "LIVEKIT_API_SECRET",
    "PSIM_SCENARIO",
    "PSIM_MODEL",
    "PSIM_PORT",
    "PSIM_STREAM_TTS",
    "PSIM_LOG_DIR",
    "PSIM_VOICE_ID",
    "PSIM_MAX_TOKENS",
    "PSIM_SOFT_MAX_WORDS",
    "PSIM_PLACEHOLDER_LLM",
    "PSIM_TRUST_SHELL",
    "PSIM_WORKSPACE",
    "PSIM_SCENARIOS_DIR",
}

SECRET_HINT = re.compile(r"(KEY|SECRET|TOKEN|PASSWORD)$")
LINE_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")

# Values that mean "not really configured".
PLACEHOLDERS = {"", "placeholder-not-used-in-dryrun", "changeme", "xxx", "<your-key>"}


def fingerprint(name: str, value: str) -> str:
    """Masked, transcript-safe description of a value."""
    if value in PLACEHOLDERS:
        return f"{name:<22} (empty / placeholder)"
    if SECRET_HINT.search(name):
        digest = hashlib.sha256(value.encode()).hexdigest()[:8]
        return f"{name:<22} set  len={len(value):<4} sha256:{digest}"
    # Non-secret (URLs, model ids, ports) — safe to show in full.
    return f"{name:<22} {value}"


def parse_env(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = LINE_RE.match(line)
        if not m:
            continue
        key, val = m.group(1), m.group(2).strip()
        # Strip matching surrounding quotes and any trailing inline comment.
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        else:
            val = val.split(" #")[0].strip()
        out[key] = val
    return out


def read_existing() -> tuple[list[str], dict[str, str]]:
    if not ENV_PATH.exists():
        return [], {}
    text = ENV_PATH.read_text(encoding="utf-8")
    return text.splitlines(), parse_env(text)


def write_env(updates: dict[str, str]) -> None:
    """Rewrite .env in place, preserving comments and key order."""
    lines, _ = read_existing()
    remaining = dict(updates)
    out: list[str] = []

    for line in lines:
        m = LINE_RE.match(line.strip()) if line.strip() and not line.strip().startswith("#") else None
        if m and m.group(1) in remaining:
            key = m.group(1)
            out.append(f"{key}={remaining.pop(key)}")
        else:
            out.append(line)

    if remaining:
        out.append("")
        out.append("# --- imported by scripts/import_env.py ---")
        for key, val in remaining.items():
            out.append(f"{key}={val}")

    if ENV_PATH.exists():
        backup = ENV_PATH.with_suffix(".env.bak")
        shutil.copy2(ENV_PATH, backup)
        os.chmod(backup, stat.S_IRUSR | stat.S_IWUSR)

    # Create with 0600 from the very first byte — never world-readable.
    fd = os.open(ENV_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out).rstrip("\n") + "\n")
    os.chmod(ENV_PATH, stat.S_IRUSR | stat.S_IWUSR)


def show_check() -> int:
    _, current = read_existing()
    if not current:
        print(f"No .env at {ENV_PATH}")
        return 1
    print(f"\n  {ENV_PATH}  (mode {oct(ENV_PATH.stat().st_mode & 0o777)})\n")
    required = ("ANTHROPIC_API_KEY", "GROQ_API_KEY", "ELEVENLABS_API_KEY")
    for key in required:
        print("  " + fingerprint(key, current.get(key, "")))
    print()
    for key, val in current.items():
        if key not in required:
            print("  " + fingerprint(key, val))
    missing = [k for k in required if current.get(k, "") in PLACEHOLDERS]
    print()
    if missing:
        print("  Still needed for voice modes: " + ", ".join(missing))
    else:
        print("  All three required keys are set.")
    print()
    return 0


def do_import(src: Path) -> int:
    if not src.exists():
        print(f"ERROR: no such file: {src}", file=sys.stderr)
        return 2
    incoming = parse_env(src.read_text(encoding="utf-8"))
    if not incoming:
        print(f"ERROR: no KEY=VALUE lines found in {src}", file=sys.stderr)
        return 2

    accepted = {k: v for k, v in incoming.items() if k in KNOWN and v not in PLACEHOLDERS}
    skipped_unknown = sorted(set(incoming) - KNOWN)
    skipped_empty = sorted(k for k, v in incoming.items() if k in KNOWN and v in PLACEHOLDERS)

    if not accepted:
        print("Nothing to import (all lines were unknown or empty).")
        return 2

    write_env(accepted)

    print(f"\n  Imported {len(accepted)} value(s) into {ENV_PATH}\n")
    for key in accepted:
        print("  " + fingerprint(key, accepted[key]))
    if skipped_empty:
        print("\n  Skipped (empty/placeholder): " + ", ".join(skipped_empty))
    if skipped_unknown:
        print("  Skipped (not a known key):   " + ", ".join(skipped_unknown))
    print(f"\n  Source file still exists: {src}")
    print("  Delete it now that the values are imported:")
    print(f"    shred -u {src}  ||  rm -f {src}\n")
    return 0


def do_set(key: str) -> int:
    if key not in KNOWN:
        print(f"ERROR: '{key}' is not a known key. One of:\n  " + "\n  ".join(sorted(KNOWN)), file=sys.stderr)
        return 2
    # SECURITY: getpass only guarantees hidden input when it can open the
    # real terminal. With no tty (piped stdin, an agent shell, most CI) it
    # falls back to fallback_getpass, which prints
    # "Warning: Password input may be echoed" and reads the value in CLEAR
    # TEXT. Silently accepting a secret down that path is exactly the leak
    # this script exists to prevent, so refuse instead of degrading.
    try:
        tty = open("/dev/tty", "r+")
        tty.close()
        has_tty = sys.stdin.isatty()
    except OSError:
        has_tty = False

    if not has_tty:
        print(
            "REFUSING: no interactive terminal, so input cannot be hidden.\n"
            "Python's getpass would echo the value in clear text here.\n\n"
            "Run this from a real terminal, or use the file-based path:\n"
            "    python3 scripts/import_env.py --from /path/to/your.env\n",
            file=sys.stderr,
        )
        return 3

    # Real tty: echo is off, so nothing lands in the terminal, the
    # transcript, or shell history.
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        try:
            value = getpass.getpass(f"Paste value for {key} (input hidden): ").strip()
        except getpass.GetPassWarning:
            print("REFUSING: echo could not be disabled; nothing written.", file=sys.stderr)
            return 3

    if not value:
        print("Empty input — nothing changed.")
        return 2
    write_env({key: value})
    print("\n  Updated: " + fingerprint(key, value) + "\n")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--from", dest="src", metavar="PATH", help="import KEY=VALUE lines from this file")
    g.add_argument("--set", dest="key", metavar="KEY", help="prompt for one key with hidden input")
    g.add_argument("--check", action="store_true", help="show current config, masked")
    args = ap.parse_args()

    if args.check:
        return show_check()
    if args.key:
        return do_set(args.key)
    return do_import(Path(args.src).expanduser())


if __name__ == "__main__":
    raise SystemExit(main())
