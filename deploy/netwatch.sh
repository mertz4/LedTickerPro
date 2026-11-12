#!/usr/bin/env bash
# Network watchdog: brings the Pi back by itself when it has dropped off the network (typically the Wi-Fi chip
# staying stuck after the router was unplugged and came back).
#   - offline for RESTART_AFTER seconds: restart Wi-Fi, and again every RESTART_AFTER while it stays offline
#     (first the radio off and on, after that the Wi-Fi driver and NetworkManager too)
#   - offline for REBOOT_AFTER seconds: reboot, once per outage. If the router really is down, the Pi does not
#     keep rebooting: it goes back to restarting Wi-Fi until the network has been seen again.
# "Online" = the router answers a ping (or, failing that, 1.1.1.1 does).
# deploy/setup-pi.sh copies this to /usr/local/sbin/ledticker-netwatch and ledticker-netwatch.timer runs it as
# root every minute. Logs: journalctl -t ledticker-netwatch
set -u

RESTART_AFTER="${RESTART_AFTER:-180}"
REBOOT_AFTER="${REBOOT_AFTER:-900}"
RUN="${NETWATCH_RUN:-/run/ledticker-netwatch}"          # cleared by a reboot
KEEP="${NETWATCH_KEEP:-/var/lib/ledticker-netwatch}"    # survives a reboot

say() { logger -t ledticker-netwatch -- "$*" 2>/dev/null || true; echo "$*"; }

online() {
  local gw
  gw="$(ip -4 route show default 2>/dev/null | awk '$1 == "default" && $2 == "via" { print $3; exit }')"
  if [[ -n "$gw" ]] && ping -c 2 -W 3 -q "$gw" >/dev/null 2>&1; then
    return 0
  fi
  ping -c 2 -W 3 -q 1.1.1.1 >/dev/null 2>&1
}

restart_wifi() {            # $1 = how many times it has been restarted in this outage so far
  if [[ "$1" -ge 1 ]]; then
    say "restarting the Wi-Fi driver and NetworkManager"
    if modprobe -r brcmfmac_wcc brcmfmac 2>/dev/null || modprobe -r brcmfmac 2>/dev/null; then
      sleep 2
      modprobe brcmfmac || true
    fi
    systemctl restart NetworkManager 2>/dev/null || true
  elif command -v nmcli >/dev/null 2>&1; then
    say "turning Wi-Fi off and on"
    nmcli radio wifi off || true
    sleep 3
    nmcli radio wifi on || true
  else
    local dev
    for dev in /sys/class/net/wl*; do
      [[ -e "$dev" ]] || continue
      say "restarting ${dev##*/}"
      ip link set "${dev##*/}" down || true
      sleep 3
      ip link set "${dev##*/}" up || true
    done
  fi
}

main() {
  mkdir -p "$RUN" "$KEEP"
  if online; then
    if [[ -f "$RUN/down_since" ]]; then
      say "back online"
    fi
    rm -f "$RUN/down_since" "$RUN/kicked_at" "$RUN/kicks" "$KEEP/rebooted"
    return 0
  fi

  local up since down kicked_at kicks
  up="$(cut -d. -f1 /proc/uptime)"                       # seconds since boot: unaffected by clock changes
  [[ -f "$RUN/down_since" ]] || echo "$up" >"$RUN/down_since"
  since="$(cat "$RUN/down_since")"
  down=$((up - since))
  kicked_at="$(cat "$RUN/kicked_at" 2>/dev/null || echo "$since")"
  kicks="$(cat "$RUN/kicks" 2>/dev/null || echo 0)"

  if (( down >= REBOOT_AFTER )) && [[ ! -f "$KEEP/rebooted" ]]; then
    say "offline for ${down} s: rebooting (once for this outage)"
    touch "$KEEP/rebooted"
    sync
    systemctl reboot
    return 0
  fi
  if (( up - kicked_at >= RESTART_AFTER )); then
    say "offline for ${down} s"
    echo "$up" >"$RUN/kicked_at"
    echo $((kicks + 1)) >"$RUN/kicks"
    restart_wifi "$kicks"
  fi
}

main "$@"
