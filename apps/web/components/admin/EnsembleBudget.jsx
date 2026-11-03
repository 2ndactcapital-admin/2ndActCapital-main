/**
 * AIBUDGET1 — the per-model daily budget, rendered right after each model
 * select in the ensemble picker, plus the read-only budget cells used by the
 * active selection and the history list.
 *
 * Pure components (no server actions) so the node render tests can mount
 * them with real envelopes. Every option, label, unit and sentence comes from
 * the server's envelope (lib/aiBudget.mjs); the write control renders only
 * when the server said can_write === true.
 */

import {
  budgetError,
  budgetKinds,
  canWrite,
  formatBudget,
  kindOf,
  rowBudget,
  semanticsText,
  usageText,
} from "@/lib/aiBudget.mjs";

const SELECT = "mt-1 w-full rounded-md border border-border bg-bg-card p-2 text-sm text-text-primary";

export function BudgetControl({ envelope, slot, slotLabel, model, budget, onChange }) {
  if (!canWrite(envelope) || !model) return null;
  const kinds = budgetKinds(envelope);
  const kind = budget?.kind || "";
  const value = budget?.value ?? "";
  const k = kindOf(kinds, kind);
  const err = kind ? budgetError(kinds, kind, value) : null;
  const usage = envelope?.usage_today?.[model];
  const sentence = semanticsText(envelope, model, kind, value);
  return (
    <div className="mt-2 rounded-md border border-border p-3" data-budget-slot={slot}>
      <label className="block text-sm">
        <span className="font-medium text-text-primary">{slotLabel} daily budget</span>
        <select
          value={kind}
          onChange={(e) => onChange({ kind: e.target.value, value: "" })}
          className={SELECT}
          name={`${slot}_budget_kind`}
        >
          <option value="">Choose a budget</option>
          {kinds.map((o) => (
            <option key={o.key} value={o.key}>
              {o.label}
            </option>
          ))}
        </select>
      </label>
      {k?.unit && (
        <label className="mt-2 block text-sm">
          <span className="text-text-muted">{k.unit === "USD" ? "Dollars per day" : `${k.label} (${k.unit})`}</span>
          <input
            type="text"
            inputMode={k.input === "whole_number" ? "numeric" : "decimal"}
            value={value}
            name={`${slot}_budget_value`}
            onChange={(e) => onChange({ kind, value: e.target.value })}
            className={SELECT}
            placeholder={k.unit === "USD" ? "25.00" : "1000000"}
          />
        </label>
      )}
      {usage && <p className="mt-2 text-xs text-text-muted">{model}: {usageText(usage)}</p>}
      {err && <p className="mt-1 text-xs text-text-muted">{err}</p>}
      {sentence && <p className="mt-2 text-sm text-text-primary">{sentence}</p>}
    </div>
  );
}

export function BudgetCell({ envelope, row, slot }) {
  const text = rowBudget(budgetKinds(envelope), row, slot);
  return <span className="text-text-muted">{text || "—"}</span>;
}

export function DayBoundaryNote({ envelope }) {
  const day = envelope?.day_boundary;
  if (!day) return null;
  return (
    <p className="mt-2 text-xs text-text-muted">
      {day.label}. Today is {day.today} ({day.timezone}). {envelope?.vocabularies?.shared_rule || ""}
    </p>
  );
}

export { formatBudget };
