"use client";

import { useEffect, useState, useTransition } from "react";
import EntityPicker from "@/components/EntityPicker";

function formatPct(val) {
  if (val == null) return "—";
  return `${parseFloat(val).toFixed(2)}%`;
}

function formatDate(val) {
  if (!val) return "—";
  try {
    return new Date(val + "T00:00:00").toLocaleDateString();
  } catch {
    return val;
  }
}

function OwnershipRow({ item, onEdit, onDelete }) {
  return (
    <div className="flex items-center justify-between rounded-lg border bg-bg-card p-4" style={{ borderColor: "#ece8dd" }}>
      <div className="flex-1">
        <p className="font-medium text-text-primary">{item.counterparty?.display_name || "—"}</p>
        <p className="mt-0.5 text-xs text-text-muted capitalize">
          {item.counterparty?.entity_type?.replace(/_/g, " ")}
        </p>
      </div>
      <div className="flex items-center gap-6">
        <div className="text-right">
          <p className="text-sm font-semibold text-navy">{formatPct(item.ownership_pct)}</p>
          {item.effective_date && (
            <p className="text-xs text-text-muted">as of {formatDate(item.effective_date)}</p>
          )}
        </div>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={() => onEdit(item)}
            className="rounded px-2.5 py-1 text-xs font-medium text-text-secondary hover:bg-gray-100"
          >
            Edit
          </button>
          <button
            type="button"
            onClick={() => onDelete(item)}
            className="rounded px-2.5 py-1 text-xs font-medium text-error hover:bg-red-50"
          >
            Remove
          </button>
        </div>
      </div>
    </div>
  );
}

function AddForm({ entityId, direction, onSuccess, onCancel }) {
  const [selectedEntity, setSelectedEntity] = useState(null);
  const [pct, setPct] = useState("");
  const [effectiveDate, setEffectiveDate] = useState("");
  const [note, setNote] = useState("");
  const [error, setError] = useState(null);
  const [pending, startTransition] = useTransition();

  function submit() {
    if (!selectedEntity) { setError("Select an entity."); return; }
    startTransition(async () => {
      try {
        const res = await fetch(`/api/entities/${entityId}/ownership`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            direction,
            counterparty_id: selectedEntity.id,
            ownership_pct: pct ? parseFloat(pct) : null,
            effective_date: effectiveDate || null,
            note: note || null,
            change_reason: "initial",
          }),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || "Failed");
        onSuccess();
      } catch (err) {
        setError(err.message);
      }
    });
  }

  return (
    <div className="mt-3 rounded-lg border p-4 space-y-3" style={{ borderColor: "#ece8dd" }}>
      <div>
        <label className="block text-xs font-semibold uppercase tracking-wide text-text-muted mb-1">
          {direction === "owns" ? "Owned Entity" : "Owner Entity"}
        </label>
        <EntityPicker
          value={selectedEntity}
          onChange={setSelectedEntity}
          placeholder="Search entities…"
          allowCreate
        />
      </div>
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="block text-xs font-semibold uppercase tracking-wide text-text-muted mb-1">
            Ownership %
          </label>
          <input
            type="number"
            min="0"
            max="100"
            step="0.01"
            value={pct}
            onChange={(e) => setPct(e.target.value)}
            className="w-full rounded border border-border px-3 py-1.5 text-sm"
            placeholder="e.g. 50.00"
          />
        </div>
        <div>
          <label className="block text-xs font-semibold uppercase tracking-wide text-text-muted mb-1">
            Effective Date
          </label>
          <input
            type="date"
            value={effectiveDate}
            onChange={(e) => setEffectiveDate(e.target.value)}
            className="w-full rounded border border-border px-3 py-1.5 text-sm"
          />
        </div>
      </div>
      <div>
        <label className="block text-xs font-semibold uppercase tracking-wide text-text-muted mb-1">
          Note
        </label>
        <input
          type="text"
          value={note}
          onChange={(e) => setNote(e.target.value)}
          className="w-full rounded border border-border px-3 py-1.5 text-sm"
          placeholder="Optional note"
        />
      </div>
      {error && <p className="text-xs text-error">{error}</p>}
      <div className="flex gap-2">
        <button
          type="button"
          onClick={submit}
          disabled={pending}
          className="rounded bg-navy px-4 py-1.5 text-sm font-medium text-white disabled:opacity-50"
        >
          {pending ? "Saving…" : "Add"}
        </button>
        <button
          type="button"
          onClick={onCancel}
          className="rounded border border-border px-4 py-1.5 text-sm font-medium text-text-secondary"
        >
          Cancel
        </button>
      </div>
    </div>
  );
}

