"use client";

/**
 * DocumentViewer — a data-room document read page by page (DEALROOM3).
 *
 * The original never reaches the browser: each page is a PNG the API renders
 * (and watermarks when the room watermarks), requested one at a time from
 * /api/deal-room/deals/{deal}/documents/{doc}/pages/{n}. There is NO download
 * control here — under view-only the server sends none, and the viewer says
 * plainly what view-only can and cannot do. Every page view is logged
 * server-side.
 */

import { useState } from "react";

export default function DocumentViewer({ dealId, info, viewOnlyNotice, onClose }) {
  const [page, setPage] = useState(1);
  if (!info) return null;
  const n = info.page_count || 0;
  const src = `/api/deal-room/deals/${dealId}/documents/${info.document_id}/pages/${page}`;
  return (
    <div className="mt-3 rounded-[6px] border border-[#ece8dd] bg-white p-4" data-testid="document-viewer">
      <div className="flex flex-wrap items-center justify-between gap-2 text-sm text-text-secondary">
        <span className="font-medium text-navy">{info.file_name}</span>
        <span>
          Page {page} of {n}
          {info.watermarked ? " · watermarked with your name" : ""}
        </span>
      </div>
      {viewOnlyNotice && <p className="mt-2 text-xs text-text-muted" data-testid="view-only-notice">{viewOnlyNotice}</p>}
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src={src} alt={`${info.file_name} — page ${page}`} className="mt-3 w-full border border-border"
        onContextMenu={(e) => e.preventDefault()} draggable={false} />
      <div className="mt-3 flex items-center gap-2">
        <button type="button" disabled={page <= 1} className="rounded border border-navy px-3 py-1 text-xs text-navy disabled:opacity-40"
          onClick={() => setPage((p) => Math.max(1, p - 1))}>Previous</button>
        <button type="button" disabled={page >= n} className="rounded border border-navy px-3 py-1 text-xs text-navy disabled:opacity-40"
          onClick={() => setPage((p) => Math.min(n, p + 1))}>Next</button>
        {onClose && (
          <button type="button" className="ml-auto text-xs text-text-muted hover:underline" onClick={onClose}>Close</button>
        )}
      </div>
    </div>
  );
}
