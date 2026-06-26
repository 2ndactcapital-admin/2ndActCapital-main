"use client";

import { useState, useTransition } from "react";
import { IconThumbUp, IconThumbDown } from "@tabler/icons-react";

async function castVote(dealId, vote) {
  try {
    const res = await fetch("/api/marketplace/vote", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ dealId, vote }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      console.error("[VoteButtons] vote failed", res.status, data);
    }
    return data;
  } catch (error) {
    console.error("[VoteButtons] request threw:", error?.message || error);
    return { ok: false, error: error?.message };
  }
}

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
      const res = await castVote(dealId, vote);
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
        <IconThumbUp size={iconSize} stroke={2} />
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
        <IconThumbDown size={iconSize} stroke={2} />
        <span className="tabular-nums">{downvotes}</span>
      </button>
    </div>
  );
}
