"use client";

// Chancery Phase 11b — semantic (vector) document search (client).
// Submits a natural-language query; the backend embeds it (Voyage), ranks by
// pgvector cosine similarity, and applies the same visibility engines as the
// rest of the app. Each result cites back to its source document. Rule 5: calls
// the Next route, not FastAPI.

import { useState } from "react";
import SearchResultList from "@/components/documents/SearchResultList";

export default function SemanticSearch() {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState(null); // null = not searched yet
  const [notIndexed, setNotIndexed] = useState([]);
  const [notIndexedNote, setNotIndexedNote] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  async function runSearch(e) {
    e.preventDefault();
    const q = query.trim();
    if (!q) {
      setResults([]);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(
        `/api/semantic-search?q=${encodeURIComponent(q)}`,
        { cache: "no-store" },
      );
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError(data.error || "Search failed.");
        setResults([]);
      } else {
        setResults(Array.isArray(data.results) ? data.results : []);
        // BUGFIX1 — visible documents matching the words but with no embedding.
        setNotIndexed(Array.isArray(data.not_indexed) ? data.not_indexed : []);
        setNotIndexedNote(data.not_indexed_note || null);
      }
    } catch (err) {
      setError(err.message || "Search failed.");
      setResults([]);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="mt-6">
      <form onSubmit={runSearch} className="flex flex-wrap items-center gap-3">
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Describe what you're looking for…"
          className="min-w-0 flex-1 rounded-md border border-border bg-bg-card px-3 py-2 text-sm text-text-primary outline-none focus:ring-2 focus:ring-navy"
          aria-label="Semantic document search"
        />
        <button
          type="submit"
          disabled={loading}
          className="rounded-md bg-navy px-4 py-2 text-sm font-medium text-bg-app hover:opacity-90 disabled:opacity-60"
        >
          {loading ? "Searching…" : "Search"}
        </button>
      </form>

      <div className="mt-6">
        {error ? (
          <p className="text-sm text-[#9B2335]">{error}</p>
        ) : results === null ? (
          <p className="text-sm text-text-muted">
            Enter a description above to search this organization's documents by
            meaning.
          </p>
        ) : (
          <>
            {results.length === 0 ? (
              <p className="text-sm text-text-muted">No documents matched by meaning.</p>
            ) : (
              <SearchResultList
                results={results}
                extraTag={(d) => (
                  <span className="inline-flex items-center rounded-full bg-bg-app px-2 py-0.5 text-[10px] font-medium text-text-muted">
                    {Math.round((d.similarity ?? 0) * 100)}% match
                  </span>
                )}
              />
            )}
            {notIndexed.length > 0 && (
              <div className="mt-6">
                <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-text-muted">
                  Not yet indexed for meaning search
                </h3>
                <SearchResultList results={notIndexed} note={notIndexedNote} />
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
