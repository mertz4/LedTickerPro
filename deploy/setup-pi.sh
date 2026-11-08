#!/usr/bin/env bash
# One-time Pi setup: Python venv, ledticker systemd service, Tailscale + HTTPS.
# Run from the repo root on the Pi:   bash deploy/setup-pi.sh
# Safe to re-run.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_USER="${SUDO_USER:-$USER}"
TS_HOSTNAME="${TS_HOSTNAME:-ledticker}"
PORT=8000

if [[ $EUID -eq 0 && -z "${SUDO_USER:-}" ]]; then
  echo "Run this as your normal user (it calls sudo itself), not as root." >&2
  exit 1
fi

echo "==> Repo: $ROOT   user: $RUN_USER"

# ---- 1. Python environment -------------------------------------------------
echo "==> Installing Python packages"
sudo apt-get update -qq
sudo apt-get install -y -qq python3-venv python3-pip curl
if [[ ! -x "$ROOT/.venv/bin/python" ]]; then
  python3 -m venv "$ROOT/.venv"
fi
"$ROOT/.venv/bin/python" -m pip install -q --upgrade pip
"$ROOT/.venv/bin/python" -m pip install -q -e "$ROOT"

if [[ -f "$ROOT/secrets.env" ]] && ! grep -q "^LEDTICKER_SUPERUSERS=" "$ROOT/secrets.env"; then
  echo "    NOTE: add LEDTICKER_SUPERUSERS=you@gmail.com to secrets.env (your Tailscale login), then:"
  echo "      sudo systemctl restart ledticker"
fi
if [[ ! -f "$ROOT/secrets.env" ]]; then
  echo "    NOTE: no secrets.env yet. Copy it from your PC (it is git-ignored), e.g.:"
  echo "      scp secrets.env $RUN_USER@<pi-address>:$ROOT/"
fi

# ---- 2. systemd service ----------------------------------------------------
echo "==> Installing ledticker.service"
sed -e "s|__USER__|$RUN_USER|g" -e "s|__ROOT__|$ROOT|g" \
  "$ROOT/deploy/ledticker.service" | sudo tee /etc/systemd/system/ledticker.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable ledticker.service
sudo systemctl restart ledticker.service

# ---- 3. Tailscale ----------------------------------------------------------
if ! command -v tailscale >/dev/null 2>&1; then
  echo "==> Installing Tailscale"
  curl -fsSL https://tailscale.com/install.sh | sh
fi

if ! tailscale status >/dev/null 2>&1; then
  echo "==> Logging in to Tailscale. Open the URL below and sign in with YOUR account (the superuser)."
  sudo tailscale up --hostname="$TS_HOSTNAME"
fi

# Publish http://127.0.0.1:8000 as https://<hostname>.<tailnet>.ts.net (tailnet only, persists across reboots).
# The first time, Tailscale may print a link asking you to enable HTTPS certificates for the tailnet.
echo "==> Publishing the dashboard on the tailnet"
sudo tailscale serve --bg "$PORT"

echo
echo "Done."
tailscale serve status || true
echo
echo "Service logs:   journalctl -u ledticker -f"
echo "Restart:        sudo systemctl restart ledticker"
