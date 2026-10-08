"use client";

import { useEffect, useMemo, useState } from "react";

import CorrelationsView from "@/components/market/CorrelationsView";
import { requestJson } from "@/components/market/marketClient.mjs";
import {
  buildCorrelationRequest,
  chooseFocus,
  clampLag,
  correlationRows,
  correlationSeries,
  createCorrelationLoader,
  lagOptions,
  lagRange,
} from "@/lib/market/correlationModel.mjs";
import { resolveEnd } from "@/lib/market/viewsModel.mjs";

const postJson = (url, body) => requestJson(url, { method: "POST", body });

/**
 * The correlations card's state and requests (mkt04c Task 5): one POST
 * /api/market/correlations per burst of focus, lag, selection or anchor
 * changes (debounced, newest wins — lib/market/correlationModel.mjs
 * createCorrelationLoader). A response is shown only for the request that is
 * current; anything else reads as loading.
 *
 * Props: catalog, selection, settings (anchor, end), today.
 */
export default function CorrelationsPanel({ catalog, selection, settings, today }) {
  const [focus, setFocus] = useState(null);
  const [lag, setLag] = useState(0);
  const [result, setResult] = useState({ kind: "idle", key: "" });
  const [loader] = useState(() => createCorrelationLoader({ fetchJson: postJson, onResult: setResult }));

  const series = useMemo(() => correlationSeries(selection, catalog), [selection, catalog]);
  const range = lagRange(catalog);
  const focusKey = chooseFocus(focus, series);
  const end = resolveEnd(settings.end, today);
  const req = useMemo(
    () => buildCorrelationRequest({ selection, catalog, focus: focusKey, lag, anchor: settings.anchor, end }),
    [selection, catalog, focusKey, lag, settings.anchor, end],
  );

  useEffect(() => {
    if (!req) loader.cancel();
    else loader.request(req);
  }, [req, loader]);
  useEffect(() => () => loader.cancel(), [loader]);

  let state;
  if (!req) state = { kind: "empty" };
  else if (result.key !== req.key) state = { kind: "loading" };
  else if (result.kind === "ready") {
    state = {
      kind: "ready",
      rows: correlationRows(result.response, { series, frequencies: catalog.vocabularies?.frequencies }),
      lagConvention: typeof result.response.lag_convention === "string" ? result.response.lag_convention : null,
    };
  } else state = { kind: "error", message: result.message };

  return (
    <CorrelationsView
      series={series}
      focusKey={focusKey}
      onFocus={setFocus}
      lag={clampLag(lag, range)}
      lagOptions={lagOptions(range)}
      lagEnabled={range !== null}
      onLag={(v) => setLag(clampLag(v, range))}
      state={state}
    />
  );
}
