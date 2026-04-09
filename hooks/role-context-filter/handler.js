import path from "node:path";
import os from "node:os";
import fs from "node:fs/promises";
import { existsSync, readFileSync, appendFileSync } from "node:fs";

const WORKSPACE = path.join(os.homedir(), ".openclaw", "workspace-construction-v2");
const DATA_DIR = path.join(WORKSPACE, "data");

// ── Data loading ────────────────────────────────────────────────────────────

async function loadJSON(filepath) {
  try {
    return JSON.parse(await fs.readFile(filepath, "utf-8"));
  } catch {
    return null;
  }
}

// ── Caller resolution ───────────────────────────────────────────────────────

function extractCallerIdFromSessionKey(sessionKey) {
  // Format: agent:construction:telegram:direct:8513508883
  const match = sessionKey?.match(/telegram:direct:(\d+)/);
  return match ? match[1] : null;
}

function resolveCaller(team, callerId) {
  if (!team || !callerId) return { name: "Inconnu", persona: "unknown", id: null };
  const member = (team.members || []).find(m => String(m.telegramId) === String(callerId));
  if (!member) return { name: callerId, persona: "unknown", id: callerId };
  return { name: member.name, persona: member.persona, role: member.role, id: member.id, telegramId: callerId };
}

// ── Data filtering ──────────────────────────────────────────────────────────

async function getContractType() {
  try {
    const project = await loadJSON(path.join(DATA_DIR, "project.json"));
    return project?.contractType || "fixed-price";
  } catch { return "fixed-price"; }
}

function filterSchedule(schedule, persona, callerName, contractType) {
  if (!schedule) return { tasks: [], dependencies: [] };
  const tasks = schedule.tasks || [];
  const deps = schedule.dependencies || [];

  if (persona === "gc") return { tasks, dependencies: deps, milestones: schedule.milestones || [] };

  if (persona === "owner") {
    if (contractType === "cost-plus") {
      // Cost-plus: owner sees everything except private notes
      return { tasks, dependencies: deps, milestones: schedule.milestones || [] };
    }
    // Fixed-price: summary without private notes
    const filtered = tasks.map(t => ({
      id: t.id, name: t.name, start: t.start, end: t.end,
      who: t.who, status: t.status, progress: t.progress, phase: t.phase,
    }));
    return { tasks: filtered, dependencies: deps, milestones: schedule.milestones || [] };
  }

  // Sub and expert: only their tasks
  const myTasks = tasks.filter(t => t.who === callerName);
  const myIds = new Set(myTasks.map(t => t.id));

  // Include blocking dependencies (tasks that block my tasks)
  const blockingDeps = deps.filter(d => myIds.has(d.to));
  const blockerIds = new Set(blockingDeps.map(d => d.from));
  const blockers = tasks.filter(t => blockerIds.has(t.id)).map(t => ({
    id: t.id, name: t.name, status: t.status, progress: t.progress, end: t.end,
  }));

  return {
    tasks: myTasks.map(t => ({ id: t.id, name: t.name, start: t.start, end: t.end, status: t.status, progress: t.progress })),
    blockers,
    dependencies: blockingDeps,
  };
}

function filterBudget(budget, persona, contractType) {
  if (!budget) return null;
  if (persona === "gc") return budget;
  if (persona === "owner") {
    if (contractType === "cost-plus") return budget; // Full transparency
    // Fixed-price: totals only
    const budgets = (budget.budgets || []).map(b => ({ id: b.id, name: b.name, envelope: b.envelope, spent: b.spent }));
    return { budgets, totalBudget: budget.totalBudget, contingency: budget.contingency };
  }
  return null; // sub/expert: no access
}

function filterTeam(team, persona, contractType) {
  if (!team) return null;
  if (persona === "gc") return team;
  if (persona === "owner" && contractType === "cost-plus") return team; // Full transparency
  return null;
}

