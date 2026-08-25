"use client";

/**
 * TransactionDetailPane — the right-hand pane of the Transactions screen.
 *
 * Opens on a grid row click. Never navigates away from the grid, which stays
 * mounted and selected on the left — an operator checking twenty entries should
 * not pay a page load per entry. The ONE link that does navigate is the
 * click-through to the owning position on the Positions screen, which is a
 * different screen and a deliberate exit.
 *
 * Everything here comes from ONE call, GET /api/portfolio/transactions/{id}:
 * the entry, the position it belongs to, and the full correction chain. Linked
 * source documents come from the existing Chancery Phase-9 DocumentsPanel,
 * embedded unchanged with record_type 'portfolio_transaction' — the API
 * supplies that string rather than the component hardcoding it, because
 * document_record_links.record_type has no CHECK constraint and a typo would
 * write a link nothing ever reads back.
 *
 * THE POSITION LINK IS NOT ALWAYS `transaction.position_id`
 * ─────────────────────────────────────────────────────────────────────────
 * A transaction stays attached to the position row it was recorded against.
 * When that position is later restated — by a corporate action or a hand
 * correction — a NEW position id is minted and the old row is closed. The
 * Positions grid hides closed rows by default, so linking `position_id`
 * directly would frequently land the user on nothing. The API returns
 * `position.current_position_id` alongside, and that is what the link uses,
 * with the historical id shown next to it so the difference is visible rather
 * than papered over.
 *
 * A CORRECTION IS NOT AN EDIT
 * ─────────────────────────────────────────────────────────────────────────
 * portfolio.transactions is an append-only ledger. Saving POSTs to
 * .../corrections, which closes this entry and records a successor with a NEW
 * id. The pane adopts that id; the previous version stays queryable and is
 * listed under "Versions" below. The form is disabled outright on a
 * non-current entry — correcting history in place is the one thing the ledger
 * must never allow.
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import DocumentsPanel from "@/components/DocumentsPanel";

const CARD = { borderColor: "#ece8dd", boxShadow: "0 1px 3px rgba(0,0,0,0.06)" };

const LABEL =
  "block text-[10px] font-semibold uppercase tracking-[0.12em] text-[var(--2a-text-muted)]";
const INPUT =
  "mt-1 w-full rounded border border-[var(--2a-border)] bg-white px-2 py-1.5 text-xs text-[var(--2a-text)] focus:outline-none focus:ring-1 focus:ring-[var(--2a-gold)]";

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

// Money arrives as an exact decimal STRING and is only ever converted to a
// Number for DISPLAY. Nothing is computed from these on the client — every
// figure the pane shows was computed server-side in Decimal.
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

// The money/quantity fields, in the order the form shows them.
const MONEY_FIELDS = [
  { field: "quantity", label: "Quantity", kind: "number" },
  { field: "price", label: "Price", kind: "money" },
  { field: "gross_amount", label: "Gross amount", kind: "money" },
  { field: "fees", label: "Fees", kind: "money" },
  { field: "taxes", label: "Taxes", kind: "money" },
  { field: "net_amount", label: "Net amount", kind: "money" },
];

export default function TransactionDetailPane({
  transactionId,
  transactionTypes,
  onCorrected,
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
    if (!transactionId) {
      setData(null);
      return;
    }
    setLoading(true);
    setError(null);
    setSaveError(null);
    setSaved(false);
    try {
      const res = await fetch(`/api/portfolio/transactions/${transactionId}`, {
        cache: "no-store",
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok)
        throw new Error(body.error || "Could not load this transaction.");
      setData(body);
      setDraft({});
    } catch (err) {
      setError(err.message);
      setData(null);
    } finally {
      setLoading(false);
    }
  }, [transactionId]);

  useEffect(() => {
    load();
  }, [load]);

  const txn = data?.transaction;
  const position = data?.position;

  // Read from THIS pane's own response, not threaded down from the grid. The
  // pane fetches the detail endpoint itself, so a permission answer passed as a
  // prop would be a second copy that could go stale while this one is fresh.
  const permissions = data?.permissions;
  const vocabularies = data?.vocabularies;

  // The server publishes which fields a correction may name. The form renders
  // that list rather than keeping its own copy that could drift — and the list
  // is EMPTY for a caller without manage_portfolio, so the same mechanism that
  // marks a field uncorrectable also stands the whole form down. No
  // `|| DEFAULTS`.
  const correctable = useMemo(
    () => new Set(vocabularies?.correctable || []),
    [vocabularies],
  );
  const canCorrect = !!permissions?.can_correct;

  const activeTypes = useMemo(
    () =>
      (transactionTypes || []).filter(
        (t) => t.is_active || t.code === txn?.transaction_type_code,
      ),
    [transactionTypes, txn],
  );

  function setField(name, value) {
    setDraft((prev) => ({ ...prev, [name]: value }));
    setSaved(false);
  }

  function current(name) {
    return draft[name] !== undefined ? draft[name] : (txn?.[name] ?? "");
  }

  const dirty = Object.keys(draft).length > 0;

  async function save() {
    if (!dirty || !txn) return;
    setSaving(true);
    setSaveError(null);
    setSaved(false);
    try {
      // Empty string means "clear this field". null is meaningful to the API:
      // a fee that was never real has to be clearable, not merely settable to
      // zero, because unrecorded fees and zero fees are different facts.
      const payload = {};
      for (const [key, value] of Object.entries(draft)) {
        if (typeof value === "string" && value.trim() === "") payload[key] = null;
        else payload[key] = value;
      }
      const res = await fetch(
        `/api/portfolio/transactions/${txn.id}/corrections`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        },
      );
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(
          body.error || body.detail || "The correction was refused.",
        );
      }
      // A correction closes this entry and records a successor with a DIFFERENT
      // id. Adopt it, or every subsequent read on this pane would be against a
      // closed row.
      setData(body);
      setDraft({});
      setSaved(true);
      onCorrected?.(body);
    } catch (err) {
      setSaveError(err.message);
    } finally {
      setSaving(false);
    }
  }

  if (!transactionId) {
    return (
      <div className="flex h-full items-center justify-center px-8 text-center">
        <div>
          <p className="text-sm font-medium text-[var(--2a-text-secondary)]">
            Select a transaction
          </p>
          <p className="mt-1 text-xs text-[var(--2a-text-muted)]">
            Its figures, its position and its source documents appear here.
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

  if (!txn) return null;

  const positionHref = position
    ? `/portfolio/positions?position=${position.current_position_id || position.id}`
    : null;

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
              {txn.transaction_type_label}
            </h2>
            <p className="mt-0.5 truncate text-xs text-[var(--2a-text-muted)]">
              {txn.asset_name}
              {` · ${txn.owner_name}`}
              {` · traded ${fmtDate(txn.trade_date)}`}
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

        {/* Phase F: an adjustment must never read as an ordinary trade, in the
            pane any more than in the grid. */}
        {txn.is_corporate_action_adjustment && (
          <p
            className="mt-2 rounded px-2 py-1.5 text-[10px] leading-snug"
            style={{
              backgroundColor: "var(--2a-gold-light)",
              color: "var(--2a-navy)",
            }}
          >
            Corporate-action adjustment. Recorded by the corporate-actions engine
            to restate this holding — it is not a trade, and a realized-gain
            calculation excludes it.
            {txn.corporate_action_id && (
              <span className="ml-1 opacity-70">
                Action {txn.corporate_action_id.slice(0, 8)}…
              </span>
            )}
          </p>
        )}

        {!txn.is_current && (
          <p className="mt-2 rounded bg-[var(--2a-bg-sidebar)] px-2 py-1 text-[10px] text-[var(--2a-text-muted)]">
            This entry was superseded by a later correction — it is history, not
            the current ledger entry.
          </p>
        )}
      </div>

      {/* ── Headline figure ────────────────────────────────────────────── */}
      <div className="px-5 py-4">
        <span className={LABEL}>Net amount</span>
        {txn.net_amount != null ? (
          <p className="mt-1 text-2xl font-semibold tabular-nums text-[var(--2a-navy)]">
            {fmtMoney(txn.net_amount, txn.currency_code)}
          </p>
        ) : (
          <>
            <p className="mt-1 text-2xl font-semibold text-[var(--2a-text-muted)]">
              —
            </p>
            {/* Never rendered as $0. An unrecorded net and a genuine zero are
                different facts and the API keeps them apart. */}
            <p className="mt-1 text-[11px] leading-snug text-[var(--2a-text-muted)]">
              No net amount was recorded on this entry.
            </p>
          </>
        )}
        <div className="mt-2 grid grid-cols-3 gap-x-4 gap-y-1 text-[11px] text-[var(--2a-text-secondary)]">
          <span>
            {txn.amount_basis === "units" ? "Units basis" : "Currency basis"}
          </span>
          <span className="text-center capitalize">
            {(txn.transaction_type_category || "").replace(/_/g, " ")}
          </span>
          <span className="text-right capitalize">
            {(txn.performance_impact || "no").replace(/_/g, " ")} impact
          </span>
        </div>
      </div>

      {/* ── The owning position, linked ────────────────────────────────── */}
      <Section title="Position">
        {!position ? (
          <Empty>The owning position could not be read.</Empty>
        ) : (
          <div className="rounded border border-[var(--2a-border)] bg-[var(--2a-bg)] px-3 py-2">
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0">
                <p className="truncate text-xs font-medium text-[var(--2a-text)]">
                  {position.asset_name}
                </p>
                <p className="mt-0.5 truncate text-[11px] text-[var(--2a-text-muted)]">
                  {position.owner_name} · as of {fmtDate(position.as_of_date)} ·{" "}
                  {position.ownership_basis}
                </p>
              </div>
              <a
                href={positionHref}
                className="shrink-0 text-[11px] font-medium text-[var(--2a-navy)] underline hover:text-[var(--2a-gold)]"
              >
                Open in Positions →
              </a>
            </div>
            {!position.is_current && (
              <p className="mt-2 text-[10px] leading-snug text-[var(--2a-text-muted)]">
                This entry is attached to a position row that has since been
                restated — the link above opens the CURRENT row for the same
                owner and asset
                {position.current_position_id
                  ? ""
                  : ", which no longer exists; nothing current remains for this holding"}
                .
              </p>
            )}
          </div>
        )}
      </Section>

      {/* ── The correction form ────────────────────────────────────────── */}
      <Section
        title="Entry"
        right={
          // The Correct/Discard toolbar EXISTS only for a caller the server
          // says may correct. Not disabled — absent. The correction CHAIN
          // ("Versions", below) is unconditional: reading what was already
          // corrected is a read, and a view-only member is entitled to it.
          // Seeing the history and adding to it are separate rights.
          canCorrect ? (
            <div className="flex items-center gap-2">
              {saved && (
                <span className="text-[10px] text-[#2D6A4F]">Corrected</span>
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
                disabled={!dirty || saving || !txn.is_current}
                className="rounded px-3 py-1 text-[11px] font-medium text-white disabled:opacity-40"
                style={{ backgroundColor: "var(--2a-navy)" }}
              >
                {saving ? "Saving…" : "Correct"}
              </button>
            </div>
          ) : null
        }
      >
        {!canCorrect && (
          <p className="mb-3 rounded bg-[var(--2a-bg-sidebar)] px-3 py-2 text-[11px] leading-snug text-[var(--2a-text-muted)]">
            Read-only. Correcting an entry requires{" "}
            {permissions?.write_permission}. The entry's figures are below and
            its correction history is under Versions.
          </p>
        )}
        {saveError && (
          <div className="mb-3 rounded bg-[#FEF3F2] px-3 py-2 text-[11px] leading-snug text-[#9B2335]">
            {saveError}
          </div>
        )}
        {dirty && (
          <p className="mb-3 text-[10px] leading-snug text-[var(--2a-text-muted)]">
            This is an append-only ledger. Saving closes this entry and records a
            successor pointing back at it (Rule 3). The current figures stay
            queryable and appear under Versions below.
          </p>
        )}

        {/* A caller who cannot correct gets the same facts as flat text and no
            form at all. Every input below is already `disabled` when its field
            is absent from `correctable` — which, without manage_portfolio, is
            all of them — but a grid of fifteen greyed inputs still advertises
            an operation this user will never be allowed to perform, and one
            future edit that forgets a `disabled=` turns it into a live control
            over a 403. Rendering nothing has no such failure mode. */}
        {!canCorrect ? (
          <div className="grid grid-cols-2 gap-3">
            <div className="col-span-2">
              <Field label="Type">
                {activeTypes.find((t) => t.code === txn.transaction_type_code)
                  ?.label || txn.transaction_type_code}
              </Field>
            </div>
            <Field label="Trade date">{fmtDate(txn.trade_date)}</Field>
            <Field label="Settle date">{fmtDate(txn.settle_date)}</Field>
            <Field label="Currency">{txn.currency_code || "—"}</Field>
            <Field label="Authority">{txn.authority}</Field>
            <Field label="Source system">{txn.source_system}</Field>
            <Field label="Custodian reference">{txn.external_ref || "—"}</Field>
          </div>
        ) : (
        <div className="grid grid-cols-2 gap-3">
          <div className="col-span-2">
            <label className={LABEL} htmlFor="td-type">
              Type
            </label>
            <select
              id="td-type"
              className={INPUT}
              disabled={!correctable.has("transaction_type_code")}
              value={current("transaction_type_code")}
              onChange={(e) => setField("transaction_type_code", e.target.value)}
            >
              {/* Retired types are filtered out of the choices but the entry's
                  OWN type is always offered, so a historical row does not
                  silently re-type itself on the first unrelated correction. */}
              {activeTypes.map((t) => (
                <option key={t.code} value={t.code}>
                  {t.label}
                  {t.is_active ? "" : " (retired)"}
                </option>
              ))}
            </select>
            <p className="mt-1 text-[10px] leading-snug text-[var(--2a-text-muted)]">
              A type is refused if its market does not fit the asset — a capital
              call against a listed equity, or a buy against a private fund
              interest, is almost always a mis-mapped feed.
            </p>
          </div>

          <div>
            <label className={LABEL} htmlFor="td-trade">
              Trade date
            </label>
            <input
              id="td-trade"
              type="date"
              className={INPUT}
              disabled={!correctable.has("trade_date")}
              value={current("trade_date") || ""}
              onChange={(e) => setField("trade_date", e.target.value)}
            />
          </div>

          <div>
            <label className={LABEL} htmlFor="td-settle">
              Settle date
            </label>
            <input
              id="td-settle"
              type="date"
              className={INPUT}
              disabled={!correctable.has("settle_date")}
              value={current("settle_date") || ""}
              onChange={(e) => setField("settle_date", e.target.value)}
            />
          </div>

          {MONEY_FIELDS.map(({ field, label }) => (
            <div key={field}>
              <label className={LABEL} htmlFor={`td-${field}`}>
                {label}
              </label>
              <input
                id={`td-${field}`}
                type="text"
                inputMode="decimal"
                className={INPUT}
                disabled={!correctable.has(field)}
                value={current(field) ?? ""}
                onChange={(e) => setField(field, e.target.value)}
                placeholder="—"
              />
            </div>
          ))}

          <div>
            <label className={LABEL} htmlFor="td-currency">
              Currency
            </label>
            <input
              id="td-currency"
              type="text"
              className={INPUT}
              disabled={!correctable.has("currency_code")}
              value={current("currency_code") ?? ""}
              onChange={(e) => setField("currency_code", e.target.value)}
              placeholder="USD"
            />
          </div>

          <div>
            <label className={LABEL} htmlFor="td-authority">
              Authority
            </label>
            <select
              id="td-authority"
              className={INPUT}
              disabled={!correctable.has("authority")}
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

          <div>
            <label className={LABEL} htmlFor="td-source">
              Source system
            </label>
            <select
              id="td-source"
              className={INPUT}
              disabled={!correctable.has("source_system")}
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

          <div>
            <label className={LABEL} htmlFor="td-ref">
              Custodian reference
            </label>
            <input
              id="td-ref"
              type="text"
              className={INPUT}
              disabled={!correctable.has("external_ref")}
              value={current("external_ref") ?? ""}
              onChange={(e) => setField("external_ref", e.target.value)}
              placeholder="—"
            />
          </div>
        </div>
        )}

        {/* What a correction may NOT change, shown rather than hidden. A user
            who cannot see these has no way to understand why the fields are
            missing. */}
        <div className="mt-4 grid grid-cols-2 gap-3 border-t border-[var(--2a-border)] pt-3">
          <Field label="Kind">
            {txn.is_corporate_action_adjustment
              ? "corporate-action adjustment"
              : "trade"}
          </Field>
          <Field label="Recorded">{fmtDate(txn.recorded_at)}</Field>
          <div className="col-span-2">
            <span className={LABEL}>Not correctable</span>
            <p className="mt-0.5 text-[10px] leading-snug text-[var(--2a-text-muted)]">
              The position this entry belongs to, and its corporate-action
              markers. Re-pointing an entry at a different holding is a
              different entry; and the two corporate-action fields are the key
              that stops an action being applied to a holding twice.
            </p>
          </div>
        </div>
      </Section>

      {/* ── Figures, read-only summary ─────────────────────────────────── */}
      <Section title="Figures">
        <div className="grid grid-cols-3 gap-3">
          <Field label="Quantity">{fmtNumber(txn.quantity)}</Field>
          <Field label="Price">{fmtMoney(txn.price, txn.currency_code)}</Field>
          <Field label="Gross">
            {fmtMoney(txn.gross_amount, txn.currency_code)}
          </Field>
          <Field label="Fees">{fmtMoney(txn.fees, txn.currency_code)}</Field>
          <Field label="Taxes">{fmtMoney(txn.taxes, txn.currency_code)}</Field>
          <Field label="Net">{fmtMoney(txn.net_amount, txn.currency_code)}</Field>
        </div>
      </Section>

      {/* ── The correction chain ───────────────────────────────────────── */}
      {data.correction_history.length > 1 && (
        <Section title="Versions" count={data.correction_history.length}>
          <ul className="space-y-1">
            {data.correction_history.map((v, i) => (
              <li
                key={v.id}
                className="flex items-center justify-between gap-2 text-[11px]"
                style={v.is_current ? undefined : { opacity: 0.6 }}
                title={
                  v.is_current
                    ? "The current entry."
                    : "Superseded by a correction. Kept, not edited — both rows stay queryable."
                }
              >
                <span className="text-[var(--2a-text-secondary)]">
                  v{i + 1} · {fmtDate(v.trade_date)}
                  {v.is_current ? " · current" : ""}
                </span>
                <span className="tabular-nums text-[var(--2a-text)]">
                  {fmtMoney(v.net_amount, v.currency_code)}
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
          recordId={txn.id}
          title="Source documents"
        />
      </div>
    </div>
  );
}

export { CARD, fmtDate, fmtMoney, fmtNumber };
