#!/bin/sh
# Stepwise universal installer
# Usage: curl -fsSL https://stepwise.run/install.sh | sh
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

# ── Done ──────────────────────────────────────────────────────────────
echo ""
info "Stepwise installed successfully!"
echo ""

# Interactive welcome prompt (plain shell — works when piped from curl)
echo "  The welcome flow walks through a simulated dev workflow:"
echo "    plan → implement (parallel) → test → review → deploy"
echo ""
echo "  Try it now?"
echo "    1) Browser  (opens a live web UI)"
echo "    2) Terminal  (runs in your terminal)"
echo "    3) Skip"
echo ""
printf "  Choice [1/2/3]: "
read -r choice </dev/tty
case "$choice" in
    1)
        echo ""
        exec stepwise run @stepwise:welcome --watch </dev/tty
        ;;
    2)
        echo ""
        exec stepwise run @stepwise:welcome </dev/tty
        ;;
    *)
        echo ""
        echo "  No problem! Get started:"
        echo "    stepwise new my-flow                    # scaffold a flow"
        echo "    stepwise run my-flow --watch            # run it in the browser"
        echo ""
        echo "  Update later:"
        echo "    stepwise update"
        echo ""
        ;;
esac
