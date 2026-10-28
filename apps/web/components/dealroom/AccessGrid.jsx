"use client";

/**
 * AccessGrid — Marketplace → Deal access (DEALROOM1).
 *
 * Server-side DataGrid over GET /deal-room/access-grid. The SERVER scopes the
 * rows (publisher users and delegates: their deals; manage_deals / org admin:
 * the firm; Super Admin: all), groups them (deal / publisher / firm / all),
 * filters, sorts and pages. Approve / decline / revoke — single or bulk —
 * render only from `gridActions(envelope)`, i.e. only when the envelope says
 * `can_write === true`; every label comes from the envelope's vocabularies.
 *
 * DEALROOM2: each agreement cell shows the version signed (or "covered by
 * master NDA signed <date>", or the stale version) from the row's
 * `agreement_detail`; the status filter includes resign_required (from the
 * vocabulary); "Require re-sign" is a bulk action like the others.
 *
 * DEALROOM3: an "Access" column (status line with the end date or the paused
 * reason), the investor's advisors under them, the return-or-destroy notice
 * and its acknowledgement; bulk "Extend" (a new end date, audited) and an
 * optional "send return-or-destroy notice" with Revoke — every control from
 * the envelope's actions, none without can_write.
 */

import { useCallback, useState } from "react";
import DataGrid from "@/components/ui/DataGrid";
import { accessLine, advisorLines, gridActions, gridCell, gridColumns } from "@/lib/dealRoom.mjs";

const SORT_KEYS = { investor_name: "investor", deal_name: "deal", publisher_name: "publisher",
  status_label: "status", decided: "decided_at" };

export function gridColumnDefs(envelope, { selected, toggle }) {
  const actions = gridActions(envelope);
  const cols = gridColumns(envelope).map((c) => ({
    field: c.field, headerName: c.headerName, enableSorting: c.field in SORT_KEYS,
    cell: (_v, row) => gridCell(row, c),
  }));
  const group = { field: "group", headerName: envelope?.group_by === "all" ? "" : "Group", enableSorting: false,
    cell: (_v, row) => row.group };
  cols.push({
    field: "access", headerName: "Access · advisors", enableSorting: false,
    cell: (_v, row) => (
      <div className="text-xs" data-access-row={row.id}>
        <div>{accessLine(row)}</div>
        {advisorLines(row.advisors).map((l) => <div key={l} className="text-text-muted">Advisor: {l}</div>)}
        {row.return_notice && (
          <div className="text-text-muted">
            Return-or-destroy notice sent {row.return_notice.sent_at.slice(0, 10)} —{" "}
            {row.return_notice.acknowledged_at ? `acknowledged ${row.return_notice.acknowledged_at.slice(0, 10)}` : "not yet acknowledged"}
          </div>
        )}
      </div>
    ),
  });
  if (!actions.length) return [group, ...cols];
  return [{
    field: "select", headerName: "", enableSorting: false,
    cell: (_v, row) => (
      <input type="checkbox" aria-label={`Select ${row.investor_name}`} checked={selected.includes(row.id)}
        onChange={() => toggle(row.id)} />
    ),
  }, group, ...cols];
}

