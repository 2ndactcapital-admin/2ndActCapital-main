"use client";

// NAV3 — one read for a record-page tab through its EXISTING Next route. A 401 / 403 / 404 is a refusal: the tab
// tells the page (onRefused) and the page hides it — a refused tab is never rendered as an empty one.
import { useEffect, useState } from "react";

export function useTabFetch(url, onRefused) {
  const [state, setState] = useState({ loading: Boolean(url), data: null, error: null });
  useEffect(() => {
    if (!url) { setState({ loading: false, data: null, error: null }); return undefined; }
    let live = true;
    setState({ loading: true, data: null, error: null });
    fetch(url, { cache: "no-store" })
      .then(async (r) => {
        const body = await r.json().catch(() => ({}));
        if (!live) return;
        if (r.status === 401 || r.status === 403 || r.status === 404) {
          setState({ loading: false, data: null, error: "refused" });
          if (onRefused) onRefused();
          return;
        }
        if (!r.ok) { setState({ loading: false, data: null, error: body?.error || `HTTP ${r.status}` }); return; }
        setState({ loading: false, data: body, error: null });
      })
      .catch((e) => { if (live) setState({ loading: false, data: null, error: String(e?.message || e) }); });
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [url]);
  return state;
}

export function TabLoading() {
  return <p className="py-10 text-center text-sm text-text-muted">Loading…</p>;
}

export function TabError({ error }) {
  return (
    <div className="rounded-[6px] border border-[#ece8dd] bg-white p-4 text-sm text-error">
      Could not load this tab ({error}).
    </div>
  );
}

export function Card({ title, children, note }) {
  return (
    <section className="rounded-[6px] border border-[#ece8dd] bg-white p-6">
      {title ? (
        <div className="flex items-baseline justify-between gap-4">
          <h2 className="text-sm font-semibold text-text-secondary">{title}</h2>
          {note ? <span className="text-xs text-text-muted">{note}</span> : null}
        </div>
      ) : null}
      <div className={title ? "mt-4" : ""}>{children}</div>
    </section>
  );
}
