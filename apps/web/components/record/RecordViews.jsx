// NAV3 — the record page's pure views (no fetch, no router): header, tab list, status badge, the Holdings split and
// the custom-field (UDF) sections. Fed real envelopes by RecordPage and by the node render tests.
import { nextTabIndex, statusLabel, splitDifferenceCents } from "../../lib/recordTabs.mjs";
import { typeLabel } from "../../lib/entityTypes.js";

const money = (s) => {
  if (s === null || s === undefined || s === "") return "—";
  const n = Number(s);
  return Number.isFinite(n) ? n.toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 }) : String(s);
};

// ── Status badge ───────────────────────────────────────────────────────────────────────────────────────────────
export function StatusBadge({ status, vocab }) {
  const label = statusLabel(vocab, status);
  if (!label) return null;
  return (
    <span data-status={status}
      className="inline-flex items-center rounded-full border border-navy px-2.5 py-0.5 text-xs font-medium text-navy">
      {label}
    </span>
  );
}

// ── Header ─────────────────────────────────────────────────────────────────────────────────────────────────────
export function contextLine({ household, owners }) {
  const parts = [];
  if (household && household.name) parts.push(`Household: ${household.name}`);
  const names = (owners || []).map((o) => o.parent_name).filter(Boolean);
  if (names.length) parts.push(`Owned by ${names.slice(0, 3).join(", ")}${names.length > 3 ? ` +${names.length - 3}` : ""}`);
  return parts.join(" · ");
}

export function RecordHeader({ entity, vocab, household, owners, asOf, today, onAsOf, todayTabs = [] }) {
  const ctx = contextLine({ household, owners });
  return (
    <header className="rounded-[6px] bg-navy px-6 py-5 text-white">
      <nav className="text-sm text-white/70" aria-label="Breadcrumb">
        <a href="/crm" className="hover:text-white">Entities</a>
        <span className="mx-2">›</span>
        <span className="text-white">{entity.display_name}</span>
      </nav>
      <div className="mt-2 flex flex-wrap items-center gap-3">
        <h1 className="text-3xl font-semibold" style={{ fontFamily: "Spectral, Georgia, serif" }}>{entity.display_name}</h1>
        <span className="inline-flex items-center rounded-full bg-gold-light px-2.5 py-0.5 text-xs font-medium text-navy">
          {typeLabel(entity.entity_type)}
        </span>
        <span className="rounded-full bg-white">
          <StatusBadge status={entity.status} vocab={vocab} />
        </span>
      </div>
      {ctx ? <p className="mt-1 text-sm text-white/80" data-context-line="">{ctx}</p> : null}
      <div className="mt-3 flex flex-wrap items-center gap-3 text-sm">
        <label htmlFor="record-as-of" className="text-white/80">As of</label>
        <input id="record-as-of" type="date" value={asOf} max={today}
          onChange={(e) => onAsOf && onAsOf(e.target.value)}
          className="rounded-md border border-white/30 bg-white px-2 py-1 text-sm text-[#0F172A]" />
        {asOf !== today ? (
          <button type="button" onClick={() => onAsOf && onAsOf(today)} className="text-gold-light hover:underline">
            Back to today
          </button>
        ) : null}
        {asOf !== today && todayTabs.length ? (
          <span data-shows-today="" className="text-white/80">Shows today: {todayTabs.join(", ")}</span>
        ) : null}
      </div>
    </header>
  );
}

// ── Accessible tab list (role=tablist / tab; roving tabindex; arrows / Home / End) ────────────────────────────────
export function RecordTabList({ tabs, active, onSelect, idPrefix = "record" }) {
  const idx = Math.max(0, tabs.findIndex((t) => t.key === active));
  function onKeyDown(e) {
    const n = nextTabIndex(idx, e.key, tabs.length);
    if (n !== idx && n >= 0) {
      e.preventDefault();
      onSelect(tabs[n].key, true);
    }
  }
  return (
    <div role="tablist" aria-label="Record sections" className="flex flex-wrap gap-1 border-b border-border"
      onKeyDown={onKeyDown}>
      {tabs.map((t) => {
        const sel = t.key === active;
        return (
          <button key={t.key} type="button" role="tab" id={`${idPrefix}-tab-${t.key}`}
            aria-selected={sel ? "true" : "false"} aria-controls={`${idPrefix}-panel-${t.key}`}
            tabIndex={sel ? 0 : -1} onClick={() => onSelect(t.key)}
            className={`-mb-px border-b-2 px-4 py-2 text-sm font-medium transition-colors ${
              sel ? "border-navy text-navy" : "border-transparent text-text-muted hover:text-text-secondary"}`}>
            {t.label}
          </button>
        );
      })}
    </div>
  );
}

export function ShowsToday() {
  return <p data-shows-today-tab="" className="mb-4 text-xs text-text-muted">Shows today — this tab has no as-of date.</p>;
}

