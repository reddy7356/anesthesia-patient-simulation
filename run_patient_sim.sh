#!/usr/bin/env bash
# run_patient_sim.sh — patient simulation launcher
#
# Usage:
#   ./run_patient_sim.sh <scenario_id> [dev|console]
#   ./run_patient_sim.sh --list
#
# This project is completely independent of the mock oral examination system.
# It has its own .venv, its own .env, its own worker port, and PSIM_*-namespaced
# options so a stale ORCH_* export cannot reach it.

set -euo pipefail
cd "$(dirname "$0")"

list_scenarios() {
    printf "\nAvailable scenarios:\n"
    for m in scenarios/*/; do
        [ -d "$m" ] || continue
        id=$(basename "$m")
        title=$(grep -m1 '^\*\*Title:\*\*' "$m/manifest.md" 2>/dev/null | sed 's/\*\*Title:\*\* //')
        printf "  %-16s %s\n" "$id" "${title:-(no manifest title)}"
    done
    printf "\n"
}

case "${1:-}" in
    ""|-h|--help) echo "Usage: $0 <scenario_id> [dev|console]"; list_scenarios; exit 0 ;;
    -l|--list)    list_scenarios; exit 0 ;;
esac

SCENARIO="$1"
MODE="${2:-dev}"

[ -d "scenarios/$SCENARIO" ] || { echo "ERROR: no scenario '$SCENARIO'." >&2; list_scenarios; exit 1; }
[ -x ".venv/bin/python" ] || { echo "ERROR: .venv/bin/python not found. See README Setup." >&2; exit 2; }
[ -f ".env" ] || { echo "ERROR: .env not found. cp .env.example .env and fill it in." >&2; exit 2; }

# python-dotenv does NOT override a variable already exported in the shell.
# The mock-oral system was once sent to the wrong LiveKit project by a stale
# ~/.zshrc export; the same trap applies here, so this file's .env wins.
# `env -u` also scrubs every ORCH_* leftover from the mock oral system.
read_env() { grep "^$1=" .env | head -1 | cut -d= -f2- ; }
LK_URL=$(read_env LIVEKIT_URL)
LK_KEY=$(read_env LIVEKIT_API_KEY)
LK_SECRET=$(read_env LIVEKIT_API_SECRET)

if [ "$MODE" = "dev" ] || [ "$MODE" = "start" ] || [ "$MODE" = "connect" ]; then
    if [ -z "$LK_URL" ] || [ -z "$LK_KEY" ] || [ -z "$LK_SECRET" ]; then
        echo "ERROR: LIVEKIT_URL / LIVEKIT_API_KEY / LIVEKIT_API_SECRET missing from .env" >&2
        echo "       (use '$0 $SCENARIO console' to test without a LiveKit room)" >&2
        exit 2
    fi
fi

echo ""
echo "════════════════════════════════════════════════════════════════"
echo "  ANESTHESIA PATIENT SIMULATION"
echo "  Scenario : $SCENARIO"
echo "  Mode     : $MODE"
[ -n "$LK_URL" ] && echo "  LiveKit  : $LK_URL"
echo "════════════════════════════════════════════════════════════════"
echo ""

exec env -u PYTHONPATH \
    $(env | grep -o '^ORCH_[A-Z_]*' | sed 's/^/-u /' | tr '\n' ' ') \
    PSIM_SCENARIO="$SCENARIO" \
    PSIM_STREAM_TTS="${PSIM_STREAM_TTS:-1}" \
    ${LK_URL:+LIVEKIT_URL="$LK_URL"} \
    ${LK_KEY:+LIVEKIT_API_KEY="$LK_KEY"} \
    ${LK_SECRET:+LIVEKIT_API_SECRET="$LK_SECRET"} \
    .venv/bin/python -m app.livekit_agent "$MODE"
