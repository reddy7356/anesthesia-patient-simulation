#!/usr/bin/env bash
# install_aws_cli.sh — install AWS CLI v2 on this machine.
#
# Handles the things a one-line curl|unzip recipe gets wrong: CPU
# architecture, a missing unzip, a stale ./aws directory from a previous
# attempt, macOS vs Linux, and installing without root.
#
# Usage:
#   ./deploy/aws/install_aws_cli.sh              # system-wide (uses sudo)
#   ./deploy/aws/install_aws_cli.sh --user       # into ~/.local, no sudo
#
# Installs nothing if a working aws-cli/2.x is already on PATH.

set -euo pipefail

MODE="${1:-system}"

have() { command -v "$1" >/dev/null 2>&1; }

# ---- already installed? -------------------------------------------------
if have aws; then
    VER="$(aws --version 2>&1 || true)"
    case "$VER" in
        *aws-cli/2.*)
            echo "AWS CLI v2 already installed: ${VER}"
            echo "Nothing to do. Next:  aws configure"
            exit 0 ;;
        *aws-cli/1.*)
            echo "WARNING: found AWS CLI v1 (${VER})." >&2
            echo "         v2 is required. This installs v2 alongside it; if the" >&2
            echo "         old one keeps winning, remove it (e.g. pip uninstall awscli)." >&2
            echo "" >&2 ;;
    esac
fi

OS="$(uname -s)"
ARCH="$(uname -m)"
TMPDIR_WORK="$(mktemp -d)"
trap 'rm -rf "$TMPDIR_WORK"' EXIT

# ---- macOS --------------------------------------------------------------
if [ "$OS" = "Darwin" ]; then
    if have brew; then
        echo "Installing via Homebrew..."
        brew install awscli
    else
        echo "Installing via the official macOS package..."
        curl -fsSL "https://awscli.amazonaws.com/AWSCLIV2.pkg" -o "${TMPDIR_WORK}/AWSCLIV2.pkg"
        sudo installer -pkg "${TMPDIR_WORK}/AWSCLIV2.pkg" -target /
    fi
    hash -r 2>/dev/null || true
    aws --version
    echo ""
    echo "Done. Next:  aws configure"
    exit 0
fi

if [ "$OS" != "Linux" ]; then
    echo "ERROR: unsupported OS '${OS}'." >&2
    echo "       On Windows use:  winget install Amazon.AWSCLI" >&2
    echo "       (or run this from WSL, which reports Linux)" >&2
    exit 2
fi

# ---- Linux: pick the right build for this CPU ---------------------------
case "$ARCH" in
    x86_64|amd64)  PKG_ARCH="x86_64" ;;
    aarch64|arm64) PKG_ARCH="aarch64" ;;
    *)
        echo "ERROR: no AWS CLI v2 build for architecture '${ARCH}'." >&2
        exit 2 ;;
esac
echo "Detected: Linux ${ARCH} -> awscli-exe-linux-${PKG_ARCH}"

# ---- unzip is required and is frequently absent -------------------------
if ! have unzip; then
    echo "unzip not found; installing it..."
    if   have apt-get; then sudo apt-get update -y && sudo apt-get install -y unzip
    elif have dnf;     then sudo dnf install -y unzip
    elif have yum;     then sudo yum install -y unzip
    elif have zypper;  then sudo zypper --non-interactive install unzip
    elif have apk;     then sudo apk add --no-cache unzip
    elif have pacman;  then sudo pacman -Sy --noconfirm unzip
    else
        echo "ERROR: could not install unzip automatically. Install it, then re-run." >&2
        exit 2
    fi
fi

# ---- download + unpack into a scratch dir -------------------------------
# Unpacking in a temp dir (not the repo) avoids the classic failure where a
# leftover ./aws directory makes unzip prompt or the installer use stale files.
echo "Downloading AWS CLI v2..."
curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-${PKG_ARCH}.zip" \
     -o "${TMPDIR_WORK}/awscliv2.zip"

echo "Unpacking..."
unzip -q "${TMPDIR_WORK}/awscliv2.zip" -d "${TMPDIR_WORK}"

# ---- install ------------------------------------------------------------
if [ "$MODE" = "--user" ]; then
    # No root needed. Requires ~/.local/bin on PATH.
    echo "Installing into ~/.local (no sudo)..."
    "${TMPDIR_WORK}/aws/install" \
        --install-dir "${HOME}/.local/aws-cli" \
        --bin-dir "${HOME}/.local/bin" \
        --update
    # Verify via the absolute path: ~/.local/bin may not be on PATH yet in
    # this shell, so a bare `aws` here would report "command not found" even
    # though the install succeeded.
    AWS_BIN="${HOME}/.local/bin/aws"
    PATH_NOTE=""
    case ":${PATH}:" in
        *":${HOME}/.local/bin:"*) : ;;
        *) PATH_NOTE="yes" ;;
    esac
else
    if have sudo; then
        echo "Installing system-wide (sudo)..."
        sudo "${TMPDIR_WORK}/aws/install" --update
    elif [ "$(id -u)" = "0" ]; then
        echo "Installing system-wide (already root)..."
        "${TMPDIR_WORK}/aws/install" --update
    else
        echo "ERROR: no sudo and not root. Re-run without root:" >&2
        echo "       $0 --user" >&2
        exit 2
    fi
    AWS_BIN="/usr/local/bin/aws"
    PATH_NOTE=""
fi

hash -r 2>/dev/null || true
echo ""
if [ -x "$AWS_BIN" ]; then
    "$AWS_BIN" --version
else
    echo "ERROR: install finished but ${AWS_BIN} is not executable." >&2
    exit 2
fi

if [ -n "$PATH_NOTE" ]; then
    cat <<PATHMSG

IMPORTANT: ${HOME}/.local/bin is not on your PATH, so typing 'aws' will not
work yet. Add it, then reload:

  echo 'export PATH="\$HOME/.local/bin:\$PATH"' >> ~/.bashrc
  source ~/.bashrc
  aws --version
PATHMSG
fi
cat <<'EOF'

Done. Next steps:

  aws configure                    # access key id, secret, region (us-east-1)
  aws sts get-caller-identity      # should print your account and ARN

Get an access key at:
  IAM -> Users -> your user -> Security credentials -> Create access key

Then list your EC2 key pairs to confirm the name to pass to deploy.sh:

  aws ec2 describe-key-pairs --region us-east-1 \
    --query 'KeyPairs[].KeyName' --output table

EOF