function EditModal({ item, onSuccess, onClose }) {
  const [pct, setPct] = useState(item.ownership_pct != null ? String(item.ownership_pct) : "");
  const [effectiveDate, setEffectiveDate] = useState(item.effective_date || "");
  const [note, setNote] = useState(item.notes || "");
  const [changeReason, setChangeReason] = useState("manual_edit");
  const [error, setError] = useState(null);
  const [pending, startTransition] = useTransition();

  function submit() {
    startTransition(async () => {
      try {
        const res = await fetch(`/api/entity-relationships/${item.relationship_id}/ownership`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            ownership_pct: pct ? parseFloat(pct) : null,
            effective_date: effectiveDate || null,
            note: note || null,
            change_reason: changeReason,
          }),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || "Failed");
        onSuccess();
        onClose();
      } catch (err) {
        setError(err.message);
      }
    });
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="w-full max-w-md rounded-lg bg-white p-6 shadow-lg">
        <h3 className="text-base font-semibold text-navy mb-4">Edit Ownership</h3>
        <p className="text-sm text-text-secondary mb-4">{item.counterparty?.display_name}</p>
        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs font-semibold uppercase tracking-wide text-text-muted mb-1">
                Ownership %
              </label>
              <input
                type="number"
                min="0"
                max="100"
                step="0.01"
                value={pct}
                onChange={(e) => setPct(e.target.value)}
                className="w-full rounded border border-border px-3 py-1.5 text-sm"
              />
            </div>
            <div>
              <label className="block text-xs font-semibold uppercase tracking-wide text-text-muted mb-1">
                Effective Date
              </label>
              <input
                type="date"
                value={effectiveDate}
                onChange={(e) => setEffectiveDate(e.target.value)}
                className="w-full rounded border border-border px-3 py-1.5 text-sm"
              />
            </div>
          </div>
          <div>
            <label className="block text-xs font-semibold uppercase tracking-wide text-text-muted mb-1">
              Note
            </label>
            <input
              type="text"
              value={note}
              onChange={(e) => setNote(e.target.value)}
              className="w-full rounded border border-border px-3 py-1.5 text-sm"
            />
          </div>
          <div>
            <label className="block text-xs font-semibold uppercase tracking-wide text-text-muted mb-1">
              Change Reason
            </label>
            <input
              type="text"
              value={changeReason}
              onChange={(e) => setChangeReason(e.target.value)}
              className="w-full rounded border border-border px-3 py-1.5 text-sm"
              placeholder="e.g. correction, restructure"
            />
          </div>
        </div>
        {error && <p className="mt-2 text-xs text-error">{error}</p>}
        <div className="mt-4 flex gap-2">
          <button
            type="button"
            onClick={submit}
            disabled={pending}
            className="rounded bg-navy px-4 py-1.5 text-sm font-medium text-white disabled:opacity-50"
          >
            {pending ? "Saving…" : "Save"}
          </button>
          <button
            type="button"
            onClick={onClose}
            className="rounded border border-border px-4 py-1.5 text-sm font-medium text-text-secondary"
          >
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}

