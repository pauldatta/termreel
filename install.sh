#!/usr/bin/env bash
# ==============================================================================
# TermReel Universal Installer
# Installs the pre-compiled TermReel standalone binary for Linux / macOS.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/pauldatta/termreel/main/install.sh | bash
# ==============================================================================

set -euo pipefail

REPO="pauldatta/termreel"
GITHUB_URL="https://github.com/${REPO}"

# Colors
BOLD="$(tput bold 2>/dev/null || echo '')"
CYAN="$(tput setaf 6 2>/dev/null || echo '')"
GREEN="$(tput setaf 2 2>/dev/null || echo '')"
YELLOW="$(tput setaf 3 2>/dev/null || echo '')"
RED="$(tput setaf 1 2>/dev/null || echo '')"
RESET="$(tput sgr0 2>/dev/null || echo '')"

info() {
    echo "${CYAN}${BOLD}[termreel-install]${RESET} $*"
}

success() {
    echo "${GREEN}${BOLD}[termreel-install] ✅${RESET} $*"
}

warn() {
    echo "${YELLOW}${BOLD}[termreel-install] ⚠️${RESET} $*"
}

error() {
    echo "${RED}${BOLD}[termreel-install] ❌${RESET} $*" >&2
}

# 1. Detect OS and Architecture
OS="$(uname -s | tr '[:upper:]' '[:lower:]')"
ARCH="$(uname -m)"

case "${OS}" in
    linux*)  PLATFORM="linux" ;;
    darwin*) PLATFORM="darwin" ;;
    *)
        error "Unsupported operating system: ${OS}. TermReel requires Linux or macOS."
        exit 1
        ;;
esac

case "${ARCH}" in
    x86_64|amd64)   TARGET_ARCH="x86_64" ;;
    arm64|aarch64)  TARGET_ARCH="arm64" ;;
    *)
        error "Unsupported architecture: ${ARCH}."
        exit 1
        ;;
esac

BINARY_NAME="termreel-${PLATFORM}-${TARGET_ARCH}"
info "Detected platform: ${BOLD}${PLATFORM}/${TARGET_ARCH}${RESET}"

# 2. Determine installation directory
if [ -n "${TERMREEL_INSTALL_DIR:-}" ]; then
    INSTALL_DIR="${TERMREEL_INSTALL_DIR}"
elif [ -w "/usr/local/bin" ]; then
    INSTALL_DIR="/usr/local/bin"
else
    INSTALL_DIR="${HOME}/.local/bin"
fi

mkdir -p "${INSTALL_DIR}"
TARGET_PATH="${INSTALL_DIR}/termreel"

# 3. Download Release Binary or Fallback to uv tool
DOWNLOAD_URL="${GITHUB_URL}/releases/latest/download/${BINARY_NAME}"
info "Fetching release binary from: ${DOWNLOAD_URL}"

TEMP_FILE="$(mktemp "/tmp/termreel_bin_XXXXXX")"
trap 'rm -f "${TEMP_FILE}"' EXIT

if curl -fsSL "${DOWNLOAD_URL}" -o "${TEMP_FILE}" 2>/dev/null; then
    chmod +x "${TEMP_FILE}"
    mv "${TEMP_FILE}" "${TARGET_PATH}"
    success "Installed binary to ${BOLD}${TARGET_PATH}${RESET}"
else
    warn "Pre-compiled binary not found on GitHub Releases. Falling back to uv/pip installation..."
    if command -v uv >/dev/null 2>&1; then
        info "Installing with uv..."
        uv tool install "git+${GITHUB_URL}.git" --force
        success "Installed TermReel via uv tool!"
    elif command -v pipx >/dev/null 2>&1; then
        info "Installing with pipx..."
        pipx install "git+${GITHUB_URL}.git" --force
        success "Installed TermReel via pipx!"
    elif command -v pip3 >/dev/null 2>&1; then
        info "Installing with pip3..."
        pip3 install "git+${GITHUB_URL}.git"
        success "Installed TermReel via pip3!"
    else
        error "Could not download binary and neither uv, pipx, nor pip is installed."
        exit 1
    fi
fi

# 4. Check PATH
case ":${PATH}:" in
    *:"${INSTALL_DIR}":*) ;;
    *)
        warn "${INSTALL_DIR} is not in your PATH."
        echo "   Add it to your shell configuration (.bashrc / .zshrc):"
        echo "   export PATH=\"${INSTALL_DIR}:\$PATH\""
        ;;
esac

# 5. Check FFmpeg dependency
if ! command -v ffmpeg >/dev/null 2>&1; then
    warn "FFmpeg is not installed or not in PATH."
    echo "   TermReel requires FFmpeg for video rendering."
    if [ "${PLATFORM}" = "darwin" ]; then
        echo "   Install via Homebrew: brew install ffmpeg"
    elif [ -f "/etc/debian_version" ]; then
        echo "   Install via APT: sudo apt update && sudo apt install -y ffmpeg"
    elif [ -f "/etc/redhat-release" ]; then
        echo "   Install via DNF: sudo dnf install -y ffmpeg"
    elif [ -f "/etc/arch-release" ]; then
        echo "   Install via Pacman: sudo pacman -S ffmpeg"
    fi
else
    success "FFmpeg detected: $(ffmpeg -version 2>&1 | head -n 1)"
fi

echo ""
echo "${GREEN}${BOLD}🎉 TermReel installation complete!${RESET}"
echo "Run ${BOLD}termreel --help${RESET} or ${BOLD}termreel probe <cli>${RESET} to get started."
