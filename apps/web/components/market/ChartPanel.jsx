"use client";

import { useEffect, useState } from "react";

import ChartView from "@/components/market/ChartView";
import {
  KEY_DATES_ROUTE,
  KEY_DATES_UNAVAILABLE,
  buildSeriesRequest,
  createSeriesLoader,
  interpretKeyDates,
} from "@/lib/market/chartRequest.mjs";

/** GET a Next.js market route; the caller interprets {ok, body}. */
async function fetchJson(url) {
  const res = await fetch(url, { cache: "no-store" });
  const body = await res.json().catch(() => null);
  return { ok: res.ok, body };
}

/**
 * Chart tab data (mkt04b Task 4h): one GET /api/market/series per burst of
 * selection or resolution changes (debounced, newest wins), and GET
 * /api/market/key-dates once. Anchor, measure, axis and overlay changes never
 * refetch — the browser re-measures the same points. Both responses go
 * through the fail-closed interpreters in lib/market/chartRequest.mjs.
 */
export default function ChartPanel({ catalog, selection, settings, onSettingsChange, today }) {
  const request = buildSeriesRequest(selection, catalog, settings.resolution);
  const url = request && !request.blocked ? request.url : "";

  const [series, setSeries] = useState({ kind: "loading", url: "" });
  const [keyDates, setKeyDates] = useState({ kind: "loading" });
  const [loader] = useState(() => createSeriesLoader({ fetchJson, onResult: setSeries }));

  useEffect(() => {
    // An empty or refused request drops any pending call and orphans any in flight.
    if (!url) loader.cancel();
    else loader.request(url);
  }, [url, loader]);
  useEffect(() => () => loader.cancel(), [loader]);

  useEffect(() => {
    let alive = true;
    (async () => {
      let next;
      try {
        next = interpretKeyDates(await fetchJson(KEY_DATES_ROUTE));
      } catch {
        next = { kind: "error", message: KEY_DATES_UNAVAILABLE };
      }
      if (alive) setKeyDates(next);
    })();
    return () => {
      alive = false;
    };
  }, []);

  // The last accepted response keeps showing (marked stale) while a newer one loads.
  return (
    <ChartView
      catalog={catalog}
      selection={selection}
      settings={settings}
      onSettingsChange={onSettingsChange}
      today={today}
      request={request}
      seriesState={series}
      keyDatesState={keyDates}
      stale={series.url !== url}
    />
  );
}
