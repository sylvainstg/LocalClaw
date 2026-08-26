#!/usr/bin/env python3
"""
Fieldy batch extractor — runs every 5 min via launchd (ai.openclaw.fieldy-extractor).
Also invoked ad-hoc by the webhook on urgency-keyword match for immediate processing.

Flow:
  1. Query unprocessed rows from fieldy.db
  2. Build context block (last N transcriptions)
  3. Spawn `claude -p` with extraction prompt → parse JSON output
  4. For each action: create pending-action in shared construction store
  5. Send grouped Telegram message via construction bot with inline buttons
  6. Mark rows as processed_at = now

Inline urgency detection (also runs on the webhook side) ensures urgent items
trigger an immediate run via `launchctl kickstart`.
"""
import json
import os
import re
import sqlite3
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

HOME = Path.home()
DB_PATH = HOME / ".local" / "share" / "fieldy" / "fieldy.db"
OPENCLAW = HOME / ".openclaw"
EXTRACTOR_DIR = OPENCLAW / "workspace" / "fieldy-extractor"
PROMPT_FILE = EXTRACTOR_DIR / "synapse-prompt.md"
KEYWORDS_FILE = EXTRACTOR_DIR / "urgency-keywords.json"
PROJECTS_FILE = OPENCLAW / "projects.json"
LOG_FILE = OPENCLAW / "logs" / "fieldy-extractor.log"
CLAUDE_BIN = HOME / ".nvm" / "versions" / "node" / "v24.11.1" / "bin" / "claude"

WORKSPACE = OPENCLAW / "workspace"  # Synapse workspace for the -p session
# Deliberately outside WORKSPACE: running the extraction call from the Synapse
# workspace pulled in workspace/CLAUDE.md (full persona — voice-message rules,
# memory-tool instructions, startup file reads) on every batch run, inflating a
# simple JSON-extraction call into a multi-file-read agent session and blowing
# past the 120s timeout. The prompt is self-contained; it doesn't need that context.
EXTRACTION_CWD = OPENCLAW / ".fieldy-extraction-cwd"
WEARER = "sylvain"
GC_CHAT_ID = "8351648514"
CONTEXT_WINDOW = 30  # last N transcriptions to include (covers ~15 min of audio)


