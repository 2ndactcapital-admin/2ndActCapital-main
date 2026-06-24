"use client";

import { useActionState, useState } from "react";
import {
  approveComplianceReviewAction,
  denyComplianceReviewAction,
} from "@/lib/marketplaceActions";
import { formatDate } from "@/lib/format";

function RequestRow({ dealId, req }) {
  const [approveState, approveAction, approvePending] = useActionState(
    approveComplianceReviewAction.bind(null, dealId, req.id),
    {}
  );
  const [denyState, denyAction, denyPending] = useActionState(
    denyComplianceReviewAction.bind(null, dealId, req.id),
    {}
  );

  const resolved = req.status !== "pending" || approveState?.ok || denyState?.ok;
  const currentStatus = approveState?.ok
    ? "approved"
    : denyState?.ok
    ? "denied"
    : req.status;

  const statusColor =
    currentStatus === "approved"
      ? "text-[#166534] bg-[#DCFCE7]"
      : currentStatus === "denied"
      ? "text-[#9B2335] bg-[#FEF2F2]"
      : "text-text-muted bg-border";

  return (
    <div className="rounded-lg border border-border bg-bg-card p-4">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0 flex-1">
          <p className="text-xs text-text-muted">User: {String(req.user_id).slice(0, 8)}…</p>
          {req.request_notes && (
            <p className="mt-1 text-sm text-text-secondary">{req.request_notes}</p>
          )}
          <p className="mt-1 text-xs text-text-muted">
            {formatDate(req.created_at)}
          </p>
        </div>
        <span
          className={`shrink-0 rounded-full px-2.5 py-0.5 text-xs font-medium ${statusColor}`}
        >
          {currentStatus}
        </span>
      </div>

      {!resolved && (
        <div className="mt-3 flex gap-2">
          <form action={approveAction}>
            <button
              type="submit"
              disabled={approvePending || denyPending}
              className="rounded-md bg-navy px-3 py-1.5 text-xs font-medium text-bg-app hover:opacity-90 disabled:opacity-60"
            >
              {approvePending ? "Approving…" : "Approve"}
            </button>
          </form>
          <form action={denyAction}>
            <button
              type="submit"
              disabled={approvePending || denyPending}
              className="rounded-md border border-border px-3 py-1.5 text-xs font-medium text-text-secondary hover:bg-border disabled:opacity-60"
            >
              {denyPending ? "Denying…" : "Deny"}
            </button>
          </form>
          {(approveState?.error || denyState?.error) && (
            <p className="text-xs text-[#9B2335]">
              {approveState?.error || denyState?.error}
            </p>
          )}
        </div>
      )}
    </div>
  );
}

export default function ComplianceRequests({ dealId, initial = [] }) {
  const [requests] = useState(initial);

  return (
    <section>
      <h2 className="text-base font-semibold text-navy">
        Compliance Review Requests
      </h2>
      <div className="mt-4 space-y-3">
        {requests.map((req) => (
          <RequestRow key={req.id} dealId={dealId} req={req} />
        ))}
      </div>
    </section>
  );
}
