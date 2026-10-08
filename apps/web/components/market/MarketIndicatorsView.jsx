"use client";

import { useCallback, useState } from "react";

import ChartPanel from "@/components/market/ChartPanel";
import GridPanel from "@/components/market/GridPanel";
import SelectionPanel from "@/components/market/SelectionPanel";
import { ERROR_BOX, ERROR_STYLE } from "@/components/market/marketStyles.mjs";
import { initialChartSettings } from "@/lib/market/chartRequest.mjs";
import { initialControls } from "@/lib/market/gridRequest.mjs";
import { sanitize } from "@/lib/market/selection.mjs";

const TABS = [
  { key: "chart", label: "Chart" },
  { key: "grid", label: "Grid" },
];

/**
 * The page body for a given catalog state. FAIL CLOSED: the selection panel
 * and the grid controls exist only inside the "ready" branch, and
 * catalogModel.interpretCatalog only returns "ready" for a response whose
 * permissions envelope grants read. There is no other path to them.
 *
 * Props: catalogState ({kind: "loading"} | {kind: "error", message} |
 * {kind: "ready", catalog}), today (YYYY-MM-DD), and — for tests and for
 * mkt04c's saved views — initialTab and initialSelection.
 */
export default function MarketIndicatorsView({ catalogState, today, initialTab, initialSelection }) {
  if (catalogState?.kind === "ready") {
    return (
      <ReadyView
        catalog={catalogState.catalog}
        today={today}
        initialTab={initialTab}
        initialSelection={initialSelection}
      />
    );
  }
  if (catalogState?.kind === "error") {
    return (
      <div className={ERROR_BOX} style={ERROR_STYLE} role="alert" data-market-state="error">
        {catalogState.message}
      </div>
    );
  }
  return (
    <p className="text-sm text-[var(--2a-text-muted)]" data-market-state="loading">
      Loading market data…
    </p>
  );
}

function ReadyView({ catalog, today, initialTab, initialSelection }) {
  const [tab, setTab] = useState(initialTab === "chart" ? "chart" : "grid");
  const [selection, setSelection] = useState(() => sanitize(initialSelection ?? [], catalog));
  const [controls, setControls] = useState(() => initialControls(catalog, today));
  // Chart settings are the chart's own (mkt04b); the grid's controls are untouched.
  const [chartSettings, setChartSettings] = useState(() => initialChartSettings(catalog, today));
  const patchChartSettings = useCallback((patch) => setChartSettings((prev) => ({ ...prev, ...patch })), []);

  return (
    <div className="grid min-w-0 grid-cols-[20rem_minmax(0,1fr)] gap-5" data-market-state="ready">
      <SelectionPanel catalog={catalog} selection={selection} onChange={setSelection} />

      <section className="min-w-0">
        <div className="mb-4 flex gap-1 border-b border-[var(--2a-border)]" role="tablist" data-market-control="tabs">
          {TABS.map((t) => (
            <button
              key={t.key}
              type="button"
              role="tab"
              aria-selected={tab === t.key}
              onClick={() => setTab(t.key)}
              className={`-mb-px border-b-2 px-4 py-2 text-sm ${
                tab === t.key
                  ? "border-[var(--2a-gold)] font-semibold text-[var(--2a-navy)]"
                  : "border-transparent text-[var(--2a-text-secondary)] hover:text-[var(--2a-navy)]"
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>

        {tab === "chart" ? (
          <ChartPanel
            catalog={catalog}
            selection={selection}
            settings={chartSettings}
            onSettingsChange={patchChartSettings}
            today={today}
          />
        ) : (
          <GridPanel
            catalog={catalog}
            selection={selection}
            controls={controls}
            onControlsChange={setControls}
            today={today}
          />
        )}
      </section>
    </div>
  );
}
