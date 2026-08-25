"use client";

/**
 * PositionDetailPane — the right-hand pane of the Positions screen.
 *
 * Opens on a grid row click. Never navigates: the grid stays mounted and
 * selected on the left, which is the whole point of the layout — an operator
 * checking twenty positions should not pay a page load per position.
 *
 * Everything here comes from ONE call, GET /api/portfolio/positions/{id}:
 * the position, its asset, its owner, the resolved current value, the
 * GOVERNING valuation that produced it, the asset's valuation history, the
 * position's transaction history, and the restatement chain. Linked source
 * documents come from the existing Chancery Phase-9 DocumentsPanel, embedded
 * unchanged with record_type 'portfolio_position' — the API supplies that
 * string rather than the component hardcoding it, because
 * document_record_links.record_type has no CHECK constraint and a typo would
 * write a link nothing ever reads back.
 *
 * The edit form lives here rather than in a grid cell on purpose. Changing a
 * quantity, a percentage, a value or the ownership basis can be REFUSED by the
 * basis contract (portfolio_assets._validate_basis — the only thing enforcing
 * it, since the table has no CHECK for it), and a refusal that surfaces as an
 * inline cell silently snapping back is worse than no inline edit at all.
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import DocumentsPanel from "@/components/DocumentsPanel";

const CARD = { borderColor: "#ece8dd", boxShadow: "0 1px 3px rgba(0,0,0,0.06)" };

const LABEL =
  "block text-[10px] font-semibold uppercase tracking-[0.12em] text-[var(--2a-text-muted)]";
const INPUT =
  "mt-1 w-full rounded border border-[var(--2a-border)] bg-white px-2 py-1.5 text-xs text-[var(--2a-text)] focus:outline-none focus:ring-1 focus:ring-[var(--2a-gold)]";

// Valuation status → pill styling. Reuses the conventions already established
// by DocumentsPanel (success green / navy in-progress / gold pending) rather
// than introducing a new colour vocabulary.
const STATUS_CFG = {
  audited: { bg: "#E8F5E9", color: "#2D6A4F" },
  final: { bg: "#EEF4FF", color: "var(--2a-navy)" },
  preliminary: { bg: "var(--2a-gold-light)", color: "var(--2a-navy)" },
  estimated: { bg: "var(--2a-bg-sidebar)", color: "var(--2a-text-muted)" },
  restated: { bg: "var(--2a-bg-sidebar)", color: "var(--2a-text-muted)" },
};

function StatusPill({ status }) {
  if (!status) return <span className="text-[var(--2a-text-muted)]">—</span>;
  const cfg = STATUS_CFG[status] || {
    bg: "var(--2a-bg-sidebar)",
    color: "var(--2a-text-muted)",
  };
  return (
    <span
      className="inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-medium capitalize"
      style={{ backgroundColor: cfg.bg, color: cfg.color }}
    >
      {status}
    </span>
  );
}

function fmtDate(value) {
  if (!value) return "—";
  const iso = value.length === 10 ? `${value}T00:00:00` : value;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return value;
  return d.toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

// Money arrives as an exact decimal STRING from the API and is only ever
// converted to a Number for DISPLAY. Nothing is computed from these on the
// client — every figure the pane shows was computed server-side in Decimal.
function fmtMoney(value, currency = "USD") {
  if (value == null || value === "") return "—";
  const n = Number(value);
  if (!Number.isFinite(n)) return value;
  return n.toLocaleString("en-US", {
    style: "currency",
    currency: currency || "USD",
    maximumFractionDigits: 2,
  });
}

function fmtNumber(value) {
  if (value == null || value === "") return "—";
  const n = Number(value);
  if (!Number.isFinite(n)) return value;
  return n.toLocaleString("en-US", { maximumFractionDigits: 6 });
}

function Section({ title, count, children, right }) {
  return (
    <section className="border-t border-[var(--2a-border)] px-5 py-4">
      <div className="mb-2 flex items-center justify-between gap-3">
        <h3 className="text-[11px] font-semibold uppercase tracking-[0.18em] text-[var(--2a-gold)]">
          {title}
          {count != null && (
            <span className="ml-2 font-normal normal-case tracking-normal text-[var(--2a-text-muted)]">
              {count}
            </span>
          )}
        </h3>
        {right}
      </div>
      {children}
    </section>
  );
}

function Field({ label, children }) {
  return (
    <div>
      <span className={LABEL}>{label}</span>
      <p className="mt-0.5 text-xs text-[var(--2a-text)]">{children}</p>
    </div>
  );
}

function Empty({ children }) {
  return (
    <p className="rounded border border-[var(--2a-border)] bg-[var(--2a-bg)] px-3 py-4 text-center text-xs text-[var(--2a-text-muted)]">
      {children}
    </p>
  );
}

// ─── The measure a basis actually authorises ─────────────────────────────────

// `ownership_basis` selects which of three columns is AUTHORITATIVE. The form
// below renders exactly that one as editable and shows the others read-only,
// so the shape of the UI matches the contract the backend enforces instead of
// offering three boxes of which two will be refused.
const BASIS_MEASURE = {
  units: { field: "quantity", label: "Quantity" },
  percent: { field: "ownership_pct", label: "Ownership %" },
  value: { field: "market_value", label: "Market value" },
};

export default function PositionDetailPane({
  positionId,
  taxonomy,
  onSaved,
  onClose,
}) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const [draft, setDraft] = useState({});
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState(null);
  const [saved, setSaved] = useState(false);

  const load = useCallback(async () => {
    if (!positionId) {
      setData(null);
      return;
    }
    setLoading(true);
    setError(null);
    setSaveError(null);
    setSaved(false);
    try {
      const res = await fetch(`/api/portfolio/positions/${positionId}`, {
        cache: "no-store",
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(body.error || "Could not load this position.");
      setData(body);
      setDraft({});
    } catch (err) {
      setError(err.message);
      setData(null);
    } finally {
      setLoading(false);
    }
  }, [positionId]);

  useEffect(() => {
    load();
  }, [load]);

  const position = data?.position;
  const governing = data?.governing_valuation;

  // Read from THIS pane's own response, not threaded down from the grid. The
  // pane fetches the detail endpoint itself, so a permission answer passed as a
  // prop would be a second copy that could go stale while this one is fresh.
  const permissions = data?.permissions;
  const vocabularies = data?.vocabularies;

  // Empty for a caller without manage_portfolio, because the SERVER empties it.
  // No `|| DEFAULTS` — that would restore the whole form for a view-only user.
  const editable = useMemo(
    () => new Set(vocabularies?.editable || []),
    [vocabularies],
  );
  const canWrite = !!permissions?.can_write;

  const taxonomyOptions = useMemo(
    () =>
      Object.entries(taxonomy || {}).sort((a, b) => a[1].localeCompare(b[1])),
    [taxonomy],
  );

  const measure = position ? BASIS_MEASURE[position.ownership_basis] : null;

  function setField(name, value) {
    setDraft((prev) => ({ ...prev, [name]: value }));
    setSaved(false);
  }

  function current(name) {
    return draft[name] !== undefined ? draft[name] : (position?.[name] ?? "");
  }

  const dirty = Object.keys(draft).length > 0;

  async function save() {
    if (!dirty || !position) return;
    setSaving(true);
    setSaveError(null);
    setSaved(false);
    try {
      // Empty string means "clear this field". null is meaningful to the API —
      // it is what switching ownership basis requires, because the outgoing
      // measure must become NULL for the contract to hold.
      const payload = {};
      for (const [key, value] of Object.entries(draft)) {
        if (typeof value === "string" && value.trim() === "") payload[key] = null;
        else payload[key] = value;
      }
      const res = await fetch(`/api/portfolio/positions/${position.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(
          body.error || body.detail || "The edit was refused.",
        );
      }
      // An edit is a bi-temporal restatement: the row we just edited is now
      // history and the response carries a DIFFERENT id. Adopt it, or every
      // subsequent read on this pane would be reading a closed row.
      setData(body);
      setDraft({});
      setSaved(true);
      onSaved?.(body);
    } catch (err) {
      setSaveError(err.message);
    } finally {
      setSaving(false);
    }
  }

  if (!positionId) {
    return (
      <div className="flex h-full items-center justify-center px-8 text-center">
        <div>
          <p className="text-sm font-medium text-[var(--2a-text-secondary)]">
            Select a position
          </p>
          <p className="mt-1 text-xs text-[var(--2a-text-muted)]">
            Its valuation, transactions and linked documents appear here.
          </p>
        </div>
      </div>
    );
  }

  if (loading && !data) {
    return (
      <p className="px-5 py-8 text-center text-sm text-[var(--2a-text-muted)]">
        Loading…
      </p>
    );
  }

  if (error) {
    return (
      <div className="px-5 py-8 text-center">
        <p className="text-sm text-[#9B2335]">{error}</p>
        <button
          type="button"
          onClick={load}
          className="mt-2 text-xs font-medium text-[var(--2a-navy)] hover:underline"
        >
          Retry
        </button>
      </div>
    );
  }

  if (!position) return null;

  return (
    <div className="flex h-full flex-col overflow-auto">
      {/* ── Header ─────────────────────────────────────────────────────── */}
      <div className="sticky top-0 z-10 border-b border-[var(--2a-border)] bg-white px-5 py-4">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <h2
              className="truncate text-base font-semibold text-[var(--2a-navy)]"
              style={{ fontFamily: "Spectral, Georgia, serif" }}
            >
              {position.asset_name}
            </h2>
            <p className="mt-0.5 truncate text-xs text-[var(--2a-text-muted)]">
              {position.owner_name}
              {position.asset_type ? ` · ${position.asset_type}` : ""}
              {` · as of ${fmtDate(position.as_of_date)}`}
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close detail"
            className="shrink-0 rounded px-2 py-1 text-xs text-[var(--2a-text-muted)] hover:bg-[var(--2a-bg)]"
          >
            ✕
          </button>
        </div>

        {!position.is_current && (
          <p className="mt-2 rounded bg-[var(--2a-bg-sidebar)] px-2 py-1 text-[10px] text-[var(--2a-text-muted)]">
            This row was closed by a later restatement — it is history, not the
            current position.
          </p>
        )}
      </div>

      {/* ── Resolved current value + its governing valuation ───────────── */}
      <div className="px-5 py-4">
        <span className={LABEL}>Current value</span>
        {position.current_value != null ? (
          <p className="mt-1 text-2xl font-semibold tabular-nums text-[var(--2a-navy)]">
            {fmtMoney(position.current_value, position.currency_code)}
          </p>
        ) : (
          <>
            <p className="mt-1 text-2xl font-semibold text-[var(--2a-text-muted)]">
              —
            </p>
            {/* Never rendered as $0. An absence of measurement and a genuine
                zero are different facts, and the API keeps them apart all the
                way out to this string. */}
            <p className="mt-1 text-[11px] leading-snug text-[var(--2a-text-muted)]">
              No value could be resolved: {position.current_value_reason}
            </p>
          </>
        )}

        <div className="mt-3 rounded border border-[var(--2a-border)] bg-[var(--2a-bg)] px-3 py-2">
          <div className="flex items-center justify-between gap-2">
            <span className={LABEL}>Governing valuation</span>
            <StatusPill status={governing?.status} />
          </div>
          {governing?.valuation_id ? (
            <div className="mt-1.5 grid grid-cols-2 gap-x-4 gap-y-1 text-[11px] text-[var(--2a-text-secondary)]">
              <span>Dated {fmtDate(governing.valuation_date)}</span>
              <span className="text-right tabular-nums">
                {fmtMoney(governing.asset_value, governing.currency_code)}
              </span>
              <span className="capitalize">
                {(governing.value_basis || "").replace(/_/g, " ")}
              </span>
              <span className="text-right">
                {governing.is_superseded ? "superseded" : "current"}
              </span>
              {position.ownership_basis === "percent" && (
                <span className="col-span-2 text-[10px] text-[var(--2a-text-muted)]">
                  This position is {fmtNumber(position.ownership_pct)}% of the
                  asset — its value is that fraction of the mark above, not the
                  stored figure.
                </span>
              )}
            </div>
          ) : (
            <p className="mt-1 text-[11px] leading-snug text-[var(--2a-text-muted)]">
              {governing?.reason || "No valuation resolved for this asset."}
            </p>
          )}
        </div>
      </div>

      {/* ── The edit form ──────────────────────────────────────────────── */}
      <Section
        title="Position"
        right={
          // The Save/Discard toolbar EXISTS only for a caller the server says
          // may write. Not disabled — absent. A disabled Save still tells a
          // view-only member that editing is a thing this screen does and that
          // they are one click from finding out it is not.
          canWrite ? (
            <div className="flex items-center gap-2">
              {saved && (
                <span className="text-[10px] text-[#2D6A4F]">Restated</span>
              )}
              {dirty && (
                <button
                  type="button"
                  onClick={() => {
                    setDraft({});
                    setSaveError(null);
                  }}
                  className="text-[11px] text-[var(--2a-text-muted)] hover:underline"
                >
                  Discard
                </button>
              )}
              <button
                type="button"
                onClick={save}
                disabled={!dirty || saving || !position.is_current}
                className="rounded px-3 py-1 text-[11px] font-medium text-white disabled:opacity-40"
                style={{ backgroundColor: "var(--2a-navy)" }}
              >
                {saving ? "Saving…" : "Save"}
              </button>
            </div>
          ) : null
        }
      >
        {!canWrite && (
          <p className="mb-3 rounded bg-[var(--2a-bg-sidebar)] px-3 py-2 text-[11px] leading-snug text-[var(--2a-text-muted)]">
            Read-only. Restating a position requires{" "}
            {permissions?.write_permission}.
          </p>
        )}
        {saveError && (
          <div className="mb-3 rounded bg-[#FEF3F2] px-3 py-2 text-[11px] leading-snug text-[#9B2335]">
            {saveError}
          </div>
        )}
        {dirty && (
          <p className="mb-3 text-[10px] leading-snug text-[var(--2a-text-muted)]">
            Saving closes this row and records a successor (Rule 3). The
            previous state stays queryable and appears in Restatements below.
          </p>
        )}

        <div className="grid grid-cols-2 gap-3">
          {/* Every control below renders if and only if the SERVER named its
              field in `vocabularies.editable`. That list is empty without
              manage_portfolio, so a view-only caller gets the same figures as
              flat read-only text and no input to type into. The vocabulary
              options come from the same response — the hardcoded
              ["units","percent","value"] fallback that used to sit on the basis
              select is gone, because a client-side list is exactly the thing
              that survives an empty envelope and puts a control back. */}
          {editable.has("ownership_basis") ? (
            <div>
              <label className={LABEL} htmlFor="pd-basis">
                Ownership basis
              </label>
              <select
                id="pd-basis"
                className={INPUT}
                value={current("ownership_basis")}
                onChange={(e) => setField("ownership_basis", e.target.value)}
              >
                {(vocabularies?.ownership_basis || []).map((b) => (
                  <option key={b} value={b}>
                    {b}
                  </option>
                ))}
              </select>
            </div>
          ) : (
            <Field label="Ownership basis">{position.ownership_basis}</Field>
          )}

          {editable.has("as_of_date") ? (
            <div>
              <label className={LABEL} htmlFor="pd-asof">
                As of date
              </label>
              <input
                id="pd-asof"
                type="date"
                className={INPUT}
                value={current("as_of_date") || ""}
                onChange={(e) => setField("as_of_date", e.target.value)}
              />
            </div>
          ) : (
            <Field label="As of date">{fmtDate(position.as_of_date)}</Field>
          )}

          {/* The authoritative measure for the CURRENT (possibly edited) basis. */}
          {(() => {
            const basis = current("ownership_basis");
            const active = BASIS_MEASURE[basis] || measure;
            if (!active) return null;
            if (!editable.has(active.field)) {
              return (
                <Field label={`${active.label} · authoritative`}>
                  {position[active.field] ?? "—"}
                </Field>
              );
            }
            return (
              <div>
                <label className={LABEL} htmlFor="pd-measure">
                  {active.label} <span className="text-[var(--2a-gold)]">·
                  authoritative</span>
                </label>
                <input
                  id="pd-measure"
                  type="text"
                  inputMode="decimal"
                  className={INPUT}
                  value={current(active.field) ?? ""}
                  onChange={(e) => setField(active.field, e.target.value)}
                  placeholder="0.00"
                />
              </div>
            );
          })()}

          {editable.has("cost_basis") ? (
            <div>
              <label className={LABEL} htmlFor="pd-cost">
                Cost basis
              </label>
              <input
                id="pd-cost"
                type="text"
                inputMode="decimal"
                className={INPUT}
                value={current("cost_basis") ?? ""}
                onChange={(e) => setField("cost_basis", e.target.value)}
              />
            </div>
          ) : (
            <Field label="Cost basis">{position.cost_basis ?? "—"}</Field>
          )}

          {/* The two measures the basis does NOT authorise. Shown, disabled,
              with the reason — the contract requires them to be NULL, and a
              user who cannot see them has no way to understand why an edit
              that "only changed the basis" was refused. */}
          {/* Only shown to a caller who can actually attempt that edit — the
              block exists to explain why an edit WOULD be refused, and it
              carries a "Clear" button, which is a write control. A view-only
              caller sees the same three measures below, as plain figures. */}
          {canWrite &&
            ["quantity", "ownership_pct", "market_value"]
            .filter((f) => f !== (BASIS_MEASURE[current("ownership_basis")]?.field))
            .map((f) => (
              <div key={f}>
                <span className={LABEL}>
                  {f.replace(/_/g, " ")}{" "}
                  <span className="text-[var(--2a-text-muted)]">· must be empty</span>
                </span>
                <div className="mt-1 flex items-center gap-2">
                  <input
                    type="text"
                    disabled
                    className={`${INPUT} cursor-not-allowed bg-[var(--2a-bg)] opacity-60`}
                    value={current(f) ?? ""}
                    readOnly
                  />
                  {current(f) !== "" && current(f) != null && (
                    <button
                      type="button"
                      onClick={() => setField(f, "")}
                      className="shrink-0 text-[10px] text-[var(--2a-navy)] hover:underline"
                    >
                      Clear
                    </button>
                  )}
                </div>
              </div>
            ))}

          {/* The three measures, read-only, for a caller with no edit form. */}
          {!canWrite &&
            ["quantity", "ownership_pct", "market_value"]
              .filter(
                (f) => f !== BASIS_MEASURE[position.ownership_basis]?.field,
              )
              .map((f) => (
                <Field key={f} label={f.replace(/_/g, " ")}>
                  {position[f] ?? "—"}
                </Field>
              ))}

          {editable.has("authority") ? (
            <div>
              <label className={LABEL} htmlFor="pd-authority">
                Authority
              </label>
              <select
                id="pd-authority"
                className={INPUT}
                value={current("authority")}
                onChange={(e) => setField("authority", e.target.value)}
              >
                {(vocabularies?.authority || []).map((a) => (
                  <option key={a} value={a}>
                    {a}
                  </option>
                ))}
              </select>
            </div>
          ) : (
            <Field label="Authority">{position.authority}</Field>
          )}

          {editable.has("source_system") ? (
            <div>
              <label className={LABEL} htmlFor="pd-source">
                Source system
              </label>
              <select
                id="pd-source"
                className={INPUT}
                value={current("source_system")}
                onChange={(e) => setField("source_system", e.target.value)}
              >
                {(vocabularies?.source_system || []).map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </select>
            </div>
          ) : (
            <Field label="Source system">{position.source_system}</Field>
          )}

          {editable.has("taxonomy_key") ? (
            <div className="col-span-2">
              <label className={LABEL} htmlFor="pd-taxonomy">
                Taxonomy
              </label>
              <select
                id="pd-taxonomy"
                className={INPUT}
                value={current("taxonomy_key") ?? ""}
                onChange={(e) => setField("taxonomy_key", e.target.value)}
              >
                <option value="">— unassigned —</option>
                {taxonomyOptions.map(([key, label]) => (
                  <option key={key} value={key}>
                    {label}
                  </option>
                ))}
              </select>
            </div>
          ) : (
            <div className="col-span-2">
              <Field label="Taxonomy">
                {position.taxonomy_label ||
                  position.taxonomy_key ||
                  "— unassigned —"}
              </Field>
            </div>
          )}
        </div>

        <div className="mt-4 grid grid-cols-2 gap-3 border-t border-[var(--2a-border)] pt-3">
          <Field label="Owner">{position.owner_name}</Field>
          <Field label="Asset class">{position.asset_class}</Field>
          <Field label="Valuation method">
            {(position.valuation_method || "").replace(/_/g, " ")}
          </Field>
          <Field label="Source state">
            {position.is_superseded
              ? `outranked by ${position.superseded_by_source}`
              : "current winner"}
          </Field>
        </div>
      </Section>

      {/* ── Valuation history ──────────────────────────────────────────── */}
      <Section title="Valuation history" count={data.valuation_history.length}>
        {data.valuation_history.length === 0 ? (
          <Empty>No valuations recorded for this asset.</Empty>
        ) : (
          <table className="w-full text-[11px]">
            <thead className="text-[var(--2a-text-muted)]">
              <tr className="border-b border-[var(--2a-border)]">
                <th className="pb-1 text-left font-semibold">Date</th>
                <th className="pb-1 text-right font-semibold">Value</th>
                <th className="pb-1 text-left font-semibold">Basis</th>
                <th className="pb-1 text-center font-semibold">Status</th>
              </tr>
            </thead>
            <tbody>
              {data.valuation_history.map((v) => (
                <tr
                  key={v.id}
                  className="border-t border-[var(--2a-border)]"
                  style={v.is_superseded ? { opacity: 0.55 } : undefined}
                  title={
                    v.is_superseded
                      ? "Restated by a later valuation. Kept, not edited — both rows stay queryable."
                      : undefined
                  }
                >
                  <td className="py-1 text-[var(--2a-text-secondary)]">
                    {fmtDate(v.valuation_date)}
                  </td>
                  <td className="py-1 text-right tabular-nums text-[var(--2a-text)]">
                    {fmtMoney(v.value, v.currency_code)}
                  </td>
                  <td className="py-1 text-[var(--2a-text-muted)]">
                    {(v.value_basis || "").replace(/_/g, " ")}
                  </td>
                  <td className="py-1 text-center">
                    <StatusPill status={v.status} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Section>

      {/* ── Transaction history ────────────────────────────────────────── */}
      <Section title="Transactions" count={data.transactions.length}>
        {data.transactions.length === 0 ? (
          <Empty>No transactions recorded against this position.</Empty>
        ) : (
          <table className="w-full text-[11px]">
            <thead className="text-[var(--2a-text-muted)]">
              <tr className="border-b border-[var(--2a-border)]">
                <th className="pb-1 text-left font-semibold">Trade date</th>
                <th className="pb-1 text-left font-semibold">Type</th>
                <th className="pb-1 text-right font-semibold">Quantity</th>
                <th className="pb-1 text-right font-semibold">Net</th>
              </tr>
            </thead>
            <tbody>
              {data.transactions.map((t) => (
                <tr key={t.id} className="border-t border-[var(--2a-border)]">
                  <td className="py-1 text-[var(--2a-text-secondary)]">
                    {fmtDate(t.trade_date)}
                  </td>
                  <td className="py-1 text-[var(--2a-text-secondary)]">
                    {t.transaction_type_label}
                    {t.is_corporate_action_adjustment && (
                      <span
                        className="ml-1 rounded-full px-1.5 py-0.5 text-[9px]"
                        style={{
                          backgroundColor: "var(--2a-gold-light)",
                          color: "var(--2a-navy)",
                        }}
                        title="Corporate-action adjustment — excluded from realized gain"
                      >
                        adj
                      </span>
                    )}
                  </td>
                  <td className="py-1 text-right tabular-nums text-[var(--2a-text)]">
                    {fmtNumber(t.quantity)}
                  </td>
                  <td className="py-1 text-right tabular-nums text-[var(--2a-text)]">
                    {fmtMoney(t.net_amount, t.currency_code)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Section>

      {/* ── Restatement chain ──────────────────────────────────────────── */}
      {data.restatement_history.length > 1 && (
        <Section title="Restatements" count={data.restatement_history.length}>
          <ul className="space-y-1">
            {data.restatement_history.map((r) => (
              <li
                key={r.id}
                className="flex items-center justify-between gap-2 text-[11px]"
                style={r.is_current ? undefined : { opacity: 0.6 }}
              >
                <span className="text-[var(--2a-text-secondary)]">
                  {fmtDate(r.valid_from)}
                  {r.is_current ? " · current" : ` → ${fmtDate(r.valid_to)}`}
                </span>
                <span className="tabular-nums text-[var(--2a-text)]">
                  {r.ownership_basis === "percent"
                    ? `${fmtNumber(r.ownership_pct)}%`
                    : r.ownership_basis === "units"
                      ? fmtNumber(r.quantity)
                      : fmtMoney(r.market_value)}
                </span>
              </li>
            ))}
          </ul>
        </Section>
      )}

      {/* ── Linked source documents — the REAL Phase-9 panel, unchanged ── */}
      <div className="border-t border-[var(--2a-border)] px-5 py-4">
        <DocumentsPanel
          recordType={data.document_record_type}
          recordId={position.id}
          title="Source documents"
        />
      </div>
    </div>
  );
}

export { CARD, fmtDate, fmtMoney, fmtNumber };
