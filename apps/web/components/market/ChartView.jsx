"use client";

import { useMemo } from "react";

import CorrelationsPanel from "@/components/market/CorrelationsPanel";
import KeyDatesPanel from "@/components/market/KeyDatesPanel";
import MarketChart from "@/components/market/MarketChart";
import { BUTTON, CONTROL, ERROR_BOX, ERROR_STYLE, EYEBROW, QUIET } from "@/components/market/marketStyles.mjs";
import { MEASURE_SIGMA, SCALE_LINEAR, SCALE_LOG } from "@/lib/market/chartContract.mjs";
import { prepareChartData } from "@/lib/market/chartModel.mjs";
import { chartMeasureOptions, resolutionOptions } from "@/lib/market/chartRequest.mjs";
import { startOfData, yearsAgo } from "@/lib/market/gridRequest.mjs";
import { resolveDateSelection } from "@/lib/market/keyDatesModel.mjs";
import { DEFAULT_ANCHOR_YEARS_BACK } from "@/lib/market/marketDefaults.mjs";
import { resolveSelection } from "@/lib/market/selection.mjs";

const OVERLAY_TOGGLES = [
  { key: "events", label: "Regimes & events" },
  { key: "band", label: "Cohort band" },
  { key: "emphasis", label: "Highlight outliers" },
];
const SCALES = [
  { key: SCALE_LOG, label: "Log" },
  { key: SCALE_LINEAR, label: "Linear" },
];

/**
 * The Chart tab's body for a given data state (mkt04b Task 4a, 4h). Renders
 * the controls, then exactly one of: the empty prompt, a refusal made before
 * sending (over the limit), the server's error, a loading line, or the chart.
 * FAIL CLOSED: the chart exists only for seriesState.kind === "ready", which
 * lib/market/chartRequest.mjs interpretSeries grants only to a response whose
 * envelope grants read.
 *
 * Props: catalog, selection, settings, onSettingsChange(patch), today,
 * request (buildSeriesRequest), seriesState, keyDatesState, stale,
 * initialWidth (tests), and (mkt04c) dateSel, onPickDate(sel, anchorIso),
 * onKeyDatesChanged(): the key-dates bar under the chart and the "What moves
 * with it" card above the trends table render with the chart.
 */
