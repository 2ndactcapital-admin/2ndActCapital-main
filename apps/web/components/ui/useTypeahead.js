"use client";

// CATCHALL1 — the shared picker search (lib/typeahead.mjs): runs when the QUERY changes, whatever changed it
// (keystroke, paste, autofill), debounced; stale responses are dropped. Wire the input with BOTH onChange and onInput
// to setQuery (autofill in some browsers fires only input events).
import { useEffect, useRef, useState } from "react";
import { TYPEAHEAD_DELAY_MS, sequence, shouldSearch } from "@/lib/typeahead.mjs";

export function useTypeahead(search, { minChars = 1, delay = TYPEAHEAD_DELAY_MS } = {}) {
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState([]);
  const [error, setError] = useState(null);
  const [tick, setTick] = useState(0);   // rerun(): the same query again (a Search button / Enter)
  const last = useRef("");
  const seq = useRef(sequence());
  const tooShort = String(query ?? "").trim().length < minChars;
  useEffect(() => {
    const q = String(query ?? "").trim();
    if (q.length < minChars) { last.current = ""; return undefined; }   // hits are hidden below, not reset here
    if (!shouldSearch(q, last.current, minChars)) return undefined;
    const id = setTimeout(async () => {
      last.current = q;
      const n = seq.current.next();
      try {
        const out = await search(q);
        if (seq.current.isLatest(n)) { setHits(Array.isArray(out) ? out : []); setError(null); }
      } catch (e) {
        if (seq.current.isLatest(n)) { setHits([]); setError(e?.message || String(e)); }
      }
    }, delay);
    return () => clearTimeout(id);
  }, [query, tick]); // eslint-disable-line react-hooks/exhaustive-deps
  const reset = () => { setQuery(""); setHits([]); last.current = ""; };
  const rerun = () => { last.current = ""; setTick((n) => n + 1); };
  return { query, setQuery, hits: tooShort ? [] : hits, error: tooShort ? null : error, reset, rerun };
}
