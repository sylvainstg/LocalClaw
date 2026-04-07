#!/bin/bash
# Centralized Whisper transcription daemon
# Watches three directories for new .ogg/.oga files and transcribes them:
#   - ~/.openclaw/media/inbound/                        (legacy/openclaw downloads)
#   - ~/.claude/channels/telegram/inbox/                (Synapse Claude Code plugin)
#   - ~/.claude/channels/telegram-batisseur/inbox/      (Batisseur Claude Code plugin)

export PATH="/Users/sylvain/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

INBOUND_DIR="$HOME/.openclaw/media/inbound"
INBOX_DIR="$HOME/.claude/channels/telegram/inbox"
INBOX_BATISSEUR_DIR="$HOME/.claude/channels/telegram-batisseur/inbox"
LOG_FILE="/tmp/openclaw-whisper-watcher.log"
POLL_INTERVAL=5

log() {
  echo "[$(date '+%Y-%m-%dT%H:%M:%S')] $*" | tee -a "$LOG_FILE"
}

log "Whisper watcher started. Watching: $INBOUND_DIR | $INBOX_DIR | $INBOX_BATISSEUR_DIR"

transcribe_dir() {
  local dir="$1"
  [ -d "$dir" ] || return

  for audio_file in "$dir"/*.ogg "$dir"/*.oga; do
    [ -f "$audio_file" ] || continue

    # Strip extension (.ogg or .oga) to build .txt path
    base="${audio_file%.*}"
    txt_file="${base}.txt"
    [ -f "$txt_file" ] && continue

    log "Transcribing: $(basename "$audio_file")"
    mlx_whisper "$audio_file" \
      --model mlx-community/whisper-large-v3-turbo \
      --language fr \
      --output-format txt \
      --output-dir "$dir" \
      >> "$LOG_FILE" 2>&1

    if [ -f "$txt_file" ]; then
      log "Done: $(basename "$txt_file")"
    else
      log "ERROR: transcription failed for $(basename "$audio_file")"
    fi
  done
}

while true; do
  transcribe_dir "$INBOUND_DIR"
  transcribe_dir "$INBOX_DIR"
  transcribe_dir "$INBOX_BATISSEUR_DIR"
  sleep "$POLL_INTERVAL"
done
