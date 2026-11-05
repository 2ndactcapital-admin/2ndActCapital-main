"use client";

// PORTENTRY1 — the CRM's per-org vocabularies from GET /api/crm/vocabularies (the API envelope). Until it loads, or
// if it fails, the vocabulary is EMPTY — never a client-side default list.
import { useEffect, useState } from "react";

export function useCrmVocab() {
  const [vocab, setVocab] = useState(null);
  useEffect(() => {
    let live = true;
    fetch("/api/crm/vocabularies", { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => { if (live) setVocab(d?.vocabularies || null); })
      .catch(() => { if (live) setVocab(null); });
    return () => { live = false; };
  }, []);
  return vocab;
}
