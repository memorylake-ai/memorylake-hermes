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

VERSION=""
API_KEY=""
PROJECT_ID=""

# Parse arguments
while [[ $# -gt 0 ]]; do
    case "$1" in
        --version) VERSION="$2"; shift 2 ;;
        --api-key) API_KEY="$2"; shift 2 ;;
        --project-id) PROJECT_ID="$2"; shift 2 ;;
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

# Copy plugin files
mkdir -p "$TARGET_DIR"
cp "$TMPDIR/extracted/memorylake_hermes/"* "$TARGET_DIR/"

echo "Installed memorylake plugin to $TARGET_DIR"

# Activate memorylake as memory provider
hermes config set memory.provider memorylake
echo "Set memory.provider = memorylake"

# Write env_passthrough so MEMORYLAKE_* vars are forwarded to the agent process
CONFIG_FILE="$(hermes config path)"
if [[ -f "$CONFIG_FILE" ]]; then
    # Remove existing env_passthrough block (if any) to avoid duplicates
    sed -i.bak '/^env_passthrough:/,/^[^ ]/{ /^env_passthrough:/d; /^  - /d; }' "$CONFIG_FILE"
    rm -f "$CONFIG_FILE.bak"
fi
cat >> "$CONFIG_FILE" <<'ENVBLOCK'
env_passthrough:
  - HERMES_HOME
  - MEMORYLAKE_HOST
  - MEMORYLAKE_API_KEY
  - MEMORYLAKE_PROJECT_ID
  - MEMORYLAKE_USER_ID
  - MEMORYLAKE_TOP_K
  - MEMORYLAKE_SEARCH_THRESHOLD
  - MEMORYLAKE_RERANK
  - MEMORYLAKE_MEMORY_MODE
  - MEMORYLAKE_AUTO_UPLOAD
  - MEMORYLAKE_WEB_SEARCH_INCLUDE_DOMAINS
  - MEMORYLAKE_WEB_SEARCH_EXCLUDE_DOMAINS
  - MEMORYLAKE_WEB_SEARCH_COUNTRY
  - MEMORYLAKE_WEB_SEARCH_TIMEZONE
ENVBLOCK
echo "Configured env_passthrough for MEMORYLAKE_* variables"

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
for pair in "MEMORYLAKE_API_KEY=$API_KEY" "MEMORYLAKE_PROJECT_ID=$PROJECT_ID"; do
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