def log(msg: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%dT%H:%M:%S%z")
    line = f"[{ts}] {msg}\n"
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(line)
    print(line, end="", file=sys.stderr)


def get_conn() -> sqlite3.Connection:
    """Busy timeout so transient contention with the webhook (concurrent writer on the
    same file) retries instead of raising immediately."""
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn


def get_synapse_bot_token() -> str:
    """Returns the Synapse (default account) bot token. Fieldy notifications are personal-
    domain by design — Synapse is Sylvain's general channel; Maestro is Irlande-specific.
    The Synapse daemon needs the patched plugin (act: callback routing) — verified via
    integrity check sentinel `plugin_act_patch` and Synapse daemon restart on 2026-05-19."""
    cfg = json.loads((OPENCLAW / "openclaw.json").read_text())
    return cfg["channels"]["telegram"]["accounts"]["default"]["botToken"]


def load_keywords() -> list[str]:
    cfg = json.loads(KEYWORDS_FILE.read_text())
    w = cfg["wearers"].get(WEARER, {})
    return [p for group in w.values() for p in group]


def urgency_check(text: str, patterns: list[str]) -> bool:
    return any(re.search(p, text, re.IGNORECASE) for p in patterns)


def query_unprocessed(conn: sqlite3.Connection) -> list[tuple[int, str, str]]:
    return conn.execute(
        "SELECT id, received_at, transcription FROM transcriptions "
        "WHERE processed_at IS NULL ORDER BY received_at"
    ).fetchall()


def mark_processed(conn: sqlite3.Connection, ids: list[int]) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.executemany(
        "UPDATE transcriptions SET processed_at = ? WHERE id = ?",
        [(now, i) for i in ids],
    )
    conn.commit()


def build_context_block(rows: list[tuple[int, str, str]]) -> str:
    parts = []
    for _id, ts, txt in rows[-CONTEXT_WINDOW:]:
        ts_short = ts[:19].replace("T", " ")
        parts.append(f"[{ts_short}] {txt.strip()}")
    return "\n".join(parts)


def build_projects_block() -> str:
    """Renders the projects.json into a human-readable signal table for the LLM prompt."""
    cfg = json.loads(PROJECTS_FILE.read_text())
    lines = []
    for p in cfg["projects"]:
        lines.append(f"### {p['id']} — {p['name']}")
        sig = p.get("signals", {})
        # If people come from a JSON file, inline a short list
        if "people_source" in sig:
            try:
                team = json.loads((OPENCLAW / sig["people_source"]).read_text())
                names = [m.get("name") for m in team.get("members", []) if m.get("name")]
                lines.append(f"- People: {', '.join(names)}")
            except Exception:
                pass
        if "people" in sig:
            lines.append(f"- People: {', '.join(sig['people'])}")
        if "places" in sig:
            lines.append(f"- Places: {', '.join(sig['places'])}")
        if "topics" in sig:
            lines.append(f"- Topics: {', '.join(sig['topics'])}")
        if "suppliers" in sig:
            lines.append(f"- Suppliers: {', '.join(sig['suppliers'])}")
        if sig.get("default_for_unmatched"):
            lines.append("- ⭐ Default project si aucun signal Irlande détecté")
        lines.append("")
    return "\n".join(lines)


def get_pending_store_for_project(project_id: str) -> Path:
    """Returns the pending-actions store path for a given project_id. Falls back to 'personnel'."""
    cfg = json.loads(PROJECTS_FILE.read_text())
    for p in cfg["projects"]:
        if p["id"] == project_id:
            return OPENCLAW / p["pending_store"]
    # Unknown project (e.g., 'ambiguous'): use default project's store
    default = cfg.get("default_project", "personnel")
    for p in cfg["projects"]:
        if p["id"] == default:
            return OPENCLAW / p["pending_store"]
    # Last-resort fallback
    return OPENCLAW / "workspace-construction-v2" / "data" / "pending-actions.json"


def run_extraction(context_block: str) -> dict | None:
    EXTRACTION_CWD.mkdir(parents=True, exist_ok=True)
    prompt = PROMPT_FILE.read_text()
    prompt = prompt.replace("{TRANSCRIPTIONS_BLOCK}", context_block)
    prompt = prompt.replace("{PROJECTS_BLOCK}", build_projects_block())
    try:
        result = subprocess.run(
            [
                str(CLAUDE_BIN), "-p", "--dangerously-skip-permissions",
                # The prompt is fully self-contained (transcriptions + projects block
                # already substituted) — this is a narrow text-to-JSON completion, not
                # an interactive Synapse session. Keep it that way:
                "--tools", "",             # no tool use needed
                "--strict-mcp-config",     # no MCP servers to spin up
                "--effort", "medium",      # skip the global high/xhigh thinking overhead
                prompt,
            ],
            capture_output=True,
            timeout=120,
            cwd=str(EXTRACTION_CWD),
            text=True,
        )
    except subprocess.TimeoutExpired:
        log("ERROR: claude -p timed out after 120s")
        return None
    if result.returncode != 0:
        log(f"ERROR: claude -p exit {result.returncode}: {result.stderr[:300]}")
        return None
    stdout = result.stdout.strip()
    # Strip code fences if present
    if stdout.startswith("```"):
        stdout = re.sub(r"^```[a-z]*\n", "", stdout)
        stdout = re.sub(r"\n```$", "", stdout)
    try:
        return json.loads(stdout)
    except json.JSONDecodeError as e:
        log(f"ERROR: claude returned non-JSON ({e}). Output head: {stdout[:300]}")
        return None


def short_id() -> str:
    return uuid.uuid4().hex[:6]


def log_activities_directly(activities: list[dict]) -> list[dict]:
    """Activity-type items are append-only narrative entries — they bypass the pending-actions
    confirmation flow entirely. We call the construction MCP CLI tool `log_activity` directly
    for each, then return them so the caller can include them in the post-batch Telegram summary."""
    logged = []
    construction_dir = OPENCLAW / "workspace-construction-v2"
    for a in activities:
        cmd = [
            "node", "mcp/tools-cli.mjs", "log_activity",
            "--text", a.get("source_text") or a.get("summary", ""),
            "--category", a.get("activity_category", "other"),
            "--source", "fieldy",
            "--auto_tag_confidence", str(a.get("confidence", 0.0)),
        ]
        for kw in ("mention_tasks", "mention_people", "mention_suppliers"):
            vals = a.get(kw) or []
            if isinstance(vals, str): vals = [vals]
            if vals:
                cmd += [f"--{kw}", json.dumps(vals, ensure_ascii=False)]
        try:
            result = subprocess.run(cmd, capture_output=True, timeout=20, cwd=str(construction_dir), text=True)
            if result.returncode == 0:
                resp = json.loads(result.stdout.strip())
                if resp.get("ok"):
                    logged.append({**a, "_logged_id": resp["data"]["id"]})
                else:
                    log(f"log_activity refused: {resp.get('error')}")
            else:
                log(f"log_activity exit {result.returncode}: {result.stderr[:200]}")
        except Exception as e:
            log(f"log_activity exception: {e}")
    return logged


def append_pending_actions(actions: list[dict]) -> list[tuple[str, dict]]:
    """Append fieldy-* pending-actions to per-project stores. Routes by project_id.
    Returns list of (id, action) preserving the input order.
    Ambiguous actions go to the default project's store but are tagged so the UX
    knows to render a project-picker instead of plain confirm/reject buttons.
    """
    now = datetime.now(timezone.utc).isoformat()
    created = []
    # Group actions by their target store so we batch I/O
    by_store: dict[str, list[tuple[str, dict, dict]]] = {}
    for a in actions:
        proj = a.get("project_id", "ambiguous")
        store_path = get_pending_store_for_project(proj)
        pid = short_id()
        entry = {
            "id": pid,
            "type": f"fieldy-{a['type']}",
            "context": {
                "wearer": WEARER,
                "project_id": proj,
                "project_reason": a.get("project_reason"),
                "summary": a.get("summary", ""),
                "when": a.get("when"),
                "who": a.get("who"),
                "source_text": a.get("source_text", ""),
                "confidence": a.get("confidence", 0.0),
                "urgent": a.get("urgent", False),
                "ambiguous": (proj == "ambiguous"),
            },
            "createdAt": now,
            "expiresAt": now,  # UX handles expiry
            "resolved": False,
        }
        by_store.setdefault(str(store_path), []).append((pid, a, entry))
        created.append((pid, a))

    for path_str, items in by_store.items():
        path = Path(path_str)
        if not path.exists():
            store = {"version": 1, "actions": []}
        else:
            store = json.loads(path.read_text())
        for _pid, _a, entry in items:
            store["actions"].append(entry)
        path.write_text(json.dumps(store, indent=2, ensure_ascii=False))

    return created


def format_telegram_message(created: list[tuple[str, dict]], urgent_in_batch: bool, logged_activities: list[dict] | None = None) -> tuple[str, dict]:
    """Returns (text, reply_markup)."""
    logged_activities = logged_activities or []
    n = len(created)
    n_act = len(logged_activities)
    when_label = datetime.now().strftime("%H:%M")
    if urgent_in_batch:
        header = "🚨 URGENT"
    elif n and n_act:
        header = f"📋 {n} action{'s' if n > 1 else ''} + 📝 {n_act} activité{'s' if n_act > 1 else ''}"
    elif n_act:
        header = f"📝 {n_act} activité{'s' if n_act > 1 else ''} loggée{'s' if n_act > 1 else ''} (chantier Irlande)"
    else:
        header = f"📋 {n} action{'s' if n > 1 else ''}"
    lines = [f"{header} · {when_label}", ""]
    # Activities first, no buttons — just narrative confirmation
    if logged_activities:
        for a in logged_activities:
            cat_emoji = {"progress": "📈", "issue": "⚠️", "visit": "👷", "sub-update": "🔧", "decision": "✅", "other": "📝"}.get(a.get("activity_category", "other"), "📝")
            txt = (a.get("source_text") or a.get("summary", ""))[:120]
            lines.append(f"{cat_emoji} {txt}")
            mentions = []
            for kw in ("mention_tasks", "mention_people", "mention_suppliers"):
                vals = a.get(kw) or []
                if isinstance(vals, str): vals = [vals]
                mentions.extend(vals)
            if mentions:
                lines.append(f"   _→ {', '.join(mentions[:6])}_")
            lines.append("")
    keyboard = []
    digits = "1️⃣2️⃣3️⃣4️⃣5️⃣6️⃣7️⃣8️⃣9️⃣🔟"
    for i, (pid, action) in enumerate(created):
        emoji = digits[i] if i < 10 else f"({i+1})"
        urgent_mark = " 🚨" if action.get("urgent") else ""
        conf = action.get("confidence", 0.0)
        conf_mark = " ⚠️ incertain" if conf < 0.7 else ""
        proj = action.get("project_id", "ambiguous")
        proj_label = {
            "irlande": "🏠 Irlande",
            "personnel": "👤 Perso",
            "ambiguous": "❓ Projet?",
        }.get(proj, f"❓ {proj}")
        line1 = f"{emoji}{urgent_mark} **{action['type'].upper()}** · {proj_label}{conf_mark}"
        line2 = f"   {action.get('summary', '(no summary)')[:80]}"
        if action.get("when"):
            line2 += f" — {action['when']}"
        if action.get("who"):
            line2 += f" ({action['who']})"
        lines.extend([line1, line2, ""])
        if proj == "ambiguous":
            # Picker: tap to confirm AND assign project in one click
            keyboard.append(
                [
                    {"text": f"🏠 Irlande #{i+1}", "callback_data": f"act:{pid}-irlande"},
                    {"text": f"👤 Perso #{i+1}", "callback_data": f"act:{pid}-perso"},
                    {"text": f"❌ #{i+1}", "callback_data": f"act:{pid}-n"},
                ]
            )
        else:
            # High-confidence path: simple confirm/reject
            keyboard.append(
                [
                    {"text": f"✅ #{i+1}", "callback_data": f"act:{pid}-y"},
                    {"text": f"❌ #{i+1}", "callback_data": f"act:{pid}-n"},
                ]
            )
    text = "\n".join(lines).strip()
    return text, {"inline_keyboard": keyboard}


def send_telegram(text: str, reply_markup: dict) -> int | None:
    token = get_synapse_bot_token()
    payload = {
        "chat_id": GC_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown",
        "reply_markup": reply_markup,
    }
    try:
        result = subprocess.run(
            [
                "curl", "-sS", "-m", "15", "-X", "POST",
                f"https://api.telegram.org/bot{token}/sendMessage",
                "-H", "Content-Type: application/json",
                "--data", json.dumps(payload, ensure_ascii=False),
            ],
            capture_output=True,
            timeout=20,
            text=True,
        )
        resp = json.loads(result.stdout)
        if not resp.get("ok"):
            log(f"ERROR: telegram sendMessage failed: {resp}")
            return None
        return resp["result"]["message_id"]
    except Exception as e:
        log(f"ERROR: telegram send exception: {e}")
        return None


def main():
    log("--- extractor run start ---")
    conn = get_conn()
    try:
        rows = query_unprocessed(conn)
        if not rows:
            log("nothing to process; exit clean")
            return
        log(f"{len(rows)} unprocessed transcriptions; building context")

        context_block = build_context_block(rows)
        keywords = load_keywords()
        urgent_in_block = urgency_check(context_block, keywords)
        log(f"urgency keyword present: {urgent_in_block}")

        result = run_extraction(context_block)
        if result is None:
            # Don't mark processed — let next run retry
            log("extraction failed; leaving rows for retry")
            return

        all_items = result.get("actions", [])
        log(f"extracted {len(all_items)} items; reasoning: {result.get('reasoning_brief', '')[:120]}")

        # Split: activity-type items are append-only logs (no buttons); others go through
        # the pending-actions confirmation flow.
        activities = [a for a in all_items if a.get("type") == "activity" and a.get("project_id") == "irlande"]
        actions = [a for a in all_items if not (a.get("type") == "activity" and a.get("project_id") == "irlande")]

        logged_activities = log_activities_directly(activities) if activities else []
        if logged_activities:
            log(f"{len(logged_activities)} activities auto-logged to activity-log.jsonl")

        if actions or logged_activities:
            urgent_any = any(a.get("urgent") for a in actions) or urgent_in_block
            created = append_pending_actions(actions) if actions else []
            text, kb = format_telegram_message(created, urgent_any, logged_activities)
            mid = send_telegram(text, kb)
            if mid:
                log(f"telegram sent: message_id {mid}, {len(created)} actions + {len(logged_activities)} activities")
            else:
                log("telegram send failed; actions still in pending-actions for callback-only flow")
        else:
            log("no actions or activities; not sending telegram")

        ids = [r[0] for r in rows]
        mark_processed(conn, ids)
        log(f"marked {len(ids)} rows processed")
    finally:
        conn.close()
        log("--- extractor run end ---\n")


if __name__ == "__main__":
    main()
