"use client";

/**
 * AgreementViewer — what an investor reads before signing (DEALROOM2).
 *
 * A template agreement shows its rendered text. An uploaded PDF shows EVERY
 * page as an image the server renders (GET …/agreements/{id}/pages/{n}), and
 * a joinder's Exhibit A (the Underlying NDA PDF) follows the same way. All of
 * it sits in one scrolling box; `onScrolledToEnd` fires once the reader
 * reaches the bottom, which is what enables Sign (lib/dealRoom.signEnabled).
 * Uploaded agreements have no fill-in fields — the viewer says so.
 */

import { isScrolledToEnd, viewerPages } from "@/lib/dealRoom.mjs";

export default function AgreementViewer({ dealId, doc, onScrolledToEnd }) {
  if (!doc) return null;
  const pages = viewerPages(dealId, doc);
  const check = (el) => {
    if (el && isScrolledToEnd(el)) onScrolledToEnd?.();
  };
  return (
    <div className="space-y-2">
      {doc.kind === "upload" && (
        <p className="text-xs text-text-muted" data-testid="upload-note">
          Uploaded by the publisher as a PDF{doc.version ? ` (version ${doc.version})` : ""} — {doc.page_count} page
          {doc.page_count === 1 ? "" : "s"}. This document has no fill-in fields; you sign it exactly as shown.
        </p>
      )}
      {doc.template_status === "draft" && (
        <p className="text-xs text-[#9B2335]">Template marked DRAFT — pending counsel review.</p>
      )}
      <div
        data-testid="agreement-viewer"
        className="max-h-[32rem] overflow-auto rounded border border-border bg-bg-app p-4"
        onScroll={(e) => check(e.currentTarget)}
        ref={(el) => {
          // a short document that never scrolls is read once it is on screen
          if (el && el.scrollHeight <= el.clientHeight + 4) check(el);
        }}
      >
        {doc.kind !== "upload" && (
          <pre className="whitespace-pre-wrap font-sans text-sm text-text-primary">{doc.text}</pre>
        )}
        {pages.map((p) => (
          <figure key={p.src} className="mb-4">
            <img src={p.src} alt={p.label} className="w-full rounded-[6px] border border-[#ece8dd] bg-white" />
            <figcaption className="mt-1 text-center text-xs text-text-muted">{p.label}</figcaption>
          </figure>
        ))}
      </div>
      {pages.length > 0 && (
        <p className="text-xs text-text-muted">Scroll to the last page to enable signing.</p>
      )}
    </div>
  );
}
