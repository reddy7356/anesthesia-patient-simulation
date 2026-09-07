"""Spec section 29 item 10 / section 14: the mock oral system is unchanged.

This is the automated form of the golden rule. Run it after every milestone.
It asserts the protected repository is still at its baseline commit with a
clean working tree. It never writes to that repository.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

BASELINE = "73e0377194ee7a7e28fca07e9262a31b84b75295"
CANDIDATES = [
    Path.home() / "perplexity-mock-oral",
    Path.home() / "mnt" / "perplexity-mock-oral",
]


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, check=True,
    ).stdout.strip()


def main() -> int:
    repo = next((p for p in CANDIDATES if (p / ".git").is_dir()), None)
    if repo is None:
        print("SKIP  protected repo not reachable from this machine/session")
        print("      (checked: " + ", ".join(str(p) for p in CANDIDATES) + ")")
        return 0

    print(f"Protected repo: {repo}")
    head = _git(repo, "rev-parse", "HEAD")
    dirty = _git(repo, "status", "--porcelain")

    ok = True
    if head == BASELINE:
        print(f"  PASS  HEAD == baseline ({BASELINE[:12]})")
    else:
        print(f"  FAIL  HEAD moved: {head} != {BASELINE}")
        ok = False

    if not dirty:
        print("  PASS  working tree clean — zero modifications")
    else:
        print("  FAIL  working tree dirty:")
        for line in dirty.splitlines():
            print(f"          {line}")
        print("\n  STOP. Do not continue development until these are reverted")
        print("  or explicitly reviewed by the project owner.")
        ok = False

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
