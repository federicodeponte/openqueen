#!/usr/bin/env bash
# OpenQueen installer — sets up Python env, deps, directories, and systemd services
# Usage: curl -fsSL https://raw.githubusercontent.com/federicodeponte/openqueen/main/install.sh | bash
set -euo pipefail

OQ_HOME="${OPENQUEEN_HOME:-$HOME/openqueen}"
REPO_URL="https://github.com/federicodeponte/openqueen"
VENV="$OQ_HOME/.venv"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()  { echo -e "${GREEN}[openqueen]${NC} $*"; }
warn()  { echo -e "${YELLOW}[openqueen]${NC} $*"; }
error() { echo -e "${RED}[openqueen]${NC} $*" >&2; exit 1; }

# ── Detect upgrade vs fresh install ───────────────────────────────────────────
UPGRADING=false
if [ -f "$OQ_HOME/config.json" ]; then
    UPGRADING=true
    warn "Existing install detected at $OQ_HOME — upgrading (config.json and auth/ preserved)"
fi

# ── Requirements ──────────────────────────────────────────────────────────────
command -v python3 >/dev/null 2>&1 || error "Python 3.10+ required. Install it first."
PY_VER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)' || error "Python 3.10+ required (got $PY_VER)"
command -v git >/dev/null 2>&1 || error "git required. Install it first."

# ── Detect transport choice ────────────────────────────────────────────────────
TRANSPORT="${OQ_TRANSPORT:-}"
if [ -z "$TRANSPORT" ]; then
    echo ""
    echo "Choose your transport:"
    echo "  1) WhatsApp (scan QR code on first run)"
    echo "  2) Telegram — experimental (create bot via @BotFather)"
    read -rp "Enter 1 or 2 [1]: " choice
    choice="${choice:-1}"
    case "$choice" in
        2) TRANSPORT="telegram" ;;
        *) TRANSPORT="whatsapp" ;;
    esac
fi
info "Transport: $TRANSPORT"

# ── Create directory structure ─────────────────────────────────────────────────
info "Creating $OQ_HOME directory structure..."
mkdir -p "$OQ_HOME"/{logs/sessions,logs/transcripts,auth,context/global,context/skills,tasks}

# ── Clone or update repo ───────────────────────────────────────────────────────
if [ -d "$OQ_HOME/.git" ]; then
    info "Updating from $REPO_URL..."
    git -C "$OQ_HOME" pull --ff-only
else
    info "Cloning $REPO_URL..."
    # Clone into temp dir and copy (preserves existing config/auth)
    TMP_REPO=$(mktemp -d)
    git clone --depth=1 "$REPO_URL" "$TMP_REPO"
    # Copy everything except config.json and auth/ (preserve existing)
    rsync -a --exclude='config.json' --exclude='auth/' "$TMP_REPO/" "$OQ_HOME/"
    rm -rf "$TMP_REPO"
fi

# ── Python venv + deps ────────────────────────────────────────────────────────
info "Setting up Python venv..."
python3 -m venv "$VENV"
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet -r "$OQ_HOME/requirements.txt"

# Install openqueen CLI to PATH
install -m 755 "$OQ_HOME/cli.py" /usr/local/bin/openqueen 2>/dev/null || \
    sudo install -m 755 "$OQ_HOME/cli.py" /usr/local/bin/openqueen

# ── WhatsApp: Node.js + Baileys ───────────────────────────────────────────────
if [ "$TRANSPORT" = "whatsapp" ]; then
    command -v node >/dev/null 2>&1 || error "Node.js required for WhatsApp transport. Install Node.js 18+ first."
    NODE_VER=$(node --version | sed 's/v//' | cut -d. -f1)
    [ "$NODE_VER" -ge 18 ] 2>/dev/null || error "Node.js 18+ required (got v$NODE_VER)"
    warn "WhatsApp/Baileys violates WhatsApp ToS. Your account may be banned. Proceed at own risk."
    info "Installing Node.js deps for wa-listener..."
    cd "$OQ_HOME/wa-listener" && npm install --silent
    cd "$OQ_HOME"
fi

# ── Write env file template (if not upgrading) ────────────────────────────────
ENV_FILE="$OQ_HOME/.env"
if [ ! -f "$ENV_FILE" ]; then
    cat > "$ENV_FILE" << ENVEOF
# OpenQueen environment — set your API keys here
OPENQUEEN_HOME=$OQ_HOME
GOOGLE_API_KEY=your_gemini_api_key_here
OQ_TRANSPORT=$TRANSPORT
OQ_WORKER=claude

# Telegram (if OQ_TRANSPORT=telegram)
# OQ_TELEGRAM_TOKEN=your_bot_token_here
# OQ_TELEGRAM_CHAT_ID=your_chat_id_here

# WhatsApp (if OQ_TRANSPORT=whatsapp)
# OQ_GROUP_JID=your_group_jid@g.us

# Workspace to auto-scan for projects (optional — or use projects.json)
# OQ_WORKSPACE=$HOME/projects
ENVEOF
    info "Created $ENV_FILE — edit it to add your API key"
fi

# ── Systemd service ───────────────────────────────────────────────────────────
if command -v systemctl >/dev/null 2>&1; then
    SYSTEMD_DIR="/etc/systemd/system"
    if [ "$TRANSPORT" = "telegram" ]; then
        cat > "$SYSTEMD_DIR/openqueen-listen.service" << SVCEOF
[Unit]
Description=OpenQueen — Telegram-controlled coding agent
After=network.target

[Service]
Type=simple
EnvironmentFile=$OQ_HOME/.env
ExecStart=$VENV/bin/python3 $OQ_HOME/listen.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
SVCEOF
    else
        cat > "$SYSTEMD_DIR/openqueen-listen.service" << SVCEOF
[Unit]
Description=OpenQueen — WhatsApp-controlled coding agent
After=network.target

[Service]
Type=simple
EnvironmentFile=$OQ_HOME/.env
ExecStart=$VENV/bin/python3 $OQ_HOME/listen.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
SVCEOF
        cat > "$SYSTEMD_DIR/openqueen-wa.service" << SVCEOF
[Unit]
Description=OpenQueen WhatsApp listener
After=network.target

[Service]
Type=simple
EnvironmentFile=$OQ_HOME/.env
WorkingDirectory=$OQ_HOME/wa-listener
ExecStart=/usr/bin/node $OQ_HOME/wa-listener/index.js
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
SVCEOF
    fi
    systemctl daemon-reload 2>/dev/null || true
    info "Systemd services written. Enable with: systemctl enable --now openqueen-listen"
fi

# ── Run tests to verify install ───────────────────────────────────────────────
info "Running tests to verify install..."
cd "$OQ_HOME" && OPENQUEEN_HOME="$OQ_HOME" "$VENV/bin/pytest" tests/ -q 2>&1 | tail -3
cd "$OLDPWD"

echo ""
info "Install complete!"
if [ "$UPGRADING" = true ]; then
    echo "  Upgraded — restart services to apply: systemctl restart openqueen-listen"
else
    echo "  Next steps:"
echo "    1. Edit $OQ_HOME/.env and set your GOOGLE_API_KEY"
echo "    2. Run 'openqueen init' to configure and connect your transport"
echo "    3. Run 'systemctl enable --now openqueen-listen' to start"
fi
