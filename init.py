#!/usr/bin/env python3
"""
openqueen-init — interactive setup wizard.
Run after install.sh to configure API keys and connect transport.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

OQ_HOME = Path(os.environ.get("OPENQUEEN_HOME", str(Path.home() / "openqueen")))
ENV_FILE = OQ_HOME / ".env"

GREEN = "\033[0;32m"
YELLOW = "\033[1;33m"
RED = "\033[0;31m"
CYAN = "\033[0;36m"
NC = "\033[0m"


def info(msg):  print(f"{GREEN}[openqueen]{NC} {msg}")
def warn(msg):  print(f"{YELLOW}[openqueen]{NC} {msg}")
def error(msg): print(f"{RED}[openqueen]{NC} {msg}", file=sys.stderr); sys.exit(1)
def ask(prompt, default=""):
    suffix = f" [{default}]" if default else ""
    val = input(f"{CYAN}  >{NC} {prompt}{suffix}: ").strip()
    return val or default


def load_env() -> dict:
    env = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


def save_env(env: dict):
    lines = []
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                k = stripped.split("=", 1)[0].strip()
                if k in env:
                    lines.append(f"{k}={env.pop(k)}")
                else:
                    lines.append(line)
            else:
                lines.append(line)
    # Append any new keys not already in file
    for k, v in env.items():
        lines.append(f"{k}={v}")
    ENV_FILE.write_text("\n".join(lines) + "\n")


def check_transport(env: dict) -> str:
    transport = env.get("OQ_TRANSPORT", "")
    if not transport:
        print("\nTransport not configured. Run install.sh first.")
        sys.exit(1)
    return transport


def setup_telegram(env: dict):
    print(f"\n{YELLOW}=== Telegram Setup ==={NC}")
    print("  1. Message @BotFather on Telegram")
    print("  2. Send /newbot and follow prompts")
    print("  3. Copy the token it gives you\n")

    token = ask("Telegram bot token", env.get("OQ_TELEGRAM_TOKEN", ""))
    if not token or token == "your_bot_token_here":
        warn("No token provided — skipping Telegram setup")
        return

    # Verify token
    info("Verifying token...")
    import urllib.request, urllib.error
    try:
        url = f"https://api.telegram.org/bot{token}/getMe"
        with urllib.request.urlopen(url, timeout=10) as r:
            data = json.loads(r.read())
        botname = data["result"]["username"]
        info(f"Token valid — bot: @{botname}")
    except Exception as e:
        warn(f"Could not verify token: {e}")

    env["OQ_TELEGRAM_TOKEN"] = token

    chat_id = ask("Your Telegram chat ID (send /start to your bot, then check getUpdates)", env.get("OQ_TELEGRAM_CHAT_ID", ""))
    if chat_id:
        env["OQ_TELEGRAM_CHAT_ID"] = chat_id
        # Send a test message to verify chat_id
        try:
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            data = json.dumps({"chat_id": chat_id, "text": "✓ OpenQueen connected! Send me a task."}).encode()
            with urllib.request.urlopen(
                urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}),
                timeout=10,
            ) as r:
                json.loads(r.read())
            info("Test message sent successfully — check your Telegram!")
        except Exception as e:
            warn(f"Could not send test message: {e} — check your chat_id")


def setup_whatsapp(env: dict):
    print(f"\n{YELLOW}=== WhatsApp Setup ==={NC}")
    print("  The WhatsApp listener will show a QR code.")
    print("  Scan it with your phone (WhatsApp > Linked Devices).\n")

    wa_dir = OQ_HOME / "wa-listener"
    if not wa_dir.exists():
        warn("wa-listener/ not found — was install.sh run?")
        return

    info("When you start the service (systemctl start openqueen-wa), a QR code will appear in the logs.")
    info("Scan it with WhatsApp > Linked Devices > Link a Device.")
    print()

    group_jid = ask("WhatsApp group JID (e.g. 1234567890-1234@g.us)", env.get("OQ_GROUP_JID", ""))
    if group_jid:
        env["OQ_GROUP_JID"] = group_jid


def setup_api_key(env: dict):
    print(f"\n{YELLOW}=== Gemini API Key ==={NC}")
    print("  Get yours at: https://aistudio.google.com/apikey\n")

    key = ask("Gemini API key", env.get("GOOGLE_API_KEY", ""))
    if key and key != "your_gemini_api_key_here":
        env["GOOGLE_API_KEY"] = key

        # Quick validation
        info("Validating API key...")
        try:
            import urllib.request, urllib.error
            url = f"https://generativelanguage.googleapis.com/v1beta/models?key={key}"
            with urllib.request.urlopen(url, timeout=10) as r:
                data = json.loads(r.read())
            models = [m["name"] for m in data.get("models", []) if "gemini" in m["name"]]
            info(f"API key valid — {len(models)} Gemini models available")
        except Exception as e:
            warn(f"Could not validate API key: {e}")
    else:
        warn("No API key provided — agent will not work")


def setup_worker(env: dict):
    print(f"\n{YELLOW}=== Worker Configuration ==={NC}")
    print("  Which AI worker should agents use?")
    print("  1) claude  (Claude Code CLI — best quality)")
    print("  2) codex   (OpenAI Codex CLI)")
    print("  3) gemini  (Gemini CLI)\n")

    worker_map = {"1": "claude", "2": "codex", "3": "gemini"}
    current = env.get("OQ_WORKER", "claude")
    choice = ask("Enter 1-3", "1")
    env["OQ_WORKER"] = worker_map.get(choice, current)
    info(f"Worker: {env['OQ_WORKER']}")


def verify_install():
    print(f"\n{YELLOW}=== Verifying Install ==={NC}")
    venv = OQ_HOME / ".venv"
    if not venv.exists():
        warn("Virtual env not found — run install.sh first")
        return False

    result = subprocess.run(
        [str(venv / "bin" / "pytest"), "tests/", "-q", "--tb=no"],
        capture_output=True, text=True, cwd=str(OQ_HOME)
    )
    if result.returncode == 0:
        passed = [l for l in result.stdout.splitlines() if "passed" in l]
        info(f"Tests: {passed[-1] if passed else 'OK'}")
        return True
    else:
        warn("Some tests failed — check with: pytest tests/ -v")
        return False


def print_next_steps(transport: str, env: dict):
    print(f"\n{GREEN}=== Setup Complete ==={NC}")
    print(f"\n  Config: {ENV_FILE}")
    print(f"  Home:   {OQ_HOME}\n")

    if transport == "telegram":
        print("  Start: systemctl start openqueen")
        print("  Logs:  journalctl -u openqueen -f")
        if env.get("OQ_TELEGRAM_TOKEN") and env.get("OQ_TELEGRAM_CHAT_ID"):
            print(f"\n  Message your bot on Telegram to test:")
            print(f"  'fix the README in my project'")
    else:
        print("  Start: systemctl start openqueen openqueen-wa")
        print("  Logs:  journalctl -u openqueen -f")
        print("\n  Send a WhatsApp message to your group to test:")
        print("  '!task fix the README in my project'")

    print()


def main():
    print(f"\n{GREEN}OpenQueen Setup Wizard{NC}")
    print(f"  Home: {OQ_HOME}\n")

    if not OQ_HOME.exists():
        error(f"OpenQueen not installed at {OQ_HOME}. Run install.sh first.")

    env = load_env()
    transport = check_transport(env)

    setup_api_key(env)
    setup_worker(env)

    if transport == "telegram":
        setup_telegram(env)
    else:
        setup_whatsapp(env)

    save_env(env)
    info(f"Config saved to {ENV_FILE}")

    verify_install()
    print_next_steps(transport, env)


if __name__ == "__main__":
    main()
