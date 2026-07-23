"use client";

import { useState, useTransition } from "react";
import {
  revokeGrantAction,
  upsertGrantAction,
} from "@/lib/tradingAuthorityActions";

const CARD = {
  borderColor: "#ece8dd",
  boxShadow: "0 1px 3px rgba(0,0,0,0.06)",
};

// Plain-language description of each tier's money-movement authority.
const TIER_HINT = {
  inquiry: "View only — cannot propose any money movement.",
  limited: "May propose movement within an account; cannot direct funds to a third party.",
  full: "May direct funds to any third party (custody-triggering).",
};

function userLabel(u) {
  return u.full_name || u.email || u.id;
}

function entityLabel(e) {
  return e.display_name || e.name || e.id;
}

function Card({ title, hint, children }) {
  return (
    <section className="rounded-lg border bg-bg-card p-5" style={CARD}>
      <h2 className="text-base font-semibold text-navy">{title}</h2>
      {hint && <p className="mt-1 text-sm text-text-muted">{hint}</p>}
      <div className="mt-4">{children}</div>
    </section>
  );
}

function inputClass() {
  return "rounded-md border border-border bg-bg-card px-3 py-2 text-sm text-text-primary outline-none focus:ring-2 focus:ring-navy";
}

export default function TradingAuthorityManager({
  initialGrants = [],
  users = [],
  entities = [],
  tiers = ["inquiry", "limited", "full"],
}) {
  const [grants, setGrants] = useState(initialGrants);
  const [pending, startTransition] = useTransition();
  const [error, setError] = useState(null);

  const [entityId, setEntityId] = useState("");
  const [userId, setUserId] = useState("");
  const [tier, setTier] = useState(tiers[0] || "inquiry");

  function run(fn) {
    setError(null);
    startTransition(async () => {
      const res = await fn();
      if (!res.ok) setError(res.error || "Request failed.");
      return res;
    });
  }

  function submit() {
    if (!entityId || !userId) {
      setError("Pick an account and a member.");
      return;
    }
    run(async () => {
      const res = await upsertGrantAction(entityId, userId, tier);
      if (res.ok) {
        setGrants(res.grants);
        setUserId("");
      }
      return res;
    });
  }

  function revoke(gEntityId, gUserId) {
    run(async () => {
      const res = await revokeGrantAction(gEntityId, gUserId);
      if (res.ok) setGrants(res.grants);
      return res;
    });
  }

  return (
    <div className="mt-6 space-y-6">
      {error && (
        <div className="rounded-md border border-border bg-bg-card px-4 py-2 text-sm text-[#9B2335]">
          {error}
        </div>
      )}
      {pending && <p className="text-xs text-text-muted">Working…</p>}

      {/* Assign a tier */}
      <Card
        title="Assign trading authority"
        hint="Set a member's authority tier for an account. This gates who may propose a money-movement action; the proposer and approver must always be different people."
      >
        <div className="flex flex-wrap items-end gap-3">
          <div className="flex flex-col">
            <label className="text-xs font-medium uppercase tracking-wide text-text-muted">
              Account
            </label>
            <select
              className={`mt-1 w-64 ${inputClass()}`}
              value={entityId}
              onChange={(e) => setEntityId(e.target.value)}
            >
              <option value="">Select an account…</option>
              {entities.map((e) => (
                <option key={e.id} value={e.id}>
                  {entityLabel(e)}
                </option>
              ))}
            </select>
          </div>
          <div className="flex flex-col">
            <label className="text-xs font-medium uppercase tracking-wide text-text-muted">
              Member
            </label>
            <select
              className={`mt-1 w-64 ${inputClass()}`}
              value={userId}
              onChange={(e) => setUserId(e.target.value)}
            >
              <option value="">Select a member…</option>
              {users.map((u) => (
                <option key={u.id} value={u.id}>
                  {userLabel(u)}
                </option>
              ))}
            </select>
          </div>
          <div className="flex flex-col">
            <label className="text-xs font-medium uppercase tracking-wide text-text-muted">
              Tier
            </label>
            <select
              className={`mt-1 w-48 ${inputClass()}`}
              value={tier}
              onChange={(e) => setTier(e.target.value)}
            >
              {tiers.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
          </div>
          <button
            type="button"
            onClick={submit}
            disabled={pending}
            className="rounded-md bg-navy px-4 py-2 text-sm font-medium text-bg-app hover:opacity-90 disabled:opacity-60"
          >
            Assign
          </button>
        </div>
        <p className="mt-3 text-xs text-text-muted">{TIER_HINT[tier]}</p>
      </Card>

      {/* Current grants */}
      <Card title="Current grants" hint="Each member's authority tier per account.">
        {grants.length === 0 ? (
          <p className="text-sm text-text-muted">No trading-authority grants yet.</p>
        ) : (
          <ul className="space-y-2">
            {grants.map((g) => (
              <li
                key={g.id}
                className="flex items-center justify-between rounded-md border border-border bg-bg-app p-3"
              >
                <div className="text-sm text-text-primary">
                  <span className="font-medium">
                    {g.user_name || g.user_email || g.user_id}
                  </span>
                  <span className="mx-2 text-text-muted">on</span>
                  <span className="font-medium">
                    {g.entity_name || g.entity_id}
                  </span>
                  <span
                    className="ml-2 rounded px-2 py-0.5 text-xs font-semibold uppercase tracking-wide"
                    style={{ background: "#F5F1EB", color: "#1B2B4B" }}
                  >
                    {g.authority_tier}
                  </span>
                </div>
                <button
                  type="button"
                  onClick={() => revoke(g.entity_id, g.user_id)}
                  disabled={pending}
                  className="text-xs text-[#9B2335] hover:underline disabled:opacity-60"
                >
                  Revoke
                </button>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
