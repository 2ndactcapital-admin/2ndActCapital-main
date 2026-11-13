"use client";

import { Fragment, Suspense, createContext, useContext } from "react";
import { usePathname, useSearchParams } from "next/navigation";
import { useEntityContext, forgetEntity } from "@/lib/useEntityContext";
import { filteredBanner, NOT_FILTERED_NOTICE, typeLabel, withEntity } from "@/lib/entityContext.mjs";
import { FILTERS, screenDecision } from "@/lib/entityScreens.mjs";

// NAV2 — wraps a book screen: draws the "Filtered to <name>" banner or the "isn't filtered" notice (lib/entityScreens),
// waits for the chip's lookup to settle (so a grid never fetches with a pending or forbidden id) and hands the screen
// ONLY a visible entity, through ScreenEntityContext. Outside this wrapper the context is null → screens unchanged.

export const ScreenEntityContext = createContext(null);

/** The screen's entity ({ id, display_name, entity_type }) or null. */
export function useScreenEntity() {
  return useContext(ScreenEntityContext)?.entity || null;
}

/** Banner / notice presentation — pure, exported for node tests. */
export function EntityBanner({ decision, onClear }) {
  if (!decision || !decision.banner) return null;
  if (decision.banner === "filtered") {
    const e = decision.entity;
    return (
      <div role="status" data-testid="entity-banner" data-banner="filtered"
        className="mb-4 flex flex-wrap items-center justify-between gap-2 rounded-md border px-4 py-2 text-sm"
        style={{ borderColor: "#ece8dd", background: "#FFFFFF", color: "#334155" }}>
        <span>
          {e.entity_type && <span className="mr-2 text-xs font-semibold uppercase tracking-wide" style={{ color: "#C5A880" }}>{typeLabel(e.entity_type)}</span>}
          {filteredBanner(e.display_name)}
        </span>
        {onClear && (
          <button type="button" onClick={onClear} className="rounded px-2 py-0.5 text-sm font-medium"
            style={{ color: "#1B2B4B" }} data-testid="entity-banner-clear">
            Clear
          </button>
        )}
      </div>
    );
  }
  return (
    <div role="note" data-testid="entity-banner" data-banner="not_filtered"
      className="mb-4 rounded-md border px-4 py-2 text-sm"
      style={{ borderColor: "#ece8dd", background: "#FFFFFF", color: "#64748B" }}>
      {NOT_FILTERED_NOTICE}
    </div>
  );
}

function Inner({ children }) {
  const pathname = usePathname() || "/";
  const sp = useSearchParams();
  const ctx = useEntityContext();
  const search = sp ? sp.toString() : "";
  const decision = screenDecision(pathname, search, ctx.state);
  // Only a FILTERING screen waits for the lookup — it must never fetch with a pending (possibly forbidden) id.
  if (decision.mode === FILTERS && ctx.state.kind === "loading") {
    return <p className="text-sm text-text-muted" role="status">Loading…</p>;
  }
  function clear() {
    if (ctx.userId) forgetEntity(ctx.userId);
    window.location.assign(withEntity(`${pathname}${window.location.search}`, null));
  }
  return (
    <ScreenEntityContext.Provider value={{ entity: decision.entity }}>
      <EntityBanner decision={decision} onClear={clear} />
      <Fragment key={decision.entity?.id || "all"}>{children}</Fragment>
    </ScreenEntityContext.Provider>
  );
}

export default function EntityScreen({ children }) {
  return (
    <Suspense fallback={children}>
      <Inner>{children}</Inner>
    </Suspense>
  );
}
