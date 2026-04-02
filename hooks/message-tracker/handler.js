import path from "node:path";
import os from "node:os";
import fs from "node:fs/promises";
import { existsSync, readFileSync } from "node:fs";

const DATA_DIR = path.join(os.homedir(), ".openclaw", "workspace-construction-v2", "data");
const LIFECYCLE_FILE = path.join(DATA_DIR, "message-lifecycle.jsonl");
const EVENT_LOG_FILE = path.join(DATA_DIR, "event-log.jsonl");
const STALL_THRESHOLD_MS = 120_000; // 2 minutes
const WATCHDOG_INTERVAL_MS = 60_000; // check every 60s

// ── In-memory index: messageId → lifecycle record ──────────────────────────
const lifecycleIndex = new Map();
let indexLoaded = false;

async function loadIndex() {
  if (indexLoaded) return;
  try {
    if (existsSync(LIFECYCLE_FILE)) {
      const raw = readFileSync(LIFECYCLE_FILE, "utf-8");
      for (const line of raw.split("\n")) {
        if (!line.trim()) continue;
        try {
          const rec = JSON.parse(line);
          if (rec.id) lifecycleIndex.set(String(rec.id), rec);
        } catch {}
      }
    }
  } catch {}
  indexLoaded = true;
}

async function persistRecord(rec) {
  lifecycleIndex.set(String(rec.id), rec);
  await fs.appendFile(LIFECYCLE_FILE, JSON.stringify(rec) + "\n", "utf-8");
}

// ── Sender name resolution ─────────────────────────────────────────────────
let senderNames = null;
async function resolveSenderName(senderId) {
  if (!senderNames) {
    senderNames = {};
    try {
      const teamPath = path.join(DATA_DIR, "team.json");
      const raw = await fs.readFile(teamPath, "utf-8");
      const team = JSON.parse(raw);
      for (const m of team.members || []) {
        if (m.telegramId) senderNames[String(m.telegramId)] = m.name;
      }
    } catch {}
  }
  return senderNames[String(senderId)] || String(senderId);
}

function detectType(content) {
  if (!content) return "unknown";
  if (content.includes("[Audio]") || content.includes(".ogg") || content.includes("<media:audio>")) return "voice";
  if (content.includes("[Photo]") || content.includes(".jpg") || content.includes(".png")) return "photo";
  if (content.includes("[Video]") || content.includes(".mp4")) return "video";
  if (content.includes("[Document]")) return "document";
  return "text";
}

// ── Watchdog: detect stalled messages ──────────────────────────────────────
let watchdogStarted = false;

function startWatchdog() {
  if (watchdogStarted) return;
  watchdogStarted = true;

  setInterval(async () => {
    try {
      const now = Date.now();
      for (const [id, rec] of lifecycleIndex) {
        if (rec.status === "delivered" || rec.status === "failed" || rec.status === "stalled") continue;

        // Find the latest stage timestamp
        const stages = rec.stages || {};
        const latestStage = Object.values(stages).reduce((max, s) => {
          const t = new Date(s.at).getTime();
          return t > max ? t : max;
        }, 0);

        if (latestStage && (now - latestStage) > STALL_THRESHOLD_MS) {
          rec.status = "stalled";
          rec.stalledAt = new Date().toISOString();
          await persistRecord(rec);

          // Log to event-log
          const event = {
            type: "message-stall",
            description: `Message de ${rec.senderName || rec.senderId} en attente depuis ${Math.round((now - latestStage) / 1000)}s`,
            timestamp: new Date().toISOString(),
            messageId: rec.id,
            senderId: rec.senderId,
            senderName: rec.senderName,
          };
          await fs.appendFile(EVENT_LOG_FILE, JSON.stringify(event) + "\n", "utf-8");
        }
      }
    } catch {}
  }, WATCHDOG_INTERVAL_MS);
}

