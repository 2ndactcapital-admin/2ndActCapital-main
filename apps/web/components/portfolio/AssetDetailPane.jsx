"use client";

/**
 * AssetDetailPane — the right-hand pane of the Securities & Assets screen.
 *
 * Opens on a grid row click. Never navigates: the grid stays mounted and
 * selected on the left, which is the whole point of the layout — an operator
 * checking twenty instruments should not pay a page load per instrument.
 *
 * Everything here comes from ONE call, GET /api/portfolio/securities/{id}: the
 * asset, the linked global security with its identifiers / price history /
 * underlyings, the org's own identifiers, the resolved current value and the
 * GOVERNING valuation that produced it, the valuation history, the positions
 * held against the asset, and the asset's version history. Linked source
 * documents come from the existing Chancery DocumentsPanel, embedded unchanged
 * with the record_type the API supplies — the component does not hardcode that
 * string, because `document_record_links.record_type` has no CHECK constraint
 * and a typo would write a link nothing ever reads back.
 *
 * THE TWO SCOPES, AND WHY THEY LOOK DIFFERENT ON PURPOSE
 * ─────────────────────────────────────────────────────────────────────────
 * The upper block is the org's OWN asset row. Its fields are rendered as
 * editable inputs if and only if the server published them in
 * `vocabularies.editable`, which is empty for a caller without
 * `manage_portfolio`.
 *
 * The lower block — "Platform security" — is `portfolio.securities_global`. It
 * has NO form, no input, no save button and no `editable` branch anywhere in
 * its subtree. Not a disabled input: absent. It is read-only for every caller
 * of this screen, super admin included, because the global write path is a
 * different endpoint (PATCH /portfolio/global-securities/{id}) with a different
 * gate, and a field writable from two places under two rules is exactly the
 * bug this split exists to prevent.
 *
 * The distinction is also *stated*, not just implied by the absence of a box.
 * `name`, `short_name` and `currency_code` exist on BOTH tables, so the pane
 * labels the platform ones explicitly and shows what governs them.
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import DocumentsPanel from "@/components/DocumentsPanel";

const CARD = { borderColor: "#ece8dd", boxShadow: "0 1px 3px rgba(0,0,0,0.06)" };

const LABEL =
  "block text-[10px] font-semibold uppercase tracking-[0.12em] text-[var(--2a-text-muted)]";
const INPUT =
  "mt-1 w-full rounded border border-[var(--2a-border)] bg-white px-2 py-1.5 text-xs text-[var(--2a-text)] focus:outline-none focus:ring-1 focus:ring-[var(--2a-gold)]";

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
function fmtMoney(value, currency = "USD", digits = 2) {
  if (value == null || value === "") return "—";
  const n = Number(value);
  if (!Number.isFinite(n)) return value;
  return n.toLocaleString("en-US", {
    style: "currency",
    currency: currency || "USD",
    maximumFractionDigits: digits,
  });
}

function fmtNumber(value) {
  if (value == null || value === "") return "—";
  const n = Number(value);
  if (!Number.isFinite(n)) return value;
  return n.toLocaleString("en-US", { maximumFractionDigits: 6 });
}

function titleise(value) {
  return (value || "").replace(/_/g, " ");
}

function Section({ title, count, children, right, tone }) {
  return (
    <section className="border-t border-[var(--2a-border)] px-5 py-4">
      <div className="mb-2 flex items-center justify-between gap-3">
        <h3
          className="text-[11px] font-semibold uppercase tracking-[0.18em]"
          style={{ color: tone || "var(--2a-gold)" }}
        >
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

/**
 * A platform-sourced value. Deliberately its own component with NO editable
 * branch — there is nothing to pass to make one appear, which is a stronger
 * guarantee than a prop that defaults to false.
 */
