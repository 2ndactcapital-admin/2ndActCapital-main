"use client";

import { useEffect, useState } from "react";

import MarketGridTable from "@/components/market/MarketGridTable";
import { BUTTON, CONTROL, ERROR_BOX, ERROR_STYLE, EYEBROW, QUIET } from "@/components/market/marketStyles.mjs";
import { errorMessage, grantsRead } from "@/lib/market/catalogModel.mjs";
import {
  buildGridBody,
  createDebouncer,
  createLatestGate,
  startOfData,
  yearsAgo,
} from "@/lib/market/gridRequest.mjs";
import { buildGridView } from "@/lib/market/gridView.mjs";
import { DEFAULT_ANCHOR_YEARS_BACK } from "@/lib/market/marketDefaults.mjs";
import { resolveSelection } from "@/lib/market/selection.mjs";

/**
 * Grid tab: controls, then POST /api/market/grid for the current selection.
 * Requests are debounced (one request per burst of clicks) and gated so only
 * the newest response renders. Nothing is requested for an empty selection.
 * The table shows the server's strings; nothing is recomputed here.
 */
export default function GridPanel({ catalog, selection, controls, onControlsChange, today }) {
  const vocab = catalog.vocabularies ?? {};
  const body = buildGridBody(selection, catalog, controls);
  const bodyKey = body ? JSON.stringify(body) : "";

  // { response, selection } of the newest accepted reply, or { error }.
  const [result, setResult] = useState({ response: null, selection: [], error: null, loading: false });
  const [gate] = useState(createLatestGate);
  const [debouncer] = useState(() =>
    createDebouncer(async ({ requestBody, requestSelection }) => {
      const token = gate.next();
      setResult((prev) => ({ ...prev, loading: true }));
      let next;
      try {
        const res = await fetch("/api/market/grid", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(requestBody),
          cache: "no-store",
        });
        const data = await res.json().catch(() => null);
        if (res.ok && grantsRead(data)) {
          next = { response: data, selection: requestSelection, error: null, loading: false };
        } else if (res.ok) {
          next = { response: null, selection: [], error: "The grid response did not confirm your access.", loading: false };
        } else {
          next = { response: null, selection: [], error: errorMessage(data, "The grid could not be loaded."), loading: false };
        }
      } catch {
        next = { response: null, selection: [], error: "The grid could not be loaded.", loading: false };
      }
      if (gate.isLatest(token)) setResult(next);
    }),
  );

  useEffect(() => {
    if (!bodyKey) {
      // Nothing to ask for: drop any pending request and orphan any in flight.
      debouncer.cancel();
      gate.next();
      return;
    }
    debouncer.call({ requestBody: JSON.parse(bodyKey), requestSelection: selection });
    // `selection` travels with the request so the header matches the reply.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bodyKey, debouncer, gate]);

  useEffect(() => () => debouncer.cancel(), [debouncer]);

  const resolved = resolveSelection(selection, catalog);
  const dataStart = startOfData(resolved);
  const set = (patch) => onControlsChange({ ...controls, ...patch });

  return (
    <div className="min-w-0 space-y-3" data-market-control="grid">
      <div className="flex flex-wrap items-end gap-4" data-market-control="grid-controls">
        <label className="flex flex-col gap-1">
          <span className={EYEBROW}>Measure</span>
          <select className={CONTROL} value={controls.mode ?? ""} onChange={(e) => set({ mode: e.target.value })}>
            {(vocab.modes ?? []).map((m) => (
              <option key={m.key} value={m.key}>
                {m.label}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1">
          <span className={EYEBROW}>Frequency</span>
          <select
            className={CONTROL}
            value={controls.frequency ?? ""}
            onChange={(e) => set({ frequency: e.target.value })}
          >
            {(vocab.grid_frequencies ?? []).map((f) => (
              <option key={f.key} value={f.key}>
                {f.label}
              </option>
            ))}
          </select>
        </label>
        <div className="flex flex-col gap-1">
          <span className={EYEBROW}>Anchor</span>
          <div className="flex items-center gap-1.5">
            <input
              type="date"
              className={CONTROL}
              value={controls.anchor ?? ""}
              onChange={(e) => set({ anchor: e.target.value })}
            />
            <button type="button" className={BUTTON} disabled={!dataStart} onClick={() => set({ anchor: dataStart })}>
              Start of data
            </button>
            <button
              type="button"
              className={BUTTON}
              onClick={() => set({ anchor: yearsAgo(today, DEFAULT_ANCHOR_YEARS_BACK) })}
            >
              {DEFAULT_ANCHOR_YEARS_BACK} years ago
            </button>
          </div>
        </div>
        <div className="flex flex-col gap-1">
          <span className={EYEBROW}>End (optional)</span>
          <div className="flex items-center gap-1.5">
            <input type="date" className={CONTROL} value={controls.end ?? ""} onChange={(e) => set({ end: e.target.value })} />
            {controls.end && (
              <button type="button" className={BUTTON} onClick={() => set({ end: "" })}>
                Clear
              </button>
            )}
          </div>
        </div>
      </div>

      {!body ? (
        <p className={`${QUIET} py-10 text-center`} data-market="grid-empty">
          Select indicators to see their values.
        </p>
      ) : result.error ? (
        <div className={ERROR_BOX} style={ERROR_STYLE} role="alert" data-market="grid-error">
          {result.error}
        </div>
      ) : result.response ? (
        <>
          <p className={QUIET}>
            {result.response.row_count} rows · {result.response.anchor} to {result.response.end}
            {result.loading ? " · updating…" : ""}
          </p>
          <MarketGridTable view={buildGridView(result.response, result.selection, catalog)} />
        </>
      ) : (
        <p className={`${QUIET} py-10 text-center`}>Loading values…</p>
      )}
    </div>
  );
}
