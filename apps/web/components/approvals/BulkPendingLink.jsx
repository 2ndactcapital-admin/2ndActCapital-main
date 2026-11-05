"use client";

/**
 * BulkPendingLink — "N pending — review in bulk" (BULKAPPROVE1). Reads the
 * server's own counts (GET /approvals/summary, optionally for one household
 * or account) and renders the link only when more than one pending item is
 * waiting for THIS viewer's decision (lib/bulkApprove.mjs pendingLink).
 * Renders nothing on a refusal, an error or a lost envelope.
 */

import { useEffect, useState } from "react";
import { pendingLink } from "@/lib/bulkApprove.mjs";

export function BulkPendingLinkView({ summary, type }) {
  const link = pendingLink(summary, type);
  if (!link) return null;
  return (
    <p className="text-sm" data-bulk-link="1">
      <a className="text-navy underline" href={link.href}>{link.text}</a>
    </p>
  );
}

export default function BulkPendingLink({ householdId, accountId, type }) {
  const [summary, setSummary] = useState(null);
  useEffect(() => {
    let live = true;
    const q = new URLSearchParams();
    if (householdId) q.set("household_id", householdId);
    if (accountId) q.set("account_id", accountId);
    fetch(`/api/approvals/summary${q.toString() ? `?${q}` : ""}`).then((r) => (r.ok ? r.json() : null))
      .then((d) => { if (live) setSummary(d); }).catch(() => {});
    return () => { live = false; };
  }, [householdId, accountId]);
  return <BulkPendingLinkView summary={summary} type={type} />;
}
