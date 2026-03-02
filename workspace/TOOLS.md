# TOOLS.md - Local Notes

Skills define _how_ tools work. This file is for _your_ specifics — the stuff that's unique to your setup.

## What Goes Here

Things like:

- Camera names and locations
- SSH hosts and aliases
- Preferred voices for TTS
- Speaker/room names
- Device nicknames
- Anything environment-specific

## Examples

```markdown
### Cameras

- living-room → Main area, 180° wide angle
- front-door → Entrance, motion-triggered

### SSH

- home-server → 192.168.1.100, user: admin

### TTS

- Preferred voice: "Nova" (warm, slightly British)
- Default speaker: Kitchen HomePod
```

## Why Separate?

Skills are shared. Your setup is yours. Keeping them apart means you can update skills without losing your notes, and share skills without leaking your infrastructure.

---

## People CRM

A personal CRM at `/Users/sylvain/Dev/openclaw/` that scans Gmail + Google Calendar to track contacts and relationships.

**Run commands** (from any directory):
```bash
uv run --directory /Users/sylvain/Dev/openclaw people <command>
```

**Key commands:**
- `people contacts list` — list all active contacts (use `--stale` for stale ones, `--sort health|name|recent`)
- `people contacts show <email-or-id>` — full profile with timeline and reminders
- `people search "<query>"` — natural language search ("who do I know at Google?")
- `people remind list [--overdue]` — list reminders
- `people remind add <email> --due YYYY-MM-DD [--note "..."]` — add reminder
- `people health --recalculate-all` — recompute health scores
- `people sync [--full]` — ingest new Gmail + Calendar data

**Database:** `~/.local/share/people/people.db` (SQLite — can query directly)
```bash
sqlite3 ~/.local/share/people/people.db "SELECT COUNT(*) FROM contacts WHERE is_noise=0"
sqlite3 ~/.local/share/people/people.db "SELECT name, company, health_score FROM contacts WHERE is_noise=0 ORDER BY health_score LIMIT 10"
```

**Web UI:** http://localhost:8000 (start with `people serve`)

**Google accounts synced:** sylvainstg@gmail.com, sylvainstgermain@naramachine.ai

**Health score:** 0–100. ≥70 = green (healthy), 40–69 = amber (needs attention), <40 = red (stale).

---

Add whatever helps you do your job. This is your cheat sheet.