// ── Main handler ───────────────────────────────────────────────────────────
const handler = async (event) => {
  try {
    if (event.type !== "message") return;
    await loadIndex();
    startWatchdog();

    const ctx = event.context || {};
    const now = event.timestamp ? event.timestamp.toISOString() : new Date().toISOString();
    const msgId = String(ctx.messageId || Date.now().toString(36));

    // ── RECEIVED ──
    if (event.action === "received") {
      const senderId = ctx.from || ctx.senderId || "unknown";
      const senderName = await resolveSenderName(senderId);
      const preview = (ctx.content || ctx.body || "").slice(0, 100) || undefined;

      const rec = {
        id: msgId,
        senderId: String(senderId),
        senderName,
        accountId: ctx.accountId || "unknown",
        channelId: ctx.channelId || "telegram",
        conversationId: ctx.conversationId || "",
        type: detectType(ctx.content),
        status: "received",
        stages: {
          received: { at: now },
        },
        preview: preview || undefined,
        error: null,
      };
      await persistRecord(rec);
    }

    // ── TRANSCRIBED ──
    if (event.action === "transcribed") {
      const senderId = ctx.senderId || ctx.from || "unknown";
      const senderName = ctx.senderName || await resolveSenderName(senderId);
      const transcript = (ctx.transcript || "").slice(0, 200) || undefined;

      const rec = lifecycleIndex.get(msgId) || {
        id: msgId,
        senderId: String(senderId),
        senderName,
        accountId: ctx.accountId || "unknown",
        channelId: ctx.channelId || "telegram",
        conversationId: ctx.conversationId || "",
        type: "voice",
        stages: {},
        error: null,
      };
      rec.status = "transcribed";
      rec.type = "voice";
      rec.stages.transcribed = { at: now };
      if (transcript) rec.transcript = transcript;
      await persistRecord(rec);
    }

    // ── PREPROCESSED (routed) ──
    if (event.action === "preprocessed") {
      const senderId = ctx.senderId || ctx.from || "unknown";
      const senderName = ctx.senderName || await resolveSenderName(senderId);
      const preview = (ctx.transcript || ctx.body || ctx.content || "").slice(0, 200) || undefined;

      const rec = lifecycleIndex.get(msgId) || {
        id: msgId,
        senderId: String(senderId),
        senderName,
        accountId: ctx.accountId || "unknown",
        channelId: ctx.channelId || "telegram",
        conversationId: ctx.conversationId || "",
        type: ctx.transcript ? "voice" : detectType(ctx.body),
        stages: {},
        error: null,
      };
      rec.status = "routed";
      rec.stages.routed = { at: now };
      if (preview) rec.preview = preview;
      if (ctx.transcript && !rec.transcript) rec.transcript = (ctx.transcript || "").slice(0, 200);
      await persistRecord(rec);
    }

    // ── SENT (delivered or failed) ──
    if (event.action === "sent") {
      const recipientId = ctx.to || "unknown";
      // Find the most recent routed message for this recipient
      let rec = null;
      for (const [, r] of lifecycleIndex) {
        if (r.status !== "delivered" && r.status !== "failed" &&
            (r.conversationId || "").includes(recipientId)) {
          if (!rec || (r.stages?.routed?.at || "") > (rec.stages?.routed?.at || "")) {
            rec = r;
          }
        }
      }

      if (rec) {
        const success = ctx.success !== false;
        rec.status = success ? "delivered" : "failed";
        rec.stages.delivered = { at: now };
        if (!success) rec.error = ctx.error || "Unknown error";

        // Calculate total duration
        const firstStage = rec.stages.received?.at || rec.stages.routed?.at;
        if (firstStage) {
          rec.durationMs = new Date(now).getTime() - new Date(firstStage).getTime();
        }
        await persistRecord(rec);
      } else {
        // Outbound-only message (no matching inbound) — log separately
        const entry = {
          id: msgId,
          senderId: null,
          senderName: null,
          to: recipientId,
          accountId: ctx.accountId || "unknown",
          channelId: ctx.channelId || "telegram",
          conversationId: ctx.conversationId || "",
          type: "reply",
          status: "delivered",
          stages: { delivered: { at: now } },
          error: ctx.success === false ? (ctx.error || "Unknown error") : null,
        };
        await persistRecord(entry);
      }
    }
  } catch (err) {
    // Silent - don't break message flow
  }
};

export default handler;