export default function AccessGrid({ initialEnvelope }) {
  const [env, setEnv] = useState(initialEnvelope);
  const [query, setQuery] = useState({ group_by: initialEnvelope?.group_by || "deal", status: "", unsigned: false,
    page: 0, page_size: initialEnvelope?.page_size || 50, sort: "since", desc: true });
  const [selected, setSelected] = useState([]);
  const [level, setLevel] = useState("after_agreements");
  const [reason, setReason] = useState("");
  const [endsOn, setEndsOn] = useState("");
  const [sendNotice, setSendNotice] = useState(false);
  const [msg, setMsg] = useState(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async (q) => {
    const params = new URLSearchParams();
    for (const [k, v] of Object.entries(q)) if (v !== "" && v !== false && v != null) params.set(k, String(v));
    const res = await fetch(`/api/deal-room/access-grid?${params}`);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
    setEnv(data);
  }, []);
  const update = async (patch) => {
    const q = { ...query, ...patch };
    setQuery(q);
    try {
      await load(q);
    } catch (e) {
      setMsg(e.message);
    }
  };
  const toggle = (id) => setSelected((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id]));
  const actions = gridActions(env);
  const v = env?.vocabularies || {};

  const decide = async (action) => {
    setBusy(true);
    setMsg(null);
    try {
      const res = await fetch("/api/deal-room/access/decide", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ access_ids: selected, action, level: action === "approve" ? level : null,
          reason: reason || null, ends_on: action === "extend" ? endsOn || null : null,
          send_return_notice: action === "revoke" ? sendNotice : false }),
      });
      const data = await res.json().catch(() => ({}));
      const results = data.results || data.detail?.results || [];
      const failed = results.filter((r) => !r.ok);
      setMsg(res.ok ? (failed.length ? `${results.length - failed.length} done; ${failed.length} refused: `
        + failed.map((f) => f.detail).join("; ") : `${results.length} updated.`) : data.error);
      setSelected([]);
      await load(query);
    } catch (e) {
      setMsg(e.message);
    } finally {
      setBusy(false);
    }
  };

  if (!env) return <p className="text-sm text-text-muted">The access grid is not available.</p>;
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end gap-3 text-sm text-text-secondary">
        <label>Group by
          <select className="ml-2 rounded border border-border bg-white px-2 py-1" value={query.group_by}
            onChange={(e) => update({ group_by: e.target.value, page: 0 })}>
            {(v.group_by || []).map((g) => <option key={g} value={g}>{g}</option>)}
          </select>
        </label>
        <label>Status
          <select className="ml-2 rounded border border-border bg-white px-2 py-1" value={query.status}
            onChange={(e) => update({ status: e.target.value, page: 0 })}>
            <option value="">All</option>
            {(v.statuses || []).map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}
          </select>
        </label>
        <label className="flex items-center gap-1">
          <input type="checkbox" checked={query.unsigned} onChange={(e) => update({ unsigned: e.target.checked, page: 0 })} />
          Unsigned only
        </label>
      </div>

      {actions.length > 0 && (
        <div className="flex flex-wrap items-center gap-2 rounded-[6px] border border-[#ece8dd] bg-white p-3 text-sm"
          data-testid="grid-actions">
          <span className="text-text-muted">{selected.length} selected</span>
          {actions.includes("approve") && (
            <>
              <select className="rounded border border-border bg-white px-2 py-1" value={level}
                onChange={(e) => setLevel(e.target.value)} aria-label="Level to grant">
                {(v.grant_levels || []).map((l) => <option key={l.key} value={l.key}>{l.label}</option>)}
              </select>
              <button type="button" disabled={busy || !selected.length} onClick={() => decide("approve")}
                className="rounded bg-navy px-3 py-1.5 text-white disabled:opacity-50">Approve</button>
            </>
          )}
          <input className="rounded border border-border px-2 py-1" placeholder="Reason" value={reason}
            onChange={(e) => setReason(e.target.value)} />
          {actions.includes("decline") && (
            <button type="button" disabled={busy || !selected.length || !reason.trim()} onClick={() => decide("decline")}
              className="rounded border border-navy px-3 py-1.5 text-navy disabled:opacity-50">Decline</button>
          )}
          {actions.includes("require_resign") && (
            <button type="button" disabled={busy || !selected.length} onClick={() => decide("require_resign")}
              className="rounded border border-navy px-3 py-1.5 text-navy disabled:opacity-50">Require re-sign</button>
          )}
          {actions.includes("extend") && (
            <>
              <input type="date" aria-label="New end date" className="rounded border border-border px-2 py-1" value={endsOn}
                onChange={(e) => setEndsOn(e.target.value)} />
              <button type="button" disabled={busy || !selected.length || !endsOn} onClick={() => decide("extend")}
                className="rounded border border-navy px-3 py-1.5 text-navy disabled:opacity-50">Extend</button>
            </>
          )}
          {actions.includes("revoke") && (
            <>
              <label className="flex items-center gap-1 text-xs text-text-secondary">
                <input type="checkbox" checked={sendNotice} onChange={(e) => setSendNotice(e.target.checked)} />
                Send return-or-destroy notice
              </label>
              <button type="button" disabled={busy || !selected.length} onClick={() => decide("revoke")}
                className="rounded border border-[#9B2335] px-3 py-1.5 text-[#9B2335] disabled:opacity-50">Revoke</button>
            </>
          )}
        </div>
      )}
      {msg && <p className="text-sm text-text-secondary" role="status">{msg}</p>}

      <div className="rounded-[6px] border border-[#ece8dd] bg-white p-3">
        <DataGrid
          gridId="deal-access"
          columnDefs={gridColumnDefs(env, { selected, toggle })}
          rowData={env.rows || []}
          getRowId={(r) => r.id}
          emptyMessage="No access requests in your scope."
          serverSide={{
            totalRows: env.total || 0, pageIndex: query.page, pageSize: query.page_size,
            sorting: [{ id: Object.keys(SORT_KEYS).find((k) => SORT_KEYS[k] === query.sort) || "investor_name",
              desc: query.desc }].filter(() => query.sort !== "since"),
            onSortingChange: (s) => update({ sort: SORT_KEYS[s[0]?.id] || "since", desc: s[0]?.desc ?? true }),
            onPageChange: (i) => update({ page: i }),
            loading: busy,
          }}
        />
      </div>
    </div>
  );
}