function HistorySection({ entityId }) {
  const [open, setOpen] = useState(false);
  const [history, setHistory] = useState(null);
  const [loading, setLoading] = useState(false);

  async function load() {
    setLoading(true);
    try {
      const res = await fetch(`/api/entities/${entityId}/ownership/history`);
      const data = await res.json();
      setHistory(data.history || []);
    } catch {
      setHistory([]);
    } finally {
      setLoading(false);
    }
  }

  function toggle() {
    if (!open && !history) load();
    setOpen((v) => !v);
  }

  return (
    <div className="mt-6">
      <button
        type="button"
        onClick={toggle}
        className="flex items-center gap-1 text-sm font-medium text-text-secondary hover:text-navy"
      >
        <span>{open ? "▾" : "▸"}</span> Change History
      </button>
      {open && (
        <div className="mt-3">
          {loading && <p className="text-sm text-text-muted">Loading…</p>}
          {history && history.length === 0 && (
            <p className="text-sm text-text-muted">No history recorded yet.</p>
          )}
          {history && history.length > 0 && (
            <div className="space-y-2">
              {history.map((h) => (
                <div
                  key={h.id}
                  className="rounded border p-3 text-xs"
                  style={{ borderColor: "#ece8dd" }}
                >
                  <div className="flex justify-between text-text-muted mb-1">
                    <span className="font-medium capitalize">{h.change_reason}</span>
                    <span>{h.created_at ? new Date(h.created_at).toLocaleString() : ""}</span>
                  </div>
                  <div className="flex gap-4 text-text-secondary">
                    <span>
                      Before: {h.prior_pct != null ? `${parseFloat(h.prior_pct).toFixed(2)}%` : "—"}
                    </span>
                    <span>
                      After: {h.new_pct != null ? `${parseFloat(h.new_pct).toFixed(2)}%` : "—"}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default function OwnershipTab({ entityId }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [asOf, setAsOf] = useState("");
  const [addingOwns, setAddingOwns] = useState(false);
  const [addingOwnedBy, setAddingOwnedBy] = useState(false);
  const [editItem, setEditItem] = useState(null);
  const [actionError, setActionError] = useState(null);

  async function load(date) {
    setLoading(true);
    setError(null);
    try {
      const qs = date ? `?as_of=${date}` : "";
      const res = await fetch(`/api/entities/${entityId}/ownership${qs}`);
      const json = await res.json();
      if (!res.ok) throw new Error(json.error || "Failed");
      setData(json);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load(asOf || undefined); }, [entityId]);

  function applyAsOf() { load(asOf || undefined); }

  async function handleDelete(item) {
    if (!confirm(`Remove ownership link to ${item.counterparty?.display_name}?`)) return;
    setActionError(null);
    try {
      const res = await fetch(`/api/entity-relationships/${item.relationship_id}/ownership`, {
        method: "DELETE",
      });
      if (!res.ok) {
        const d = await res.json().catch(() => ({}));
        throw new Error(d.error || "Delete failed");
      }
      await load(asOf || undefined);
    } catch (err) {
      setActionError(err.message);
    }
  }

  const isHistorical = !!asOf && asOf < new Date().toISOString().slice(0, 10);

  if (loading) return <p className="py-6 text-sm text-text-muted">Loading ownership…</p>;
  if (error) return <p className="py-6 text-sm text-error">{error}</p>;
  if (!data) return null;

  const totalPct = data.owned_by_total_pct ?? 0;

  return (
    <div className="space-y-8">
      {/* Time-travel bar */}
      <div className="flex items-center gap-3">
        <label className="text-sm font-medium text-text-secondary">View as of</label>
        <input
          type="date"
          value={asOf}
          onChange={(e) => setAsOf(e.target.value)}
          className="rounded border border-border px-3 py-1.5 text-sm"
        />
        <button
          type="button"
          onClick={applyAsOf}
          className="rounded border border-border px-3 py-1.5 text-sm font-medium text-text-secondary hover:bg-gray-50"
        >
          Apply
        </button>
        {asOf && (
          <button
            type="button"
            onClick={() => { setAsOf(""); load(undefined); }}
            className="text-xs text-text-muted hover:text-navy"
          >
            Clear
          </button>
        )}
        {isHistorical && (
          <span className="rounded bg-gold-light px-2 py-0.5 text-xs font-medium text-navy">
            Historical view — read only
          </span>
        )}
      </div>

      {actionError && (
        <p className="rounded bg-red-50 px-3 py-2 text-sm text-error">{actionError}</p>
      )}

      {/* Owned By panel */}
      <section>
        <div className="flex items-center justify-between mb-3">
          <div>
            <h3 className="text-sm font-semibold uppercase tracking-wide text-text-muted">
              Owned By
            </h3>
            {totalPct > 0 && (
              <p className="text-xs text-text-muted mt-0.5">
                Total: {totalPct.toFixed(2)}%
                {totalPct > 100 && (
                  <span className="ml-2 text-error font-medium">⚠ Exceeds 100%</span>
                )}
              </p>
            )}
          </div>
          {!isHistorical && (
            <button
              type="button"
              onClick={() => { setAddingOwnedBy(true); setAddingOwns(false); }}
              className="rounded border border-navy px-3 py-1 text-xs font-medium text-navy hover:bg-navy hover:text-white transition-colors"
            >
              + Add Owner
            </button>
          )}
        </div>

        {addingOwnedBy && (
          <AddForm
            entityId={entityId}
            direction="owned_by"
            onSuccess={() => { setAddingOwnedBy(false); load(asOf || undefined); }}
            onCancel={() => setAddingOwnedBy(false)}
          />
        )}

        {data.owned_by.length === 0 && !addingOwnedBy && (
          <p className="text-sm text-text-muted">No owners recorded.</p>
        )}
        <div className="space-y-2 mt-2">
          {data.owned_by.map((item) => (
            <OwnershipRow
              key={item.relationship_id}
              item={item}
              onEdit={(i) => { setEditItem(i); setActionError(null); }}
              onDelete={handleDelete}
            />
          ))}
        </div>
      </section>

      {/* Owns panel */}
      <section>
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-semibold uppercase tracking-wide text-text-muted">Owns</h3>
          {!isHistorical && (
            <button
              type="button"
              onClick={() => { setAddingOwns(true); setAddingOwnedBy(false); }}
              className="rounded border border-navy px-3 py-1 text-xs font-medium text-navy hover:bg-navy hover:text-white transition-colors"
            >
              + Add Holding
            </button>
          )}
        </div>

        {addingOwns && (
          <AddForm
            entityId={entityId}
            direction="owns"
            onSuccess={() => { setAddingOwns(false); load(asOf || undefined); }}
            onCancel={() => setAddingOwns(false)}
          />
        )}

        {data.owns.length === 0 && !addingOwns && (
          <p className="text-sm text-text-muted">No holdings recorded.</p>
        )}
        <div className="space-y-2 mt-2">
          {data.owns.map((item) => (
            <OwnershipRow
              key={item.relationship_id}
              item={item}
              onEdit={(i) => { setEditItem(i); setActionError(null); }}
              onDelete={handleDelete}
            />
          ))}
        </div>
      </section>

      {/* Change history */}
      <HistorySection entityId={entityId} />

      {/* Edit modal */}
      {editItem && (
        <EditModal
          item={editItem}
          onSuccess={() => load(asOf || undefined)}
          onClose={() => setEditItem(null)}
        />
      )}
    </div>
  );
}
