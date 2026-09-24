#!/usr/bin/env bash
# put_secrets.sh — push your .env into encrypted SSM SecureString parameters.
#
# RUN THIS ON YOUR OWN MACHINE, from your VS Code terminal. Your keys go
# straight from your laptop to AWS KMS-encrypted storage. They are never
# printed, never committed, never pasted into a chat, and never passed to
# anyone else's sandbox.
#
# Usage:
#   ./deploy/aws/put_secrets.sh <stack-name> <region> [path-to-.env]
#
# Example:
#   ./deploy/aws/put_secrets.sh psim us-east-1 .env
#
# Verify afterwards (prints fingerprints only, never values):
#   ./deploy/aws/put_secrets.sh <stack-name> <region> --check

set -euo pipefail

STACK="${1:-}"
REGION="${2:-}"
ENV_FILE="${3:-.env}"

if [ -z "$STACK" ] || [ -z "$REGION" ]; then
    echo "Usage: $0 <stack-name> <region> [path-to-.env | --check]" >&2
    exit 1
fi

PREFIX="/psim/${STACK}"

# Keys that belong in SSM. PSIM_* tuning knobs are NOT secrets and are written
# by psim-fetch-env on the instance, so they are deliberately excluded here.
KEYS=(
    ANTHROPIC_API_KEY
    GROQ_API_KEY
    ELEVENLABS_API_KEY
    LIVEKIT_URL
    LIVEKIT_API_KEY
    LIVEKIT_API_SECRET
)

# Check the LOCAL file before touching AWS: a missing .env is the most common
# mistake and the operator should hear about it without first having to fix
# unrelated AWS credential problems.
if [ "$ENV_FILE" != "--check" ] && [ ! -f "$ENV_FILE" ]; then
    cat >&2 <<MSG

ERROR: no such file: ${ENV_FILE}

  A fresh clone has no .env -- it is gitignored deliberately, so that a
  credential can never end up in the repository. Create one here:

    cp .env.example .env
    chmod 600 .env

  Then fill in the keys. Either open it in VS Code, or avoid echoing the
  values at all:

    python3 scripts/import_env.py --set ANTHROPIC_API_KEY
    python3 scripts/import_env.py --set GROQ_API_KEY
    python3 scripts/import_env.py --set ELEVENLABS_API_KEY

  If your real .env lives elsewhere, point at it directly:

    $0 ${STACK} ${REGION} ~/path/to/your/.env

MSG
    exit 2
fi

if ! command -v aws >/dev/null 2>&1; then
    HERE_SEC="$(cd "$(dirname "$0")" && pwd)"
    APT_CAND=""
    command -v apt-cache >/dev/null 2>&1 && \
        APT_CAND="$(apt-cache policy awscli 2>/dev/null | awk '/Candidate:/{print $2}')"

    echo "" >&2
    echo "ERROR: AWS CLI v2 not found." >&2
    echo "" >&2
    case "$APT_CAND" in
        2.*)
            echo "  RUN THIS -- your distro packages AWS CLI v${APT_CAND}:" >&2
            echo "" >&2
            echo "      sudo apt install awscli" >&2
            echo "" >&2
            echo "  (Do NOT use 'snap install aws-cli' -- that is v1.)" >&2
            ;;
        *)
            echo "  RUN THIS -- the bundled installer:" >&2
            echo "" >&2
            echo "      ${HERE_SEC}/install_aws_cli.sh              # system-wide, uses sudo" >&2
            echo "      ${HERE_SEC}/install_aws_cli.sh --user       # into ~/.local, no sudo" >&2
            echo "" >&2
            echo "  On Windows (outside WSL):  winget install Amazon.AWSCLI" >&2
            ;;
    esac
    echo "" >&2
    echo "  Then run 'aws configure' and re-run this script." >&2
    echo "" >&2
    exit 2
fi

# Fail early and clearly if credentials are not configured.
if ! CALLER="$(aws sts get-caller-identity --region "$REGION" --query 'Arn' --output text 2>&1)"; then
    echo "" >&2
    echo "ERROR: AWS credentials are not working for region ${REGION}." >&2
    echo "       ${CALLER}" >&2
    echo "       Run 'aws configure' or export AWS_PROFILE first." >&2
    echo "" >&2
    exit 2