export default function ChartView({
  catalog,
  selection,
  settings,
  onSettingsChange,
  today,
  request,
  seriesState,
  keyDatesState,
  stale,
  initialWidth,
  dateSel,
  onPickDate,
  onKeyDatesChanged,
}) {
  const vocab = catalog.vocabularies ?? {};
  const measures = chartMeasureOptions(vocab);
  const resolutions = resolutionOptions(vocab);
  const sigma = settings.measure === MEASURE_SIGMA;
  const response = seriesState?.kind === "ready" ? seriesState.response : null;
  const prepared = useMemo(
    () => (response ? prepareChartData(response, selection, catalog, settings.resolution) : null),
    [response, selection, catalog, settings.resolution],
  );
  const dateSelection = useMemo(() => resolveDateSelection(dateSel ?? null, keyDatesState), [dateSel, keyDatesState]);
  const dataStart = startOfData(resolveSelection(selection, catalog));
  const setOverlay = (key, on) => onSettingsChange({ overlays: { ...settings.overlays, [key]: on } });

  let body;
  if (!request) {
    body = (
      <p className={`${QUIET} py-10 text-center`} data-market="chart-empty">
        Select indicators to chart them.
      </p>
    );
  } else if (request.blocked) {
    body = (
      <p className={`${QUIET} py-10 text-center`} role="status" data-market="chart-blocked">
        {request.message}
      </p>
    );
  } else if (seriesState?.kind === "error" && !stale) {
    body = (
      <div className={ERROR_BOX} style={ERROR_STYLE} role="alert" data-market="chart-error">
        {seriesState.message}
      </div>
    );
  } else if (!prepared) {
    body = <p className={`${QUIET} py-10 text-center`}>Loading the chart…</p>;
  } else if (measures.length === 0) {
    body = (
      <p className={`${QUIET} py-10 text-center`} data-market="chart-no-measure">
        The server offers no chart measure.
      </p>
    );
  } else {
    body = (
      <>
        {stale && <p className={QUIET}>Updating…</p>}
        <MarketChart
          prepared={prepared}
          settings={settings}
          onAnchorChange={(anchor) => onSettingsChange({ anchor })}
          modes={vocab.modes}
          seriesVocab={response.vocabularies ?? {}}
          keyDates={keyDatesState}
          initialWidth={initialWidth}
          dateSelection={dateSelection}
          belowChart={
            <KeyDatesPanel
              keyDates={keyDatesState}
              dateSel={dateSel}
              periods={prepared.periods}
              onPickDate={onPickDate}
              onKeyDatesChanged={onKeyDatesChanged}
            />
          }
          beforeTable={<CorrelationsPanel catalog={catalog} selection={selection} settings={settings} today={today} />}
        />
        {keyDatesState?.kind === "error" && settings.overlays?.events && (
          <p className={QUIET} data-market="key-dates-error">
            {keyDatesState.message}
          </p>
        )}
      </>
    );
  }

  return (
    <div className="min-w-0 space-y-3" data-market-tab="chart">
      <div className="flex flex-wrap items-end gap-4" data-market-control="chart-controls">
        <label className="flex flex-col gap-1">
          <span className={EYEBROW}>Measure</span>
          <select
            className={CONTROL}
            value={settings.measure ?? ""}
            onChange={(e) => onSettingsChange({ measure: e.target.value })}
            data-chart-control="measure"
          >
            {measures.map((m) => (
              <option key={m.key} value={m.key}>
                {m.label}
              </option>
            ))}
          </select>
        </label>

        <div className="flex flex-col gap-1">
          <span className={EYEBROW}>Axis</span>
          <div className="flex" role="group" aria-label="Axis" data-chart-control="scale">
            {SCALES.map((s, i) => {
              const on = !sigma && settings.scale === s.key;
              return (
                <button
                  key={s.key}
                  type="button"
                  disabled={sigma}
                  aria-pressed={on}
                  onClick={() => onSettingsChange({ scale: s.key })}
                  className={`border border-[var(--2a-border)] px-2.5 py-1 text-xs disabled:opacity-50 ${
                    i === 0 ? "rounded-l" : "-ml-px rounded-r"
                  } ${on ? "bg-[var(--2a-navy)] text-white" : "bg-white text-[var(--2a-text-secondary)]"}`}
                >
                  {s.label}
                </button>
              );
            })}
          </div>
        </div>

        <label className="flex flex-col gap-1">
          <span className={EYEBROW}>Resolution</span>
          <select
            className={CONTROL}
            value={settings.resolution ?? ""}
            onChange={(e) => onSettingsChange({ resolution: e.target.value })}
            data-chart-control="resolution"
          >
            {resolutions.map((f) => (
              <option key={f.key} value={f.key}>
                {f.label}
              </option>
            ))}
          </select>
        </label>

        <div className="flex flex-col gap-1">
          <span className={EYEBROW}>Anchor</span>
          <div className="flex items-center gap-1.5">
            <button
              type="button"
              className={BUTTON}
              disabled={!dataStart}
              onClick={() => onSettingsChange({ anchor: dataStart })}
            >
              Start of data
            </button>
            <button
              type="button"
              className={BUTTON}
              onClick={() =>
                onSettingsChange({ anchor: yearsAgo(today, DEFAULT_ANCHOR_YEARS_BACK), anchorYears: DEFAULT_ANCHOR_YEARS_BACK })
              }
            >
              {DEFAULT_ANCHOR_YEARS_BACK} years ago
            </button>
          </div>
        </div>

        <fieldset className="flex flex-col gap-1">
          <legend className={EYEBROW}>Overlays</legend>
          <div className="flex flex-wrap items-center gap-3">
            {OVERLAY_TOGGLES.map((t) => (
              <label key={t.key} className="inline-flex items-center gap-1.5 text-xs text-[var(--2a-text)]">
                <input
                  type="checkbox"
                  checked={settings.overlays?.[t.key] === true}
                  onChange={(e) => setOverlay(t.key, e.target.checked)}
                  data-chart-overlay={t.key}
                />
                {t.label}
              </label>
            ))}
          </div>
        </fieldset>
      </div>

      {body}
    </div>
  );
}
