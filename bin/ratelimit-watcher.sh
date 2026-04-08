#!/bin/bash
# Rate limit watcher for Synapse and Bâtisseur tmux sessions.
# Detects when Claude hits its plan limit, notifies user via Telegram,
# auto-selects "wait for reset", then notifies again when unblocked.

set -u

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

# ── Config ──────────────────────────────────────────────────────────────────
OWNER_CHAT_ID="8351648514"
STATE_DIR="/tmp/openclaw-ratelimit"
POLL_INTERVAL=60
LOG_FILE="/Users/sylvain/.openclaw/logs/ratelimit-watcher.log"

mkdir -p "$STATE_DIR"

# Session → bot token mapping (bash 3 compatible)
# Format: "session_name:bot_token" per line
SESSIONS_CONFIG=(
  "synapse:8450836625:AAHghq-vfasppPFjmy7J-XgUPD8pXF0orxs"
)
# Bâtisseur runs under OpenClaw gateway, not tmux — skip for now

log() {
  echo "[$(date '+%Y-%m-%dT%H:%M:%S')] $*" | tee -a "$LOG_FILE"
}

send_telegram() {
  local token="$1"
  local text="$2"
  curl -s "https://api.telegram.org/bot${token}/sendMessage" \
    --data-urlencode "chat_id=${OWNER_CHAT_ID}" \
    --data-urlencode "text=${text}" \
    --data-urlencode "parse_mode=Markdown" > /dev/null 2>&1
}

# Calculate hours until reset given "5pm", "11am", etc.
hours_until() {
  local target_str="$1"
  python3 -c "
import re, datetime
m = re.match(r'(\d+)(am|pm)', '$target_str'.lower())
if not m: print('?'); exit()
hour = int(m.group(1))
if m.group(2) == 'pm' and hour != 12: hour += 12
if m.group(2) == 'am' and hour == 12: hour = 0
now = datetime.datetime.now()
target = now.replace(hour=hour, minute=0, second=0, microsecond=0)
if target < now: target += datetime.timedelta(days=1)
diff = (target - now).total_seconds() / 3600
print(f'{diff:.1f}')
" 2>/dev/null || echo "?"
}

check_session() {
  local session="$1"
  local token="$2"
  local flag_file="${STATE_DIR}/${session}.blocked"

  # Session exists?
  if ! tmux has-session -t "$session" 2>/dev/null; then
    return
  fi

  # Capture current pane
  local pane
  pane=$(tmux capture-pane -p -t "$session" 2>/dev/null || echo "")

  # Detect rate-limit menu
  if echo "$pane" | grep -qE "You've hit your limit|Stop and wait for limit|rate-limit-options"; then
    if [ ! -f "$flag_file" ]; then
      # First detection — notify + auto-select option 1
      local reset_time hours_left
      reset_time=$(echo "$pane" | grep -oE "resets [0-9]+(am|pm)" | head -1 | awk '{print $2}')
      hours_left=$(hours_until "$reset_time")

      local msg
      if [ "$hours_left" != "?" ]; then
        msg="⏸️ Je dois mettre tes messages en file, je vais te revenir dans ${hours_left}h environ (reset à ${reset_time})."
      else
        msg="⏸️ Je dois mettre tes messages en file, je reviens dès que ma limite horaire est réinitialisée."
      fi

      log "$session: rate-limit detected (reset=$reset_time, hours=$hours_left)"
      send_telegram "$token" "$msg"

      # Auto-select option 1 (Stop and wait for limit to reset)
      sleep 2
      tmux send-keys -t "$session" Enter 2>/dev/null || true

      # Save state
      echo "$reset_time" > "$flag_file"
    fi
  else
    # Not blocked — if previously blocked, notify resume
    if [ -f "$flag_file" ]; then
      log "$session: rate-limit cleared, resuming"
      send_telegram "$token" "▶️ Je reprends, je traite tes messages en attente maintenant."
      rm -f "$flag_file"
    fi
  fi
}

log "Rate-limit watcher started (interval: ${POLL_INTERVAL}s)"

while true; do
  for config in "${SESSIONS_CONFIG[@]}"; do
    session="${config%%:*}"
    token="${config#*:}"
    check_session "$session" "$token"
  done
  sleep "$POLL_INTERVAL"
done