fi

fingerprint() {
    # Masked descriptor: length + short sha256. Safe to show anyone.
    local name="$1" value="$2" digest
    if [ -z "$value" ]; then
        printf '  %-22s (empty)\n' "$name"
        return
    fi
    digest="$(printf '%s' "$value" | shasum -a 256 2>/dev/null | cut -c1-8 \
              || printf '%s' "$value" | sha256sum | cut -c1-8)"
    case "$name" in
        *KEY|*SECRET|*TOKEN)
            printf '  %-22s set  len=%-4s sha256:%s\n' "$name" "${#value}" "$digest" ;;
        *)
            # URLs and ids are not secret; show them so you can spot a typo.
            printf '  %-22s %s\n' "$name" "$value" ;;
    esac
}

# ---- --check mode: report what is in SSM, masked -------------------------
if [ "$ENV_FILE" = "--check" ]; then
    echo ""
    echo "  SSM ${PREFIX}/*  (region ${REGION})"
    echo ""
    for KEY in "${KEYS[@]}"; do
        VAL="$(aws ssm get-parameter --name "${PREFIX}/${KEY}" --with-decryption \
                 --region "$REGION" --query 'Parameter.Value' --output text 2>/dev/null || echo '')"
        [ "$VAL" = "None" ] && VAL=""
        fingerprint "$KEY" "$VAL"
    done
    echo ""
    exit 0
fi

# ---- upload mode ---------------------------------------------------------
# Existence was already checked above, before any AWS call.

# Refuse a world-readable .env: if others can read it locally, fix that first.
PERMS="$(stat -f '%Lp' "$ENV_FILE" 2>/dev/null || stat -c '%a' "$ENV_FILE" 2>/dev/null || echo '')"
case "$PERMS" in
    600|400|640|440|"") : ;;
    *) echo "WARNING: ${ENV_FILE} has permissions ${PERMS}. Tighten it:  chmod 600 ${ENV_FILE}" >&2 ;;
esac

echo ""
echo "  Uploading to SSM ${PREFIX}/*  (region ${REGION}, encrypted with aws/ssm KMS key)"
echo ""

UPLOADED=0
SKIPPED=""

for KEY in "${KEYS[@]}"; do
    # Read the value without ever echoing it. Handles `export K=v`, quotes and
    # inline comments, and takes the LAST occurrence if the file repeats a key.
    VALUE="$(sed -n "s/^[[:space:]]*\(export[[:space:]]\+\)\?${KEY}[[:space:]]*=[[:space:]]*//p" "$ENV_FILE" \
             | tail -1 \
             | sed -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'\$/\1/" \
             | sed -e 's/[[:space:]]\+#.*$//' -e 's/[[:space:]]*$//')"

    # Treat the sandbox placeholders and empties as "not provided".
    case "$VALUE" in
        ""|placeholder-not-used-in-dryrun|changeme|"<your-key>")
            SKIPPED="${SKIPPED} ${KEY}"
            continue ;;
    esac

    # --overwrite makes this idempotent and is how you rotate a key later.
    # Value is passed via stdin-free argv to the AWS CLI only; it is not
    # echoed, and `set -x` is deliberately NOT enabled in this script.
    aws ssm put-parameter \
        --name "${PREFIX}/${KEY}" \
        --value "$VALUE" \
        --type SecureString \
        --overwrite \
        --region "$REGION" \
        --description "anesthesia-patient-simulation credential" \
        >/dev/null

    fingerprint "$KEY" "$VALUE"
    UPLOADED=$((UPLOADED + 1))
done

echo ""
echo "  Uploaded ${UPLOADED} parameter(s)."
[ -n "$SKIPPED" ] && echo "  Skipped (empty/placeholder in ${ENV_FILE}):${SKIPPED}"
cat <<EOF

  Next:
    # refresh .env on the instance and start the worker
    aws ssm start-session --target <instance-id> --region ${REGION}
    sudo /usr/local/bin/psim-fetch-env
    sudo systemctl enable --now psim
    journalctl -u psim -f     # or: tail -f /opt/psim/app/logs/worker.log

  Your local ${ENV_FILE} was only read, never modified or uploaded as a file.

EOF