function filterPurchases(purchases, persona, contractType) {
  if (!purchases) return null;
  if (persona === "gc") return purchases;
  if (persona === "owner") {
    if (contractType === "cost-plus") return purchases; // Full transparency including soumissions + invoices
    // Fixed-price: pas de soumissions, pas de factures, pas de variance — le client paie un montant fixe
    const items = (purchases.items || []).map(p => ({
      id: p.id, description: p.description, room: p.room, status: p.status, qty: p.qty,
      priceEstimate: p.priceEstimate,
    }));
    return { items };
  }
  return null; // sub/expert: no access
}

// ── CONTEXT.md generation ───────────────────────────────────────────────────

function generateContextMd(caller, filteredData) {
  const lines = [];
  const { persona, name, role } = caller;

  // Header
  const personaLabels = { gc: "chargé de projet", owner: "propriétaire", sub: "sous-traitant", expert: "expert" };
  lines.push(`# Contexte filtré — ${name} (${personaLabels[persona] || persona}${role ? ', ' + role : ''})`);
  lines.push("");
  lines.push(`> Ce fichier est généré automatiquement. Il contient UNIQUEMENT les données autorisées pour ton rôle.`);
  lines.push(`> **caller_id: ${caller.telegramId}** — utilise ce caller_id pour toutes les commandes tools-cli.mjs`);
  lines.push("");

  // Tasks
  const { tasks, blockers, dependencies, milestones } = filteredData.schedule;
  if (persona === "gc" || persona === "owner") {
    lines.push(`## Toutes les tâches (${tasks.length})`);
  } else {
    lines.push(`## Tes tâches (${tasks.length})`);
  }
  if (tasks.length) {
    lines.push("| ID | Tâche | Début | Fin | Statut | Prog. |" + (persona === "gc" ? " Qui |" : ""));
    lines.push("|---|---|---|---|---|---|" + (persona === "gc" ? "---|" : ""));
    tasks.forEach(t => {
      const who = persona === "gc" ? ` ${t.who || ""} |` : "";
      lines.push(`| ${t.id} | ${t.name} | ${t.start} | ${t.end} | ${t.status} | ${t.progress || 0}% |${who}`);
    });
  } else {
    lines.push("Aucune tâche assignée.");
  }
  lines.push("");

  // Blockers (sub/expert only)
  if (blockers && blockers.length) {
    lines.push("## Dépendances bloquantes");
    blockers.forEach(b => {
      const myDeps = dependencies.filter(d => d.from === b.id);
      const blocks = myDeps.map(d => d.to).join(", ");
      lines.push(`- **${b.name}** (${b.status}, ${b.progress || 0}%) → bloque ${blocks}`);
    });
    lines.push("");
  }

  // Budget
  lines.push("## Budget");
  if (filteredData.budget) {
    if (persona === "gc") {
      const b = filteredData.budget;
      (b.budgets || []).forEach(env => {
        lines.push(`- **${env.name}**: ${env.envelope?.toLocaleString()}$ (dépensé: ${env.spent?.toLocaleString() || 0}$)`);
      });
      if (b.contingency) lines.push(`- **Contingence**: ${b.contingency.remaining?.toLocaleString()}$ restant (${b.contingency.pct}%)`);
    } else {
      // Owner: totals only
      const b = filteredData.budget;
      lines.push(`- Budget total: ${b.totalBudget?.toLocaleString() || "N/A"}$`);
      (b.budgets || []).forEach(env => {
        lines.push(`- ${env.name}: ${env.envelope?.toLocaleString()}$`);
      });
    }
  } else {
    lines.push("Accès non autorisé pour ton rôle.");
  }
  lines.push("");

  // Team
  lines.push("## Équipe");
  if (filteredData.team) {
    (filteredData.team.members || []).forEach(m => {
      const phone = m.contactMethods?.phone || "";
      const email = m.contactMethods?.email || "";
      lines.push(`- **${m.name}** — ${m.role}${phone ? ' · ' + phone : ''}${email ? ' · ' + email : ''}`);
    });
  } else {
    lines.push("Accès non autorisé pour ton rôle.");
  }
  lines.push("");

  // Purchases
  if (filteredData.purchases) {
    lines.push("## Achats");
    const items = filteredData.purchases.items || [];
    if (items.length) {
      items.slice(0, 30).forEach(p => {
        lines.push(`- ${p.description} (${p.room || "—"}) — ${p.status}`);
      });
      if (items.length > 30) lines.push(`... et ${items.length - 30} autres`);
    }
    lines.push("");
  }

  return lines.join("\n");
}

