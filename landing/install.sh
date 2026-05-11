#!/usr/bin/env bash
set -e

# ── SongHero Installer ──
# curl -sSL https://songhero.jackwallner.com/install.sh | bash

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
PURPLE='\033[0;35m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

REPO="https://github.com/jackwallner/any-song-clone-hero-cli.git"
INSTALL_DIR="$HOME/.songhero"
GEMINI_KEY="${GEMINI_API_KEY:-}"

banner() {
  echo ""
  echo -e "${PURPLE}${BOLD}   🎸  SongHero — AI Clone Hero Charts  🎸${NC}"
  echo -e "   ${CYAN}https://songhero.jackwallner.com${NC}"
  echo ""
}

info()  { echo -e "   ${BLUE}➜${NC} $1"; }
ok()    { echo -e "   ${GREEN}✓${NC} $1"; }
warn()  { echo -e "   ${YELLOW}⚠${NC} $1"; }
fail()  { echo -e "   ${RED}✗${NC} $1"; }
step()  { echo -e "\n ${PURPLE}${BOLD}━${NC} $1 ${PURPLE}━${NC}"; }

banner

# ── OS check ──
step "Checking system"
OS="$(uname -s)"
if [ "$OS" = "Darwin" ]; then
  ok "macOS detected"
elif [ "$OS" = "Linux" ]; then
  ok "Linux detected"
else
  fail "Unsupported OS: $OS. SongHero works on macOS and Linux."
  exit 1
fi

# ── Homebrew ──
if ! command -v brew &>/dev/null; then
  if [ "$OS" = "Darwin" ]; then
    warn "Homebrew not found. Installing..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    eval "$(/opt/homebrew/bin/brew shellenv 2>/dev/null || /usr/local/bin/brew shellenv 2>/dev/null)"
    ok "Homebrew installed"
  else
    warn "Homebrew not found (Linux). Install it: https://brew.sh"
  fi
else
  ok "Homebrew ready"
fi

# ── System deps ──
step "Installing system dependencies"
BREW_DEPS=(yt-dlp ffmpeg)
MISSING=()
for dep in "${BREW_DEPS[@]}"; do
  if command -v "$dep" &>/dev/null; then
    ok "$dep already installed"
  else
    warn "Installing $dep..."
    brew install "$dep" && ok "$dep installed" || fail "$dep failed"
  fi
done

# ── Python deps ──
step "Installing Python dependencies"
if ! command -v python3 &>/dev/null && ! command -v python &>/dev/null; then
  fail "Python 3 not found. Install it: brew install python"
  exit 1
fi
PYTHON=$(command -v python3 || command -v python)
ok "Python: $($PYTHON --version 2>&1)"

PIP_DEPS=(librosa soundfile numpy scipy)
$PYTHON -m pip install --quiet "${PIP_DEPS[@]}" 2>/dev/null && \
  ok "librosa, soundfile, numpy, scipy installed" || \
  warn "Some Python packages may need manual install: pip3 install librosa soundfile numpy scipy"

# ── Clone & install ──
step "Installing SongHero"
if [ -d "$INSTALL_DIR" ]; then
  warn "SongHero already installed at $INSTALL_DIR"
  info "Updating..."
  git -C "$INSTALL_DIR" pull --rebase --quiet && ok "Updated" || warn "Update failed, continuing"
else
  info "Cloning repository..."
  git clone --depth 1 "$REPO" "$INSTALL_DIR" && ok "Cloned to $INSTALL_DIR" || {
    fail "Clone failed"
    exit 1
  }
fi
chmod +x "$INSTALL_DIR/index.js"
ok "index.js executable"

# ── PATH setup ──
step "Setting up PATH"
SHELL_RC=""
case "$SHELL" in
  */zsh)  SHELL_RC="$HOME/.zshrc" ;;
  */bash) SHELL_RC="$HOME/.bashrc" ;;
  *)      SHELL_RC="$HOME/.profile" ;;
esac

PATH_LINE="export PATH=\"\$HOME/.songhero:\$PATH\""
if ! grep -qF "$PATH_LINE" "$SHELL_RC" 2>/dev/null; then
  echo "" >> "$SHELL_RC"
  echo "# SongHero" >> "$SHELL_RC"
  echo "$PATH_LINE" >> "$SHELL_RC"
  ok "Added to $SHELL_RC"
else
  ok "Already in $SHELL_RC"
fi

# ── Symlink for 'songhero' command ──
if [ ! -L "$INSTALL_DIR/songhero" ]; then
  ln -sf "$INSTALL_DIR/index.js" "$INSTALL_DIR/songhero"
fi
chmod +x "$INSTALL_DIR/songhero"
ok "songhero command linked"

# ── Gemini key check ──
step "Gemini API key"
if [ -n "$GEMINI_KEY" ]; then
  ok "GEMINI_API_KEY detected in environment"
else
  warn "No GEMINI_API_KEY set"
  info "Get a free key at: ${CYAN}https://aistudio.google.com/apikey${NC}"
  info "Then run: ${CYAN}export GEMINI_API_KEY=\"your-key-here\"${NC}"
fi

# ── Done ──
echo ""
echo -e " ${GREEN}${BOLD}╭─────────────────────────────────────────────╮${NC}"
echo -e " ${GREEN}${BOLD}│${NC}        🎸  SongHero is ready!  🎸          ${GREEN}${BOLD}│${NC}"
echo -e " ${GREEN}${BOLD}╰─────────────────────────────────────────────╯${NC}"
echo ""
info "Restart your terminal or run: ${CYAN}source $SHELL_RC${NC}"
echo ""
info "Try it:"
echo -e "   ${BOLD}songhero https://open.spotify.com/track/0VjIjW4GlUZAMYd2vXMi3b --gemini${NC}"
echo ""
info "For music videos:"
echo -e "   ${BOLD}songhero https://open.spotify.com/track/... --gemini --video${NC}"
echo ""
