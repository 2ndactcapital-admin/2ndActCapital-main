"use client";

/**
 * CommitmentLookupForm — TA Model Sprint 3.
 *
 * Task 1a discovery: no commitments list/detail screen exists anywhere in
 * this app, and no general "list commitments for an org" endpoint exists in
 * the backend either (services/portfolio_commitments.py has get_commitment
 * by id, create_commitment, and tax_chase_list by tax year — nothing that
 * lists an org's commitments generally). Building that list endpoint is a
 * separate subsystem, out of this sprint's scope. This is the minimal, real
 * navigation surface instead: a commitment id, typed or pasted, routed
 * straight to its real projection screen.
 */

import { useState } from "react";
import { useRouter } from "next/navigation";

const CONTROL =
  "w-full rounded border border-[var(--2a-border)] bg-white px-3 py-2 text-sm text-[var(--2a-text)] focus:outline-none focus:ring-1 focus:ring-[var(--2a-gold)]";

export default function CommitmentLookupForm() {
  const router = useRouter();
  const [value, setValue] = useState("");

  function go(e) {
    e.preventDefault();
    const id = value.trim();
    if (!id) return;
    router.push(`/portfolio/commitments/${encodeURIComponent(id)}`);
  }

  return (
    <form onSubmit={go} className="flex items-end gap-2">
      <label className="block flex-1">
        <span className="block text-[10px] font-semibold uppercase tracking-[0.12em] text-[var(--2a-text-muted)]">
          Commitment id
        </span>
        <input
          className={`${CONTROL} mt-1`}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder="e.g. 3c1f9c2e-…"
        />
      </label>
      <button
        type="submit"
        className="rounded bg-[var(--2a-navy)] px-4 py-2 text-xs font-medium text-white"
      >
        View projection
      </button>
    </form>
  );
}
