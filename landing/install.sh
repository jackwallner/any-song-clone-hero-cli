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
BIN_DIR="$INSTALL_DIR/bin"
VENV_DIR="$INSTALL_DIR/venv"
NODE_MIN=18
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

die() { fail "$1"; exit 1; }

have() { command -v "$1" &>/dev/null; }

banner

# ── OS + package manager ──
step "Checking system"
OS="$(uname -s)"
PKG=""
case "$OS" in
  Darwin) ok "macOS detected" ;;
  Linux)
    if grep -qi microsoft /proc/version 2>/dev/null; then
      ok "Linux detected (WSL)"
    else
      ok "Linux detected"
    fi
    if   have apt-get; then PKG="apt"
    elif have dnf;     then PKG="dnf"
    elif have pacman;  then PKG="pacman"
    else warn "No supported package manager found (apt/dnf/pacman)"
    fi
    ;;
  *) die "Unsupported OS: $OS. SongHero works on macOS and Linux (including WSL)." ;;
esac

SUDO=""
if [ "$(id -u)" != "0" ]; then
  have sudo && SUDO="sudo"
fi

APT_UPDATED=0
pkg_install() {
  # pkg_install <package>...
  case "$PKG" in
    apt)
      if [ "$APT_UPDATED" = "0" ]; then
        $SUDO apt-get update -qq || true
        APT_UPDATED=1
      fi
      $SUDO apt-get install -y -qq "$@"
      ;;
    dnf)    $SUDO dnf install -y -q "$@" ;;
    pacman) $SUDO pacman -Sy --noconfirm --needed "$@" ;;
    *)      return 1 ;;
  esac
}

# ── Homebrew (macOS only) ──
if [ "$OS" = "Darwin" ]; then
  if ! have brew; then
    warn "Homebrew not found. Installing..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    eval "$(/opt/homebrew/bin/brew shellenv 2>/dev/null || /usr/local/bin/brew shellenv 2>/dev/null)"
    ok "Homebrew installed"
  else
    ok "Homebrew ready"
  fi
fi

# ── Node.js ──
# The CLI is a Node program with a `#!/usr/bin/env node` shebang. Without Node
# on PATH the only symptom is a bare `env: 'node': No such file or directory`,
# so install it before anything else.
step "Installing Node.js ${NODE_MIN}+"

node_major() {
  have node || return 1
  node -v 2>/dev/null | sed 's/^v//; s/\..*//'
}

node_ok() {
  local major
  major="$(node_major)" || return 1
  [ -n "$major" ] && [ "$major" -ge "$NODE_MIN" ] 2>/dev/null
}

if node_ok; then
  ok "Node $(node -v) already installed"
else
  if have node; then
    warn "Node $(node -v) is too old (need ${NODE_MIN}+). Upgrading..."
  else
    warn "Node.js not found. Installing..."
  fi

  if [ "$OS" = "Darwin" ]; then
    brew install node || true
  else
    pkg_install nodejs npm || true
    if ! node_ok && [ "$PKG" = "apt" ]; then
      # Ubuntu 22.04 and older ship Node 12, which cannot run the CLI.
      warn "Distro Node is too old. Installing Node 20 from NodeSource..."
      pkg_install ca-certificates curl gnupg || true
      curl -fsSL https://deb.nodesource.com/setup_20.x | $SUDO -E bash - >/dev/null 2>&1 || true
      $SUDO apt-get install -y -qq nodejs || true
    fi
  fi

  if node_ok; then
    ok "Node $(node -v) installed"
  else
    die "Could not install Node ${NODE_MIN}+. Install it manually, then rerun this script."
  fi
fi

# ── Python 3 ──
step "Installing Python 3"
if ! have python3 && ! have python; then
  if [ "$OS" = "Darwin" ]; then
    brew install python || true
  else
    pkg_install python3 python3-pip python3-venv || true
  fi
fi
have python3 || have python || die "Python 3 not found and could not be installed."
PYTHON="$(command -v python3 || command -v python)"
ok "Python: $($PYTHON --version 2>&1)"

# ── ffmpeg ──
step "Installing ffmpeg"
if have ffmpeg; then
  ok "ffmpeg already installed"
else
  if [ "$OS" = "Darwin" ]; then
    brew install ffmpeg || true
  else
    pkg_install ffmpeg || true
  fi
  have ffmpeg && ok "ffmpeg installed" || warn "ffmpeg missing — audio/video conversion will fail"
fi

# ── Python analysis dependencies (isolated venv) ──
# Debian/Ubuntu mark the system Python as externally managed (PEP 668), so a
# plain `pip3 install` fails there. A venv under the install dir avoids that and
# keeps librosa's dependency tree out of the system Python.
step "Installing Python analysis dependencies"
mkdir -p "$INSTALL_DIR" "$BIN_DIR"
if [ ! -x "$VENV_DIR/bin/python" ]; then
  if ! "$PYTHON" -m venv "$VENV_DIR" 2>/dev/null; then
    [ "$PKG" = "apt" ] && pkg_install python3-venv || true
    "$PYTHON" -m venv "$VENV_DIR" || die "Could not create a Python venv at $VENV_DIR"
  fi
fi
"$VENV_DIR/bin/python" -m pip install --quiet --upgrade pip >/dev/null 2>&1 || true
if "$VENV_DIR/bin/python" -m pip install --quiet librosa soundfile numpy scipy; then
  ok "librosa, soundfile, numpy, scipy installed"
else
  die "Python dependency install failed. Rerun: $VENV_DIR/bin/pip install librosa soundfile numpy scipy"
fi

