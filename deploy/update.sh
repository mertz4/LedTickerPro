#!/usr/bin/env bash
# Auto-update: if origin/master has a new commit, pull it, install, run the tests and restart the ticker.
# If the tests fail or the app doesn't come back up, roll back to the previous commit and keep running that.
# Run by ledticker-update.timer every few minutes (installed by deploy/setup-pi.sh). Manual run:
#   sudo systemctl start ledticker-update      logs: journalctl -u ledticker-update
# Wrapped in main() so bash reads the whole script before running it: the pull may replace this file.
set -euo pipefail

main() {
  local ROOT BRANCH PY STATE LOCK
  ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
  BRANCH="${LEDTICKER_BRANCH:-master}"
  PY="$ROOT/.venv/bin/python"
  STATE="$ROOT/data/update"            # git-ignored
  mkdir -p "$STATE"
  cd "$ROOT"

  # One run at a time.
  exec 9>"$STATE/lock"
  flock -n 9 || { echo "Another update is running."; return 0; }

  git fetch --quiet origin "$BRANCH"
  local here there
  here="$(git rev-parse HEAD)"
  there="$(git rev-parse "origin/$BRANCH")"
  [[ "$here" == "$there" ]] && return 0

  if [[ -f "$STATE/failed" && "$(cat "$STATE/failed")" == "$there" ]]; then
    return 0                            # already tried this commit and rolled back; wait for a newer one
  fi

  # Never clobber work done on the Pi itself.
  local current
  current="$(git rev-parse --abbrev-ref HEAD)"
  if [[ "$current" != "$BRANCH" ]]; then
    echo "On branch '$current', not '$BRANCH'. Skipping. (git checkout $BRANCH to resume auto-updates)"
    return 0
  fi
  if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
    echo "Tracked files were edited on the Pi. Skipping so they aren't overwritten:"
    git status --short --untracked-files=no
    return 1
  fi

  echo "==> Updating ${here:0:7} -> ${there:0:7}"
  git log --oneline "$here..$there"
  if ! git merge --ff-only --quiet "origin/$BRANCH"; then
    echo "Local $BRANCH has commits that aren't on GitHub (can't fast-forward). Skipping."
    return 1
  fi

  local deploy_changed
  deploy_changed="$(git diff --name-only "$here" "$there" -- deploy/ledticker.service deploy/setup-pi.sh \
    deploy/netwatch.sh deploy/ledticker-netwatch.service deploy/ledticker-netwatch.timer)"

  if pip_install && run_tests && restart_and_check; then
    rm -f "$STATE/failed"
    echo "==> Now running ${there:0:7}"
    if [[ -n "$deploy_changed" ]]; then
      echo "NOTE: the service/setup files changed. Re-run: bash deploy/setup-pi.sh"
    fi
    return 0
  fi

  echo "==> Update failed. Rolling back to ${here:0:7}"
  echo "$there" >"$STATE/failed"
  git reset --hard --quiet "$here"       # safe: the tree was clean before the merge
  pip_install || true
  if [[ "$RESTARTED" == 1 ]]; then       # tests failing means the old version never stopped: no restart needed
    restart_and_check || echo "WARNING: the previous version didn't come back up either. Check: journalctl -u ledticker"
  fi
  return 1
}

RESTARTED=0

pip_install() {
  echo "--> pip install"
  "$PY" -m pip install -q -e "$ROOT[dev]"
}

run_tests() {
  echo "--> pytest"
  "$PY" -m pytest -q -x -p no:cacheprovider
}

restart_and_check() {
  echo "--> restarting ledticker"
  RESTARTED=1
  sudo -n systemctl restart ledticker.service || return 1
  # Up = the web server answers at all (any HTTP status; with --auth tailscale a local request gets "No access").
  local i code
  for i in $(seq 1 30); do
    sleep 2
    code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 2 http://127.0.0.1:8000/ || true)"
    if [[ "$code" != "000" ]] && systemctl is-active --quiet ledticker.service; then
      echo "    up (HTTP $code)"
      return 0
    fi
  done
  echo "    not responding after 60 s"
  journalctl -u ledticker.service -n 30 --no-pager || true
  return 1
}

main "$@"
