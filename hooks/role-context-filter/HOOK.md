---
name: role-context-filter
description: "Injects role-filtered CONTEXT.md into agent bootstrap — enforces data cloisonnement technically, not via prompt"
metadata:
  {
    "openclaw":
      {
        "emoji": "🔒",
        "events": ["agent"],
        "install": [{ "id": "custom", "kind": "local", "label": "Custom hook" }],
      },
  }
---

# Role-Based Context Filter

Intercepts `agent:bootstrap` and injects a `CONTEXT.md` file containing only the data the current user's role is allowed to see.

## How it works

1. Extract caller Telegram ID from `sessionKey` (`agent:construction:telegram:direct:{ID}`)
2. Resolve role via `team.json` (`persona`: gc/owner/sub/expert)
3. Filter `schedule.json`, `budget.json`, `team.json`, `purchases.json` by role
4. Generate `CONTEXT.md` with filtered data tables
5. Inject into `event.context.bootstrapFiles`
6. Log to `permission-audit.jsonl`

## Data visibility matrix

| Data | gc | owner | sub | expert |
|------|-----|-------|-----|--------|
| All tasks | Yes | Summary | Own only | Own only |
| Budget | Yes | Totals | No | No |
| Team contacts | Yes | No | No | No |
| Purchases | Yes | No prices | No | No |
| Private notes | Yes | No | No | No |