// ── Holdings: held directly / through ownership ──────────────────────────────────────────────────────────────
export function HoldingsSplitView({ envelope, part, onPart }) {
  const split = envelope && envelope.rows;
  if (!split) return null;
  const parts = (envelope.vocabularies && envelope.vocabularies.split_parts) || [];
  const cur = part === "through" ? "through" : "direct";
  const fig = split[cur];
  const diff = splitDifferenceCents(split);
  return (
    <section className="rounded-[6px] border border-[#ece8dd] bg-white p-6" data-holdings-split={cur}>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div role="radiogroup" aria-label="Holdings basis" className="inline-flex rounded-md border border-border">
          {parts.map((p) => (
            <button key={p.key} type="button" role="radio" aria-checked={cur === p.key ? "true" : "false"}
              onClick={() => onPart && onPart(p.key)}
              className={`px-3 py-1.5 text-sm ${cur === p.key ? "bg-navy text-white" : "text-text-secondary"}`}>
              {p.label} · {money(split[p.key] && split[p.key].gross)}
            </button>
          ))}
        </div>
        <p className="text-xs text-text-muted">
          Total {money(split.total.gross)} as of {split.as_of}
          {diff === 0 ? " · direct + through ownership = total" : diff !== null ? ` · rounding ${diff}¢` : ""}
        </p>
      </div>
      <dl className="mt-4 grid grid-cols-3 gap-4 text-sm">
        <div><dt className="text-text-muted">Gross</dt><dd data-gross="">{money(fig.gross)}</dd></div>
        <div><dt className="text-text-muted">Liabilities</dt><dd>{money(fig.liabilities)}</dd></div>
        <div><dt className="text-text-muted">Net</dt><dd>{money(fig.net)}</dd></div>
      </dl>
      {cur === "through" ? (
        (split.owned || []).length ? (
          <table className="mt-4 w-full text-sm">
            <thead><tr className="text-left text-text-muted"><th className="py-1">Owned entity</th><th>Share</th><th className="text-right">Value at share</th></tr></thead>
            <tbody>
              {split.owned.map((o) => (
                <tr key={o.entity_id} className="border-t border-border" data-owned={o.entity_id}>
                  <td className="py-1"><a className="text-navy hover:underline" href={`/crm/${o.entity_id}`}>{o.display_name || o.entity_id}</a></td>
                  <td>{o.share_pct}%</td>
                  <td className="text-right">{money(o.gross)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : <p className="mt-4 text-sm text-text-muted">Owns no other entity.</p>
      ) : null}
      {(fig.top_holdings || []).length ? (
        <table className="mt-4 w-full text-sm">
          <thead><tr className="text-left text-text-muted"><th className="py-1">Holding</th><th className="text-right">Value</th></tr></thead>
          <tbody>
            {fig.top_holdings.map((h) => (
              <tr key={h.key} className="border-t border-border"><td className="py-1">{h.label}</td><td className="text-right">{money(h.value)}</td></tr>
            ))}
          </tbody>
        </table>
      ) : <p className="mt-4 text-sm text-text-muted">No valued positions.</p>}
    </section>
  );
}

// ── Custom fields (UDF) ────────────────────────────────────────────────────────────────────────────────────────
export function udfDisplay(v) {
  if (!v) return "";
  for (const k of ["value_text", "value_numeric", "value_date"]) {
    if (v[k] !== null && v[k] !== undefined && v[k] !== "") return String(v[k]);
  }
  if (v.value_json !== null && v.value_json !== undefined) {
    const j = typeof v.value_json === "string" ? v.value_json : JSON.stringify(v.value_json);
    return j;
  }
  return "";
}

/** One item's mode: 'edit' only when the server's layout says not read-only AND the envelope grants the write. */
export function udfItemMode(item, canWrite) {
  return !item.is_read_only && canWrite === true ? "edit" : "read";
}

export function UdfTabView({ tab, layout, values, drafts = {}, onDraft, onSave, saving, message }) {
  const byDef = new Map((values && values.rows ? values.rows : []).map((r) => [r.definition_id, r]));
  const canWrite = Boolean(layout && layout.permissions && layout.permissions.can_write === true);
  const sections = (layout && layout.sections) || [];
  return (
    <section className="rounded-[6px] border border-[#ece8dd] bg-white p-6" data-udf-tab={tab.id}>
      <h2 className="text-sm font-semibold text-text-secondary">{tab.label}</h2>
      {!sections.length ? <p className="mt-3 text-sm text-text-muted">No fields on this tab yet.</p> : null}
      {sections.map((s) => (
        <div key={s.id} className="mt-4">
          {s.title ? <h3 className="text-xs font-semibold uppercase tracking-wide text-text-muted">{s.title}</h3> : null}
          <dl className="mt-2 grid gap-3 md:grid-cols-2">
            {(s.items || []).filter((i) => i.definition_id).map((i) => {
              const mode = udfItemMode(i, canWrite);
              const cur = udfDisplay(byDef.get(i.definition_id));
              const id = `udf-${i.definition_id}`;
              return (
                <div key={i.id} data-udf-field={i.definition_id} data-mode={mode}>
                  <dt><label htmlFor={id} className="text-xs text-text-muted">{i.label}</label></dt>
                  <dd>
                    {mode === "edit" ? (
                      <span className="flex gap-2">
                        <input id={id} value={drafts[i.definition_id] ?? cur}
                          onChange={(e) => onDraft && onDraft(i.definition_id, e.target.value)}
                          className="w-full rounded-md border border-border px-2 py-1 text-sm" />
                        <button type="button" disabled={saving} onClick={() => onSave && onSave(i.definition_id)}
                          className="rounded-md bg-navy px-3 py-1 text-xs text-white">Save</button>
                      </span>
                    ) : <span id={id} className="text-sm text-[#334155]">{cur || "—"}</span>}
                  </dd>
                </div>
              );
            })}
          </dl>
        </div>
      ))}
      {message ? <p className="mt-3 text-sm text-text-muted" role="status">{message}</p> : null}
    </section>
  );
}
