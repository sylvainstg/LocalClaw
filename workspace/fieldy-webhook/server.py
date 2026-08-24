"""Fieldy webhook receiver — stores transcriptions in SQLite + daily markdown."""
import json, sqlite3, os, re, subprocess
from datetime import datetime, date, timezone
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler

DB_PATH = Path.home() / ".local" / "share" / "fieldy" / "fieldy.db"
MD_DIR = Path.home() / ".local" / "share" / "fieldy" / "transcriptions"
PORT = 8080

# Urgency keyword regex — loaded once at startup from extractor config.
# Match → kickstart fieldy-extractor immediately (bypass 5-min cadence).
URGENCY_KEYWORDS_FILE = Path.home() / ".openclaw" / "workspace" / "fieldy-extractor" / "urgency-keywords.json"
URGENCY_PATTERNS = []
try:
    _kw = json.loads(URGENCY_KEYWORDS_FILE.read_text())
    URGENCY_PATTERNS = [re.compile(p, re.IGNORECASE) for groups in _kw.get("wearers", {}).values() for p in (g for grp in groups.values() for g in grp)]
except Exception as _e:
    print(f"[urgency] keywords load failed: {_e}")

def is_urgent(text: str) -> bool:
    if not text or not URGENCY_PATTERNS:
        return False
    return any(p.search(text) for p in URGENCY_PATTERNS)

def kickstart_extractor():
    """Trigger an immediate run of the fieldy-extractor via launchctl (non-blocking)."""
    try:
        subprocess.Popen(
            ["launchctl", "kickstart", f"gui/{os.getuid()}/ai.openclaw.fieldy-extractor"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception as e:
        print(f"[urgency] kickstart failed: {e}")

def get_conn():
    """Connection with a busy timeout so transient contention with the extractor
    (which reads/writes the same file) retries instead of raising immediately and
    leaving a half-open transaction that wedges the db until process restart."""
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("PRAGMA busy_timeout = 10000")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn

def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    MD_DIR.mkdir(parents=True, exist_ok=True)
    conn = get_conn()
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS transcriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            received_at TEXT NOT NULL,
            date TEXT NOT NULL,
            transcription TEXT NOT NULL,
            raw_json TEXT NOT NULL
        )""")
        cols = [r[1] for r in conn.execute("PRAGMA table_info(transcriptions)").fetchall()]
        if "processed_at" not in cols:
            conn.execute("ALTER TABLE transcriptions ADD COLUMN processed_at TEXT")
        conn.commit()
    finally:
        conn.close()

def store(payload):
    conn = get_conn()
    try:
        conn.execute("INSERT INTO transcriptions (received_at, date, transcription, raw_json) VALUES (?,?,?,?)",
            (datetime.now(timezone.utc).isoformat(), payload.get("date",""), payload.get("transcription",""), json.dumps(payload)))
        conn.commit()
    finally:
        conn.close()

def write_markdown(payload):
    """Append parsed transcription to today's markdown file."""
    # Determine the date for the file
    raw_date = payload.get("date", "")
    try:
        if "T" in raw_date:
            dt = datetime.fromisoformat(raw_date)
        else:
            dt = datetime.strptime(raw_date, "%Y-%m-%d") if raw_date else datetime.now()
    except (ValueError, TypeError):
        dt = datetime.now()

    file_date = dt.strftime("%Y-%m-%d")
    md_path = MD_DIR / f"{file_date}.md"

    # Build the markdown block
    timestamp = dt.strftime("%H:%M UTC") if dt.tzinfo else dt.strftime("%H:%M")
    segments = payload.get("transcriptions", [])

    lines = []

    # Header if new file
    if not md_path.exists():
        lines.append(f"# Fieldy — {file_date}\n")

    lines.append(f"\n## {timestamp}\n")

    if segments:
        # Structured transcription with speakers
        for seg in segments:
            speaker = seg.get("speaker", "?")
            text = seg.get("text", "").strip()
            if text:
                lines.append(f"**Speaker {speaker}:** {text}\n")
    else:
        # Flat transcription (no speaker data)
        text = payload.get("transcription", "").strip()
        if text:
            lines.append(f"{text}\n")

    md_path.open("a", encoding="utf-8").write("\n".join(lines))

    # Trigger QMD embed in background (non-blocking)
    try:
        subprocess.Popen(
            ["qmd", "embed", "fieldy"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
    except FileNotFoundError:
        pass  # qmd not available, skip

def get_today(for_date=None):
    if not for_date: for_date = date.today().isoformat()
    conn = get_conn()
    try:
        return conn.execute("SELECT date, transcription FROM transcriptions WHERE date LIKE ? ORDER BY date", (f"{for_date}%",)).fetchall()
    finally:
        conn.close()

class Handler(BaseHTTPRequestHandler):
    def log_message(self, f, *a): print(f"[{datetime.now().strftime('%H:%M:%S')}] {f%a}")
    def do_POST(self):
        if self.path == "/fieldy/story":
            body = self.rfile.read(int(self.headers.get("Content-Length",0)))
            try:
                payload = json.loads(body)
                store(payload)
                write_markdown(payload)
                # Urgency check on transcription text; kickstart extractor if matched.
                # This is non-blocking — the response goes out before the extractor runs.
                full_text = payload.get("transcription", "") or " ".join(
                    seg.get("text", "") for seg in payload.get("transcriptions", [])
                )
                if is_urgent(full_text):
                    print(f"[urgency] match → kickstart extractor")
                    kickstart_extractor()
                self.send_response(200); self.end_headers(); self.wfile.write(b"OK")
            except Exception as e:
                print(f"[ERROR] {e}")
                self.send_response(400); self.end_headers(); self.wfile.write(b"Bad Request")
        else:
            self.send_response(404); self.end_headers()
    def do_GET(self):
        if self.path == "/fieldy/health":
            self.send_response(200); self.end_headers(); self.wfile.write(b"OK")
        else:
            self.send_response(404); self.end_headers()

if __name__ == "__main__":
    init_db()
    print(f"Fieldy webhook server on :{PORT} — DB: {DB_PATH} — MD: {MD_DIR}")
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
