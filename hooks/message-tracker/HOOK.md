---
name: message-tracker
description: "Message lifecycle tracker — follows every Telegram message through the full pipeline with stall detection"
metadata:
  {
    "openclaw":
      {
        "emoji": "📨",
        "events": ["message"],
        "install": [{ "id": "custom", "kind": "local", "label": "Custom hook" }],
      },
  }
---

# Message Lifecycle Tracker

Tracks every inbound Telegram message through the full processing pipeline, detects stalls, and enables replay of failed messages.

## Pipeline

```
📥 received → 🎤 transcribed → 📨 routed → ✅ delivered
                                           → ❌ failed
                                           → ⚠️ stalled (watchdog, >2min)
                                           → 🔄 replayed
```

## Hook Events Captured

| Event | Stage | What |
|-------|-------|------|
| `message:received` | `received` | Message arrived from Telegram |
| `message:transcribed` | `transcribed` | Audio transcribed via Whisper |
| `message:preprocessed` | `routed` | Message enriched and dispatched to agent |
| `message:sent` | `delivered` / `failed` | Agent response sent (or failed) |

## Watchdog

A `setInterval` (60s) scans for messages stuck in `received`/`routed` for >2 minutes and marks them `stalled`. Stalls are logged to `event-log.jsonl`.

## Output

`~/.openclaw/workspace-construction-v2/data/message-lifecycle.jsonl`

Each message has one record updated at each stage:

```json
{
  "id": "602",
  "senderId": "8606454174",
  "senderName": "Maryse Croteau",
  "accountId": "construction",
  "type": "voice",
  "status": "delivered",
  "preview": "reporter la tâche plomberie...",
  "transcript": "Je voudrais reporter la tâche plomberie d'une semaine",
  "stages": {
    "received": { "at": "2026-04-01T14:16:56Z" },
    "transcribed": { "at": "2026-04-01T14:16:58Z" },
    "routed": { "at": "2026-04-01T14:17:02Z" },
    "delivered": { "at": "2026-04-01T14:17:17Z" }
  },
  "durationMs": 21000,
  "error": null
}
```

## Dashboard

- Health banner with delivery rate, stalls, failures
- Click any message row to expand the stage timeline
- Replay button on failed/stalled messages
- Auto-refresh every 30s when Messages tab is active
