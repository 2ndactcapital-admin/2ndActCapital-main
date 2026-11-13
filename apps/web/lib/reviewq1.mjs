// REVIEWQ1 — the Security master Review queue's pure helpers: the per-row choice, the one Run, bulk by filter, Known
// discrepancies. Everything that grants a control reads the SERVER's envelope and fails closed (a flag must be exactly
// true; a choice must be listed both in vocabularies.choices and in the row's own `choices`).
import { can, labelOf, vocab } from "../components/securityData/sourceLabel.mjs";

export const REVIEW_API = "/api/admin/security-data/master/review";
export const RUN_API = `${REVIEW_API}/run`;
export const BULK_API = `${REVIEW_API}/bulk`;
export const BULK_ESTIMATE_API = `${REVIEW_API}/bulk/estimate`;
export const moveBackApi = (id) => `${REVIEW_API}/${encodeURIComponent(id)}/move-back`;

export const canChoose = (env) => can(env, "can_choose") && vocab(env, "choices").length > 0;
export const canBulk = (env) => can(env, "can_bulk") && canChoose(env);
export const canMoveBack = (env) => can(env, "can_move_back");

// The choices a row offers: the server's vocabulary ∩ the row's own list (order of the vocabulary). Nothing otherwise.
export function rowChoices(env, row) {
  if (!canChoose(env)) return [];
  const mine = Array.isArray(row?.choices) ? row.choices : [];
  return vocab(env, "choices").filter((c) => mine.includes(c.key));
}

// selections: { [reviewId]: choiceKey } — only rows with a choice are ever sent.
export function choose(selections, id, choice) {
  const next = { ...(selections || {}) };
  if (choice == null || next[id] === choice) delete next[id];
  else next[id] = choice;
  return next;
}

export function choiceCounts(selections, env) {
  const out = {};
  for (const c of vocab(env, "choices")) out[c.key] = 0;
  for (const v of Object.values(selections || {})) if (v in out) out[v] += 1;
  return out;
}

export function runBody(selections, env, note) {
  const keys = new Set(vocab(env, "choices").map((c) => c.key));
  const choices = Object.entries(selections || {}).filter(([, c]) => keys.has(c)).map(([id, choice]) => ({ id, choice }));
  if (!choices.length) return null;
  const body = { choices };
  if (note && String(note).trim()) body.note = String(note).trim();
  return body;
}

export function runConfirmText(selections, env) {
  const counts = choiceCounts(selections, env);
  const total = Object.values(counts).reduce((a, b) => a + b, 0);
  if (!total) return null;
  const parts = vocab(env, "choices").filter((c) => counts[c.key]).map((c) => `${c.label}: ${counts[c.key]}`);
  return `Run ${total} decision${total === 1 ? "" : "s"}? ${parts.join(" · ")}. Rows with no choice stay in the queue.`;
}

export function runResultText(result, env) {
  if (!result) return "";
  const choices = vocab(env, "choices");
  const done = choices.filter((c) => result.applied_counts?.[c.key]).map((c) => `${c.label} ${result.applied_counts[c.key]}`);
  const refused = (result.refused || []).length;
  return `${done.length ? `Applied — ${done.join(" · ")}` : "Nothing applied"}${refused ? `; ${refused} refused (still in the queue)` : ""}.`;
}

// The review filter: only the names the SERVER lists (vocabularies.review_filters); empty values dropped.
export function cleanReviewFilter(raw, env) {
  const allowed = vocab(env, "review_filters");
  const out = {};
  for (const [k, v] of Object.entries(raw || {})) {
    if (!allowed.includes(k) || v == null || v === "" || v === false) continue;
    if (k === "home_vs_us") { if (v === true || v === "true") out[k] = "true"; continue; }
    out[k] = String(v);
  }
  return out;
}

export function reviewQuery(filter, env, status = "open") {
  const f = cleanReviewFilter(filter, env);
  return `${REVIEW_API}?${new URLSearchParams({ status, ...f, limit: "500" })}`;
}

export function bulkConfirmText(est, env) {
  if (!est || !est.total) return null;
  if (est.over_cap) return null;
  const label = labelOf(vocab(env, "choices"), est.choice);
  const not = est.not_offered ? ` ${est.not_offered} of them do not offer ${label} and will be refused (they stay in the queue).` : "";
  return `${label} for all ${est.total} matching item${est.total === 1 ? "" : "s"}?${not}`;
}

export function bulkBody(filter, env, est, note) {
  const filters = cleanReviewFilter(filter, env);
  if (!Object.keys(filters).length || !est) return null;
  const body = { filters, choice: est.choice, expected_total: est.total };
  if (note && String(note).trim()) body.note = String(note).trim();
  return body;
}

export function seenText(row) {
  const n = Number(row?.seen_again || 0);
  if (!n) return "not seen again";
  return `seen again ${n}×${row.last_seen ? ` · last ${String(row.last_seen).slice(0, 10)}` : ""}`;
}
