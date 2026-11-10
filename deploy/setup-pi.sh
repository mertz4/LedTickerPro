#!/usr/bin/env bash
# One-time Pi setup: Python venv, LED panel library, ledticker systemd service, Tailscale + HTTPS.
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
sudo apt-get install -y -qq python3-venv python3-pip curl git
if [[ ! -x "$ROOT/.venv/bin/python" ]]; then
  python3 -m venv "$ROOT/.venv"
fi
"$ROOT/.venv/bin/python" -m pip install -q --upgrade pip
"$ROOT/.venv/bin/python" -m pip install -q -e "$ROOT[dev]"   # [dev] = pytest, so auto-updates can test before going live

# ---- 1b. LED panel library (SPWM fork, needed for the FM6373 panels) ------
# Cloned next to the repo, built into the venv (scikit-build-core + cmake), demos built for bring-up.
# Rebuilds only when the fork's commit changes. Pin a commit with: LED_LIB_REF=<sha> bash deploy/setup-pi.sh
LED_LIB_URL="https://github.com/kingdo9/rpi-rgb-led-matrix_pwm_experiment.git"
LED_LIB_DIR="$(dirname "$ROOT")/rpi-rgb-led-matrix_pwm_experiment"
LED_LIB_REF="${LED_LIB_REF:-master}"
if grep -qs "Raspberry Pi" /proc/device-tree/model; then
  echo "==> LED library: $LED_LIB_URL ($LED_LIB_REF)"
  sudo apt-get install -y -qq build-essential cmake python3-dev cython3
  if [[ ! -d "$LED_LIB_DIR/.git" ]]; then
    git clone --quiet "$LED_LIB_URL" "$LED_LIB_DIR"
  fi
  git -C "$LED_LIB_DIR" fetch --quiet origin
  git -C "$LED_LIB_DIR" checkout --quiet "$LED_LIB_REF"
  git -C "$LED_LIB_DIR" pull --quiet --ff-only 2>/dev/null || true   # no-op when pinned to a commit
  LED_LIB_SHA="$(git -C "$LED_LIB_DIR" rev-parse HEAD)"
  MARKER="$ROOT/.venv/.rgbmatrix-commit"
  if [[ "$(cat "$MARKER" 2>/dev/null)" != "$LED_LIB_SHA" ]] || ! "$ROOT/.venv/bin/python" -c "import rgbmatrix" 2>/dev/null; then
    echo "    building ${LED_LIB_SHA:0:7} (a few minutes on a Pi 4)"
    # Failures here don't stop the rest of the setup: the dashboard runs fine without the panels.
    if "$ROOT/.venv/bin/python" -m pip install -q "$LED_LIB_DIR"; then
      echo "$LED_LIB_SHA" >"$MARKER"
    else
      echo "    WARNING: the LED library didn't build; the panels stay off. Output above." >&2
    fi
    make -s -C "$LED_LIB_DIR/examples-api-use" >/dev/null \
      || echo "    WARNING: the fork's demo didn't build (only needed for bring-up)." >&2
  else
    echo "    already built (${LED_LIB_SHA:0:7})"
  fi
  # The Pi's onboard audio uses the same PWM hardware; the library refuses to run while it's loaded.
  if [[ ! -f /etc/modprobe.d/blacklist-rgb-matrix.conf ]]; then
    echo "blacklist snd_bcm2835" | sudo tee /etc/modprobe.d/blacklist-rgb-matrix.conf >/dev/null
    echo "    NOTE: turned off onboard audio for the LED library. Reboot once: sudo reboot"
  fi
else
  echo "==> Not a Raspberry Pi: skipping the LED library"
fi

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

# ---- 2b. Auto-update from GitHub (deploy/update.sh) ------------------------
echo "==> Installing auto-update (checks origin/master every 5 minutes)"
for unit in ledticker-update.service ledticker-update.timer; do
  sed -e "s|__USER__|$RUN_USER|g" -e "s|__ROOT__|$ROOT|g" \
    "$ROOT/deploy/$unit" | sudo tee "/etc/systemd/system/$unit" >/dev/null
done
# The updater runs as $RUN_USER and may restart the ticker, and nothing else, without a password.
SUDOERS_TMP="$(mktemp)"
echo "$RUN_USER ALL=(root) NOPASSWD: $(command -v systemctl) restart ledticker.service" >"$SUDOERS_TMP"
if sudo visudo -cqf "$SUDOERS_TMP"; then
  sudo install -m 0440 -o root -g root "$SUDOERS_TMP" /etc/sudoers.d/ledticker-update
else
  echo "    WARNING: couldn't write the sudoers rule; auto-update won't be able to restart the ticker." >&2
fi
rm -f "$SUDOERS_TMP"
sudo systemctl daemon-reload
sudo systemctl enable --now ledticker-update.timer
if ! git -C "$ROOT" ls-remote --quiet origin >/dev/null 2>&1; then
  echo "    NOTE: this Pi can't read the GitHub repo yet, so auto-update will fail until it can."
  echo "      Private repo: add a deploy key (see README, 'Auto-update from GitHub')."
fi

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
echo "Update now:     sudo systemctl start ledticker-update"
echo "Update logs:    journalctl -u ledticker-update"
echo "LED test:       sudo systemctl stop ledticker && sudo $ROOT/.venv/bin/python -m ledticker matrix-test"