function PlatformField({ label, children, hint }) {
  return (
    <div>
      <span className={LABEL}>
        <span aria-hidden="true" className="mr-1">
          🔒
        </span>
        {label}
      </span>
      <p
        className="mt-0.5 text-xs text-[var(--2a-text-secondary)]"
        title={hint || "Platform-sourced. Not editable from this screen."}
      >
        {children ?? <span className="text-[var(--2a-text-muted)]">—</span>}
      </p>
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

export default function AssetDetailPane({ assetId, taxonomy, onSaved, onClose }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const [draft, setDraft] = useState({});
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState(null);
  const [saved, setSaved] = useState(false);

  const load = useCallback(async () => {
    if (!assetId) {
      setData(null);
      return;
    }
    setLoading(true);
    setError(null);
    setSaveError(null);
    setSaved(false);
    try {
      const res = await fetch(`/api/portfolio/securities/${assetId}`, {
        cache: "no-store",
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(body.error || body.detail || "Could not load this asset.");
      }
      setData(body);
      setDraft({});
    } catch (err) {
      setError(err.message);
      setData(null);
    } finally {
      setLoading(false);
    }
  }, [assetId]);

  useEffect(() => {
    load();
  }, [load]);

  const asset = data?.asset;
  const governing = data?.governing_valuation;
  const global_ = data?.global_security;
  const vocabularies = data?.vocabularies;
  const permissions = data?.permissions;

  // Server-published, permission-aware, EMPTY for a view-only caller. There is
  // no local default: a `|| FALLBACK` here would silently restore the whole
  // edit form the first time the envelope went missing for an unrelated reason.
  const editable = useMemo(
    () => new Set(vocabularies?.editable || []),
    [vocabularies],
  );
  // Published so this list is the server's, not a copy. Every key in it is
  // read-only on this screen — asserted server-side too: the API refuses any of
  // them on the org-scoped PATCH with a 403, whatever the UI renders.
  const globalFields = useMemo(
    () => new Set(vocabularies?.global_fields || []),
    [vocabularies],
  );
  const canWrite = !!permissions?.can_write;

  const taxonomyOptions = useMemo(
    () => Object.entries(taxonomy || {}).sort((a, b) => a[1].localeCompare(b[1])),
    [taxonomy],
  );

  function setField(name, value) {
    setDraft((prev) => ({ ...prev, [name]: value }));
    setSaved(false);
  }

  function current(name) {
    return draft[name] !== undefined ? draft[name] : (asset?.[name] ?? "");
  }

  const dirty = Object.keys(draft).length > 0;

  async function save() {
    if (!dirty || !asset) return;
    setSaving(true);
    setSaveError(null);
    setSaved(false);
    try {
      // Empty string means "clear this field"; null is what the API reads as a
      // clear. "This asset has no maturity" is a different statement from
      // "leave the maturity alone", and only the second is an absent key.
      const payload = {};
      for (const [key, value] of Object.entries(draft)) {
        if (typeof value === "string" && value.trim() === "") payload[key] = null;
        else payload[key] = value;
      }
      const res = await fetch(`/api/portfolio/securities/${asset.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(body.error || body.detail || "The edit was refused.");
      }
      // The id did NOT change — the outgoing version was archived on the system
      // axis and the live row kept its id, so every position and valuation
      // still points at it. Nothing to re-key.
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

  if (!assetId) {
    return (
      <div className="flex h-full items-center justify-center px-8 text-center">
        <div>
          <p className="text-sm font-medium text-[var(--2a-text-secondary)]">
            Select an asset
          </p>
          <p className="mt-1 text-xs text-[var(--2a-text-muted)]">
            Its valuation, its platform security and its source documents appear
            here.
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

  if (!asset) return null;

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
              {asset.name}
            </h2>
            <p className="mt-0.5 truncate text-xs text-[var(--2a-text-muted)]">
              {titleise(asset.asset_type)}
              {` · ${titleise(asset.asset_class)}`}
              {asset.org_name ? ` · ${asset.org_name}` : ""}
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

        {!asset.is_current && (
          <p className="mt-2 rounded bg-[var(--2a-bg-sidebar)] px-2 py-1 text-[10px] text-[var(--2a-text-muted)]">
            This is an archived version of the asset — it is history, not the
            current row.
          </p>
        )}
        {!canWrite && (
          <p className="mt-2 rounded bg-[var(--2a-bg-sidebar)] px-2 py-1 text-[10px] text-[var(--2a-text-muted)]">
            Read-only. Editing an asset requires {permissions?.write_permission}.
          </p>
        )}
      </div>

      {/* ── Resolved current value + its governing valuation ───────────── */}
      <div className="px-5 py-4">
        <span className={LABEL}>Current value</span>
        {asset.current_value != null ? (
          <p className="mt-1 text-2xl font-semibold tabular-nums text-[var(--2a-navy)]">
            {fmtMoney(asset.current_value, asset.currency_code)}
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
              No value could be resolved: {asset.current_value_reason}
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
                {titleise(governing.value_basis)}
              </span>
              <span className="text-right">
                {governing.is_superseded ? "superseded" : "current"}
              </span>
            </div>
          ) : (
            <p className="mt-1 text-[11px] leading-snug text-[var(--2a-text-muted)]">
              {governing?.reason || "No valuation resolved for this asset."}
            </p>
          )}
        </div>
      </div>

      {/* ── ORG-OWNED FIELDS. Editable subject to manage_portfolio. ────── */}
      <Section
        title="This org’s asset"
        right={
          canWrite ? (
            <div className="flex items-center gap-2">
              {saved && <span className="text-[10px] text-[#2D6A4F]">Saved</span>}
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
                disabled={!dirty || saving || !asset.is_current}
                className="rounded px-3 py-1 text-[11px] font-medium text-white disabled:opacity-40"
                style={{ backgroundColor: "var(--2a-navy)" }}
              >
                {saving ? "Saving…" : "Save"}
              </button>
            </div>
          ) : null
        }
      >
        {saveError && (
          <div className="mb-3 rounded bg-[#FEF3F2] px-3 py-2 text-[11px] leading-snug text-[#9B2335]">
            {saveError}
          </div>
        )}
        {dirty && (
          <p className="mb-3 text-[10px] leading-snug text-[var(--2a-text-muted)]">
            Saving preserves the current version as history and updates this row
            in place — the asset keeps its id, so every position and valuation
            stays attached to it.
          </p>
        )}

        <div className="grid grid-cols-2 gap-3">
          {editable.has("name") ? (
            <div className="col-span-2">
              <label className={LABEL} htmlFor="a-name">
                Name
              </label>
              <input
                id="a-name"
                className={INPUT}
                value={current("name") || ""}
                onChange={(e) => setField("name", e.target.value)}
              />
            </div>
          ) : (
            <div className="col-span-2">
              <Field label="Name">{asset.name}</Field>
            </div>
          )}

          {editable.has("short_name") ? (
            <div>
              <label className={LABEL} htmlFor="a-short">
                Short name
              </label>
              <input
                id="a-short"
                className={INPUT}
                value={current("short_name") || ""}
                onChange={(e) => setField("short_name", e.target.value)}
              />
            </div>
          ) : (
            <Field label="Short name">{asset.short_name || "—"}</Field>
          )}

          {editable.has("asset_type") ? (
            <div>
              <label className={LABEL} htmlFor="a-type">
                Asset type
              </label>
              {/* Open text on purpose: assets.asset_type is NOT NULL with NO
                  CHECK constraint. Offering a made-up dropdown would reject
                  values the database accepts and hide the real vocabulary. */}
              <input
                id="a-type"
                className={INPUT}
                value={current("asset_type") || ""}
                onChange={(e) => setField("asset_type", e.target.value)}
              />
            </div>
          ) : (
            <Field label="Asset type">{titleise(asset.asset_type)}</Field>
          )}

          {editable.has("asset_class") ? (
            <div>
              <label className={LABEL} htmlFor="a-class">
                Asset class
              </label>
              <select
                id="a-class"
                className={INPUT}
                value={current("asset_class") || ""}
                onChange={(e) => setField("asset_class", e.target.value)}
              >
                {(vocabularies?.asset_class || []).map((c) => (
                  <option key={c} value={c}>
                    {titleise(c)}
                  </option>
                ))}
              </select>
            </div>
          ) : (
            <Field label="Asset class">{titleise(asset.asset_class)}</Field>
          )}

          {editable.has("valuation_method") ? (
            <div>
              <label className={LABEL} htmlFor="a-valuation">
                Valuation method
              </label>
              <select
                id="a-valuation"
                className={INPUT}
                value={current("valuation_method") || ""}
                onChange={(e) => setField("valuation_method", e.target.value)}
              >
                {(vocabularies?.valuation_method || []).map((m) => (
                  <option key={m} value={m}>
                    {titleise(m)}
                  </option>
                ))}
              </select>
              <p className="mt-1 text-[10px] leading-snug text-[var(--2a-text-muted)]">
                Also decides which market this asset trades in, and therefore
                which transaction types it will accept.
              </p>
            </div>
          ) : (
            <Field label="Valuation method">
              {titleise(asset.valuation_method)}
            </Field>
          )}

          {editable.has("ownership_basis") ? (
            <div>
              <label className={LABEL} htmlFor="a-basis">
                Default ownership basis
              </label>
              <select
                id="a-basis"
                className={INPUT}
                value={current("ownership_basis") || ""}
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
            <Field label="Default ownership basis">{asset.ownership_basis}</Field>
          )}

          {editable.has("default_taxonomy_key") ? (
            <div>
              <label className={LABEL} htmlFor="a-taxonomy">
                Default taxonomy
              </label>
              <select
                id="a-taxonomy"
                className={INPUT}
                value={current("default_taxonomy_key") || ""}
                onChange={(e) =>
                  setField("default_taxonomy_key", e.target.value || "")
                }
              >
                <option value="">— unassigned —</option>
                {asset.default_taxonomy_key && !taxonomy?.[asset.default_taxonomy_key] && (
                  <option value={asset.default_taxonomy_key}>
                    {asset.default_taxonomy_key} (unknown key)
                  </option>
                )}
                {taxonomyOptions.map(([key, label]) => (
                  <option key={key} value={key}>
                    {label}
                  </option>
                ))}
              </select>
            </div>
          ) : (
            <Field label="Default taxonomy">
              {asset.taxonomy_label || asset.default_taxonomy_key || "—"}
            </Field>
          )}

          {editable.has("currency_code") ? (
            <div>
              <label className={LABEL} htmlFor="a-currency">
                Currency
              </label>
              <input
                id="a-currency"
                className={INPUT}
                value={current("currency_code") || ""}
                onChange={(e) => setField("currency_code", e.target.value)}
              />
            </div>
          ) : (
            <Field label="Currency">{asset.currency_code || "—"}</Field>
          )}

          {editable.has("inception_date") ? (
            <div>
              <label className={LABEL} htmlFor="a-inception">
                Inception
              </label>
              <input
                id="a-inception"
                type="date"
                className={INPUT}
                value={current("inception_date") || ""}
                onChange={(e) => setField("inception_date", e.target.value)}
              />
            </div>
          ) : (
            <Field label="Inception">{fmtDate(asset.inception_date)}</Field>
          )}

          {editable.has("maturity_date") ? (
            <div>
              <label className={LABEL} htmlFor="a-maturity">
                Maturity
              </label>
              <input
                id="a-maturity"
                type="date"
                className={INPUT}
                value={current("maturity_date") || ""}
                onChange={(e) => setField("maturity_date", e.target.value)}
              />
            </div>
          ) : (
            <Field label="Maturity">{fmtDate(asset.maturity_date)}</Field>
          )}

          <div className="col-span-2 flex flex-wrap gap-5">
            {editable.has("include_in_performance") ? (
              <label className="flex items-center gap-2 text-xs text-[var(--2a-text-secondary)]">
                <input
                  type="checkbox"
                  checked={
                    draft.include_in_performance !== undefined
                      ? !!draft.include_in_performance
                      : !!asset.include_in_performance
                  }
                  onChange={(e) =>
                    setField("include_in_performance", e.target.checked)
                  }
                  className="accent-[var(--2a-navy)]"
                />
                Include in performance
              </label>
            ) : (
              <Field label="Include in performance">
                {asset.include_in_performance ? "Yes" : "No"}
              </Field>
            )}

            {editable.has("is_active") ? (
              <label className="flex items-center gap-2 text-xs text-[var(--2a-text-secondary)]">
                <input
                  type="checkbox"
                  checked={
                    draft.is_active !== undefined
                      ? !!draft.is_active
                      : !!asset.is_active
                  }
                  onChange={(e) => setField("is_active", e.target.checked)}
                  className="accent-[var(--2a-navy)]"
                />
                Active
                {asset.position_count > 0 && (
                  <span className="text-[10px] text-[var(--2a-gold)]">
                    · {asset.position_count} live position
                    {asset.position_count === 1 ? "" : "s"}
                  </span>
                )}
              </label>
            ) : (
              <Field label="Active">{asset.is_active ? "Yes" : "No"}</Field>
            )}
          </div>
        </div>
      </Section>

      {/* ── PLATFORM SECURITY. Read-only for EVERY caller. ─────────────── */}
      <Section
        title="Platform security"
        tone="var(--2a-navy)"
        right={
          <span className="text-[10px] text-[var(--2a-text-muted)]">
            shared across all tenants · read-only here
          </span>
        }
      >
        {global_ ? (
          <>
            <p className="mb-3 rounded border border-[var(--2a-border)] bg-[var(--2a-bg)] px-3 py-2 text-[10px] leading-snug text-[var(--2a-text-muted)]">
              These values come from{" "}
              <span className="font-mono">portfolio.securities_global</span>,
              which has no org and is shared by every tenant. They are not
              editable from this screen by anyone — including a Super Admin,
              whose write path is a separate endpoint. Note that{" "}
              <span className="font-mono">name</span> and{" "}
              <span className="font-mono">currency</span> exist on both records:
              the ones above are this org’s, the ones below are the platform’s.
              {global_.was_merged && (
                <>
                  {" "}
                  This asset was linked to a security that has since been merged
                  away; the surviving record is shown.
                </>
              )}
            </p>

            <div className="grid grid-cols-2 gap-3">
              <div className="col-span-2">
                <PlatformField label="Security name">{global_.name}</PlatformField>
              </div>
              <PlatformField label="Security type">
                {titleise(global_.security_type)}
              </PlatformField>
              <PlatformField label="Currency">
                {global_.currency_code}
              </PlatformField>
              <PlatformField
                label="Price coverage"
                hint="Whether a usable daily price series exists for this instrument. A human finding, not a pipeline derivation."
              >
                {titleise(global_.price_coverage)}
              </PlatformField>
              <PlatformField label="Identifiers">
                {global_.identifiers?.length
                  ? global_.identifiers
                      .map((i) => `${i.id_type.toUpperCase()} ${i.id_value}`)
                      .join(" · ")
                  : null}
              </PlatformField>
            </div>

            <div className="mt-3">
              <span className={LABEL}>
                <span aria-hidden="true" className="mr-1">
                  🔒
                </span>
                Latest platform price
              </span>
              {asset.latest_price != null ? (
                <p className="mt-0.5 text-sm font-medium tabular-nums text-[var(--2a-text)]">
                  {fmtMoney(asset.latest_price, asset.latest_price_currency, 4)}
                  <span className="ml-2 text-[11px] font-normal text-[var(--2a-text-muted)]">
                    {fmtDate(asset.latest_price_date)} · {asset.latest_price_type}
                    {asset.latest_price_source ? ` · ${asset.latest_price_source}` : ""}
                  </span>
                </p>
              ) : (
                // Never $0. Three genuinely different absences, and the API
                // says which one applies.
                <p className="mt-0.5 text-[11px] leading-snug text-[var(--2a-text-muted)]">
                  {asset.latest_price_reason || "No platform price."}
                </p>
              )}
            </div>

            {global_.relationships?.length > 0 && (
              <div className="mt-3">
                <span className={LABEL}>Underlyings</span>
                <ul className="mt-1 space-y-1">
                  {global_.relationships.map((r) => (
                    <li
                      key={r.id}
                      className="flex items-baseline justify-between gap-2 text-[11px] text-[var(--2a-text-secondary)]"
                    >
                      <span className="truncate">
                        {r.target_name || r.raw_underlying_text}
                      </span>
                      <span className="shrink-0 text-[10px] uppercase tracking-wide text-[var(--2a-text-muted)]">
                        {r.link_state}
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </>
        ) : (
          <Empty>
            Not linked to a platform security. That is a legitimate permanent
            state — a property, a private interest or a collectible has no global
            counterpart and must not be forced to invent one.
          </Empty>
        )}
      </Section>

      {/* ── The org's OWN identifiers — a different table entirely ─────── */}
      <Section title="This org’s identifiers" count={data.own_identifiers?.length}>
        {data.own_identifiers?.length ? (
          <ul className="space-y-1">
            {data.own_identifiers.map((i) => (
              <li
                key={i.id}
                className="flex items-baseline justify-between gap-2 text-[11px]"
              >
                <span className="uppercase tracking-wide text-[var(--2a-text-muted)]">
                  {i.id_type}
                </span>
                <span className="font-mono text-[var(--2a-text-secondary)]">
                  {i.id_value}
                </span>
              </li>
            ))}
          </ul>
        ) : (
          <Empty>
            No org-owned identifiers. These are separate from the platform ones
            above and can carry keys the global master does not accept — a parcel
            number or a VIN.
          </Empty>
        )}
      </Section>

      {/* ── Positions held against this asset ─────────────────────────── */}
      <Section title="Positions" count={data.positions?.length}>
        {data.positions?.length ? (
          <ul className="space-y-1.5">
            {data.positions.map((p) => (
              <li
                key={p.id}
                className="flex items-baseline justify-between gap-2 text-[11px]"
              >
                <span className="truncate text-[var(--2a-text-secondary)]">
                  {p.owner_name}
                </span>
                <span className="shrink-0 tabular-nums text-[var(--2a-text)]">
                  {p.ownership_basis === "percent"
                    ? `${fmtNumber(p.ownership_pct)}%`
                    : p.ownership_basis === "units"
                      ? fmtNumber(p.quantity)
                      : fmtMoney(p.market_value, asset.currency_code)}
                </span>
              </li>
            ))}
          </ul>
        ) : (
          <Empty>No current positions hold this asset.</Empty>
        )}
      </Section>

      {/* ── Valuation history ─────────────────────────────────────────── */}
      <Section title="Valuations" count={data.valuation_history?.length}>
        {data.valuation_history?.length ? (
          <ul className="space-y-1.5">
            {data.valuation_history.map((v) => (
              <li
                key={v.id}
                className="flex items-baseline justify-between gap-2 text-[11px]"
              >
                <span className="text-[var(--2a-text-secondary)]">
                  {fmtDate(v.valuation_date)}
                  {v.is_superseded && (
                    <span className="ml-2 text-[10px] text-[var(--2a-text-muted)]">
                      superseded
                    </span>
                  )}
                </span>
                <span className="flex shrink-0 items-baseline gap-2">
                  <StatusPill status={v.status} />
                  <span className="tabular-nums text-[var(--2a-text)]">
                    {fmtMoney(v.value, v.currency_code)}
                  </span>
                </span>
              </li>
            ))}
          </ul>
        ) : (
          <Empty>No valuations recorded for this asset.</Empty>
        )}
      </Section>

      {/* ── Version history — what the archive on the system axis buys ── */}
      <Section title="Versions" count={data.version_history?.length}>
        {data.version_history?.length ? (
          <ul className="space-y-1.5">
            {data.version_history.map((v) => (
              <li
                key={v.id}
                className="flex items-baseline justify-between gap-2 text-[11px]"
              >
                <span className="truncate text-[var(--2a-text-secondary)]">
                  {v.name}
                  <span className="ml-2 text-[10px] text-[var(--2a-text-muted)]">
                    {titleise(v.asset_type)}
                  </span>
                </span>
                <span className="shrink-0 text-[10px] text-[var(--2a-text-muted)]">
                  {v.is_current ? "current" : `archived ${fmtDate(v.system_to)}`}
                </span>
              </li>
            ))}
          </ul>
        ) : (
          <Empty>No version history.</Empty>
        )}
      </Section>

      {/* ── Source documents — the existing generic link mechanism ─────── */}
      <div className="border-t border-[var(--2a-border)]">
        <DocumentsPanel
          recordType={data.document_record_type}
          recordId={asset.id}
          title="Source documents"
        />
      </div>
    </div>
  );
}

export { CARD, fmtDate, fmtMoney, fmtNumber };
