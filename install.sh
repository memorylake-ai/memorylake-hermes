#!/usr/bin/env bash
set -euo pipefail

# MemoryLake plugin installer for hermes-agent
#
# Downloads memorylake-hermes from PyPI and installs to hermes-agent
# plugin directory.
#
# Usage:
#   ./install.sh                                                    # install, then prompt
#   ./install.sh --api-key sk-... --project-id proj-...             # install with credentials
#   ./install.sh --version 1.0.0                                    # install specific version
#   ./install.sh --host https://custom.example                      # override MemoryLake host

VERSION=""
API_KEY=""
PROJECT_ID=""
HOST=""

# Parse arguments
while [[ $# -gt 0 ]]; do
    case "$1" in
        --version) VERSION="$2"; shift 2 ;;
        --api-key) API_KEY="$2"; shift 2 ;;
        --project-id) PROJECT_ID="$2"; shift 2 ;;
        --host) HOST="$2"; shift 2 ;;
        *) echo "Unknown argument: $1"; exit 1 ;;
    esac
done

# Locate hermes-agent directory via hermes CLI
if ! command -v hermes &>/dev/null; then
    echo "Error: 'hermes' command not found. Please install hermes first."
    exit 1
fi

HERMES_HOME="$(dirname "$(hermes config path)")"
HERMES_DIR="$HERMES_HOME/hermes-agent"

TARGET_DIR="$HERMES_DIR/plugins/memory/memorylake"

if [[ ! -d "$HERMES_DIR/plugins/memory" ]]; then
    echo "Error: $HERMES_DIR/plugins/memory/ does not exist."
    exit 1
fi

# Download wheel from PyPI
TMPDIR=$(mktemp -d)
trap 'rm -rf "$TMPDIR"' EXIT

PKG="memorylake-hermes"
if [[ -n "$VERSION" ]]; then
    PKG="memorylake-hermes==$VERSION"
fi

echo "Downloading $PKG from PyPI..."
pip download --no-deps --dest "$TMPDIR" "$PKG" 2>&1 | tail -1

# Find the downloaded wheel
WHL=$(find "$TMPDIR" -name 'memorylake_hermes-*.whl' | head -1)
if [[ -z "$WHL" ]]; then
    echo "Error: Failed to download memorylake-hermes wheel."
    exit 1
fi

echo "Extracting $(basename "$WHL")..."
unzip -q -o "$WHL" -d "$TMPDIR/extracted"

# Copy plugin files (recursive to include skills/ subdirectory)
mkdir -p "$TARGET_DIR"
cp -R "$TMPDIR/extracted/memorylake_hermes/"* "$TARGET_DIR/"

echo "Installed memorylake plugin to $TARGET_DIR"

# Activate memorylake as memory provider
hermes config set memory.provider memorylake
echo "Set memory.provider = memorylake"


# Configure credentials
if [[ -z "$API_KEY" ]]; then
    read -rp "MemoryLake API key: " API_KEY
fi
if [[ -z "$PROJECT_ID" ]]; then
    read -rp "MemoryLake project ID: " PROJECT_ID
fi

if [[ -z "$API_KEY" || -z "$PROJECT_ID" ]]; then
    echo "Warning: API key or project ID not set. Run 'hermes memory setup' to configure later."
    exit 0
fi

# Write credentials to .env
ENV_FILE="$(hermes config env-path)"
touch "$ENV_FILE"

# Update or append each variable
pairs=("MEMORYLAKE_API_KEY=$API_KEY" "MEMORYLAKE_PROJECT_ID=$PROJECT_ID")
if [[ -n "$HOST" ]]; then
    pairs+=("MEMORYLAKE_HOST=$HOST")
fi

for pair in "${pairs[@]}"; do
    KEY="${pair%%=*}"
    if grep -q "^${KEY}=" "$ENV_FILE" 2>/dev/null; then
        sed -i.bak "s|^${KEY}=.*|${pair}|" "$ENV_FILE" && rm -f "$ENV_FILE.bak"
    else
        echo "$pair" >> "$ENV_FILE"
    fi
done

echo ""
echo "Done! MemoryLake is ready to use."
echo "Start a new session to activate."
