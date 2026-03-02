# PROJECTS.md — Sylvain's Build List

_26 projects to build. Tracked here for context and prioritization._

---

## Status Legend
- 🔲 Not started
- 🔨 In progress
- ✅ Done
- ⏸ Blocked

---

## Projects

| # | Name | Status | Notes |
|---|------|--------|-------|
| 1 | Personal CRM | 🔨 | Gmail + Calendar + SQLite + vector search + Box |
| 2 | Meeting Action Items (Fathom) | 🔲 | Depends on #1 (CRM); uses Google Tasks (not Todoist) |
| 3 | Urgent Email Detection | 🔲 | Depends on #1 (CRM), #21 (Gmail) |
| 4 | Knowledge Base (RAG) | 🔲 | SQLite + vector embeddings, Telegram ingest |
| 5 | Business Advisory Council | 🔲 | Depends on #7, #23, #25 data feeds |
| 6 | Security Council | 🔲 | Nightly code review, 4 perspectives |
| 7 | Social Media Tracking | 🔲 | YouTube, Instagram, X, TikTok → SQLite |
| 8 | Video Idea Pipeline | 🔲 | Depends on #4 (KB), #25 (Asana), Slack |
| 9 | Earnings Reports | 🔲 | Watchlist, cron jobs, Telegram topic |
| 10 | Food Journal / Health Tracking | 🔲 | Telegram input, weekly analysis |
| 11 | Daily Briefing | 🔲 | Depends on #1, #2, #3, #7 |
| 12 | Messaging Setup | 🔲 | Telegram topics, Slack mention-only |
| 13 | Security and Safety | 🔲 | Prompt injection defense, approval gates |
| 14 | Database Backups | 🔲 | Hourly, encrypted, Google Drive, 7-day retention |
| 15 | Git Auto-Sync | 🔲 | Hourly commit+push, conflict detection |
| 16 | Prompt Engineering Guide | 🔲 | Claude Opus 4.6 specifics |
| 17 | AI Writing Humanizer | 🔲 | Skill to strip AI patterns from prose |
| 18 | Image Generation (Nano Banana) | 🔲 | Gemini image gen, edit, compose |
| 19 | Video Generation (Veo 3) | 🔲 | Short clips from text/image |
| 20 | Video Analysis (Gemini Video Watch) | 🔲 | Upload to Gemini, analyze content |
| 21 | Google Workspace Integration | 🔲 | OAuth CLI, Gmail/Calendar/Drive/Docs |
| 22 | Platform Health Council | 🔲 | 9-area automated health review |
| 23 | Newsletter & CRM Platform Integration | 🔲 | Beehiiv + HubSpot → SQLite |
| 24 | Model Usage & Cost Tracking | 🔲 | All providers, JSONL logs, daily reports |
| 25 | Asana Integration | 🔲 | Task sync, video pipeline cards |
| 26 | Health Monitoring | 🔲 | Heartbeat system, daily/weekly/monthly checks |

---

## Dependency Map

```
#21 (Google Workspace) ──► #1 (CRM) ──► #3 (Urgent Email)
                                    ──► #11 (Daily Briefing)
                                    ──► #2 (Meeting Items)

#7 (Social Media) ──► #5 (Advisory Council) ◄── #23 (Newsletter/HubSpot)
                                             ◄── #25 (Asana)

#4 (Knowledge Base) ──► #8 (Video Pipeline)

#12 (Messaging Setup) ── foundation for all Telegram delivery

#14 (DB Backups) + #15 (Git Sync) ── infrastructure, build early
```

## Suggested Build Order

**Phase 1 — Foundation**
12 → 14 → 15 → 21 → 1

**Phase 2 — Intelligence Layer**
4 → 3 → 2 → 11 → 7

**Phase 3 — Advisory & Automation**  
5 → 8 → 9 → 22 → 26

**Phase 4 — Integrations**
23 → 25 → 6 → 13

**Phase 5 — Media & Tooling**
17 → 18 → 19 → 20 → 16 → 10 → 24
