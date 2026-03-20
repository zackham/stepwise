#!/bin/sh
# Stepwise universal installer
# Usage: curl -fsSL https://raw.githubusercontent.com/zackham/stepwise/master/install.sh | sh
set -e

BOLD='\033[1m'
DIM='\033[2m'
RESET='\033[0m'

info()  { printf "${BOLD}%s${RESET}\n" "$*"; }
dim()   { printf "${DIM}%s${RESET}\n" "$*"; }
error() { printf "Error: %s\n" "$*" >&2; exit 1; }

# ── Check OS ──────────────────────────────────────────────────────────
OS="$(uname -s)"
case "$OS" in
    Linux|Darwin) ;;
    *) error "Unsupported OS: $OS (Linux and macOS only)" ;;
esac

# ── Ensure uv is available ────────────────────────────────────────────
if ! command -v uv >/dev/null 2>&1; then
    info "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh

    # Source the env file uv's installer creates
    if [ -f "$HOME/.local/bin/env" ]; then
        . "$HOME/.local/bin/env"
    elif [ -f "$HOME/.cargo/env" ]; then
        . "$HOME/.cargo/env"
    fi

    # Verify it worked
    if ! command -v uv >/dev/null 2>&1; then
        export PATH="$HOME/.local/bin:$PATH"
    fi
    command -v uv >/dev/null 2>&1 || error "Failed to install uv. Install manually: https://docs.astral.sh/uv/"
fi

dim "Using uv at $(command -v uv)"

# ── Install stepwise ──────────────────────────────────────────────────
STEPWISE_PKG="stepwise-run@git+https://github.com/zackham/stepwise.git"
info "Installing stepwise..."
uv tool install --force --reinstall "$STEPWISE_PKG"

# ── Install acpx (agent protocol CLI, needed for agent/LLM steps) ────
if command -v npm >/dev/null 2>&1; then
    if ! command -v acpx >/dev/null 2>&1; then
        info "Installing acpx..."
        npm install -g acpx
    else
        dim "acpx already installed at $(command -v acpx)"
    fi
else
    echo ""
    echo "  Warning: npm not found — skipping acpx install."
    echo "  Agent and LLM steps require acpx. Install Node.js, then run:"
    echo "    npm install -g acpx"
fi

# ── Done ──────────────────────────────────────────────────────────────
echo ""
info "Stepwise installed successfully!"
echo ""

echo "  Try the demo (browser):"
echo ""
echo "    stepwise run @stepwise:welcome --watch"
echo ""
echo "  Or in the terminal:"
echo ""
echo "    stepwise run @stepwise:welcome"
echo ""
echo "  Create your own flow:"
echo ""
echo "    stepwise new my-flow"
echo "    stepwise run my-flow --watch"
echo ""
echo "  Update later:"
echo ""
echo "    stepwise update"
echo ""
