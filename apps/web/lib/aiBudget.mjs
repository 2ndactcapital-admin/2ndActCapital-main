// AIBUDGET1 — pure helpers for the ensemble budget control and the daily-run
// screen. Every vocabulary (budget kinds, their labels and units, the status
// labels, the day boundary, the sentence templates) comes from the server's
// envelope; nothing here carries a fallback list. A missing envelope yields
// no options and no write controls (fail closed).

/** Write controls render only when the server said exactly true. */
export function canWrite(envelope) {
  return envelope?.permissions?.can_write === true;
}

export function budgetKinds(envelope) {
  const kinds = envelope?.vocabularies?.budget_kinds;
  return Array.isArray(kinds) ? kinds : [];
}

export function kindOf(kinds, key) {
  return kinds.find((k) => k.key === key) || null;
}

/** The control's starting state for a freshly chosen model (server's default kind). */
export function defaultBudget(option) {
  return { kind: option?.default_budget_kind || "", value: "" };
}

const fmtInt = (n) => Number(n).toLocaleString("en-US", { maximumFractionDigits: 0 });
const fmtUsd = (n, digits = 2) =>
  `$${Number(n).toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits })}`;

/** "1,000,000 tokens" / "$25.00", using the unit the server published for the kind. */
export function formatBudget(kinds, kind, value) {
  const k = kindOf(kinds, kind);
  if (!k) return "";
  if (!k.unit) return k.label;
  if (value === null || value === undefined || value === "") return k.label;
  if (k.unit === "USD") return `${fmtUsd(value)} per day`;
  return `${fmtInt(value)} ${k.unit} per day`;
}

/** Client-side mirror of the server's rule, for an immediate hint only — the server decides. */
export function budgetError(kinds, kind, value) {
  const k = kindOf(kinds, kind);
  if (!k) return "Choose a budget.";
  if (!k.unit) return null;
  const s = String(value ?? "").trim();
  if (!s) return `Enter the ${k.label.toLowerCase()} amount.`;
  if (!/^\d+(\.\d+)?$/.test(s)) return "Enter a positive number.";
  const n = Number(s);
  if (!(n > 0)) return "The amount must be positive.";
  if (k.input === "whole_number" && !/^\d+$/.test(s)) return "Tokens are a whole number.";
  if (k.input === "dollars" && !/^\d+(\.\d{1,2})?$/.test(s)) return "Dollars in whole cents.";
  return null;
}

/** The sentence under the control: what this budget means, from the server's templates. */
export function semanticsText(envelope, model, kind, value) {
  if (!model || !kind) return "";
  const kinds = budgetKinds(envelope);
  const k = kindOf(kinds, kind);
  if (!k) return "";
  const v = envelope?.vocabularies || {};
  if (!k.unit) return (v.unlimited_template || "").replaceAll("{model}", model);
  if (budgetError(kinds, kind, value)) return "";
  return (v.semantics_template || "")
    .replaceAll("{model}", model)
    .replaceAll("{budget}", formatBudget(kinds, kind, value).replace(/ per day$/, ""));
}

export function usageText(usage) {
  if (!usage) return "";
  const tokens = Number(usage.total_tokens || 0);
  const cost = Number(usage.cost_usd || 0);
  return `${fmtInt(tokens)} tokens · ${fmtUsd(cost, 4)} used today`;
}

/** The activation body's budget fields for both slots. */
export function budgetPayload(choice) {
  const out = {};
  for (const slot of ["model_1", "model_2"]) {
    const b = choice?.[`${slot}_budget`] || {};
    out[`${slot}_budget_kind`] = b.kind || null;
    out[`${slot}_budget_value`] = b.kind && b.value !== "" && b.value !== undefined ? String(b.value).trim() : null;
  }
  return out;
}

/** A config row's budget for one slot, formatted ("Unlimited" / "250,000 tokens per day"). */
export function rowBudget(kinds, row, slot) {
  return formatBudget(kinds, row?.[`${slot}_budget_kind`], row?.[`${slot}_budget_value`]);
}

export function statusLabel(envelope, key) {
  const s = (envelope?.vocabularies?.statuses || []).find((x) => x.key === key);
  return s ? s.label : key || "";
}

/** "gpt-5-mini: 1,234 in / 567 out · $0.0012" lines for a run's usage map. */
export function runUsageLines(usage) {
  return Object.entries(usage || {}).map(
    ([model, u]) =>
      `${model}: ${fmtInt(u.input_tokens || 0)} in / ${fmtInt(u.output_tokens || 0)} out · ${fmtUsd(u.cost_usd || 0, 4)}`,
  );
}

export function stopText(kinds, run) {
  if (!run || run.status !== "stopped_budget" || !run.stopped_model) return "";
  return `${run.stopped_model} reached its budget (${formatBudget(kinds, run.stopped_budget_kind, run.stopped_budget_value)})`;
}
