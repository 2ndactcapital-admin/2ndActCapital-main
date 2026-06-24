"use client";

import { useState, useTransition } from "react";
import { IconChevronUp, IconChevronDown } from "@tabler/icons-react";
import { voteDealAction } from "@/lib/marketplaceActions";

// Up/down vote control with toggle behavior. Optimistic, reconciled with the
// server's returned summary.
export default function VoteButtons({
  dealId,
  initialUpvotes = 0,
  initialDownvotes = 0,
  initialUserVote = null,
  size = "sm",
}) {
  const [upvotes, setUpvotes] = useState(initialUpvotes);
  const [downvotes, setDownvotes] = useState(initialDownvotes);
  const [userVote, setUserVote] = useState(initialUserVote);
  const [pending, startTransition] = useTransition();

  function cast(vote, e) {
    e?.preventDefault();
    e?.stopPropagation();
    if (pending) return;
    startTransition(async () => {
      const res = await voteDealAction(dealId, vote);
      if (res.ok && res.summary) {
        setUpvotes(res.summary.upvotes ?? 0);
        setDownvotes(res.summary.downvotes ?? 0);
        setUserVote(res.summary.user_vote ?? null);
      }
    });
  }

  const iconSize = size === "lg" ? 22 : 18;
  const textCls = size === "lg" ? "text-base" : "text-sm";

  return (
    <div className={`flex items-center gap-3 ${textCls}`}>
      <button
        type="button"
        onClick={(e) => cast(1, e)}
        disabled={pending}
        aria-label="Upvote"
        className={`flex items-center gap-1 rounded-md px-1.5 py-1 transition-colors hover:bg-border disabled:opacity-60 ${
          userVote === 1 ? "text-gold" : "text-text-muted"
        }`}
      >
        <IconChevronUp size={iconSize} stroke={2} />
        <span className="tabular-nums">{upvotes}</span>
      </button>
      <button
        type="button"
        onClick={(e) => cast(-1, e)}
        disabled={pending}
        aria-label="Downvote"
        className={`flex items-center gap-1 rounded-md px-1.5 py-1 transition-colors hover:bg-border disabled:opacity-60 ${
          userVote === -1 ? "text-navy" : "text-text-muted"
        }`}
      >
        <IconChevronDown size={iconSize} stroke={2} />
        <span className="tabular-nums">{downvotes}</span>
      </button>
    </div>
  );
}
