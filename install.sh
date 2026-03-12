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
info "Installing stepwise..."
if ! uv tool install stepwise-run@git+https://github.com/zackham/stepwise.git 2>/tmp/sw-install-err; then
    if grep -q "already exists" /tmp/sw-install-err; then
        printf "A 'stepwise' command already exists. Replace it? [y/N] "
        read -r answer
        case "$answer" in
            y|Y|yes|Yes)
                uv tool install --force stepwise-run@git+https://github.com/zackham/stepwise.git
                ;;
            *)
                echo "Installation cancelled."
                rm -f /tmp/sw-install-err
                exit 0
                ;;
        esac
    else
        cat /tmp/sw-install-err >&2
        rm -f /tmp/sw-install-err
        exit 1
    fi
    rm -f /tmp/sw-install-err
fi

# ── Done ──────────────────────────────────────────────────────────────
echo ""
info "Stepwise installed successfully!"
echo ""
echo "  Get started:"
echo "    stepwise init"
echo "    stepwise run hello.flow.yaml --watch"
echo ""
echo "  Update later:"
echo "    stepwise self-update"
echo ""