// ── Audit (with de-duplication on state change) ─────────────────────────────

const lastStateByUser = new Map(); // user_id → fingerprint

function logAudit(caller, taskCount, contractType, filteredData) {
  const budgetVisible = !!filteredData.budget;
  const teamVisible = !!filteredData.team;
  const purchasesVisible = !!filteredData.purchases;

  // Fingerprint to detect changes
  const fingerprint = `${taskCount}|${budgetVisible}|${teamVisible}|${purchasesVisible}|${contractType}`;
  const previous = lastStateByUser.get(String(caller.telegramId));
  if (previous === fingerprint) return; // No change → skip log
  lastStateByUser.set(String(caller.telegramId), fingerprint);

  const entry = {
    ts: new Date().toISOString(),
    event: "bootstrap_filter",
    user_id: caller.telegramId,
    user_name: caller.name,
    role: caller.persona,
    contract_type: contractType,
    tasks_visible: taskCount,
    budget_visible: budgetVisible,
    team_visible: teamVisible,
    purchases_visible: purchasesVisible,
  };
  try {
    appendFileSync(path.join(DATA_DIR, "permission-audit.jsonl"), JSON.stringify(entry) + "\n", "utf-8");
  } catch {}
}

// ── Handler ─────────────────────────────────────────────────────────────────

const handler = async (event) => {
  try {
    if (event.type !== "agent" || event.action !== "bootstrap") return;

    const ctx = event.context || {};
    const sessionKey = event.sessionKey || ctx.sessionKey || "";
    const agentId = ctx.agentId || "";

    // Only filter for construction agent
    if (agentId !== "construction" && !sessionKey.includes("construction")) return;

    // Extract caller
    const callerId = extractCallerIdFromSessionKey(sessionKey);
    if (!callerId) return;

    // Load data
    const team = await loadJSON(path.join(DATA_DIR, "team.json"));
    const caller = resolveCaller(team, callerId);
    if (caller.persona === "unknown") return; // Unknown user, no filtering

    const schedule = await loadJSON(path.join(DATA_DIR, "schedule.json"));
    const budget = await loadJSON(path.join(DATA_DIR, "budget.json"));
    const purchases = await loadJSON(path.join(DATA_DIR, "purchases.json"));
    const contractType = await getContractType();

    // Filter
    const filteredData = {
      schedule: filterSchedule(schedule, caller.persona, caller.name, contractType),
      budget: filterBudget(budget, caller.persona, contractType),
      team: filterTeam(team, caller.persona, contractType),
      purchases: filterPurchases(purchases, caller.persona, contractType),
    };

    // Generate CONTEXT.md
    const contextMd = generateContextMd(caller, filteredData);

    // Inject into bootstrap files
    if (ctx.bootstrapFiles && Array.isArray(ctx.bootstrapFiles)) {
      ctx.bootstrapFiles.push({
        name: "CONTEXT.md",
        path: path.join(WORKSPACE, ".synthetic", "CONTEXT.md"),
        content: contextMd,
        missing: false,
      });
    }

    // Audit
    logAudit(caller, filteredData.schedule.tasks.length, contractType, filteredData);

  } catch (err) {
    // Silent — don't break agent bootstrap
  }
};

export default handler;