# ── yt-dlp ──
# Distro yt-dlp packages go stale within weeks and then fail on every YouTube
# URL, so pin the official standalone build inside the install dir instead.
step "Installing yt-dlp"
if [ "$OS" = "Darwin" ] && have brew; then
  have yt-dlp && ok "yt-dlp already installed" || { brew install yt-dlp && ok "yt-dlp installed"; }
else
  if curl -fsSL https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp -o "$BIN_DIR/yt-dlp"; then
    chmod +x "$BIN_DIR/yt-dlp"
    ok "yt-dlp installed to $BIN_DIR"
  elif have yt-dlp; then
    warn "Download failed; using the yt-dlp already on PATH"
  else
    die "Could not install yt-dlp"
  fi
fi

# ── Clone & install ──
step "Installing SongHero"
have git || pkg_install git || true
have git || die "git not found. Install git, then rerun this script."

if [ -d "$INSTALL_DIR/.git" ]; then
  info "Updating existing install..."
  git -C "$INSTALL_DIR" pull --rebase --quiet && ok "Updated" || warn "Update failed, continuing"
else
  info "Cloning repository..."
  # $INSTALL_DIR already holds bin/ and venv/, so clone beside it and move in.
  TMP_CLONE="$(mktemp -d)"
  git clone --depth 1 "$REPO" "$TMP_CLONE/repo" >/dev/null 2>&1 || die "Clone failed"
  ( cd "$TMP_CLONE/repo" && tar cf - . ) | ( cd "$INSTALL_DIR" && tar xf - )
  rm -rf "$TMP_CLONE"
  ok "Installed to $INSTALL_DIR"
fi
chmod +x "$INSTALL_DIR/index.js"

info "Installing Node dependencies..."
( cd "$INSTALL_DIR" && npm install --omit=dev --silent ) && ok "Node dependencies installed" \
  || die "npm install failed. Rerun: cd $INSTALL_DIR && npm install"

# ── songhero launcher ──
# A wrapper rather than a symlink to index.js: it can say what is missing
# instead of leaving the shell to print a bare `env: 'node': not found`.
rm -f "$INSTALL_DIR/songhero"
cat > "$BIN_DIR/songhero" <<'LAUNCHER'
#!/usr/bin/env bash
SONGHERO_HOME="$HOME/.songhero"
if ! command -v node &>/dev/null; then
  echo "songhero: Node.js is not installed or not on your PATH." >&2
  echo "  Reinstall with: curl -sSL https://songhero.jackwallner.com/install.sh | bash" >&2
  exit 127
fi
if [ -x "$SONGHERO_HOME/venv/bin/python" ]; then
  export SONGHERO_PYTHON="$SONGHERO_HOME/venv/bin/python"
fi
export PATH="$SONGHERO_HOME/bin:$PATH"
exec node "$SONGHERO_HOME/index.js" "$@"
LAUNCHER
chmod +x "$BIN_DIR/songhero"
ok "songhero command linked"

# ── PATH setup ──
step "Setting up PATH"
SHELL_RC=""
case "$SHELL" in
  */zsh)  SHELL_RC="$HOME/.zshrc" ;;
  */bash) SHELL_RC="$HOME/.bashrc" ;;
  *)      SHELL_RC="$HOME/.profile" ;;
esac

PATH_LINE="export PATH=\"\$HOME/.songhero/bin:\$PATH\""
if ! grep -qF "$PATH_LINE" "$SHELL_RC" 2>/dev/null; then
  {
    echo ""
    echo "# SongHero"
    echo "$PATH_LINE"
  } >> "$SHELL_RC"
  ok "Added to $SHELL_RC"
else
  ok "Already in $SHELL_RC"
fi
# Drop the pre-bin/ PATH entry older installs wrote.
if [ -f "$SHELL_RC" ] && grep -qF 'export PATH="$HOME/.songhero:$PATH"' "$SHELL_RC" 2>/dev/null; then
  TMP_RC="$(mktemp)"
  grep -vF 'export PATH="$HOME/.songhero:$PATH"' "$SHELL_RC" > "$TMP_RC" && mv "$TMP_RC" "$SHELL_RC"
  info "Removed the old PATH entry"
fi
export PATH="$BIN_DIR:$PATH"

# ── Gemini key check ──
step "Gemini API key"
if [ -n "$GEMINI_KEY" ]; then
  ok "GEMINI_API_KEY detected in environment"
else
  warn "No GEMINI_API_KEY set"
  info "Get a free key at: ${CYAN}https://aistudio.google.com/apikey${NC}"
  info "Then run: ${CYAN}export GEMINI_API_KEY=\"your-key-here\"${NC}"
fi

# ── Verify ──
# The old installer could report success on a machine that had no Node at all,
# so every dependency gets checked for real before we claim to be done.
step "Verifying install"
PROBLEMS=0
check() {
  if eval "$2" >/dev/null 2>&1; then ok "$1"; else fail "$1"; PROBLEMS=$((PROBLEMS + 1)); fi
}
check "node $(node -v 2>/dev/null)"        "node -e 'process.exit(0)'"
check "ffmpeg"                              "command -v ffmpeg"
check "yt-dlp"                              "command -v yt-dlp"
check "python analysis deps"                "\"$VENV_DIR/bin/python\" -c 'import librosa, numpy, scipy, soundfile'"
check "songhero launches"                   "\"$BIN_DIR/songhero\" --help"

if [ "$PROBLEMS" -gt 0 ]; then
  echo ""
  die "$PROBLEMS check(s) failed. Fix the items marked ✗ above, then rerun this script."
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
