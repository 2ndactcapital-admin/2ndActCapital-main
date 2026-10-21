import { formatDate } from "@/lib/format";

// BUGFIX1 (R2-DOC-03) — one result list for keyword search and for semantic
// search's "not yet indexed" section. Every row says where the document lives
// (the server's source_label: Document vault / Deal / Entity / SPV, plus the
// record's name) and links to it (the server's href). No hooks, so node tests
// render the real markup.
const MATCH_LABEL = { filename: "filename", category: "category", extracted_text: "text" };

export default function SearchResultList({ results, note = null, extraTag = null }) {
  return (
    <ul className="divide-y divide-border rounded-lg border border-border bg-bg-card">
      {results.map((d) => (
        <li key={`${d.source || "vault"}:${d.document_id}`} className="px-4 py-3" data-testid="search-result"
            data-source={d.source || "vault"}>
          <a href={d.href || `/admin/document-review/${d.document_id}`}
             className="group flex flex-wrap items-center justify-between gap-3">
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-medium text-navy group-hover:underline">
                {d.original_filename || "Untitled document"}
              </p>
              <div className="mt-1 flex flex-wrap items-center gap-1.5 text-xs text-text-muted">
                <span className="rounded-sm bg-gold-light px-1.5 py-0.5 font-medium text-navy" data-testid="source-label">
                  {d.source_label || "Document vault"}
                </span>
                {d.parent_name && <span>{d.parent_name}</span>}
                {d.doc_family && <span>{"· "}{d.doc_family}</span>}
                {d.created_at && <span>{"· "}{formatDate(d.created_at)}</span>}
              </div>
              {note && <p className="mt-1 text-xs italic text-text-muted" data-testid="index-note">{note}</p>}
            </div>
            <div className="flex shrink-0 flex-wrap items-center gap-1.5">
              {extraTag ? extraTag(d) : (d.matched_on || []).map((m) => (
                <span key={m} className="inline-flex items-center rounded-full bg-bg-app px-2 py-0.5 text-[10px] font-medium text-text-muted">
                  {MATCH_LABEL[m] || m}
                </span>
              ))}
            </div>
          </a>
        </li>
      ))}
    </ul>
  );
}
