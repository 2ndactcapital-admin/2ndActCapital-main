"use client";

import { useEffect, useState } from "react";

import MarketIndicatorsView from "@/components/market/MarketIndicatorsView";
import { interpretCatalog } from "@/lib/market/catalogModel.mjs";
import { todayIso } from "@/lib/market/gridRequest.mjs";

/**
 * Market indicators (mkt04a): loads the catalog through the Next.js route and
 * hands the interpreted state to MarketIndicatorsView. Everything the page
 * shows — categories, colours, option lists, limits — comes from that
 * response; a response without a permissions envelope renders an error and no
 * controls (lib/market/catalogModel.mjs interpretCatalog).
 */
export default function MarketIndicators() {
  const [catalogState, setCatalogState] = useState({ kind: "loading" });
  const [today] = useState(() => todayIso());

  useEffect(() => {
    let alive = true;
    (async () => {
      let next;
      try {
        const res = await fetch("/api/market/catalog", { cache: "no-store" });
        const body = await res.json().catch(() => null);
        next = interpretCatalog({ ok: res.ok, body });
      } catch {
        next = { kind: "error", message: "Market data could not be loaded." };
      }
      if (alive) setCatalogState(next);
    })();
    return () => {
      alive = false;
    };
  }, []);

  return <MarketIndicatorsView catalogState={catalogState} today={today} />;
}
