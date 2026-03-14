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
set +e
install_output=$(uv tool install "$STEPWISE_PKG" 2>&1)
install_rc=$?
set -e

if [ $install_rc -ne 0 ]; then
    if echo "$install_output" | grep -q "already exists"; then
        existing=$(command -v stepwise 2>/dev/null || echo "unknown")
        echo "A 'stepwise' command already exists at: $existing"
        printf "Replace it? [y/N] "
        read -r answer </dev/tty
        case "$answer" in
            y|Y|yes|Yes)
                uv tool install --force "$STEPWISE_PKG"
                ;;
            *)
                echo "Installation cancelled."
                exit 0
                ;;
        esac
    else
        echo "$install_output" >&2
        exit 1
    fi
fi

# ── Done ──────────────────────────────────────────────────────────────
echo ""
info "Stepwise installed successfully!"
echo ""
echo "  Try the demo:"
echo "    stepwise run @stepwise:welcome --watch       # interactive demo in the browser"
echo ""
echo "  Create your own:"
echo "    stepwise new my-flow                         # scaffold a flow"
echo "    stepwise run my-flow --watch                 # run it in the browser"
echo ""
echo "  Update later:"
echo "    stepwise update"
echo ""
